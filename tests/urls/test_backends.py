import logging
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from django.core.exceptions import AppRegistryNotReady
from django.test import RequestFactory

from next.pages import page
from next.urls import FileRouterBackend, PageRoot, RouterBackend, RouterFactory
from next.urls.backends import _installed_app_directories, _is_framework_app
from tests.support import file_router_backend_from_params, importable_dir


class TestRouterBackend:
    """Abstract RouterBackend cannot be instantiated."""

    def test_router_backend_is_abstract(self) -> None:
        """Direct instantiation raises TypeError."""
        with pytest.raises(TypeError):
            RouterBackend()


class TestFileRouterBackend:
    """FileRouterBackend initialization, paths, and URL generation."""

    @pytest.mark.parametrize(
        (
            "test_case",
            "pages_dir",
            "app_dirs",
            "options",
            "expected_pages_dir",
            "expected_app_dirs",
            "expected_options",
        ),
        [
            ("defaults", None, None, None, "pages", True, {}),
            ("custom", "views", False, {"custom": "value"}, "views", False, {}),
        ],
        ids=["defaults", "custom"],
    )
    def test_init_variations(
        self,
        test_case,
        pages_dir,
        app_dirs,
        options,
        expected_pages_dir,
        expected_app_dirs,
        expected_options,
    ) -> None:
        """Constructor sets pages_dir, app_dirs, options, and empty pattern cache."""
        kwargs = {}
        if pages_dir is not None:
            kwargs["pages_dir"] = pages_dir
        if app_dirs is not None:
            kwargs["app_dirs"] = app_dirs
        if options is not None:
            kwargs["options"] = options

        router = FileRouterBackend(**kwargs)
        assert router.pages_dir == expected_pages_dir
        assert router.app_dirs == expected_app_dirs
        assert router.options == expected_options
        assert router._patterns_cache == {}

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
        router = FileRouterBackend(pages_dir, app_dirs=app_dirs)
        assert repr(router) == expected_repr

    @pytest.mark.parametrize(
        ("test_case", "router1_params", "router2_params", "expected_equal"),
        [
            (
                "same_instance",
                ("pages", True, {"opt": "val"}),
                ("pages", True, {"opt": "val"}),
                True,
            ),
            ("different_instance", ("pages", True), ("views", True), False),
            ("wrong_type", "not a router", "also not a router", False),
        ],
        ids=["same_instance", "different_instance", "wrong_type"],
    )
    def test_equality_variations(
        self, test_case, router1_params, router2_params, expected_equal
    ) -> None:
        """Equality and inequality for matching config, different config, and wrong type."""
        router1 = file_router_backend_from_params(router1_params)
        router2 = file_router_backend_from_params(router2_params)

        if expected_equal:
            assert router1 == router2
        else:
            assert router1 != router2

    def test_equality_with_different_type(self) -> None:
        """Router does not equal a non router object."""
        router = FileRouterBackend("pages")
        other = "not a router"
        assert router != other

    def test_hash(self) -> None:
        """Same config yields equal hashes."""
        router1 = FileRouterBackend("pages", app_dirs=True, options={"opt": "val"})
        router2 = FileRouterBackend("pages", app_dirs=True, options={"opt": "val"})
        assert hash(router1) == hash(router2)

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
        router = FileRouterBackend(app_dirs=app_dirs)
        with patch.object(router, method_to_patch, return_value=expected_urls):
            urls = router.generate_urls()
            assert urls == expected_urls

    def test_get_app_pages_path_returns_cached_entry_without_a_lookup(
        self, router
    ) -> None:
        """A second lookup answers from the memo without touching the disk."""
        app_dir = Path("/sentinel")
        sentinel = app_dir / "pages"
        router._app_pages_path_cache["cached_app"] = (app_dir, sentinel)
        with patch.object(Path, "exists") as looked:
            result = router._get_app_pages_path("cached_app", {"cached_app": app_dir})
        assert result is sentinel
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
        ("test_case", "base_dir", "exists", "expected_result"),
        [
            ("with_base_dir", "/path/to/project", True, "mock_path_instance"),
            ("string_base_dir", "/path/to/project", True, "mock_path_instance"),
            ("no_base_dir", None, None, None),
            ("does_not_exist", "/path/to/project", False, None),
        ],
        ids=["with_base_dir", "string_base_dir", "no_base_dir", "does_not_exist"],
    )
    def test_get_root_pages_path_variations(
        self, router, mock_settings, test_case, base_dir, exists, expected_result
    ) -> None:
        """Root pages paths from BASE_DIR when directory exists or missing."""
        mock_settings.BASE_DIR = base_dir

        if base_dir is None:
            result = router._get_root_pages_paths()
            assert result == []
        else:
            root_router = FileRouterBackend(app_dirs=False)
            mock_pages_path = Mock()
            mock_pages_path.exists.return_value = exists
            mock_base = Mock()
            mock_base.__truediv__ = Mock(return_value=mock_pages_path)
            with patch("next.urls.backends.resolve_base_dir", return_value=mock_base):
                result = root_router._get_root_pages_paths()
            if exists:
                assert len(result) == 1
                assert result[0] is mock_pages_path
            else:
                assert result == []

    def test_get_root_pages_paths_from_extra_roots(self, tmp_path) -> None:
        """Paths in ``extra_root_paths`` are resolved when they exist."""
        router = FileRouterBackend(extra_root_paths=[tmp_path])
        result = router._get_root_pages_paths()
        assert len(result) == 1
        assert result[0] == tmp_path.resolve()

    def test_get_root_pages_paths_skips_nonexistent(self) -> None:
        """Nonexistent ``extra_root_paths`` entries are omitted."""
        router = FileRouterBackend(
            extra_root_paths=[Path("/nonexistent/path"), Path("/also/nonexistent")]
        )
        result = router._get_root_pages_paths()
        assert result == []

    def test_get_root_pages_paths_fallback_when_app_dirs_false(
        self, mock_settings, tmp_path
    ) -> None:
        """With app_dirs False, falls back to BASE_DIR joined with pages_dir."""
        pages_dir = tmp_path / "pages"
        pages_dir.mkdir()
        mock_settings.BASE_DIR = tmp_path
        router = FileRouterBackend(app_dirs=False)
        result = router._get_root_pages_paths()
        assert len(result) == 1
        assert result[0] == pages_dir

    def test_get_root_pages_paths_empty_when_app_dirs_true_no_extra_roots(self) -> None:
        """With app_dirs True and no extra roots, returns an empty list."""
        router = FileRouterBackend(app_dirs=True)
        result = router._get_root_pages_paths()
        assert result == []

    def test_generate_root_urls_cached_across_calls(self, tmp_path) -> None:
        """A second generate_urls reuses cached root patterns without re-walking."""
        router = FileRouterBackend(app_dirs=False, extra_root_paths=[tmp_path])
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

        router = _AppendingRouter(app_dirs=False, extra_root_paths=[tmp_path])
        with patch.object(
            router, "_generate_patterns_from_directory", return_value=iter(["p1"])
        ):
            first = router.generate_urls()
            second = router.generate_urls()
        assert first == ["p1", "extra"]
        assert second == ["p1", "extra"]

    def test_get_root_pages_paths_memoised_per_instance(self, tmp_path) -> None:
        """Repeated calls return the cached list without touching the filesystem."""
        router = FileRouterBackend(extra_root_paths=[tmp_path])
        first = router._get_root_pages_paths()
        with (
            patch.object(Path, "resolve") as mock_resolve,
            patch.object(Path, "exists") as mock_exists,
        ):
            second = router._get_root_pages_paths()
        assert second is first
        mock_resolve.assert_not_called()
        mock_exists.assert_not_called()

    def test_generate_urls_includes_root_when_app_dirs_and_extra_roots(
        self, tmp_path
    ) -> None:
        """With app_dirs and extra root paths, root directory patterns are generated."""
        router = FileRouterBackend(app_dirs=True, extra_root_paths=[tmp_path])
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
        router = FileRouterBackend()
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

    def test_scan_pages_directory_empty(self) -> None:
        """Empty iterdir yields no routes."""
        router = FileRouterBackend()

        with patch("pathlib.Path.iterdir", return_value=[]):
            pages = list(router._scan_pages_directory(Path("/tmp")))
            assert pages == []

    def test_scan_pages_directory_with_files(self) -> None:
        """Mix of subdirs and page.py delegates to recursive scan."""
        router = FileRouterBackend()

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
        router = FileRouterBackend()

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
        router = FileRouterBackend()

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
        router = FileRouterBackend(app_dirs=True, extra_root_paths=[root_tree])

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
        router = FileRouterBackend(app_dirs=True)

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
        router = FileRouterBackend(app_dirs=False, extra_root_paths=[root_tree])

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
        router = FileRouterBackend(app_dirs=False, extra_root_paths=[first, second])

        labels = [root.label for root in router.page_roots()]

        assert labels == ["Root", f"Root ({second.resolve()})"]

    def test_no_configured_tree_reports_no_roots(self) -> None:
        """A backend whose configuration resolves to nothing stays silent."""
        router = FileRouterBackend(app_dirs=True)

        with patch.object(router, "_get_installed_apps", return_value=[]):
            assert router.page_roots() == []


class TestComponentsFolderName:
    """``components_folder_name`` names the folder a backend registers."""

    def test_base_backend_registers_no_components(self, custom_backend_class) -> None:
        """A backend that walks no tree names no components folder."""
        assert custom_backend_class().components_folder_name() is None

    def test_file_router_reports_its_configured_name(self) -> None:
        """The file router answers the name its walk skips and registers."""
        router = FileRouterBackend(components_folder_name="widgets")

        assert router.components_folder_name() == "widgets"


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
        router = FileRouterBackend(app_dirs=True)
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
        router = FileRouterBackend(app_dirs=True)

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
        router = FileRouterBackend(app_dirs=True)

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
        router = FileRouterBackend(app_dirs=True)

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
            healthy = FileRouterBackend(app_dirs=True).page_roots()
            with patch("next.urls.backends.apps.get_app_configs", return_value=[]):
                blank = FileRouterBackend(app_dirs=True).page_roots()

        assert healthy == [PageRoot(path=pages, label="App 'shop'")]
        assert blank == []

    def test_a_live_backend_sees_an_installed_apps_change(
        self, tmp_path, settings
    ) -> None:
        """`INSTALLED_APPS` moves without the settings reload that rebuilds a backend."""
        pages = self._write_pages(tmp_path / "latecomer")
        (tmp_path / "latecomer" / "__init__.py").write_text("")
        router = FileRouterBackend(app_dirs=True)

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
        router = FileRouterBackend(app_dirs=True)

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
        router = FileRouterBackend(app_dirs=True)

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
        router = FileRouterBackend(app_dirs=True)

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
        router = FileRouterBackend(app_dirs=False, extra_root_paths=[root])

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
        router = FileRouterBackend(app_dirs=True)

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
        router = FileRouterBackend(app_dirs=True)

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
            router = FileRouterBackend(app_dirs=False)
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
            roots = FileRouterBackend(
                app_dirs=False, extra_root_paths=[configured]
            ).page_roots()

        assert roots == [PageRoot(path=configured.resolve(), label="Root")]


class TestRouterFactory:
    """RouterFactory.register_backend and create_backend."""

    def test_register_backend(self, custom_backend_class) -> None:
        """Registered name maps to the given class."""
        RouterFactory.register_backend("custom", custom_backend_class)
        assert "custom" in RouterFactory._backends
        assert RouterFactory._backends["custom"] == custom_backend_class

    @pytest.mark.parametrize(
        ("test_case", "config", "expected_type", "expected_attrs"),
        [
            (
                "success",
                {
                    "BACKEND": "next.urls.FileRouterBackend",
                    "PAGES_DIR": "pages",
                    "APP_DIRS": True,
                    "DIRS": [],
                    "OPTIONS": {},
                },
                FileRouterBackend,
                {"pages_dir": "pages", "app_dirs": True, "options": {}},
            )
        ],
        ids=["success"],
    )
    def test_create_backend_variations(
        self, test_case, config, expected_type, expected_attrs
    ) -> None:
        """Valid FileRouterBackend config produces a router with expected attributes."""
        router = RouterFactory.create_backend(config)
        assert isinstance(router, expected_type)

        for attr, expected_value in expected_attrs.items():
            assert getattr(router, attr) == expected_value

    def test_create_backend_resolves_string_base_dir(self) -> None:
        """``RouterFactory`` normalizes string ``BASE_DIR`` to ``Path``."""
        cfg = {
            "BACKEND": "next.urls.FileRouterBackend",
            "PAGES_DIR": "pages",
            "APP_DIRS": True,
            "DIRS": [],
            "OPTIONS": {},
        }
        mock_s = Mock()
        with patch("next.utils.settings", mock_s):
            mock_s.BASE_DIR = "/tmp/next_base_str"
            router = RouterFactory.create_backend(cfg)
        assert isinstance(router, FileRouterBackend)

    def test_create_backend_non_dict_options_treated_as_empty(self) -> None:
        """Non-dict ``OPTIONS`` is coerced to ``{}`` before merge."""
        cfg = {
            "BACKEND": "next.urls.FileRouterBackend",
            "PAGES_DIR": "pages",
            "APP_DIRS": True,
            "DIRS": [],
            "OPTIONS": None,
        }
        mock_s = Mock()
        with patch("next.utils.settings", mock_s):
            mock_s.BASE_DIR = Path("/tmp")
            router = RouterFactory.create_backend(cfg)
        assert isinstance(router, FileRouterBackend)
        assert router.options == {}

    @pytest.mark.parametrize(
        ("config", "missing_key"),
        [
            ({}, "BACKEND"),
            ({"BACKEND": "next.urls.FileRouterBackend"}, "PAGES_DIR"),
            (
                {"BACKEND": "next.urls.FileRouterBackend", "PAGES_DIR": "pages"},
                "APP_DIRS",
            ),
            (
                {
                    "BACKEND": "next.urls.FileRouterBackend",
                    "PAGES_DIR": "pages",
                    "APP_DIRS": True,
                },
                "OPTIONS",
            ),
            (
                {
                    "BACKEND": "next.urls.FileRouterBackend",
                    "PAGES_DIR": "pages",
                    "APP_DIRS": True,
                    "OPTIONS": {},
                },
                "DIRS",
            ),
        ],
        ids=[
            "missing_backend",
            "missing_pages_dir",
            "missing_app_dirs",
            "missing_options",
            "missing_dirs",
        ],
    )
    def test_create_backend_keyerror_when_required_key_missing(
        self, config, missing_key
    ) -> None:
        """FileRouterBackend config must list PAGES_DIR, APP_DIRS, OPTIONS, and DIRS."""
        with pytest.raises(KeyError) as exc:
            RouterFactory.create_backend(config)
        assert exc.value.args[0] == missing_key

    def test_create_backend_unsupported(self) -> None:
        """Unknown BACKEND string raises ValueError."""
        config = {"BACKEND": "unsupported.backend"}

        with pytest.raises(ValueError, match="Unsupported backend"):
            RouterFactory.create_backend(config)

    def test_create_backend_typeerror_when_not_router_subclass(self) -> None:
        """Registered class must be a RouterBackend subclass."""

        class Plain:
            pass

        RouterFactory.register_backend("plain.not.Router", Plain)
        with pytest.raises(TypeError, match="RouterBackend"):
            RouterFactory.create_backend({"BACKEND": "plain.not.Router"})

    def test_create_backend_non_file_router_backend(self, custom_backend_class) -> None:
        """Custom registered backend is instantiated without FileRouterBackend fields."""
        RouterFactory.register_backend("custom.backend", custom_backend_class)

        config = {"BACKEND": "custom.backend"}
        router = RouterFactory.create_backend(config)
        assert isinstance(router, custom_backend_class)

    def test_create_backend_non_file_router_backend_else_branch(
        self, custom_backend_class
    ) -> None:
        """Minimal config dict hits the non FileRouterBackend branch."""
        RouterFactory.register_backend("custom", custom_backend_class)

        backend = RouterFactory.create_backend({"BACKEND": "custom"})
        assert isinstance(backend, custom_backend_class)
        assert not hasattr(backend, "pages_dir")

    def test_resolve_components_folder_name_from_first_component_backend(self) -> None:
        """Skip-folder name comes from the first ``COMPONENT_BACKENDS`` entry."""
        with patch("next.urls.backends.next_framework_settings") as nfs:
            nfs.COMPONENT_BACKENDS = [{"COMPONENTS_DIR": "custom_comp"}]
            assert FileRouterBackend._resolve_components_folder_name() == "custom_comp"

    def test_resolve_components_folder_name_raises_when_unavailable(self) -> None:
        """Missing COMPONENTS_DIR and no valid component backend entry raises KeyError."""
        with patch("next.urls.backends.next_framework_settings") as nfs:
            nfs.COMPONENT_BACKENDS = []
            with pytest.raises(KeyError, match="COMPONENTS_DIR"):
                FileRouterBackend._resolve_components_folder_name()

    def test_resolve_components_folder_name_raises_when_first_entry_invalid(
        self,
    ) -> None:
        """First component backend dict must contain COMPONENTS_DIR."""
        with patch("next.urls.backends.next_framework_settings") as nfs:
            nfs.COMPONENT_BACKENDS = [{}]
            with pytest.raises(KeyError, match="COMPONENTS_DIR"):
                FileRouterBackend._resolve_components_folder_name()
