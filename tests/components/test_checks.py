from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.checks import run_checks
from django.core.checks.registry import registry as check_registry
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from next.checks import NEXT
from next.components import ComponentInfo, FileComponentsBackend
from next.components.checks import (
    check_component_context_registration_files,
    check_component_context_reserved_parameters,
    check_component_error_self_reference,
    check_component_module_imports,
    check_component_py_no_pages_context,
    check_component_token_age,
    check_cross_root_component_name_conflicts,
    check_deferred_component_error,
    check_deferred_component_loading,
    check_duplicate_component_names,
    check_keyless_context_props,
    check_next_components_configuration,
)
from next.components.context import ComponentContextRegistry, component
from next.components.defer import defer_declarations
from next.components.info import BoundaryFiles
from next.components.loading import ModuleLoader
from next.components.manager import components_manager
from next.components.sources import get_components_manager
from tests.support import (
    file_router_config_entry,
    importable_dir,
    next_framework_settings_stand_in as _stand_in,
    patch_checks_components_manager,
)


class TestChecks:
    """Tests for component-related Django checks."""

    def test_check_duplicate_component_names_empty_when_no_config(
        self, min_component_config: dict
    ) -> None:
        """check_duplicate_component_names returns [] when backends is not a list."""
        mock_ns = _stand_in(COMPONENT_BACKENDS=None)
        with patch("next.components.checks.next_framework_settings", mock_ns):
            assert check_duplicate_component_names() == []

    def test_backend_failing_at_import_is_reported(self) -> None:
        """A backend module raising at import becomes next.E032, not a traceback."""
        mock_ns = _stand_in(
            COMPONENT_BACKENDS=[
                {
                    "BACKEND": "myapp.backends.Broken",
                    "DIRS": [],
                    "COMPONENTS_DIR": "_components",
                }
            ]
        )
        with (
            patch("next.components.checks.next_framework_settings", mock_ns),
            patch(
                "next.conf.imports.import_class_cached",
                side_effect=ImproperlyConfigured("MYAPP_KEY is unset"),
            ),
        ):
            errors = check_next_components_configuration()
        assert [e.id for e in errors] == ["next.E032"]
        assert "MYAPP_KEY is unset" in errors[0].msg

    def test_check_component_py_no_pages_context_empty_when_no_config(self) -> None:
        """check_component_py_no_pages_context returns [] when backends is not a list."""
        mock_ns = _stand_in(COMPONENT_BACKENDS=None)
        with patch("next.components.checks.next_framework_settings", mock_ns):
            assert check_component_py_no_pages_context() == []

    def test_check_duplicate_component_names_reports_duplicate(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """check_duplicate_component_names reports when same name in same scope."""
        (tmp_path / "a.djx").write_text("a")
        (tmp_path / "b.djx").write_text("b")
        fake_backend = FileComponentsBackend(dict(min_component_config))

        fake_backend._registry.register(
            ComponentInfo("card", tmp_path, "", tmp_path / "a.djx", None, True)
        )
        fake_backend._registry.register(
            ComponentInfo("card", tmp_path, "", tmp_path / "b.djx", None, True)
        )
        fake_backend._loaded = True

        with patch_checks_components_manager(fake_backend):
            errors = check_duplicate_component_names()
        assert any(e.id == "next.E020" for e in errors)

    def test_check_duplicate_component_names_allows_a_route_scoped_override(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """A name reused under a deeper route trail is the documented override."""
        root = tmp_path.resolve()
        (root / "blog").mkdir()
        (root / "a.djx").write_text("a")
        (root / "b.djx").write_text("b")
        fake_backend = FileComponentsBackend(dict(min_component_config))
        fake_backend._registry.register(
            ComponentInfo("card", root, "", root / "a.djx", None, True)
        )
        fake_backend._registry.register(
            ComponentInfo("card", root, "blog", root / "b.djx", None, True)
        )
        fake_backend._loaded = True

        visible = fake_backend.collect_visible_components(root / "blog" / "page.djx")
        assert visible["card"].template_path == root / "b.djx"

        with patch_checks_components_manager(fake_backend):
            assert check_duplicate_component_names() == []

    def test_check_cross_root_component_name_conflicts_empty_single_tree(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """One page tree can reuse a name only under different route scopes."""
        root = tmp_path.resolve()
        (tmp_path / "a.djx").write_text("a")
        fake_backend = FileComponentsBackend(dict(min_component_config))
        fake_backend._registry.register(
            ComponentInfo("card", root, "", tmp_path / "a.djx", None, True)
        )
        fake_backend._loaded = True
        with patch_checks_components_manager(fake_backend):
            assert check_cross_root_component_name_conflicts() == []

    def test_two_configured_roots_sharing_a_name_are_reported(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """Two DIRS roots answer everywhere, so only order picks the winner."""
        first = (tmp_path / "kit").resolve()
        second = (tmp_path / "shared").resolve()
        pages = (tmp_path / "pages").resolve()
        for directory in (first, second, pages):
            directory.mkdir()
        (first / "hero.djx").write_text("x")
        (second / "hero.djx").write_text("y")
        fake_backend = FileComponentsBackend(dict(min_component_config))
        for root in (first, second):
            fake_backend._registry.mark_as_root(root)
            fake_backend._registry.register(
                ComponentInfo("hero", root, "", root / "hero.djx", None, True)
            )
        fake_backend._loaded = True

        visible = fake_backend.collect_visible_components(pages / "page.djx")
        assert visible["hero"].template_path == first / "hero.djx"

        with patch_checks_components_manager(fake_backend):
            errors = check_cross_root_component_name_conflicts()
        assert [e.id for e in errors] == ["next.E034"]
        assert str(first) in errors[0].msg
        assert str(second) in errors[0].msg

    def test_a_page_tree_overriding_a_configured_root_is_not_a_conflict(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """The resolver hands the page tree the win, so the pair is decided."""
        shared = (tmp_path / "shared").resolve()
        pages = (tmp_path / "pages").resolve()
        shared.mkdir()
        (pages / "blog").mkdir(parents=True)
        (shared / "hero.djx").write_text("x")
        (pages / "hero.djx").write_text("y")
        fake_backend = FileComponentsBackend(dict(min_component_config))
        fake_backend._registry.mark_as_root(shared)
        fake_backend._registry.register(
            ComponentInfo("hero", shared, "", shared / "hero.djx", None, True)
        )
        fake_backend._registry.register(
            ComponentInfo("hero", pages, "", pages / "hero.djx", None, True)
        )
        fake_backend._loaded = True

        visible = fake_backend.collect_visible_components(pages / "blog" / "page.djx")
        assert visible["hero"].template_path == pages / "hero.djx"

        with patch_checks_components_manager(fake_backend):
            assert check_cross_root_component_name_conflicts() == []

    def test_two_disjoint_page_trees_may_share_a_name(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """Neither tree is visible from the other, so the name is free twice."""
        first = (tmp_path / "site").resolve()
        second = (tmp_path / "docs").resolve()
        first.mkdir()
        second.mkdir()
        (first / "hero.djx").write_text("x")
        (second / "hero.djx").write_text("y")
        fake_backend = FileComponentsBackend(dict(min_component_config))
        for root in (first, second):
            fake_backend._registry.register(
                ComponentInfo("hero", root, "", root / "hero.djx", None, True)
            )
        fake_backend._loaded = True

        visible = fake_backend.collect_visible_components(second / "page.djx")
        assert visible["hero"].template_path == second / "hero.djx"

        with patch_checks_components_manager(fake_backend):
            assert check_cross_root_component_name_conflicts() == []

    def test_nested_page_trees_sharing_a_name_are_reported(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """A tree inside another leaves both at the same score under the inner one."""
        outer = (tmp_path / "site").resolve()
        inner = (outer / "admin").resolve()
        inner.mkdir(parents=True)
        (outer / "hero.djx").write_text("x")
        (inner / "hero.djx").write_text("y")
        fake_backend = FileComponentsBackend(dict(min_component_config))
        for root in (outer, inner):
            fake_backend._registry.register(
                ComponentInfo("hero", root, "", root / "hero.djx", None, True)
            )
        fake_backend._loaded = True

        visible = fake_backend.collect_visible_components(inner / "page.djx")
        assert visible["hero"].template_path == outer / "hero.djx"

        with patch_checks_components_manager(fake_backend):
            errors = check_cross_root_component_name_conflicts()
        assert [e.id for e in errors] == ["next.E034"]

    @pytest.mark.parametrize(
        "source",
        [
            "from next.pages import context\n",
            "from next import context\n",
            "from next import context as page_context\n",
            "from next import page\n\n@page.context\ndef data():\n    return {}\n",
            "import next\n\n@next.context\ndef data():\n    return {}\n",
            "import next\n\n@next.page.context\ndef data():\n    return {}\n",
            "import next.pages\n\n@next.pages.context\ndef data():\n    return {}\n",
        ],
        ids=[
            "from_next_pages",
            "from_next",
            "aliased_import",
            "page_attribute",
            "next_attribute",
            "next_page_attribute",
            "next_pages_attribute",
        ],
    )
    def test_check_component_py_no_pages_context_reports_import(
        self, source: str, tmp_path: Path, min_component_config: dict
    ) -> None:
        """Every spelling of the page context decorator in component.py is reported."""
        (tmp_path / "component.py").write_text(source)
        fake_backend = FileComponentsBackend(dict(min_component_config))

        fake_backend._registry.register(
            ComponentInfo("bad", tmp_path, "", None, tmp_path / "component.py", False)
        )
        fake_backend._loaded = True

        with patch_checks_components_manager(fake_backend):
            errors = check_component_py_no_pages_context()
        assert any(e.id == "next.E021" for e in errors)

    @pytest.mark.parametrize(
        "source",
        [
            "from next import component\n\n@component.context\ndef data():\n    return {}\n",
            "from next.components import context\n",
            "from next import component\n\n@component.page.context\ndef data():\n    return {}\n",
            "def loader():\n    return None\n\n@loader().context\ndef data():\n    return {}\n",
        ],
        ids=[
            "component_attribute",
            "from_next_components",
            "component_page_attribute",
            "call_result_attribute",
        ],
    )
    def test_check_component_py_no_pages_context_allows_component_context(
        self, source: str, tmp_path: Path, min_component_config: dict
    ) -> None:
        """The component context decorator stays clean under either spelling."""
        (tmp_path / "component.py").write_text(source)
        fake_backend = FileComponentsBackend(dict(min_component_config))

        fake_backend._registry.register(
            ComponentInfo("good", tmp_path, "", None, tmp_path / "component.py", False)
        )
        fake_backend._loaded = True

        with patch_checks_components_manager(fake_backend):
            errors = check_component_py_no_pages_context()
        assert errors == []

    def test_component_context_on_imported_helper_is_e075(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """A component context bound to a helper module is reported as E075."""
        module_path = tmp_path / "component.py"
        module_path.write_text(
            "from next.components import context\n"
            "from tests.support.attribution import handler_declared_here\n\n"
            "context('greeting')(handler_declared_here)\n"
        )
        fake_backend = FileComponentsBackend(dict(min_component_config))
        fake_backend._registry.register(
            ComponentInfo("card", tmp_path, "", None, module_path, False)
        )
        fake_backend._loaded = True

        with (
            patch.object(component, "_registry", ComponentContextRegistry()),
            patch_checks_components_manager(fake_backend),
        ):
            errors = check_component_context_registration_files()

        assert [e.id for e in errors] == ["next.E075"]
        assert "attribution.py" in errors[0].msg
        assert "handler_declared_here" in errors[0].msg
        assert str(module_path) in errors[0].msg

    def test_component_context_from_another_component_py_is_e075(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """A callable imported from a sibling component.py is still reported."""
        donor_dir = tmp_path / "donor"
        donor_dir.mkdir()
        (donor_dir / "__init__.py").write_text("")
        (donor_dir / "component.py").write_text(
            "def donated() -> str:\n    return 'hi'\n"
        )
        module_path = tmp_path / "component.py"
        module_path.write_text(
            "from next.components import context\n"
            "from donor.component import donated\n\n"
            "context('greeting')(donated)\n"
        )
        fake_backend = FileComponentsBackend(dict(min_component_config))
        fake_backend._registry.register(
            ComponentInfo("card", tmp_path, "", None, module_path, False)
        )
        fake_backend._loaded = True

        with (
            patch.object(component, "_registry", ComponentContextRegistry()),
            patch_checks_components_manager(fake_backend),
            importable_dir(tmp_path),
        ):
            errors = check_component_context_registration_files()

        assert [e.id for e in errors] == ["next.E075"]
        assert "donated" in errors[0].msg
        assert str(donor_dir / "component.py") in errors[0].msg

    def test_a_component_py_that_fails_to_import_is_e084(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """A swallowed import failure reaches the browser as a context that never ran."""
        module_path = tmp_path / "component.py"
        module_path.write_text("from nowhere_at_all import BRAND\n")
        fake_backend = FileComponentsBackend(dict(min_component_config))
        fake_backend._registry.register(
            ComponentInfo("card", tmp_path, "", None, module_path, False)
        )
        fake_backend._loaded = True

        with patch_checks_components_manager(fake_backend):
            errors = check_component_module_imports()

        assert [e.id for e in errors] == ["next.E084"]
        assert "nowhere_at_all" in errors[0].msg

    def test_a_component_py_that_imports_reports_nothing(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        module_path = tmp_path / "component.py"
        module_path.write_text("BRAND = 'next'\n")
        fake_backend = FileComponentsBackend(dict(min_component_config))
        fake_backend._registry.register(
            ComponentInfo("card", tmp_path, "", None, module_path, False)
        )
        fake_backend._loaded = True

        with patch_checks_components_manager(fake_backend):
            assert check_component_module_imports() == []

    def test_check_component_module_imports_empty_when_no_config(self) -> None:
        mock_ns = _stand_in(COMPONENT_BACKENDS=None)
        with patch("next.components.checks.next_framework_settings", mock_ns):
            assert check_component_module_imports() == []

    def test_component_context_declared_in_component_py_reports_nothing(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """A component context declared in the component.py raises nothing."""
        module_path = tmp_path / "component.py"
        module_path.write_text(
            "from next.components import context\n\n"
            "@context('greeting')\n"
            "def greeting() -> str:\n"
            "    return 'hi'\n"
        )
        fake_backend = FileComponentsBackend(dict(min_component_config))
        fake_backend._registry.register(
            ComponentInfo("card", tmp_path, "", None, module_path, False)
        )
        fake_backend._loaded = True

        with (
            patch.object(component, "_registry", ComponentContextRegistry()),
            patch_checks_components_manager(fake_backend),
        ):
            assert check_component_context_registration_files() == []


class TestPageTreeComponentsReachTheChecks:
    """Every check reading the per-run store sees a page-tree component.

    No router has walked here, so the store finds the page-tree folders itself.
    """

    def _write_project(self, tmp_path: Path) -> Path:
        pages = tmp_path / "pages"
        (pages / "hello").mkdir(parents=True)
        (pages / "hello" / "page.py").write_text('template = "ok"\n')
        card = pages / "_components" / "card"
        card.mkdir(parents=True)
        (card / "component.djx").write_text("<p>card</p>\n")
        (card / "component.py").write_text(
            "from next.components import context\n"
            "from tests.support.attribution import handler_declared_here\n\n"
            "context('greeting')(handler_declared_here)\n"
        )
        return pages

    def _framework_settings(self, pages: Path) -> dict:
        return {
            "PAGE_BACKENDS": [file_router_config_entry(pages_dir=pages)],
            "COMPONENT_BACKENDS": [
                {
                    "BACKEND": "next.components.FileComponentsBackend",
                    "DIRS": [],
                    "COMPONENTS_DIR": "_components",
                }
            ],
        }

    def test_the_next_tag_run_reports_the_misattribution(self, tmp_path: Path) -> None:
        # The `next` tag excludes Django's URL check, so the check finds the folder.
        pages = self._write_project(tmp_path)

        with (
            patch.object(component, "_registry", ComponentContextRegistry()),
            override_settings(NEXT_FRAMEWORK=self._framework_settings(pages)),
        ):
            messages = run_checks(tags=[NEXT])

        e075 = [m for m in messages if m.id == "next.E075"]
        assert len(e075) == 1
        assert str(pages / "_components" / "card" / "component.py") in e075[0].msg
        assert "handler_declared_here" in e075[0].msg

    def test_a_correctly_declared_context_stays_silent(self, tmp_path: Path) -> None:
        pages = self._write_project(tmp_path)
        (pages / "_components" / "card" / "component.py").write_text(
            "from next.components import context\n\n"
            "@context('greeting')\n"
            "def greeting() -> str:\n"
            "    return 'hi'\n"
        )

        with (
            patch.object(component, "_registry", ComponentContextRegistry()),
            override_settings(NEXT_FRAMEWORK=self._framework_settings(pages)),
        ):
            messages = run_checks(tags=[NEXT])

        assert [m for m in messages if m.id == "next.E075"] == []

    def test_a_zone_in_a_page_tree_component_is_e065(self, tmp_path: Path) -> None:
        pages = self._write_project(tmp_path)
        template = pages / "_components" / "card" / "component.djx"
        template.write_text('<div>{% zone "inner" %}x{% endzone %}</div>\n')

        with (
            patch.object(component, "_registry", ComponentContextRegistry()),
            override_settings(NEXT_FRAMEWORK=self._framework_settings(pages)),
        ):
            messages = run_checks(tags=[NEXT])

        assert [m.obj for m in messages if m.id == "next.E065"] == [str(template)]

    def test_a_page_tree_component_using_the_page_context_is_e021(
        self, tmp_path: Path
    ) -> None:
        pages = self._write_project(tmp_path)
        module_path = pages / "_components" / "card" / "component.py"
        module_path.write_text(
            "from next.pages import context\n\n"
            "@context('greeting')\n"
            "def greeting() -> str:\n"
            "    return 'hi'\n"
        )

        with (
            patch.object(component, "_registry", ComponentContextRegistry()),
            override_settings(NEXT_FRAMEWORK=self._framework_settings(pages)),
        ):
            messages = run_checks(tags=[NEXT])

        e021 = [m for m in messages if m.id == "next.E021"]
        assert [m.obj for m in e021] == [str(module_path)]

    def test_a_duplicate_name_under_a_page_tree_is_e020(self, tmp_path: Path) -> None:
        # `card.djx` and `card/component.djx` share a scope, so order picks the render.
        pages = self._write_project(tmp_path)
        (pages / "_components" / "card.djx").write_text("<p>other card</p>\n")

        with (
            patch.object(component, "_registry", ComponentContextRegistry()),
            override_settings(NEXT_FRAMEWORK=self._framework_settings(pages)),
        ):
            messages = run_checks(tags=[NEXT])

        e020 = [m for m in messages if m.id == "next.E020"]
        assert len(e020) == 1
        assert 'Component name "card"' in e020[0].msg
        assert [m for m in messages if m.id == "next.E034"] == []

    def test_the_store_the_checks_read_claims_the_folder_once(
        self, tmp_path: Path
    ) -> None:
        # One walk feeds the per-run store, so a repeat sees each component once.
        pages = self._write_project(tmp_path)

        with (
            override_settings(NEXT_FRAMEWORK=self._framework_settings(pages)),
            patch.object(component, "_registry", ComponentContextRegistry()),
        ):
            manager = get_components_manager()
            assert get_components_manager() is manager
            claimed = set(manager._walk_registered_folders)
            registered = [
                info.name
                for backend in manager._backends
                if isinstance(backend, FileComponentsBackend)
                for info in backend._registry
            ]
            live_claimed = set(components_manager._walk_registered_folders)

        assert claimed == {(pages / "_components").resolve()}
        assert registered == ["card"]
        assert live_claimed == set()

    def test_a_failing_router_manager_registers_nothing(self, tmp_path: Path) -> None:
        pages = self._write_project(tmp_path)

        with (
            override_settings(NEXT_FRAMEWORK=self._framework_settings(pages)),
            patch(
                "next.components.sources.get_router_manager", return_value=(None, [])
            ),
        ):
            manager = get_components_manager()
            assert manager._walk_registered_folders == set()


class TestComponentConfigurationCodes:
    """Each `COMPONENT_BACKENDS` mistake carries a code of its own."""

    def test_non_dict_backend_entry_is_e079(self) -> None:
        mock_ns = _stand_in(
            COMPONENT_BACKENDS=["next.components.FileComponentsBackend"]
        )
        with patch("next.components.checks.next_framework_settings", mock_ns):
            errors = check_next_components_configuration()
        assert [e.id for e in errors] == ["next.E079"]

    def test_non_string_components_dir_is_e080(self) -> None:
        mock_ns = _stand_in(
            COMPONENT_BACKENDS=[
                {
                    "BACKEND": "next.components.FileComponentsBackend",
                    "DIRS": [],
                    "COMPONENTS_DIR": 1,
                }
            ]
        )
        with patch("next.components.checks.next_framework_settings", mock_ns):
            errors = check_next_components_configuration()
        assert [e.id for e in errors] == ["next.E080"]

    @pytest.mark.parametrize(
        ("backends", "expected"),
        [
            ([], ["next.E033"]),
            ([{"DIRS": []}], ["next.E031", "next.E031"]),
            (
                [{"BACKEND": 1, "DIRS": [], "COMPONENTS_DIR": "_components"}],
                ["next.E056"],
            ),
            (
                [
                    {
                        "BACKEND": "pathlib.Path",
                        "DIRS": [],
                        "COMPONENTS_DIR": "_components",
                    }
                ],
                ["next.E055"],
            ),
            (
                [
                    {
                        "BACKEND": "next.components.FileComponentsBackend",
                        "DIRS": "templates",
                        "COMPONENTS_DIR": "_components",
                    }
                ],
                ["next.E057"],
            ),
        ],
        ids=["empty", "missing_keys", "non_string", "wrong_class", "dirs"],
    )
    def test_backend_shape_mistakes_carry_their_codes(
        self, backends: object, expected: list[str]
    ) -> None:
        mock_ns = _stand_in(COMPONENT_BACKENDS=backends)
        with patch("next.components.checks.next_framework_settings", mock_ns):
            errors = check_next_components_configuration()
        assert [e.id for e in errors] == expected


class TestComponentChecksEdgeCases:
    """Checks skip what they cannot read and stay quiet without backends."""

    @pytest.mark.parametrize(
        "check",
        [
            check_cross_root_component_name_conflicts,
            check_component_context_registration_files,
        ],
    )
    def test_no_configured_backends_reports_nothing(self, check) -> None:
        mock_ns = _stand_in(COMPONENT_BACKENDS=[])
        with patch("next.components.checks.next_framework_settings", mock_ns):
            assert check() == []

    def test_route_scoped_names_are_left_out_of_the_root_scope_check(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        root = tmp_path.resolve()
        fake_backend = FileComponentsBackend(dict(min_component_config))
        for scope_root in (root, root / "nested"):
            fake_backend._registry.register(
                ComponentInfo("card", scope_root, "blog", None, None, True)
            )
        fake_backend._loaded = True
        with patch_checks_components_manager(fake_backend):
            assert check_cross_root_component_name_conflicts() == []

    @pytest.mark.parametrize(
        "content",
        [b"\xff\xfe\x00broken", b"def broken(:\n"],
        ids=["undecodable", "syntax_error"],
    )
    def test_unreadable_component_py_is_not_reported(
        self, content: bytes, tmp_path: Path, min_component_config: dict
    ) -> None:
        module_path = tmp_path / "component.py"
        module_path.write_bytes(content)
        fake_backend = FileComponentsBackend(dict(min_component_config))
        fake_backend._registry.register(
            ComponentInfo("bad", tmp_path, "", None, module_path, False)
        )
        fake_backend._loaded = True
        with patch_checks_components_manager(fake_backend):
            assert check_component_py_no_pages_context() == []

    def test_missing_component_py_is_skipped(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        fake_backend = FileComponentsBackend(dict(min_component_config))
        fake_backend._registry.register(
            ComponentInfo("gone", tmp_path, "", None, tmp_path / "component.py", False)
        )
        fake_backend._loaded = True
        with patch_checks_components_manager(fake_backend):
            assert check_component_py_no_pages_context() == []


class _Opaque:
    """A callable whose signature `inspect` cannot read."""

    __signature__ = "unreadable"

    def __call__(self) -> str:
        return "x"


def stats(timeout: int = 3, delay: int = 0) -> int:
    return timeout + delay


def plain(sku: str) -> str:
    return sku


class TestReservedContextParameters:
    """`check_component_context_reserved_parameters` warns as next.W158."""

    @pytest.fixture()
    def module_path(self, tmp_path: Path) -> Iterator[Path]:
        path = tmp_path.resolve() / "widget" / "component.py"
        yield path
        component.registry.unregister(path)

    def test_a_reserved_parameter_yields_w158(self, module_path: Path) -> None:
        component.registry.register(module_path, "stats", stats)
        messages = [
            m
            for m in check_component_context_reserved_parameters()
            if m.obj == str(module_path)
        ]
        assert [m.id for m in messages] == ["next.W158", "next.W158"]
        assert messages[0].msg == (
            f"@component.context stats in {module_path} takes the parameter "
            "'timeout', which the component tag reserves for its boundary options, "
            "so a call site cannot pass it as a prop."
        )
        assert messages[0].hint == (
            "Rename the parameter, the tag reserves defer, delay, reload, timeout."
        )
        assert "'delay'" in messages[1].msg

    @pytest.mark.parametrize("func", [plain, _Opaque()], ids=["plain", "opaque"])
    def test_silent_on_other_parameters(self, module_path: Path, func) -> None:
        component.registry.register(module_path, "value", func)
        assert [
            m
            for m in check_component_context_reserved_parameters()
            if m.obj == str(module_path)
        ] == []

    def test_silent_without_component_backends(self, module_path: Path) -> None:
        component.registry.register(module_path, "stats", stats)
        mock_ns = _stand_in(COMPONENT_BACKENDS=[])
        with patch("next.components.checks.next_framework_settings", mock_ns):
            messages = check_component_context_reserved_parameters()
        assert [m.id for m in messages if m.obj == str(module_path)] == [
            "next.W158",
            "next.W158",
        ]


class TestErrorSelfReference:
    """`check_component_error_self_reference` refuses a self-rendering error.djx."""

    def _check(self, tmp_path: Path, config: dict, error: bytes | None) -> list[object]:
        folder = tmp_path / "card"
        folder.mkdir()
        error_path = folder / "error.djx"
        if error is not None:
            error_path.write_bytes(error)
        backend = FileComponentsBackend(dict(config))
        backend._registry.register(
            ComponentInfo(
                "card",
                tmp_path,
                "",
                folder / "component.djx",
                None,
                False,
                files=BoundaryFiles(None, error_path),
            )
        )
        backend._registry.register(
            ComponentInfo("plain", tmp_path, "", tmp_path / "plain.djx", None, True)
        )
        backend._loaded = True
        with patch_checks_components_manager(backend):
            return check_component_error_self_reference()

    def test_a_tag_naming_the_component_is_next_e229(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        source = b"{% if x %}{% #component 'box' %}{% component 'card' %}{% /component %}{% endif %}"
        (message,) = self._check(tmp_path, min_component_config, source)
        assert message.id == "next.E229"
        assert message.obj == str(tmp_path / "card" / "error.djx")
        assert "'card'" in message.msg

    @pytest.mark.parametrize(
        "error",
        [b"{% component 'other' %}", b"{% if %}", b"\xff\xfe\x00", None],
        ids=["other_component", "syntax_error", "undecodable", "missing"],
    )
    def test_anything_else_is_quiet(
        self, tmp_path: Path, min_component_config: dict, error: bytes | None
    ) -> None:
        assert self._check(tmp_path, min_component_config, error) == []

    def test_no_configured_backends_reports_nothing(self) -> None:
        mock_ns = _stand_in(COMPONENT_BACKENDS=[])
        with patch("next.components.checks.next_framework_settings", mock_ns):
            assert check_component_error_self_reference() == []


_DEFERS = "from next.components import component\n\ncomponent.defer('stream')\n"
_PROPS_ONLY = "from next.components import component\n\ncomponent.props('title')\n"
_INLINE = "from next.components import component\n\ncomponent.defer('inline')\n"


class TestDeferredComponents:
    """A deferred component without a loading or an error UI warns as W157 or W170."""

    @pytest.fixture(autouse=True)
    def _clean_declarations(self) -> Iterator[None]:
        defer_declarations.reset()
        yield
        defer_declarations.reset()

    def _info(
        self,
        root: Path,
        name: str,
        *,
        module: str | None = None,
        loading: bool = False,
        error: bool = False,
    ) -> ComponentInfo:
        folder = root / name
        folder.mkdir()
        template_path = folder / "component.djx"
        template_path.write_text("<p>x</p>")
        module_path = None
        if module is not None:
            module_path = folder / "component.py"
            module_path.write_text(module)
        loading_path = error_path = None
        if loading:
            loading_path = folder / "loading.djx"
            loading_path.write_text("...")
        if error:
            error_path = folder / "error.djx"
            error_path.write_text("!")
        files = None
        if loading or error:
            files = BoundaryFiles(loading_path, error_path)
        return ComponentInfo(
            name, root, "", template_path, module_path, False, files=files
        )

    def _messages(
        self, config: dict, *infos: ComponentInfo
    ) -> tuple[list[object], list[object]]:
        backend = FileComponentsBackend(dict(config))
        backend._registry.register_many(infos)
        backend._loaded = True
        with patch_checks_components_manager(backend):
            return check_deferred_component_loading(), check_deferred_component_error()

    def test_a_declared_component_without_files_warns_twice(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        info = self._info(tmp_path.resolve(), "card", module=_DEFERS)
        (loading,), (error,) = self._messages(min_component_config, info)
        assert (loading.id, error.id) == ("next.W157", "next.W170")
        assert loading.obj == error.obj == str(info.module_path)
        assert loading.msg == (
            "Component 'card' defers its render but has no loading.djx, and "
            'DEFER["LOADING"] names no default, so its placeholder stays empty until '
            "the content arrives."
        )
        assert error.msg == (
            "Component 'card' defers its render but has no error.djx, and "
            'DEFER["ERROR"] names no default, so a failed render leaves an empty '
            "error wrapper."
        )

    def test_an_inline_component_lacks_only_the_error_ui(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        info = self._info(tmp_path.resolve(), "card", module=_INLINE)
        loading, (error,) = self._messages(min_component_config, info)
        assert loading == []
        assert error.id == "next.W170"

    def test_a_loading_file_defers_and_lacks_only_the_error_ui(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        info = self._info(tmp_path, "card", loading=True)
        loading, (error,) = self._messages(min_component_config, info)
        assert loading == []
        assert error.id == "next.W170"
        assert error.obj == str(info.template_path)

    def test_a_deferred_component_with_both_files_is_quiet(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        info = self._info(
            tmp_path.resolve(), "card", module=_DEFERS, loading=True, error=True
        )
        assert self._messages(min_component_config, info) == ([], [])

    @pytest.mark.parametrize(
        ("module", "error"),
        [(None, False), ("", False), (_PROPS_ONLY, False), (None, True)],
        ids=["no_module", "empty_module", "props_only", "error_file_only"],
    )
    def test_a_component_that_does_not_defer_is_quiet(
        self,
        tmp_path: Path,
        min_component_config: dict,
        module: str | None,
        *,
        error: bool,
    ) -> None:
        info = self._info(tmp_path.resolve(), "card", module=module, error=error)
        assert self._messages(min_component_config, info) == ([], [])

    def test_project_defaults_silence_both(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        info = self._info(tmp_path.resolve(), "card", module=_DEFERS)
        with override_settings(
            NEXT_FRAMEWORK={"DEFER": {"LOADING": "l.djx", "ERROR": "e.djx"}}
        ):
            assert self._messages(min_component_config, info) == ([], [])

    def test_no_configured_backends_reports_nothing(self) -> None:
        mock_ns = _stand_in(COMPONENT_BACKENDS=[])
        with patch("next.components.checks.next_framework_settings", mock_ns):
            assert check_deferred_component_loading() == []
            assert check_deferred_component_error() == []

    def test_both_checks_are_registered(self) -> None:
        registered = check_registry.get_checks()
        assert check_deferred_component_loading in registered
        assert check_deferred_component_error in registered


def _token_module(max_age: int) -> str:
    return (
        "from next.components import component\n\n"
        f"component.defer('load', tokens={{'max_age': {max_age}}})\n"
    )


class TestComponentTokenAge:
    """A deferred component whose token outlives one day warns as W182."""

    @pytest.fixture(autouse=True)
    def _clean_declarations(self) -> Iterator[None]:
        defer_declarations.reset()
        yield
        defer_declarations.reset()

    def _messages(self, config: dict, root: Path, module: str) -> list[object]:
        folder = root / "card"
        folder.mkdir()
        (folder / "component.djx").write_text("<p>x</p>")
        module_path = folder / "component.py"
        module_path.write_text(module)
        info = ComponentInfo(
            "card", root, "", folder / "component.djx", module_path, False
        )
        backend = FileComponentsBackend(dict(config))
        backend._registry.register_many([info])
        backend._loaded = True
        with patch_checks_components_manager(backend):
            return check_component_token_age()

    def test_a_token_above_one_day_warns(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        root = tmp_path.resolve()
        (message,) = self._messages(min_component_config, root, _token_module(86401))
        assert message.id == "next.W182"
        assert message.obj == str(root / "card" / "component.py")
        assert message.msg == (
            "Component 'card' declares a token max_age of 86401 seconds, above one "
            "day. A leaked reload token stays usable that long."
        )

    @pytest.mark.parametrize(
        "module", [_token_module(86400), _DEFERS], ids=["one_day", "no_tokens"]
    )
    def test_a_token_within_one_day_is_quiet(
        self, tmp_path: Path, min_component_config: dict, module: str
    ) -> None:
        root = tmp_path.resolve()
        assert self._messages(min_component_config, root, module) == []

    def test_the_check_is_registered(self) -> None:
        assert check_component_token_age in check_registry.get_checks()


_KEYLESS = """from next.components import component

component.props("x", "y")


@component.context
def defaults(flag: bool | None = False):
    if flag is None:
        return
    if flag:
        return dict(x=1, **{})
    if flag == 2:
        return list()

    def inner():
        return {"y": 2}

    return {"x": "c", "z": 1, 3: "n", **{}}


@component.context("y")
def keyed():
    return {"y": 1}
"""


class TestKeylessContextProps:
    """`check_keyless_context_props` warns as next.W185."""

    @pytest.fixture()
    def module_path(self, tmp_path: Path) -> Iterator[Path]:
        path = tmp_path.resolve() / "card" / "component.py"
        path.parent.mkdir()
        yield path
        component.registry.unregister(path)
        defer_declarations.reset()

    def _messages(self, module_path: Path) -> list:
        return [m for m in check_keyless_context_props() if m.obj == str(module_path)]

    def test_a_returned_declared_prop_yields_w185(self, module_path: Path) -> None:
        module_path.write_text(_KEYLESS)
        ModuleLoader().load(module_path)
        (message,) = self._messages(module_path)
        assert message.id == "next.W185"
        assert message.msg == (
            f"The keyless @component.context defaults in {module_path} returns 'x', "
            "which the component declares as a prop, so every call that passes it "
            "raises ComponentContextConflictError."
        )

    def test_silent_without_props_or_readable_source(self, module_path: Path) -> None:
        module_path.write_text(_KEYLESS.replace('component.props("x", "y")', ""))
        ModuleLoader().load(module_path)
        assert self._messages(module_path) == []
        component.registry.unregister(module_path)
        module_path.write_text(
            'from next.components import component\n\ncomponent.props("x")\n'
        )
        ModuleLoader().load(module_path)
        component.registry.register(module_path, None, _Opaque())
        assert self._messages(module_path) == []
