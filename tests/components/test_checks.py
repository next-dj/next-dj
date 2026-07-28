from pathlib import Path
from unittest.mock import patch

import pytest

from next.checks import (
    check_component_context_registration_files,
    check_component_py_no_pages_context,
    check_cross_root_component_name_conflicts,
    check_duplicate_component_names,
    reset_check_caches,
)
from next.components import ComponentInfo, FileComponentsBackend
from next.components.context import ComponentContextRegistry, component
from tests.support import (
    importable_dir,
    next_framework_settings_for_checks_backends_value as _next_framework_settings_for_checks_backends_value,
    patch_checks_components_manager,
)


@pytest.fixture(autouse=True)
def _reset_check_caches():
    reset_check_caches()
    yield
    reset_check_caches()


class TestChecks:
    """Tests for component-related Django checks."""

    def test_check_duplicate_component_names_empty_when_no_config(
        self, min_component_config: dict
    ) -> None:
        """check_duplicate_component_names returns [] when backends is not a list."""
        mock_ns = _next_framework_settings_for_checks_backends_value(None)
        with patch("next.components.checks.next_framework_settings", mock_ns):
            assert check_duplicate_component_names() == []

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

    def test_check_duplicate_component_names_root_and_nested_scope(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """Same name at root route scope and under a nested route is rejected."""
        root = tmp_path.resolve()
        (tmp_path / "a.djx").write_text("a")
        (tmp_path / "b.djx").write_text("b")
        fake_backend = FileComponentsBackend(dict(min_component_config))
        fake_backend._registry.register(
            ComponentInfo("card", root, "", tmp_path / "a.djx", None, True)
        )
        fake_backend._registry.register(
            ComponentInfo("card", root, "blog", tmp_path / "b.djx", None, True)
        )
        fake_backend._loaded = True
        with patch_checks_components_manager(fake_backend):
            errors = check_duplicate_component_names()
        assert any(e.id == "next.E020" for e in errors)

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

    def test_check_cross_root_component_name_conflicts_reports(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """Same root-level name on two page trees is rejected."""
        custom = (tmp_path / "custom").resolve()
        pages = (tmp_path / "pages").resolve()
        custom.mkdir()
        pages.mkdir()
        (custom / "c.djx").write_text("x")
        (pages / "c.djx").write_text("y")
        fake_backend = FileComponentsBackend(dict(min_component_config))
        fake_backend._registry.register(
            ComponentInfo("hero", custom, "", custom / "c.djx", None, True)
        )
        fake_backend._registry.register(
            ComponentInfo("hero", pages, "", pages / "c.djx", None, True)
        )
        fake_backend._loaded = True
        with patch_checks_components_manager(fake_backend):
            errors = check_cross_root_component_name_conflicts()
        assert any(e.id == "next.E034" for e in errors)

    def test_check_component_py_no_pages_context_reports_import(
        self, tmp_path: Path, min_component_config: dict
    ) -> None:
        """check_component_py_no_pages_context reports when component.py imports context from next.pages."""
        (tmp_path / "component.py").write_text("from next.pages import context\n")
        fake_backend = FileComponentsBackend(dict(min_component_config))

        fake_backend._registry.register(
            ComponentInfo("bad", tmp_path, "", None, tmp_path / "component.py", False)
        )
        fake_backend._loaded = True

        with patch_checks_components_manager(fake_backend):
            errors = check_component_py_no_pages_context()
        assert any(e.id == "next.E021" for e in errors)

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
