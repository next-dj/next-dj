from pathlib import Path
from unittest.mock import patch

from next.pages.registry import (
    get_layout_djx_paths_for_watch,
    get_template_djx_paths_for_watch,
)


class TestGetLayoutDjxPathsForWatch:
    """Tests for get_layout_djx_paths_for_watch()."""

    def test_returns_layout_djx_paths_under_pages_dirs(self, tmp_path) -> None:
        """Returns resolved paths of all layout.djx under given pages dirs."""
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "layout.djx").write_text("<div>a</div>")
        (tmp_path / "a" / "b").mkdir()
        (tmp_path / "a" / "b" / "layout.djx").write_text("<div>b</div>")
        with patch("next.pages.registry.get_pages_directories_for_watch") as mock_watch:
            mock_watch.return_value = [tmp_path]
            result = get_layout_djx_paths_for_watch()
        assert len(result) == 2
        parent_names = {p.parent.name for p in result}
        assert parent_names == {"a", "b"}
        assert all(p.name == "layout.djx" for p in result)

    def test_returns_empty_when_no_layout_djx(self, tmp_path) -> None:
        """Returns empty set when no layout.djx under pages dirs."""
        with patch("next.pages.registry.get_pages_directories_for_watch") as mock_watch:
            mock_watch.return_value = [tmp_path]
            result = get_layout_djx_paths_for_watch()
        assert result == set()

    def test_swallows_oserror_on_rglob_layout(self, tmp_path) -> None:
        """When rglob raises OSError (e.g. permission), log and return partial result."""
        with (
            patch("next.pages.registry.get_pages_directories_for_watch") as mock_watch,
            patch.object(Path, "rglob", side_effect=OSError(13, "Permission denied")),
        ):
            mock_watch.return_value = [tmp_path]
            result = get_layout_djx_paths_for_watch()
        assert result == set()


class TestGetTemplateDjxPathsForWatch:
    """Tests for get_template_djx_paths_for_watch()."""

    def test_returns_template_djx_paths_under_pages_dirs(self, tmp_path) -> None:
        """Returns resolved paths of all template.djx under given pages dirs."""
        (tmp_path / "x").mkdir()
        (tmp_path / "x" / "template.djx").write_text("x")
        (tmp_path / "x" / "y").mkdir()
        (tmp_path / "x" / "y" / "template.djx").write_text("y")
        with patch("next.pages.registry.get_pages_directories_for_watch") as mock_watch:
            mock_watch.return_value = [tmp_path]
            result = get_template_djx_paths_for_watch()
        assert len(result) == 2
        assert all(p.name == "template.djx" for p in result)
        parent_names = {p.parent.name for p in result}
        assert parent_names == {"x", "y"}

    def test_returns_empty_when_no_template_djx(self, tmp_path) -> None:
        """Returns empty set when no template.djx under pages dirs."""
        with patch("next.pages.registry.get_pages_directories_for_watch") as mock_watch:
            mock_watch.return_value = [tmp_path]
            result = get_template_djx_paths_for_watch()
        assert result == set()

    def test_swallows_oserror_on_rglob_template(self, tmp_path) -> None:
        """When rglob raises OSError (e.g. permission), log and return partial result."""
        with (
            patch("next.pages.registry.get_pages_directories_for_watch") as mock_watch,
            patch.object(Path, "rglob", side_effect=OSError(13, "Permission denied")),
        ):
            mock_watch.return_value = [tmp_path]
            result = get_template_djx_paths_for_watch()
        assert result == set()
