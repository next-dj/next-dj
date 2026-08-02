from pathlib import Path

import pytest
from django.test import override_settings

from next.components import DummyBackend, components_manager
from next.testing import (
    clear_loaded_dirs,
    eager_load_components,
    eager_load_pages,
    loaders,
)


@pytest.fixture(autouse=True)
def _reset_loader_memo() -> None:
    clear_loaded_dirs()


def _write_page(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def _component_backend_config(
    root: Path, component_name: str, marker: Path
) -> dict[str, object]:
    """Write a component tree whose ``component.py`` touches ``marker`` on import."""
    comp_dir = root / "_components" / component_name
    comp_dir.mkdir(parents=True)
    (comp_dir / "component.djx").write_text("<div/>")
    (comp_dir / "component.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('loaded')\n"
    )
    return {"DIRS": [str(root / "_components")], "COMPONENTS_DIR": "_components"}


class TestEagerLoadPages:
    """eager_load_pages imports page.py modules under the directory."""

    def test_loads_all_page_py_files(self, tmp_path: Path) -> None:
        _write_page(tmp_path / "page.py", "VALUE = 1\n")
        _write_page(tmp_path / "nested" / "page.py", "VALUE = 2\n")
        loaded = eager_load_pages(tmp_path)
        assert len(loaded) == 2
        assert all(p.name == "page.py" for p in loaded)

    def test_accepts_string_path(self, tmp_path: Path) -> None:
        _write_page(tmp_path / "page.py", "VALUE = 3\n")
        loaded = eager_load_pages(str(tmp_path))
        assert len(loaded) == 1

    def test_is_idempotent_for_same_directory(self, tmp_path: Path) -> None:
        _write_page(tmp_path / "page.py", "VALUE = 4\n")
        eager_load_pages(tmp_path)
        assert eager_load_pages(tmp_path) == []

    def test_missing_directory_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="Pages directory not found"):
            eager_load_pages(tmp_path / "missing")

    def test_errors_in_page_bubble_up(self, tmp_path: Path) -> None:
        _write_page(tmp_path / "page.py", "raise RuntimeError('boom')\n")
        with pytest.raises(RuntimeError, match="boom"):
            eager_load_pages(tmp_path)

    def test_handles_bracket_segments(self, tmp_path: Path) -> None:
        _write_page(tmp_path / "[int:id]" / "page.py", "VALUE = 5\n")
        loaded = eager_load_pages(tmp_path)
        assert len(loaded) == 1

    def test_import_spec_failure_raises(self, tmp_path: Path, monkeypatch) -> None:
        _write_page(tmp_path / "page.py", "VALUE = 6\n")

        def fake_spec(*args, **kwargs) -> None:
            return None

        monkeypatch.setattr(
            loaders.importlib.util, "spec_from_file_location", fake_spec
        )
        with pytest.raises(ImportError, match="Cannot build import spec"):
            eager_load_pages(tmp_path)

    def test_clear_loaded_dirs_allows_reload(self, tmp_path: Path) -> None:
        _write_page(tmp_path / "page.py", "VALUE = 7\n")
        eager_load_pages(tmp_path)
        clear_loaded_dirs()
        loaded = eager_load_pages(tmp_path)
        assert len(loaded) == 1


class _RecordingBackend(DummyBackend):
    """Backend that answers both eager hooks and notes each call."""

    def __init__(self, label: str, calls: list[str]) -> None:
        super().__init__({})
        self._label = label
        self._calls = calls

    def discover(self) -> None:
        self._calls.append(f"discover:{self._label}")

    def import_component_modules(self) -> tuple[Path, ...]:
        self._calls.append(f"import:{self._label}")
        return ()


class _StubManager:
    """Components manager double exposing only the public backend list."""

    def __init__(self, *backends: DummyBackend) -> None:
        self._backends = backends
        self.reads = 0

    @property
    def backends(self) -> tuple[DummyBackend, ...]:
        self.reads += 1
        return self._backends


class TestEagerLoadComponents:
    def test_calls_both_hooks_on_every_backend(self, monkeypatch) -> None:
        calls: list[str] = []
        manager = _StubManager(
            _RecordingBackend("b1", calls), _RecordingBackend("b2", calls)
        )

        monkeypatch.setattr(loaders, "components_manager", manager)
        eager_load_components()
        assert calls == ["discover:b1", "import:b1", "discover:b2", "import:b2"]
        assert manager.reads == 1

    def test_imports_component_py_when_lazy_modules_enabled(
        self, tmp_path: Path
    ) -> None:
        """``eager_load_components`` loads every ``component.py`` even under lazy startup."""
        marker = tmp_path / "imported.txt"
        config = _component_backend_config(tmp_path, "eager_widget", marker)
        try:
            with override_settings(
                NEXT_FRAMEWORK={
                    "COMPONENT_BACKENDS": [config],
                    "LAZY_COMPONENT_MODULES": True,
                }
            ):
                components_manager.reload()
                assert not marker.exists()
                eager_load_components()
                assert marker.read_text() == "loaded"
        finally:
            components_manager.reload()

    def test_a_backend_that_implements_neither_hook_keeps_working(
        self, monkeypatch
    ) -> None:
        # A backend resolving names on demand owns no modules, so both
        # hooks keep their inherited no-op behaviour.
        backend = DummyBackend({})
        manager = _StubManager(backend)
        monkeypatch.setattr(loaders, "components_manager", manager)
        eager_load_components()
        assert backend.import_component_modules() == ()
        assert backend.get_component("card", Path("t.djx")) is None
