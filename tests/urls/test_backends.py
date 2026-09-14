import logging
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from django.core.exceptions import AppRegistryNotReady, ImproperlyConfigured
from django.test import RequestFactory, override_settings

from next.pages import page
from next.testing import override_next_settings
from next.urls import FileRouterBackend, PageRoot, RouterBackend, RouterFactory
from next.urls.backends import _installed_app_directories, _is_framework_app
from next.utils import forget_resolved_trees
from tests.support import (
    EntryRouter,
    file_router,
    file_router_config_entry,
    importable_dir,
    record_path_calls,
)


class NarrowFileRouter(FileRouterBackend):
    """File router subclass that refuses the entry the family contract passes."""

    def __init__(self) -> None:
        """Take nothing, so the entry the factory hands over is a TypeError."""
        super().__init__(file_router_config_entry())


class WidgetsFileRouter(FileRouterBackend):
    """File router subclass registering components from a folder of its own."""

    @staticmethod
    def _resolve_components_folder_name() -> str:
        """Answer the folder this subclass walks instead of reading the settings."""
        return "widgets"


class TestRouterBackend:
    """Abstract RouterBackend cannot be instantiated."""

    def test_router_backend_is_abstract(self) -> None:
        """Direct instantiation raises TypeError."""
        with pytest.raises(TypeError, match="abstract"):
            RouterBackend()


class TestFileRouterBackend:
    """FileRouterBackend initialization, paths, and URL generation."""

    @pytest.mark.parametrize(
        ("pages_dir", "app_dirs", "options", "expected_options"),
        [("pages", True, {}, {}), ("views", False, {"custom": "value"}, {})],
        ids=["defaults", "custom"],
    )
    def test_init_variations(self, pages_dir, app_dirs, options, expected_options):
        """The entry sets pages_dir, app_dirs, options, and an empty pattern cache."""
        router = file_router(pages_dir=pages_dir, app_dirs=app_dirs, options=options)

        assert router.pages_dir == pages_dir
        assert router.app_dirs == app_dirs
        assert router.options == expected_options
        assert router._patterns_cache == {}

    @pytest.mark.parametrize("missing", ["PAGES_DIR", "APP_DIRS", "DIRS", "OPTIONS"])
    def test_entry_missing_one_key_is_refused(self, missing: str) -> None:
        """A partial entry is a settings mistake rather than a shorthand."""
        entry = file_router_config_entry()
        del entry[missing]

        with pytest.raises(ImproperlyConfigured, match=missing):
            FileRouterBackend(entry)

    def test_non_dict_options_are_read_as_empty(self) -> None:
        """OPTIONS naming no mapping narrows to nothing rather than refusing."""
        entry = {**file_router_config_entry(), "OPTIONS": None}

        assert FileRouterBackend(entry).options == {}

    @pytest.mark.parametrize(
        ("pages_dir", "app_dirs", "expected_repr"),
        [
            ("views", False, "<FileRouterBackend pages_dir='views' app_dirs=False>"),
            ("pages", True, "<FileRouterBackend pages_dir='pages' app_dirs=True>"),
        ],
        ids=["views_false", "pages_true"],
    )
    def test_repr_variations(self, pages_dir, app_dirs, expected_repr) -> None:
        """``repr`` reflects pages_dir and app_dirs."""
        router = file_router(pages_dir=pages_dir, app_dirs=app_dirs)
        assert repr(router) == expected_repr

    @pytest.mark.parametrize(
        ("app_dirs", "method_to_patch", "expected_urls"),
        [
            (True, "_generate_app_urls", ["url1", "url2"]),
            (False, "_generate_root_urls", ["url1"]),
        ],
        ids=["app_dirs_true", "app_dirs_false"],
    )
    def test_generate_urls_variations(
        self, app_dirs, method_to_patch, expected_urls
    ) -> None:
        """Delegates to app or root URL generators based on app_dirs."""
        router = file_router(app_dirs=app_dirs)
        with patch.object(router, method_to_patch, return_value=expected_urls):
            urls = router.generate_urls()
            assert urls == expected_urls

    def test_get_app_pages_path_answers_from_the_memo(self, router) -> None:
        """A second lookup answers from the memo without touching the disk."""
        app_dir = Path("/sentinel")
        sentinel = app_dir / "pages"
        router._app_pages_path_cache["cached_app"] = (app_dir, sentinel)
        with patch.object(Path, "exists") as looked:
            result = router._get_app_pages_path("cached_app", {"cached_app": app_dir})
        assert result is sentinel
        looked.assert_not_called()

    def test_get_app_pages_path_memoises_a_missing_tree(self, tmp_path) -> None:
        """An app without a pages tree is answered from the memo too.

        The memo lives as long as this router, and a tree an app grows later reaches the
        watcher through the next router, not through this one probing again.
        """
        app_dir = tmp_path / "shop"
        app_dir.mkdir()
        router = file_router()
        directories = {"shop": app_dir}

        with override_settings(DEBUG=True):
            assert router._get_app_pages_path("shop", directories) is None
            (app_dir / "pages").mkdir()

            assert router._get_app_pages_path("shop", directories) is None

    def test_get_app_pages_path_memoises_a_tree_it_found(self, tmp_path) -> None:
        """A tree that is there is looked up once and answered from the memo."""
        app_dir = tmp_path / "shop"
        (app_dir / "pages").mkdir(parents=True)
        router = file_router()
        directories = {"shop": app_dir}

        first = router._get_app_pages_path("shop", directories)
        with patch.object(Path, "exists") as looked:
            second = router._get_app_pages_path("shop", directories)
        assert first == (app_dir / "pages").resolve()
        assert second is first
        looked.assert_not_called()

    def test_get_app_pages_path_looks_again_when_the_app_moves(self, router) -> None:
        """The memo keys on the directory, so a relocated app is not stale."""
        router._app_pages_path_cache["shop"] = (Path("/old"), Path("/old/pages"))

        with patch.object(Path, "exists", return_value=False):
            result = router._get_app_pages_path("shop", {"shop": Path("/new")})

        assert result is None

    def test_get_app_pages_path_of_an_app_outside_the_registry(self, router) -> None:
        """A name no installed app carries resolves to no pages directory."""
        assert router._get_app_pages_path("not_an_installed_app", {}) is None

    @pytest.mark.parametrize(
        ("base_dir", "exists"),
        [("/path/to/project", True), (None, None), ("/path/to/project", False)],
        ids=["with_base_dir", "no_base_dir", "does_not_exist"],
    )
    def test_get_root_pages_path_variations(
        self, router, mock_settings, base_dir, exists
    ) -> None:
        """Root pages paths from BASE_DIR when directory exists or missing."""
        mock_settings.BASE_DIR = base_dir

        if base_dir is None:
            result = router._get_root_pages_paths()
            assert result == []
        else:
            root_router = file_router(app_dirs=False)
            mock_pages_path = Mock()
            mock_pages_path.exists.return_value = exists
            mock_base = Mock()
            mock_base.__truediv__ = Mock(return_value=mock_pages_path)
            with patch("next.urls.backends.resolve_base_dir", return_value=mock_base):
                result = root_router._get_root_pages_paths()
            if exists:
                assert len(result) == 1
                assert result[0] is mock_pages_path.resolve()
            else:
                assert result == []

    def test_get_root_pages_paths_from_extra_roots(self, tmp_path) -> None:
        """Paths in ``extra_root_paths`` are resolved when they exist."""
        router = file_router(dirs=[tmp_path])
        result = router._get_root_pages_paths()
        assert len(result) == 1
        assert result[0] == tmp_path.resolve()

    def test_get_root_pages_paths_skips_nonexistent(self) -> None:
        """Nonexistent ``extra_root_paths`` entries are omitted."""
        router = file_router(
            dirs=[Path("/nonexistent/path"), Path("/also/nonexistent")]
        )
        result = router._get_root_pages_paths()
        assert result == []

    def test_a_classified_dirs_entry_is_resolved_once(
        self, tmp_path, monkeypatch
    ) -> None:
        """The classification resolves what it keeps, so the constructor need not."""
        (tmp_path / "site").mkdir()
        expected = (tmp_path / "site").resolve()
        forget_resolved_trees()

        with override_settings(BASE_DIR=tmp_path):
            seen = record_path_calls(monkeypatch, "resolve")
            router = file_router(app_dirs=False, dirs=["site"])
            held = list(router._extra_root_paths)

        assert held == [expected]
        assert seen == [tmp_path / "site"]

    def test_get_root_pages_paths_fallback_when_app_dirs_false(
        self, mock_settings, tmp_path
    ) -> None:
        """With app_dirs False, falls back to BASE_DIR joined with pages_dir."""
        pages_dir = tmp_path / "pages"
        pages_dir.mkdir()
        mock_settings.BASE_DIR = tmp_path
        router = file_router(app_dirs=False)
        result = router._get_root_pages_paths()
        assert len(result) == 1
        assert result[0] == pages_dir

    def test_get_root_pages_paths_empty_when_app_dirs_true_no_extra_roots(self) -> None:
        """With app_dirs True and no extra roots, returns an empty list."""
        router = file_router(app_dirs=True)
        result = router._get_root_pages_paths()
        assert result == []

    def test_generate_root_urls_cached_across_calls(self, tmp_path) -> None:
        """A second generate_urls reuses cached root patterns without re-walking."""
        router = file_router(app_dirs=False, dirs=[tmp_path])
        with patch.object(
            router, "_generate_patterns_from_directory", return_value=iter(["p1"])
        ) as mock_gen:
            first = router.generate_urls()
            second = router.generate_urls()
            assert first == ["p1"]
            assert second == first
            assert second is not first
            second.append("appended")
            third = router.generate_urls()
        assert third == ["p1"]
        mock_gen.assert_called_once()

    def test_subclass_append_does_not_grow_root_cache(self, tmp_path) -> None:
        """The documented super().generate_urls() + append pattern stays idempotent."""

        class _AppendingRouter(FileRouterBackend):
            def generate_urls(self):
                urls = super().generate_urls()
                urls.append("extra")
                return urls

        router = _AppendingRouter(
            file_router_config_entry(app_dirs=False, dirs=[tmp_path])
        )
        with patch.object(
            router, "_generate_patterns_from_directory", return_value=iter(["p1"])
        ):
            first = router.generate_urls()
            second = router.generate_urls()
        assert first == ["p1", "extra"]
        assert second == ["p1", "extra"]

    def test_get_root_pages_paths_answers_from_the_memo(self, tmp_path) -> None:
        """A second call answers the held list without touching the disk."""
        router = file_router(dirs=[tmp_path])
        first = router._get_root_pages_paths()
        with patch.object(Path, "exists") as looked:
            second = router._get_root_pages_paths()
        assert first == [tmp_path.resolve()]
        assert second == first
        looked.assert_not_called()

    def test_get_root_pages_paths_hands_back_a_copy(self, tmp_path) -> None:
        """A caller appending to the answer moves no root this router serves."""
        router = file_router(dirs=[tmp_path])
        first = router._get_root_pages_paths()
        first.append(Path("/appended"))

        assert first is not router._extra_root_paths
        assert router._get_root_pages_paths() == [tmp_path.resolve()]

    def test_get_root_pages_paths_memoises_the_base_dir(
        self, mock_settings, tmp_path
    ) -> None:
        """The `BASE_DIR` fallback is probed once and answered from the memo.

        A new page tree reaches the watcher through the router built for the next read.
        """
        mock_settings.BASE_DIR = tmp_path
        mock_settings.DEBUG = True
        router = file_router(app_dirs=False)

        assert router._get_root_pages_paths() == []
        (tmp_path / "pages").mkdir()

        assert router._get_root_pages_paths() == []

    def test_generate_urls_includes_root_when_app_dirs_and_extra_roots(
        self, tmp_path
    ) -> None:
        """With app_dirs and extra root paths, root directory patterns are generated."""
        router = file_router(app_dirs=True, dirs=[tmp_path])
        with (
            patch.object(router, "_generate_app_urls", return_value=[]),
            patch.object(
                router, "_generate_patterns_from_directory", return_value=[]
            ) as mock_gen,
        ):
            urls = router.generate_urls()
        assert urls == []
        mock_gen.assert_called_with(tmp_path)

    @pytest.mark.parametrize(
        (
            "test_case",
            "cache_value",
            "pages_path_return",
            "patterns_return",
            "expected_result",
        ),
        [
            ("cached", ["cached_url"], None, None, ["cached_url"]),
            ("no_pages_path", None, None, None, []),
            (
                "with_patterns",
                None,
                "mock_pages_path",
                ["pattern1", "pattern2"],
                ["pattern1", "pattern2"],
            ),
        ],
        ids=["cached", "no_pages_path", "with_patterns"],
    )
    def test_generate_urls_for_app_variations(
        self,
        router,
        test_case,
        cache_value,
        pages_path_return,
        patterns_return,
        expected_result,
    ) -> None:
        """Per app caching, missing path, and generated patterns."""
        if cache_value:
            router._patterns_cache["testapp"] = cache_value
            result = router._generate_urls_for_app("testapp", {})
            assert result == expected_result
        else:
            with patch.object(
                router, "_get_app_pages_path", return_value=pages_path_return
            ):
                if pages_path_return:
                    with patch.object(
                        router,
                        "_generate_patterns_from_directory",
                        return_value=patterns_return,
                    ):
                        result = router._generate_urls_for_app("testapp", {})
                        assert result == expected_result
                        assert router._patterns_cache["testapp"] == patterns_return
                else:
                    result = router._generate_urls_for_app("testapp", {})
                    assert result == expected_result

    def test_generate_patterns_from_directory(self) -> None:
        """Builds URL patterns from scan results via create_url_pattern."""
        router = file_router()
        mock_pages_path = Mock()

        with (
            patch.object(
                router,
                "_scan_pages_directory",
                return_value=[("url1", "file1"), ("url2", "file2")],
            ),
            patch("next.urls.backends.page.create_url_pattern") as mock_create,
        ):
            mock_create.side_effect = ["pattern1", "pattern2"]

            patterns = list(router._generate_patterns_from_directory(mock_pages_path))
            assert patterns == ["pattern1", "pattern2"]

    def test_scan_pages_directory_empty(self, tmp_path) -> None:
        """A directory holding nothing yields no routes."""
        router = file_router()

        assert list(router._scan_pages_directory(tmp_path)) == []

    def test_scan_pages_directory_with_files(self) -> None:
        """Mix of subdirs and page.py delegates to recursive scan."""
        router = file_router()

        mock_dir = Mock()
        mock_dir.name = "dir1"
        mock_dir.is_dir.return_value = True

        mock_file = Mock()
        mock_file.name = "page.py"
        mock_file.is_dir.return_value = False

        with (
            patch("pathlib.Path.iterdir", return_value=[mock_dir, mock_file]),
            patch.object(router, "_scan_pages_directory") as mock_scan,
        ):
            mock_scan.return_value = [("dir1", "file1")]

            pages = list(router._scan_pages_directory(Path("/tmp")))
            assert pages == [("dir1", "file1")]

    def test_scan_pages_directory_recursive(self) -> None:
        """Nested directories produce multiple route entries."""
        router = file_router()

        root_dir = Path("/tmp/pages")

        with patch("pathlib.Path.iterdir") as mock_iterdir:
            mock_iterdir.side_effect = [
                [Mock(name="dir1", is_dir=lambda: True)],
                [Mock(name="page.py", is_dir=lambda: False)],
            ]

            with patch.object(router, "_scan_pages_directory") as mock_scan:
                mock_scan.return_value = [("home", "file1"), ("", "file2")]

                pages = list(router._scan_pages_directory(root_dir))
                assert len(pages) == 2
                assert any("home" in str(page[0]) for page in pages)

    def test_create_url_pattern_with_args_parameter(self, tmp_path) -> None:
        """View wrapper accepts args string when URL pattern includes [[args]]."""
        router = file_router()

        page_py = tmp_path / "page.py"
        page_py.write_text(
            "def render(request, args):\n    return 'response-' + args\n"
        )

        pattern = page.create_url_pattern("test/[[args]]", page_py, router._url_parser)
        assert pattern is not None
        assert pattern.callback is not None
        response = pattern.callback(RequestFactory().get("/"), args="arg1/arg2/arg3")
        assert response.content == b"response-arg1/arg2/arg3"


class TestPageRoots:
    """``page_roots`` reports the labelled trees a backend routes."""

    def test_base_backend_reports_no_roots(self, custom_backend_class) -> None:
        """A backend that never implements it is not checked and not watched."""
        assert custom_backend_class().page_roots() == []

    def test_app_trees_come_first_then_root_trees(self, tmp_path) -> None:
        """With app_dirs the app trees lead, in installed-app order."""
        app_tree = tmp_path / "shop_pages"
        app_tree.mkdir()
        root_tree = tmp_path / "root_pages"
        root_tree.mkdir()
        router = file_router(app_dirs=True, dirs=[root_tree])

        with (
            patch.object(router, "_get_installed_apps", return_value=["shop"]),
            patch.object(router, "_get_app_pages_path", return_value=app_tree),
        ):
            roots = router.page_roots()

        assert roots == [
            PageRoot(path=app_tree, label="App 'shop'"),
            PageRoot(path=root_tree.resolve(), label="Root"),
        ]

    def test_app_without_pages_directory_is_left_out(self, tmp_path) -> None:
        """An app with no pages tree contributes no root."""
        app_tree = tmp_path / "shop_pages"
        app_tree.mkdir()
        router = file_router(app_dirs=True)

        with (
            patch.object(
                router, "_get_installed_apps", return_value=["shop", "bare_app"]
            ),
            patch.object(router, "_get_app_pages_path", side_effect=[app_tree, None]),
        ):
            roots = router.page_roots()

        assert roots == [PageRoot(path=app_tree, label="App 'shop'")]

    def test_app_trees_are_skipped_without_app_dirs(self, tmp_path) -> None:
        """A root-only backend never reports app trees."""
        root_tree = tmp_path / "root_pages"
        root_tree.mkdir()
        router = file_router(app_dirs=False, dirs=[root_tree])

        with patch.object(router, "_get_installed_apps") as installed_apps:
            roots = router.page_roots()

        installed_apps.assert_not_called()
        assert roots == [PageRoot(path=root_tree.resolve(), label="Root")]

    def test_later_root_trees_carry_their_path_in_the_label(self, tmp_path) -> None:
        """Only the first root is bare ``Root``, the rest name their path."""
        first = tmp_path / "first"
        first.mkdir()
        second = tmp_path / "second"
        second.mkdir()
        router = file_router(app_dirs=False, dirs=[first, second])

        labels = [root.label for root in router.page_roots()]

        assert labels == ["Root", f"Root ({second.resolve()})"]

    def test_no_configured_tree_reports_no_roots(self) -> None:
        """A backend whose configuration resolves to nothing stays silent."""
        router = file_router(app_dirs=True)

        with patch.object(router, "_get_installed_apps", return_value=[]):
            assert router.page_roots() == []


class TestComponentsFolderName:
    """``components_folder_name`` names the folder a backend registers."""

    def test_base_backend_registers_no_components(self, custom_backend_class) -> None:
        """A backend that walks no tree names no components folder."""
        assert custom_backend_class().components_folder_name() is None

    def test_file_router_reports_the_name_its_own_class_resolves(self) -> None:
        """A subclass renaming the folder is read, not the base class it inherits."""
        router = WidgetsFileRouter(file_router_config_entry())

        assert router.components_folder_name() == "widgets"
        assert "widgets" in router.skip_dir_names()


class TestSkipDirNames:
    """``skip_dir_names`` reports the directories a backend's walk refuses."""

    def test_base_backend_refuses_no_directory(self, custom_backend_class) -> None:
        """A backend that walks no tree refuses no directory name."""
        assert custom_backend_class().skip_dir_names() == frozenset()

    def test_file_router_reports_the_set_its_own_walk_uses(self, tmp_path) -> None:
        """The names the router answers are the names it hands its dispatcher."""
        (tmp_path / "shell").mkdir()
        entry = file_router_config_entry(pages_dir=tmp_path / "shell", dirs=["_drafts"])

        with override_next_settings(PAGE_BACKENDS=[entry]):
            router = RouterFactory.create_backend(entry)

            assert router.skip_dir_names() == frozenset({"_components", "_drafts"})

    def test_a_skip_name_is_no_root_once_a_directory_of_that_name_appears(
        self, tmp_path
    ) -> None:
        """One classification answers both, so a skip name never becomes a tree."""
        (tmp_path / "pages").mkdir()
        (tmp_path / "pages" / "page.py").write_text('template = "hi"\n')
        entry = file_router_config_entry(dirs=["_drafts"])

        with (
            override_settings(BASE_DIR=tmp_path),
            override_next_settings(PAGE_BACKENDS=[entry]),
        ):
            router = RouterFactory.create_backend(entry)
            before = [root.path for root in router.page_roots()]
            (tmp_path / "_drafts").mkdir()
            after = [root.path for root in router.page_roots()]
            urls = router.generate_urls()

        assert router.skip_dir_names() == frozenset({"_components", "_drafts"})
        assert before == after == [tmp_path / "pages"]
        assert len(urls) == 1


class TestInstalledAppSpellings:
    """An app routes its pages under either `INSTALLED_APPS` spelling."""

    def _write_app(self, root: Path, name: str, *, config_class: bool) -> None:
        """Write an importable app package with one page under `pages/hello`."""
        app = root / name
        (app / "pages" / "hello").mkdir(parents=True)
        (app / "__init__.py").write_text("")
        (app / "pages" / "hello" / "page.py").write_text('template = "hi"\n')
        if config_class:
            (app / "apps.py").write_text(
                "from django.apps import AppConfig\n\n\n"
                "class ShopConfig(AppConfig):\n"
                f'    name = "{name}"\n'
            )

    def _routed(self, tmp_path: Path, settings, entry: str, name: str) -> tuple:
        """Return the page roots and routes an app produces under one spelling."""
        self._write_app(tmp_path, name, config_class="." in entry)
        router = file_router(app_dirs=True)
        with importable_dir(tmp_path):
            settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, entry]
            roots = [(root.label, root.path) for root in router.page_roots()]
            routes = [str(pattern.pattern) for pattern in router.generate_urls()]
        return roots, routes

    def test_plain_module_entry_routes_its_pages(self, tmp_path, settings) -> None:
        """The long-standing spelling keeps its root, its label, and its URL."""
        roots, routes = self._routed(tmp_path, settings, "shop", "shop")

        assert roots == [("App 'shop'", tmp_path / "shop" / "pages")]
        assert routes == ["hello/"]

    def test_app_config_entry_routes_the_same_pages(self, tmp_path, settings) -> None:
        """An AppConfig path names the app it configures, not its config class."""
        roots, routes = self._routed(
            tmp_path, settings, "store.apps.ShopConfig", "store"
        )

        assert roots == [("App 'store'", tmp_path / "store" / "pages")]
        assert routes == ["hello/"]

    def test_app_without_a_pages_directory_contributes_nothing(
        self, tmp_path, settings
    ) -> None:
        """An installed app with no pages tree reports no root."""
        (tmp_path / "bare" / "__init__.py").parent.mkdir(parents=True)
        (tmp_path / "bare" / "__init__.py").write_text("")
        router = file_router(app_dirs=True)

        with importable_dir(tmp_path):
            settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, "bare"]
            installed = router._get_installed_apps(_installed_app_directories())
            assert list(installed) == ["bare"]
            assert router.page_roots() == []

    def test_django_and_framework_apps_are_never_page_roots(self, router) -> None:
        """The framework ships a `next/pages` package that is not a page tree."""
        installed = list(router._get_installed_apps(_installed_app_directories()))

        assert installed == []
        assert router.page_roots() == []

    @pytest.mark.parametrize(
        ("app_name", "skipped"),
        [
            ("django", True),
            ("django.contrib.auth", True),
            ("next", True),
            ("next.contrib.thing", True),
            ("django_htmx", False),
            ("django_extensions", False),
            ("nextcloud", False),
            ("shop", False),
        ],
        ids=[
            "django",
            "django_contrib",
            "next",
            "next_subpackage",
            "django_htmx",
            "django_extensions",
            "nextcloud",
            "project_app",
        ],
    )
    def test_only_django_and_next_packages_are_skipped(self, app_name, skipped) -> None:
        """A third-party name merely starting with the same letters still counts."""
        assert _is_framework_app(app_name) is skipped


class TestAppDirectoryResolution:
    """The app directory comes from the registry, with a path for every app shape."""

    def _write_pages(self, app_dir: Path) -> Path:
        """Put one page under `<app_dir>/pages` and return that pages directory."""
        pages = app_dir / "pages"
        (pages / "hello").mkdir(parents=True)
        (pages / "hello" / "page.py").write_text('template = "hi"\n')
        return pages

    def test_namespace_package_app_resolves_its_directory(
        self, tmp_path, settings
    ) -> None:
        """A PEP 420 app has no `__init__.py`, and the registry still knows its path."""
        pages = self._write_pages(tmp_path / "nsapp")
        router = file_router(app_dirs=True)

        with importable_dir(tmp_path):
            settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, "nsapp"]
            roots = router.page_roots()

        assert roots == [PageRoot(path=pages, label="App 'nsapp'")]

    def test_app_config_path_attribute_wins(self, tmp_path, settings) -> None:
        """An `AppConfig` that declares `path` points the scan at that directory."""
        elsewhere = tmp_path / "elsewhere"
        pages = self._write_pages(elsewhere)
        app = tmp_path / "movedapp"
        app.mkdir()
        (app / "__init__.py").write_text("")
        (app / "apps.py").write_text(
            "from django.apps import AppConfig\n\n\n"
            "class MovedConfig(AppConfig):\n"
            '    name = "movedapp"\n'
            f'    path = "{elsewhere}"\n'
        )
        router = file_router(app_dirs=True)

        with importable_dir(tmp_path):
            settings.INSTALLED_APPS = [
                *settings.INSTALLED_APPS,
                "movedapp.apps.MovedConfig",
            ]
            roots = router.page_roots()

        assert roots == [PageRoot(path=pages, label="App 'movedapp'")]

    def test_a_blank_registry_reports_no_roots(self, tmp_path, settings) -> None:
        """The registry is the only source of app paths, so a blank one has none."""
        pages = self._write_pages(tmp_path / "shop")
        (tmp_path / "shop" / "__init__.py").write_text("")

        with importable_dir(tmp_path):
            settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, "shop"]
            healthy = file_router(app_dirs=True).page_roots()
            with patch("next.urls.backends.apps.get_app_configs", return_value=[]):
                blank = file_router(app_dirs=True).page_roots()

        assert healthy == [PageRoot(path=pages, label="App 'shop'")]
        assert blank == []

    def test_a_live_backend_sees_an_installed_apps_change(
        self, tmp_path, settings
    ) -> None:
        """`INSTALLED_APPS` moves without the settings reload that rebuilds a backend."""
        pages = self._write_pages(tmp_path / "latecomer")
        (tmp_path / "latecomer" / "__init__.py").write_text("")
        router = file_router(app_dirs=True)

        assert router.page_roots() == []

        with importable_dir(tmp_path):
            settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, "latecomer"]
            after = router.page_roots()

        assert after == [PageRoot(path=pages, label="App 'latecomer'")]

    def test_a_registry_that_is_not_ready_reports_no_roots(
        self, tmp_path, caplog
    ) -> None:
        """A router asked before the registry populates answers instead of raising."""
        self._write_pages(tmp_path / "early")
        (tmp_path / "early" / "__init__.py").write_text("")
        router = file_router(app_dirs=True)

        with (
            importable_dir(tmp_path),
            patch(
                "next.urls.backends.apps.get_app_configs",
                side_effect=AppRegistryNotReady("Apps aren't loaded yet."),
            ),
            caplog.at_level(logging.WARNING, logger="next.urls.backends"),
        ):
            assert router.page_roots() == []

        # The empty answer reads as "this project has no app pages", so it is
        # named rather than left to pass for the truth.
        assert "read before Django populated it" in caplog.text


class TestAppRegistryPass:
    """One discovery pass reads the app registry once, not once per app."""

    def _install_apps(self, tmp_path: Path, count: int) -> list[str]:
        """Write `count` importable apps, each carrying a pages tree."""
        names = []
        for index in range(count):
            name = f"reg_app_{index}"
            (tmp_path / name / "pages").mkdir(parents=True)
            (tmp_path / name / "__init__.py").write_text("")
            names.append(name)
        return names

    def test_page_roots_reads_the_registry_once(self, tmp_path, settings) -> None:
        """Reading it per app name rebuilds the whole map per app, which is quadratic."""
        names = self._install_apps(tmp_path, 3)
        router = file_router(app_dirs=True)

        with importable_dir(tmp_path):
            settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, *names]
            with patch(
                "next.urls.backends._installed_app_directories",
                wraps=_installed_app_directories,
            ) as spy:
                roots = router.page_roots()

        assert len(roots) == 3
        assert spy.call_count == 1

    def test_generate_urls_reads_the_registry_once(self, tmp_path, settings) -> None:
        """The URL build walks the same app list and takes the same one snapshot."""
        names = self._install_apps(tmp_path, 3)
        router = file_router(app_dirs=True)

        with importable_dir(tmp_path):
            settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, *names]
            with patch(
                "next.urls.backends._installed_app_directories",
                wraps=_installed_app_directories,
            ) as spy:
                router.generate_urls()

        assert spy.call_count == 1

    def test_a_root_only_router_never_reads_the_registry(self, tmp_path) -> None:
        """Without `app_dirs` no app is resolved, so no snapshot is taken."""
        root = tmp_path / "shell"
        root.mkdir()
        router = file_router(app_dirs=False, dirs=[root])

        with patch(
            "next.urls.backends._installed_app_directories",
            wraps=_installed_app_directories,
        ) as spy:
            router.page_roots()
            router.generate_urls()

        assert spy.call_count == 0

    def test_the_snapshot_never_outlives_its_pass(self, tmp_path, settings) -> None:
        """An app installed between two passes is found by the second one."""
        names = self._install_apps(tmp_path, 1)
        router = file_router(app_dirs=True)

        with importable_dir(tmp_path):
            assert router.page_roots() == []
            settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, *names]
            labels = [root.label for root in router.page_roots()]

        assert labels == ["App 'reg_app_0'"]

    def test_the_accessors_read_only_the_snapshot_they_are_given(
        self, tmp_path, settings
    ) -> None:
        """The snapshot is the whole input, so nothing below the pass re-reads."""
        names = self._install_apps(tmp_path, 1)
        router = file_router(app_dirs=True)

        with importable_dir(tmp_path):
            settings.INSTALLED_APPS = [*settings.INSTALLED_APPS, *names]
            directories = _installed_app_directories()
            with patch("next.urls.backends._installed_app_directories") as spy:
                path = router._get_app_pages_path(names[0], directories)
                installed = list(router._get_installed_apps(directories))

        assert path == tmp_path / names[0] / "pages"
        assert installed == names
        spy.assert_not_called()


class TestWorkingDirectoryRoot:
    """`page_roots` reports what the router serves, never a tree it cannot route."""

    def _pages_beside(self, tmp_path: Path, monkeypatch) -> None:
        """Write `pages/hello/page.py` under `tmp_path` and run from there."""
        (tmp_path / "pages" / "hello").mkdir(parents=True)
        (tmp_path / "pages" / "hello" / "page.py").write_text('template = "hi"\n')
        monkeypatch.chdir(tmp_path)

    def test_unrouted_working_directory_pages_are_not_a_page_root(
        self, tmp_path, monkeypatch
    ) -> None:
        """Without BASE_DIR the tree beside the project is served by nothing."""
        self._pages_beside(tmp_path, monkeypatch)

        with patch("next.utils.settings", Mock(BASE_DIR=None)):
            router = file_router(app_dirs=False)
            roots = router.page_roots()
            routes = router.generate_urls()

        assert roots == []
        assert routes == []

    def test_configured_root_is_the_only_reported_tree(
        self, tmp_path, monkeypatch
    ) -> None:
        """A router with a real root reports that root and nothing beside it."""
        self._pages_beside(tmp_path, monkeypatch)
        configured = tmp_path / "shell"
        configured.mkdir()

        with patch("next.utils.settings", Mock(BASE_DIR=None)):
            roots = file_router(app_dirs=False, dirs=[configured]).page_roots()

        assert roots == [PageRoot(path=configured.resolve(), label="Root")]


class TestRouterFactory:
    """`RouterFactory.create_backend` resolves one entry and builds its router."""

    def test_create_backend_builds_a_file_router_from_its_entry(self) -> None:
        """A complete entry produces a router carrying the values it named."""
        router = RouterFactory.create_backend(
            file_router_config_entry(pages_dir_name="views", app_dirs=True)
        )

        assert isinstance(router, FileRouterBackend)
        assert (router.pages_dir, router.app_dirs, router.options) == (
            "views",
            True,
            {},
        )

    def test_create_backend_resolves_string_base_dir(self) -> None:
        """A string ``BASE_DIR`` is normalised to a ``Path`` while roots classify."""
        mock_s = Mock()
        with patch("next.utils.settings", mock_s):
            mock_s.BASE_DIR = "/tmp/next_base_str"
            router = RouterFactory.create_backend(
                file_router_config_entry(app_dirs=True)
            )
        assert isinstance(router, FileRouterBackend)

    @pytest.mark.parametrize(
        "dirs",
        [
            pytest.param(5, id="scalar"),
            pytest.param("src/pages", id="string"),
            pytest.param([7], id="entry_is_no_path"),
        ],
    )
    def test_create_backend_when_dirs_is_no_sequence_of_trees(
        self, dirs: object
    ) -> None:
        """A scalar DIRS or an entry naming no path costs its own router alone."""
        config = {**file_router_config_entry(app_dirs=True), "DIRS": dirs}

        with pytest.raises(ImproperlyConfigured, match="sequence of trees"):
            RouterFactory.create_backend(config)

    def test_create_backend_without_a_backend_key(self) -> None:
        """An entry naming no backend is a misconfiguration like any other."""
        with pytest.raises(ImproperlyConfigured, match="BACKEND"):
            RouterFactory.create_backend({})

    def test_create_backend_unsupported(self) -> None:
        """An unimportable BACKEND path fails the way every backend family fails."""
        with pytest.raises(ImportError):
            RouterFactory.create_backend({"BACKEND": "unsupported.backend"})

    def test_create_backend_when_import_is_not_router_subclass(self) -> None:
        """An importable BACKEND path outside the family is a misconfiguration."""
        with pytest.raises(ImproperlyConfigured, match="RouterBackend"):
            RouterFactory.create_backend({"BACKEND": "unittest.mock.MagicMock"})

    def test_create_backend_when_the_constructor_refuses_the_entry(self) -> None:
        """A router refusing the family contract is a bug that reaches the caller."""
        config = {
            **file_router_config_entry(app_dirs=True),
            "BACKEND": "tests.urls.test_backends.NarrowFileRouter",
        }

        with pytest.raises(TypeError):
            RouterFactory.create_backend(config)

    def test_create_backend_hands_the_entry_to_a_third_party_router(self) -> None:
        """A router of any other shape reads its own keys off the same entry."""
        config = {"BACKEND": "tests.support.routers.EntryRouter", "ROOTS": ["site"]}

        router = RouterFactory.create_backend(config)

        assert isinstance(router, EntryRouter)
        assert router.config == config


class TestComponentsFolderResolution:
    """The folder a file router skips comes from ``COMPONENT_BACKENDS``."""

    def test_resolve_components_folder_name_from_first_component_backend(self) -> None:
        """Skip-folder name comes from the first ``COMPONENT_BACKENDS`` entry."""
        with patch("next.urls.backends.next_framework_settings") as nfs:
            nfs.COMPONENT_BACKENDS = [{"COMPONENTS_DIR": "custom_comp"}]
            assert FileRouterBackend._resolve_components_folder_name() == "custom_comp"

    def test_resolve_components_folder_name_raises_when_unavailable(self) -> None:
        """No usable component backend entry leaves the skipped folder unnamed."""
        with patch("next.urls.backends.next_framework_settings") as nfs:
            nfs.COMPONENT_BACKENDS = []
            with pytest.raises(ImproperlyConfigured, match="COMPONENTS_DIR"):
                FileRouterBackend._resolve_components_folder_name()

    def test_resolve_components_folder_name_raises_when_first_entry_invalid(
        self,
    ) -> None:
        """First component backend dict must contain COMPONENTS_DIR."""
        with patch("next.urls.backends.next_framework_settings") as nfs:
            nfs.COMPONENT_BACKENDS = [{}]
            with pytest.raises(ImproperlyConfigured, match="COMPONENTS_DIR"):
                FileRouterBackend._resolve_components_folder_name()
