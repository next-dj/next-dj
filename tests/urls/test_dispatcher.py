from pathlib import Path
from unittest.mock import patch

from next.urls.dispatcher import scan_pages_tree


class TestScanPagesDirectory:
    """Edge cases for the standalone scan helper including skip_dir_names."""

    def test_oserror_on_iterdir_returns_nothing(self, tmp_path) -> None:
        """OSError from iterdir produces no routes."""
        with patch.object(Path, "iterdir", side_effect=OSError):
            result = list(scan_pages_tree(tmp_path))
        assert result == []

    def test_virtual_page_template_djx_only(self, tmp_path) -> None:
        """template.djx without page.py yields a synthetic page path at root."""
        (tmp_path / "template.djx").write_text("<h1>Hi</h1>")
        result = list(scan_pages_tree(tmp_path))
        assert len(result) == 1
        url_path, file_path = result[0]
        assert url_path == ""
        assert file_path.name == "page.py"

    def test_scan_recursive_with_subdir_and_page_py(self, tmp_path) -> None:
        """Root and nested page.py files both appear in results."""
        (tmp_path / "page.py").write_text("x = 1")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "page.py").write_text("y = 2")
        result = list(scan_pages_tree(tmp_path))
        assert len(result) == 2
        url_paths = {r[0] for r in result}
        assert "" in url_paths
        assert "sub" in url_paths

    def test_skip_dir_names_excludes_component_folder(self, tmp_path) -> None:
        """Skipped directory names do not appear in URL paths."""
        (tmp_path / "page.py").write_text("x = 1")
        (tmp_path / "home").mkdir()
        (tmp_path / "home" / "page.py").write_text("y = 2")
        (tmp_path / "_components").mkdir()
        (tmp_path / "_components" / "card.djx").write_text("<div>card</div>")
        (tmp_path / "_components" / "nested").mkdir()
        (tmp_path / "_components" / "nested" / "page.py").write_text("z = 3")
        result = list(scan_pages_tree(tmp_path, skip_dir_names=("_components",)))
        url_paths = {r[0] for r in result}
        assert "" in url_paths
        assert "home" in url_paths
        assert "_components" not in url_paths
        assert "_components/nested" not in url_paths
        assert len(result) == 2

    def test_scan_pages_tree_with_register_invokes_hook(self, tmp_path) -> None:
        """Component folders call the unified registration hook when enabled."""
        (tmp_path / "_components").mkdir()
        calls: list[tuple[Path, Path, str]] = []

        def capture(folder: Path, root: Path, scope: str) -> None:
            calls.append((folder, root, scope))

        with patch(
            "next.urls.dispatcher.register_components_folder_from_router_walk", capture
        ):
            list(
                scan_pages_tree(
                    tmp_path,
                    skip_dir_names=("_components",),
                    register_components=True,
                    components_folder_name="_components",
                )
            )
        assert len(calls) == 1
        assert calls[0][0].name == "_components"
