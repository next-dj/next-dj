from pathlib import Path
from unittest.mock import patch

from next.urls.dispatcher import scan_pages_tree
from next.utils import classify_dirs_entries


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


class TestClassifyDirsEntries:
    """Branch coverage for ``next.urls.classify_dirs_entries``."""

    def test_segment_when_relative_name_only(self) -> None:
        """A bare name becomes a segment when it is not a path under base_dir."""
        roots, segs = classify_dirs_entries(["extras"], Path("/nonexistent"))
        assert roots == []
        assert "extras" in segs

    def test_resolves_existing_dir_under_base(self, tmp_path: Path) -> None:
        """A relative path that exists under base_dir is classified as a path root."""
        sub = tmp_path / "nest"
        sub.mkdir()
        roots, _segs = classify_dirs_entries([Path("nest")], tmp_path)
        assert roots == [sub.resolve()]

    def test_resolves_nested_relative_path(self, tmp_path: Path) -> None:
        """A path string with a slash can resolve under base_dir when it exists."""
        nested = tmp_path / "x" / "y"
        nested.mkdir(parents=True)
        roots, _segs = classify_dirs_entries([Path("x/y")], tmp_path)
        assert roots == [nested.resolve()]

    def test_slash_path_that_is_file_becomes_segment(self, tmp_path: Path) -> None:
        """When a path with a slash exists but is a file, it is treated as a segment name."""
        f = tmp_path / "a" / "b"
        f.parent.mkdir(parents=True)
        f.write_text("x")
        roots, segs = classify_dirs_entries([Path("a/b")], tmp_path)
        assert roots == []
        assert "b" in segs

    def test_a_relative_entry_without_a_base_dir_becomes_a_segment(self) -> None:
        """Without a base dir a relative entry can only name a URL segment."""
        roots, segs = classify_dirs_entries(["shop"], None)
        assert roots == []
        assert segs == frozenset({"shop"})

    def test_skips_empty_and_dot_entries(self) -> None:
        """Empty strings and dot entries are ignored."""
        roots, segs = classify_dirs_entries(["", ".", None], Path("/tmp"))
        assert roots == []
        assert segs == frozenset()

    def test_an_entry_of_separators_alone_names_no_segment(self, tmp_path) -> None:
        """A separator-only entry reaches `skip_dir_names` as nothing at all."""
        roots, segs = classify_dirs_entries(["\\", "./"], tmp_path)
        assert roots == []
        assert segs == frozenset()
