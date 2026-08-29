from pathlib import Path
from unittest.mock import patch

import pytest
from django.core.checks import run_checks
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from next.checks import (
    NEXT,
    check_component_context_registration_files,
    check_component_py_no_pages_context,
    check_cross_root_component_name_conflicts,
    check_duplicate_component_names,
    check_next_components_configuration,
    register_all,
)
from next.checks.common import get_components_manager
from next.components import ComponentInfo, FileComponentsBackend
from next.components.context import ComponentContextRegistry, component
from next.components.manager import components_manager
from tests.support import (
    file_router_config_entry,
    importable_dir,
    next_framework_settings_component_backends_list as _next_framework_settings_component_backends_list,
    next_framework_settings_for_checks_backends_value as _next_framework_settings_for_checks_backends_value,
    patch_checks_components_manager,
)


class TestChecks:
    """Tests for component-related Django checks."""

    def test_check_duplicate_component_names_empty_when_no_config(
        self, min_component_config: dict
    ) -> None:
        """check_duplicate_component_names returns [] when backends is not a list."""
        mock_ns = _next_framework_settings_for_checks_backends_value(None)
        with patch("next.components.checks.next_framework_settings", mock_ns):
            assert check_duplicate_component_names() == []

    def test_backend_failing_at_import_is_reported(self) -> None:
        """A backend module raising at import becomes next.E032, not a traceback."""
        mock_ns = _next_framework_settings_component_backends_list(
            [
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
                "next.checks.common.import_class_cached",
                side_effect=ImproperlyConfigured("MYAPP_KEY is unset"),
            ),
        ):
            errors = check_next_components_configuration()
        assert [e.id for e in errors] == ["next.E032"]
        assert "MYAPP_KEY is unset" in errors[0].msg

    def test_check_component_py_no_pages_context_empty_when_no_config(self) -> None:
        """check_component_py_no_pages_context returns [] when backends is not a list."""
        mock_ns = _next_framework_settings_for_checks_backends_value(None)
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

    No router has walked during these runs, so the store has to find the
    folders under the page trees itself.
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
        # The `next` tag excludes Django's URL check, so nothing walks the page
        # tree during this run and the check has to find the folder itself.
        pages = self._write_project(tmp_path)
        register_all()

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
        register_all()

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
        register_all()

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
        register_all()

        with (
            patch.object(component, "_registry", ComponentContextRegistry()),
            override_settings(NEXT_FRAMEWORK=self._framework_settings(pages)),
        ):
            messages = run_checks(tags=[NEXT])

        e021 = [m for m in messages if m.id == "next.E021"]
        assert [m.obj for m in e021] == [str(module_path)]

    def test_a_duplicate_name_under_a_page_tree_is_e020(self, tmp_path: Path) -> None:
        # `card.djx` and `card/component.djx` sit in one `_components` folder,
        # so both carry the same scope and only order decides which renders.
        pages = self._write_project(tmp_path)
        (pages / "_components" / "card.djx").write_text("<p>other card</p>\n")
        register_all()

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
        # One walk feeds the per-run store, so a check asking twice sees each
        # component once and the live manager keeps whatever it already held.
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
            patch("next.checks.common.get_router_manager", return_value=(None, [])),
        ):
            manager = get_components_manager()
            assert manager._walk_registered_folders == set()
