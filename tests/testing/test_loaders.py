from pathlib import Path

import pytest

from next.testing import clear_loaded_dirs, eager_load_pages, loaders


@pytest.fixture(autouse=True)
def _reset_loader_memo() -> None:
    clear_loaded_dirs()


def _write_page(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


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

        def fake_spec(*_args: object, **_kwargs: object) -> None:
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
