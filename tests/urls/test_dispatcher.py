from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import override_settings

from next.conf import next_framework_settings
from next.pages.watch import (
    get_pages_directories_for_watch,
    iter_pages_roots_with_components_folder_names,
)
from next.urls import FileRouterBackend, RouterBackend
from next.urls.dispatcher import (
    _register_components_folder,
    _scan_pages_directory,
    scan_pages_tree,
)
from next.utils import classify_dirs_entries


class TestGetPagesDirectoriesForWatch:
    """Watch list from next.pages.get_pages_directories_for_watch (used by utils and apps)."""

    def test_returns_empty_when_routers_not_list(self) -> None:
        """When ``ROUTERS`` is not a list, returns []."""
        mock_nf = SimpleNamespace(DEFAULT_PAGE_BACKENDS={})
        with patch("next.pages.watch.next_framework_settings", mock_nf):
            assert get_pages_directories_for_watch() == []

    def test_skips_non_dict_config(self) -> None:
        """List entries that are not dicts are skipped."""
        with override_settings(
            NEXT_FRAMEWORK={"DEFAULT_PAGE_BACKENDS": ["not a dict", None]},
        ):
            next_framework_settings.reload()
            assert get_pages_directories_for_watch() == []

    def test_swallows_backend_creation_error(self) -> None:
        """Invalid backend entry is skipped, valid entries still contribute."""
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_PAGE_BACKENDS": [
                    {"BACKEND": "nonexistent.Backend"},
                    {
                        "BACKEND": "next.urls.FileRouterBackend",
                        "PAGES_DIR": "pages",
                        "APP_DIRS": False,
                        "OPTIONS": {
                            "BASE_DIR": str(
                                Path(__file__).parent.parent.parent / "tests" / "pages",
                            ),
                        },
                    },
                ],
            },
        ):
            next_framework_settings.reload()
            result = get_pages_directories_for_watch()
            assert isinstance(result, list)

    def test_skips_non_file_router_backend(self) -> None:
        """When backend is not FileRouterBackend, its paths are not added."""
        with patch("next.urls.RouterFactory.create_backend") as mock_create:
            mock_backend = Mock(spec=RouterBackend)
            mock_backend._get_root_pages_paths = Mock(return_value=[])
            mock_backend._get_installed_apps = Mock(return_value=[])
            mock_create.return_value = mock_backend
            with override_settings(
                NEXT_FRAMEWORK={
                    "DEFAULT_PAGE_BACKENDS": [{"BACKEND": "other.Backend"}]
                },
            ):
                next_framework_settings.reload()
                assert get_pages_directories_for_watch() == []

    def test_includes_root_and_app_paths_from_backend(self, tmp_path) -> None:
        """Backend root paths and app pages paths are both included."""
        app_pages = tmp_path / "myapp_pages"
        app_pages.mkdir()
        with patch("next.urls.RouterFactory.create_backend") as mock_create:
            mock_backend = Mock(spec=FileRouterBackend)
            mock_backend.pages_dir = "pages"
            mock_backend.app_dirs = True
            mock_backend.options = {}
            mock_backend.generate_urls = Mock(return_value=[])
            mock_backend._get_root_pages_paths = Mock(
                return_value=[tmp_path / "root_pages"],
            )
            mock_backend._get_installed_apps = Mock(return_value=["myapp"])
            mock_backend._get_app_pages_path = Mock(return_value=app_pages)
            mock_backend._skip_dir_names = frozenset({"_components"})
            mock_create.return_value = mock_backend
            with override_settings(
                NEXT_FRAMEWORK={
                    "DEFAULT_PAGE_BACKENDS": [
                        {
                            "BACKEND": "next.urls.FileRouterBackend",
                            "PAGES_DIR": "pages",
                            "APP_DIRS": True,
                            "DIRS": [],
                            "OPTIONS": {},
                        },
                    ],
                },
            ):
                next_framework_settings.reload()
                result = get_pages_directories_for_watch()
                assert (tmp_path / "root_pages").resolve() in result
                assert app_pages.resolve() in result


class TestIterPagesRootsWithComponentsFolderNames:
    """Pairs ``(pages root, COMPONENTS_DIR)`` for autoreload globs."""

    def test_returns_empty_when_backends_not_list(self) -> None:
        """When ``DEFAULT_PAGE_BACKENDS`` is not a list, return []."""
        mock_nf = SimpleNamespace(DEFAULT_PAGE_BACKENDS=None)
        with patch("next.pages.watch.next_framework_settings", mock_nf):
            assert iter_pages_roots_with_components_folder_names() == []

    def test_skips_non_dict_config(self) -> None:
        """Non-dict entries are skipped."""
        with override_settings(
            NEXT_FRAMEWORK={"DEFAULT_PAGE_BACKENDS": ["not a dict"]},
        ):
            next_framework_settings.reload()
            assert iter_pages_roots_with_components_folder_names() == []

    def test_skips_non_file_router_backend(self) -> None:
        """When backend is not a filesystem discovery router, its paths are not added."""
        with patch("next.urls.RouterFactory.create_backend") as mock_create:
            mock_backend = Mock(spec=RouterBackend)
            mock_backend._get_root_pages_paths = Mock(return_value=[])
            mock_backend._get_installed_apps = Mock(return_value=[])
            mock_create.return_value = mock_backend
            with override_settings(
                NEXT_FRAMEWORK={
                    "DEFAULT_PAGE_BACKENDS": [{"BACKEND": "other.Backend"}]
                },
            ):
                next_framework_settings.reload()
                assert iter_pages_roots_with_components_folder_names() == []

    def test_swallows_backend_creation_error(self) -> None:
        """Invalid backend is skipped. A valid entry still contributes pairs."""
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_PAGE_BACKENDS": [
                    {"BACKEND": "nonexistent.Backend"},
                    {
                        "BACKEND": "next.urls.FileRouterBackend",
                        "PAGES_DIR": "pages",
                        "APP_DIRS": False,
                        "DIRS": [
                            str(
                                Path(__file__).parent.parent.parent / "tests" / "pages"
                            ),
                        ],
                        "OPTIONS": {},
                    },
                ],
            },
        ):
            next_framework_settings.reload()
            result = iter_pages_roots_with_components_folder_names()
            assert isinstance(result, list)


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
        result = list(
            scan_pages_tree(tmp_path, skip_dir_names=("_components",)),
        )
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

        with patch("next.urls.dispatcher._register_components_folder", capture):
            list(
                scan_pages_tree(
                    tmp_path,
                    skip_dir_names=("_components",),
                    register_components=True,
                    components_folder_name="_components",
                ),
            )
        assert len(calls) == 1
        assert calls[0][0].name == "_components"

    def test_register_components_folder_imports_components(
        self, tmp_path: Path
    ) -> None:
        """The thin wrapper delegates to ``next.components`` once per folder."""
        comp = tmp_path / "_components"
        comp.mkdir()
        with patch(
            "next.components.register_components_folder_from_router_walk",
        ) as mock_reg:
            _register_components_folder(comp, tmp_path, "scope")
        mock_reg.assert_called_once_with(comp, tmp_path, "scope")

    def test_scan_pages_directory_matches_scan_pages_tree(self, tmp_path) -> None:
        """The module helper yields the same pairs as ``scan_pages_tree``."""
        (tmp_path / "page.py").write_text("x=1")
        a = list(scan_pages_tree(tmp_path))
        b = list(_scan_pages_directory(tmp_path))
        assert a == b


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

    def test_skips_empty_and_dot_entries(self) -> None:
        """Empty strings and dot entries are ignored."""
        roots, segs = classify_dirs_entries(["", ".", None], Path("/tmp"))
        assert roots == []
        assert segs == frozenset()
