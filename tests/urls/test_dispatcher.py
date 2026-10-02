import os
from pathlib import Path
from unittest.mock import patch

from next.urls.dispatcher import scan_pages_tree
from tests.support import file_router


class TestScanPagesDirectory:
    """Edge cases for the standalone scan helper including skip_dir_names."""

    def test_a_directory_that_does_not_list_returns_nothing(self, tmp_path) -> None:
        """A tree the process cannot read produces no routes."""
        with patch("next.utils.os.scandir", side_effect=OSError):
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

    def test_another_skipped_folder_registers_nothing(self, tmp_path) -> None:
        """Only the components folder is registered, any other skip is passed over."""
        (tmp_path / "_private").mkdir()
        calls: list[Path] = []

        def capture(folder: Path, root: Path, scope: str) -> None:
            calls.append(folder)

        with patch(
            "next.urls.dispatcher.register_components_folder_from_router_walk", capture
        ):
            list(
                scan_pages_tree(
                    tmp_path,
                    skip_dir_names=("_private", "_components"),
                    register_components=True,
                    components_folder_name="_components",
                )
            )
        assert calls == []


class TestRouteOrder:
    """The page tree yields the same patterns whatever order the directory read gives."""

    @staticmethod
    def _tree(root: Path) -> None:
        for trail in (
            "",
            "blog",
            "blog/about",
            "blog/[slug]",
            "blog/[int:id]",
            "blog/[[rest]]",
            "zeta",
            "alpha",
        ):
            folder = root / trail
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "template.djx").write_text("<h1>Page</h1>")
        (root / "docs" / "intro").mkdir(parents=True)
        (root / "docs" / "intro" / "page.py").write_text("x = 1")

    def test_static_then_parameter_then_catch_all(self, tmp_path) -> None:
        self._tree(tmp_path)
        trails = [trail for trail, _file in scan_pages_tree(tmp_path)]
        assert trails == [
            "",
            "alpha",
            "blog",
            "blog/about",
            "blog/[int:id]",
            "blog/[slug]",
            "blog/[[rest]]",
            "docs/intro",
            "zeta",
        ]

    def test_a_reversed_directory_read_yields_the_same_patterns(self, tmp_path) -> None:
        self._tree(tmp_path)
        listed = [str(p.pattern) for p in file_router(dirs=[tmp_path]).generate_urls()]
        real_scandir = os.scandir

        class _Reversed:
            def __init__(self, path: object) -> None:
                self._scan = real_scandir(path)

            def __enter__(self) -> list[os.DirEntry[str]]:
                return list(reversed(list(self._scan.__enter__())))

            def __exit__(self, *exc: object) -> None:
                self._scan.__exit__(*exc)

        with patch("next.utils.os.scandir", _Reversed):
            reversed_read = [
                str(p.pattern) for p in file_router(dirs=[tmp_path]).generate_urls()
            ]
        assert reversed_read == listed
        assert listed.index("blog/about/") < listed.index("blog/<str:slug>/")

    def test_a_static_page_wins_over_a_parameter_sibling(self, tmp_path) -> None:
        self._tree(tmp_path)
        patterns = file_router(dirs=[tmp_path]).generate_urls()
        match = next(
            found
            for pattern in patterns
            if (found := pattern.resolve("blog/about/")) is not None
        )
        assert match.kwargs == {}
        assert match.route == "blog/about/"
