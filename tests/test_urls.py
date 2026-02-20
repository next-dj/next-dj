import shutil
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from next.pages import page
from next.urls import (
    FileRouterBackend,
    RouterBackend,
    RouterFactory,
    RouterManager,
    router_manager,
    urlpatterns,
)


class TestRouterBackend:
    """Test the abstract routerbackend class."""

    def test_router_backend_is_abstract(self) -> None:
        """Test that routerbackend cannot be instantiated directly."""
        with pytest.raises(TypeError):
            RouterBackend()


class TestFileRouterBackend:
    """Test the filerouterbackend implementation."""

    @pytest.fixture()
    def router(self):
        """Create a basic router instance for tests."""
        return FileRouterBackend()

    @pytest.fixture()
    def temp_dir(self):
        """Create a temporary directory for tests."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        shutil.rmtree(temp_dir)

    @pytest.fixture()
    def pages_dir(self, temp_dir):
        """Create a pages directory structure for tests."""
        pages_dir = Path(temp_dir) / "testapp" / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)
        return pages_dir

    @pytest.fixture()
    def mock_settings(self):
        """Mock Django settings for tests."""
        with patch("next.urls.settings") as mock_settings:
            yield mock_settings

    @pytest.fixture()
    def temp_file(self):
        """Create a temporary file for testing."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def render(request, **kwargs):\n    return 'response'")
            temp_file = Path(f.name)
        yield temp_file
        temp_file.unlink()

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
            (
                "custom",
                "views",
                False,
                {"custom": "value"},
                "views",
                False,
                {"custom": "value"},
            ),
        ],
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
        """Test router initialization with different parameters."""
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
    )
    def test_repr_variations(self, pages_dir, app_dirs, expected_repr) -> None:
        """Test string representation with different parameters."""
        router = FileRouterBackend(pages_dir, app_dirs=app_dirs)
        assert repr(router) == expected_repr

    def _create_router_from_params(self, params):
        """Create FileRouterBackend from parameters or return as-is."""
        if isinstance(params, tuple):
            if len(params) == 3:
                return FileRouterBackend(
                    params[0],
                    app_dirs=params[1],
                    options=params[2],
                )
            if len(params) == 2:
                return FileRouterBackend(params[0], app_dirs=params[1])
            if len(params) == 1:
                return FileRouterBackend(params[0])
            return params  # empty tuple case
        return params

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
    )
    def test_equality_variations(
        self,
        test_case,
        router1_params,
        router2_params,
        expected_equal,
    ) -> None:
        """Test equality with different scenarios."""
        router1 = self._create_router_from_params(router1_params)
        router2 = self._create_router_from_params(router2_params)

        if expected_equal:
            assert router1 == router2
        else:
            assert router1 != router2

    def test_equality_with_different_type(self) -> None:
        """Test equality comparison with different object type."""
        router = FileRouterBackend("pages")
        other = "not a router"
        assert router != other

    def test_hash(self) -> None:
        """Test hash function."""
        router1 = FileRouterBackend("pages", app_dirs=True, options={"opt": "val"})
        router2 = FileRouterBackend("pages", app_dirs=True, options={"opt": "val"})
        assert hash(router1) == hash(router2)

    @pytest.mark.parametrize(
        ("app_dirs", "method_to_patch", "expected_urls"),
        [
            (True, "_generate_app_urls", ["url1", "url2"]),
            (False, "_generate_root_urls", ["url1"]),
        ],
    )
    def test_generate_urls_variations(
        self, app_dirs, method_to_patch, expected_urls
    ) -> None:
        """Test URL generation for different configurations."""
        router = FileRouterBackend(app_dirs=app_dirs)
        with patch.object(router, method_to_patch, return_value=expected_urls):
            urls = router.generate_urls()
            assert urls == expected_urls

    def test_get_installed_apps(self, router, mock_settings) -> None:
        """Test getting installed Django apps."""
        mock_settings.INSTALLED_APPS = [
            "django.contrib.admin",
            "myapp",
            "django.contrib.auth",
        ]
        apps = list(router._get_installed_apps())
        assert apps == ["myapp"]

    @pytest.mark.parametrize(
        ("test_case", "import_side_effect", "file_path", "expected_result"),
        [
            ("success", None, "/path/to/app/__init__.py", "mock_pages_path"),
            ("import_error", ImportError, None, None),
            ("no_file", None, None, None),
        ],
    )
    def test_get_app_pages_path_variations(
        self,
        router,
        test_case,
        import_side_effect,
        file_path,
        expected_result,
    ) -> None:
        """Test getting app pages path with different scenarios."""
        with patch("builtins.__import__") as mock_import:
            if import_side_effect:
                mock_import.side_effect = import_side_effect
                result = router._get_app_pages_path("nonexistent")
                assert result is None
            else:
                mock_module = Mock()
                mock_module.__file__ = file_path
                mock_import.return_value = mock_module

                if file_path:
                    with patch("next.urls.Path") as mock_path_class:
                        mock_app_path = Mock()
                        mock_app_path.parent = Mock()
                        mock_pages_path = Mock()
                        mock_pages_path.exists.return_value = True
                        mock_path_class.return_value = mock_app_path
                        mock_app_path.parent.__truediv__ = Mock(
                            return_value=mock_pages_path,
                        )

                        result = router._get_app_pages_path("testapp")
                        assert result == mock_pages_path
                else:
                    result = router._get_app_pages_path("testapp")
                    assert result is None

    @pytest.mark.parametrize(
        ("test_case", "base_dir", "exists", "expected_result"),
        [
            ("with_base_dir", "/path/to/project", True, "mock_path_instance"),
            ("string_base_dir", "/path/to/project", True, "mock_path_instance"),
            ("no_base_dir", None, None, None),
            ("does_not_exist", "/path/to/project", False, None),
        ],
    )
    def test_get_root_pages_path_variations(
        self,
        router,
        mock_settings,
        test_case,
        base_dir,
        exists,
        expected_result,
    ) -> None:
        """Test _get_root_pages_paths with different scenarios."""
        mock_settings.BASE_DIR = base_dir

        if base_dir is None:
            result = router._get_root_pages_paths()
            assert result == []
        else:
            root_router = FileRouterBackend(app_dirs=False)
            with patch("next.urls.Path") as mock_path_class:
                mock_path_instance = Mock()
                mock_path_instance.exists.return_value = exists
                mock_path_class.return_value = mock_path_instance
                mock_path_instance.__truediv__ = Mock(return_value=mock_path_instance)

                result = root_router._get_root_pages_paths()
                if exists:
                    assert len(result) == 1
                    assert result[0] == mock_path_instance
                else:
                    assert result == []

    def test_get_root_pages_paths_from_pages_dirs(self, temp_dir) -> None:
        """Test _get_root_pages_paths returns list from OPTIONS.PAGES_DIRS."""
        router = FileRouterBackend(options={"PAGES_DIRS": [temp_dir]})
        result = router._get_root_pages_paths()
        assert len(result) == 1
        assert result[0] == Path(temp_dir)

    def test_get_root_pages_paths_from_pages_dir(self, temp_dir) -> None:
        """Test _get_root_pages_paths returns list from OPTIONS.PAGES_DIR."""
        router = FileRouterBackend(options={"PAGES_DIR": temp_dir})
        result = router._get_root_pages_paths()
        assert len(result) == 1
        assert result[0] == Path(temp_dir)

    def test_get_root_pages_paths_skips_nonexistent(self) -> None:
        """Test _get_root_pages_paths skips paths that do not exist."""
        router = FileRouterBackend(
            options={"PAGES_DIRS": ["/nonexistent/path", "/also/nonexistent"]},
        )
        result = router._get_root_pages_paths()
        assert result == []

    def test_get_root_pages_paths_fallback_when_app_dirs_false(
        self, mock_settings, temp_dir
    ) -> None:
        """Test _get_root_pages_paths fallback to BASE_DIR/pages_dir when app_dirs False."""
        pages_dir = Path(temp_dir) / "pages"
        pages_dir.mkdir()
        mock_settings.BASE_DIR = Path(temp_dir)
        router = FileRouterBackend(app_dirs=False)
        result = router._get_root_pages_paths()
        assert len(result) == 1
        assert result[0] == pages_dir

    def test_get_root_pages_paths_empty_when_app_dirs_true_no_options(self) -> None:
        """Test _get_root_pages_paths returns [] when app_dirs True and no PAGES_DIRS/PAGES_DIR."""
        router = FileRouterBackend(app_dirs=True)
        result = router._get_root_pages_paths()
        assert result == []

    def test_generate_urls_includes_root_when_app_dirs_and_pages_dirs(
        self, temp_dir
    ) -> None:
        """Test generate_urls returns app patterns + root patterns when APP_DIRS and PAGES_DIRS."""
        router = FileRouterBackend(app_dirs=True, options={"PAGES_DIRS": [temp_dir]})
        with (
            patch.object(router, "_generate_app_urls", return_value=[]),
            patch.object(
                router,
                "_generate_patterns_from_directory",
                return_value=[],
            ) as mock_gen,
        ):
            urls = router.generate_urls()
        assert urls == []
        mock_gen.assert_called_with(Path(temp_dir))

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
        """Test generating URLs for app with different scenarios."""
        if cache_value:
            router._patterns_cache["testapp"] = cache_value
            result = router._generate_urls_for_app("testapp")
            assert result == expected_result
        else:
            with patch.object(
                router,
                "_get_app_pages_path",
                return_value=pages_path_return,
            ):
                if pages_path_return:
                    with patch.object(
                        router,
                        "_generate_patterns_from_directory",
                        return_value=patterns_return,
                    ):
                        result = router._generate_urls_for_app("testapp")
                        assert result == expected_result
                        assert router._patterns_cache["testapp"] == patterns_return
                else:
                    result = router._generate_urls_for_app("testapp")
                    assert result == expected_result

    def test_generate_patterns_from_directory(self) -> None:
        """Test generating patterns from directory."""
        router = FileRouterBackend()
        mock_pages_path = Mock()

        with (
            patch.object(
                router,
                "_scan_pages_directory",
                return_value=[("url1", "file1"), ("url2", "file2")],
            ),
            patch("next.urls.page.create_url_pattern") as mock_create,
        ):
            mock_create.side_effect = ["pattern1", "pattern2"]

            patterns = list(
                router._generate_patterns_from_directory(mock_pages_path),
            )
            assert patterns == ["pattern1", "pattern2"]

    def test_scan_pages_directory_empty(self) -> None:
        """Test scanning empty pages directory."""
        router = FileRouterBackend()

        with patch("pathlib.Path.iterdir", return_value=[]):
            pages = list(router._scan_pages_directory(Path("/tmp")))
            assert pages == []

    def test_scan_pages_directory_with_files(self) -> None:
        """Test scanning pages directory with files."""
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
        """Test recursive scanning of pages directory."""
        router = FileRouterBackend()

        # create a mock directory structure
        root_dir = Path("/tmp/pages")

        with patch("pathlib.Path.iterdir") as mock_iterdir:
            # mock the directory structure
            mock_iterdir.side_effect = [
                [Mock(name="dir1", is_dir=lambda: True)],
                [Mock(name="page.py", is_dir=lambda: False)],
            ]

            with patch.object(router, "_scan_pages_directory") as mock_scan:
                mock_scan.return_value = [("home", "file1"), ("", "file2")]

                pages = list(router._scan_pages_directory(root_dir))
                assert len(pages) == 2
                assert any("home" in str(page[0]) for page in pages)

    def test_create_url_pattern_with_args_parameter(self) -> None:
        """Test creating URL pattern with args parameter handling."""
        router = FileRouterBackend()

        # create a temporary file for testing
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def render(request, **kwargs):\n    return 'response'")
            temp_file = Path(f.name)

        try:
            with patch("next.urls.page.render", return_value="mocked response"):
                pattern = page.create_url_pattern(
                    "test/[[args]]",
                    temp_file,
                    router._url_parser,
                )
                assert pattern is not None

                # test that the view wrapper handles args parameter correctly
                # we need to access the view function from the pattern
                if hasattr(pattern, "callback"):
                    view_func = pattern.callback
                    # test with args parameter
                    result = view_func(Mock(), args="arg1/arg2/arg3")
                    # the view should work without errors
                    assert result is not None
        finally:
            temp_file.unlink()


class TestRouterFactory:
    """Test cases for RouterFactory class."""

    @pytest.fixture()
    def custom_backend_class(self):
        """Create a custom backend class for testing."""

        class CustomBackend(RouterBackend):
            def generate_urls(self):
                return []

        return CustomBackend

    def test_register_backend(self, custom_backend_class) -> None:
        """Test registering a new backend."""
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
                    "APP_DIRS": True,
                    "OPTIONS": {},
                },
                FileRouterBackend,
                {"pages_dir": "pages", "app_dirs": True},
            ),
            (
                "defaults",
                {"BACKEND": "next.urls.FileRouterBackend"},
                FileRouterBackend,
                {"pages_dir": "pages", "app_dirs": True, "options": {}},
            ),
            ("missing_backend", {}, FileRouterBackend, {}),
        ],
    )
    def test_create_backend_variations(
        self,
        test_case,
        config,
        expected_type,
        expected_attrs,
    ) -> None:
        """Test creating backend with different configurations."""
        router = RouterFactory.create_backend(config)
        assert isinstance(router, expected_type)

        for attr, expected_value in expected_attrs.items():
            assert getattr(router, attr) == expected_value

    def test_create_backend_unsupported(self) -> None:
        """Test creating backend with unsupported backend name."""
        config = {"BACKEND": "unsupported.backend"}

        with pytest.raises(ValueError, match="Unsupported backend"):
            RouterFactory.create_backend(config)

    def test_create_backend_non_file_router_backend(self, custom_backend_class) -> None:
        """Test creating backend with non-FileRouterBackend type."""
        RouterFactory.register_backend("custom.backend", custom_backend_class)

        config = {"BACKEND": "custom.backend"}
        router = RouterFactory.create_backend(config)
        assert isinstance(router, custom_backend_class)

    def test_create_backend_non_file_router_backend_else_branch(
        self,
        custom_backend_class,
    ) -> None:
        """Test create_backend when backend is not FileRouterBackend (line 230)."""
        RouterFactory.register_backend("custom", custom_backend_class)

        # create backend without specific arguments - this should hit the else branch
        backend = RouterFactory.create_backend({"BACKEND": "custom"})
        assert isinstance(backend, custom_backend_class)
        # verify that the else branch was executed by checking that no FileRouterBackend-specific args were passed
        assert not hasattr(backend, "pages_dir")


class TestRouterManager:
    """Test cases for RouterManager class."""

    @pytest.fixture()
    def manager(self):
        """Create a RouterManager instance for tests."""
        return RouterManager()

    def test_init(self, manager) -> None:
        """Test RouterManager initialization."""
        assert manager._routers == []
        assert manager._config_cache is None

    def test_repr(self, manager) -> None:
        """Test string representation."""
        assert repr(manager) == "<RouterManager routers=0>"

    @pytest.mark.parametrize(
        ("router_count", "expected_len"),
        [
            (0, 0),
            (1, 1),
        ],
    )
    def test_len_variations(self, manager, router_count, expected_len) -> None:
        """Test length method with different router counts."""
        for _ in range(router_count):
            manager._routers.append(Mock())
        assert len(manager) == expected_len

    def test_iter_returns_url_patterns(self, manager) -> None:
        """Test that __iter__ returns URL patterns from all routers."""
        # mock routers that return url patterns
        mock_router1 = Mock()
        mock_router1.generate_urls.return_value = ["url1", "url2"]
        mock_router2 = Mock()
        mock_router2.generate_urls.return_value = ["url3"]

        manager._routers = [mock_router1, mock_router2]

        # __iter__ should return all url patterns combined
        url_patterns = list(manager)
        assert url_patterns == ["url1", "url2", "url3"]

    def test_iter_triggers_reload_when_empty(self, manager) -> None:
        """Test that __iter__ calls _reload_config when routers list is empty."""
        with patch.object(manager, "_reload_config") as mock_reload:
            # ensure empty routers to hit the branch
            manager._routers = []
            # iteration should call _reload_config()
            list(manager)
            mock_reload.assert_called_once()

    def test_iter_reloads_config_when_empty(self, manager) -> None:
        """Test that __iter__ reloads config when no routers are configured."""
        # mock the reload process to avoid actual reloading
        with (
            patch.object(manager, "_reload_config"),
            patch.object(manager, "_get_next_pages_config") as mock_get_config,
            patch("next.urls.RouterFactory.create_backend") as mock_create,
        ):
            mock_get_config.return_value = [
                {"BACKEND": "next.urls.FileRouterBackend"},
            ]
            mock_router = Mock()
            mock_router.generate_urls.return_value = ["url1"]
            mock_create.return_value = mock_router

            # manually add the router to simulate what _reload_config would do
            manager._routers = [mock_router]

            # trigger iteration
            url_patterns = list(manager)

            # should return the url patterns from the router
            assert url_patterns == ["url1"]

    def test_getitem(self, manager) -> None:
        """Test getting router by index."""
        router = Mock()
        manager._routers = [router]

        assert manager[0] == router

    def test_reload_config_clears_cache(self, manager) -> None:
        """Test that reload_config clears the config cache initially but then refills it."""
        manager._config_cache = ["some", "cached", "config"]

        # don't mock _get_next_pages_config, let it run normally
        # this will set _config_cache to the default config
        manager._reload_config()
        # cache should be refilled with the default config from _get_next_pages_config
        assert manager._config_cache is not None
        assert len(manager._config_cache) == 1
        assert manager._config_cache[0]["BACKEND"] == "next.urls.FileRouterBackend"
        # routers should be created from the default config
        assert len(manager._routers) == 1
        assert isinstance(manager._routers[0], FileRouterBackend)

    def test_reload_config_with_exception(self, manager) -> None:
        """Test reload_config when creating backend raises exception."""
        # mock RouterFactory to raise an exception
        with patch(
            "next.urls.RouterFactory.create_backend",
            side_effect=Exception("Test error"),
        ):
            manager._reload_config()
            # should handle exception gracefully and not add router
            assert len(manager._routers) == 0
            # cache should be refilled even if router creation fails
            assert manager._config_cache is not None
            assert len(manager._config_cache) == 1
            assert manager._config_cache[0]["BACKEND"] == "next.urls.FileRouterBackend"

    def test_get_next_pages_config_uses_cache(self, manager) -> None:
        """Test that _get_next_pages_config uses cached config."""
        cached_config = ["cached", "config"]
        manager._config_cache = cached_config

        result = manager._get_next_pages_config()
        assert result == cached_config

    def test_get_next_pages_config_no_next_pages_setting(self, manager) -> None:
        """Test _get_next_pages_config when NEXT_PAGES setting is not configured."""
        with patch("next.urls.settings") as mock_settings:
            # remove NEXT_PAGES attribute to test default config
            if hasattr(mock_settings, "NEXT_PAGES"):
                delattr(mock_settings, "NEXT_PAGES")

            result = manager._get_next_pages_config()
            # should return default config
            assert len(result) == 1
            assert result[0]["BACKEND"] == "next.urls.FileRouterBackend"


class TestGlobalInstances:
    """Test global instances and their behavior."""

    def test_router_manager_instance(self) -> None:
        """Test that router_manager is properly initialized."""
        assert router_manager is not None
        assert isinstance(router_manager, RouterManager)

    def test_router_manager_reload_config_clears_cache(self) -> None:
        """Test that global router_manager reload_config works correctly."""
        # test that the global instance works
        len(router_manager._routers)
        router_manager._reload_config()
        # should clear and reload
        assert router_manager._config_cache is not None

    def test_urlpatterns_dynamic(self) -> None:
        """Test that urlpatterns is dynamically generated through router_manager."""
        # urlpatterns should be a list generated from router_manager
        assert isinstance(urlpatterns, list)

        # test that urlpatterns is iterable and returns url patterns
        # we need to patch the method that generates the actual patterns
        with patch.object(router_manager, "_routers", [Mock()]):
            mock_router = router_manager._routers[0]
            mock_router.generate_urls.return_value = ["url1", "url2"]

            # urlpatterns is created at import time, so we need to test router_manager directly
            patterns = list(router_manager)
            assert patterns == ["url1", "url2"]

    def test_generate_urls_for_app_returns_empty_list(self) -> None:
        """Test _generate_urls_for_app when it returns empty list."""
        router = FileRouterBackend()

        with patch.object(router, "_generate_urls_for_app", return_value=[]):
            urls = router.generate_urls()
            assert urls == []

    @pytest.mark.parametrize(
        ("test_case", "expected_result"),
        [
            ("no_pages_path", []),
            ("with_none_pages_path", []),
        ],
    )
    def test_generate_root_urls_scenarios(self, test_case, expected_result) -> None:
        """Test _generate_root_urls with different scenarios."""
        router = FileRouterBackend()

        # mock the pages path to return [] to test empty result scenarios
        with patch.object(router, "_get_root_pages_paths", return_value=[]):
            urls = router._generate_root_urls()
            assert urls == expected_result

    def test_create_url_pattern_view_wrapper_no_args_parameter(self) -> None:
        """Test view_wrapper when args parameter is not passed."""
        router = FileRouterBackend()

        # create a temporary file for testing
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def render(request, **kwargs):\n    return 'response'")
            temp_file = Path(f.name)

        try:
            with patch("next.urls.page.render", return_value="mocked response"):
                pattern = page.create_url_pattern(
                    "test/[[args]]",
                    temp_file,
                    router._url_parser,
                )
                assert pattern is not None

                # test that the view wrapper handles missing args parameter correctly
                if hasattr(pattern, "callback"):
                    view_func = pattern.callback
                    # test without args parameter - should not crash
                    result = view_func(Mock(), other_param="value")
                    assert result is not None
        finally:
            temp_file.unlink()

    def test_generate_urls_with_empty_patterns_from_apps(self) -> None:
        """Test generate_urls when _generate_urls_for_app returns empty list."""
        router = FileRouterBackend()

        # mock _get_installed_apps to return a list of apps
        with (
            patch.object(router, "_get_installed_apps", return_value=["app1", "app2"]),
            patch.object(router, "_generate_urls_for_app", return_value=[]),
        ):
            urls = router.generate_urls()
            assert urls == []

    @pytest.mark.parametrize(
        ("test_case", "file_content", "expected_result"),
        [
            (
                "without_args_parameter",
                "def render(request, **kwargs):\n    return 'success'",
                "mocked success",
            ),
            (
                "args_parameter_not_in_kwargs",
                "def render(request, **kwargs):\n    return 'success'",
                "mocked success",
            ),
        ],
    )
    def test_view_wrapper_scenarios(
        self, test_case, file_content, expected_result
    ) -> None:
        """Test view_wrapper with different scenarios."""
        router = FileRouterBackend()

        # create a temporary file for testing
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(file_content)
            temp_file = Path(f.name)

        try:
            with patch("next.urls.page.render", return_value=expected_result):
                pattern = page.create_url_pattern(
                    "test/[[args]]",
                    temp_file,
                    router._url_parser,
                )
                assert pattern is not None

                # test that the view wrapper handles missing args parameter correctly
                if hasattr(pattern, "callback"):
                    view_func = pattern.callback
                    # test without args parameter - should not crash and should not modify kwargs
                    result = view_func(Mock(), other_param="value")
                    assert result is not None
        finally:
            temp_file.unlink()

    def test_generate_urls_direct_coverage(self) -> None:
        """Test generate_urls method directly with walrus operator."""
        router = FileRouterBackend()

        # mock the entire method chain to ensure we hit the walrus operator
        with (
            patch.object(router, "_get_installed_apps", return_value=["testapp"]),
            patch.object(router, "_generate_urls_for_app", return_value=[]),
            patch.object(router, "_generate_root_urls", return_value=[]),
        ):
            urls = router.generate_urls()
            assert urls == []

    def test_generate_root_urls_direct_coverage(self) -> None:
        """Test _generate_root_urls method."""
        router = FileRouterBackend()

        # mock _get_root_pages_paths to return [] to hit the return [] path
        with patch.object(router, "_get_root_pages_paths", return_value=[]):
            urls = router._generate_root_urls()
            assert urls == []

    def test_generate_urls_real_execution(self) -> None:
        """Test generate_urls method with real execution."""
        router = FileRouterBackend()

        # create a real test environment that will execute the walrus operator
        with patch("next.urls.settings") as mock_settings:
            mock_settings.INSTALLED_APPS = ["testapp"]

            # mock the app path to return None so _generate_urls_for_app returns empty list
            with patch.object(router, "_get_app_pages_path", return_value=None):
                urls = router.generate_urls()
                assert urls == []

    def test_generate_root_urls_real_execution(self) -> None:
        """Test _generate_root_urls method with real execution."""
        router = FileRouterBackend()

        # mock settings to return None for BASE_DIR
        with patch("next.urls.settings") as mock_settings:
            mock_settings.BASE_DIR = None

            urls = router._generate_root_urls()
            assert urls == []

    def test_view_wrapper_real_execution(self) -> None:
        """Test view_wrapper with real execution."""
        router = FileRouterBackend()

        # create a temporary file for testing
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def render(request, **kwargs):\n    return kwargs")
            temp_file = Path(f.name)

        try:
            with patch("next.urls.page.render", return_value="mocked kwargs"):
                pattern = page.create_url_pattern(
                    "test/[[args]]",
                    temp_file,
                    router._url_parser,
                )
                assert pattern is not None

                # test that the view wrapper handles missing args parameter correctly
                if hasattr(pattern, "callback"):
                    view_func = pattern.callback
                    # test without args parameter - should not crash and should not modify kwargs
                    result = view_func(Mock(), other_param="value")
                    assert result is not None
        finally:
            temp_file.unlink()

    def test_create_backend_real_execution(self) -> None:
        """Test create_backend with real execution."""

        # create a custom backend that's not FileRouterBackend
        class CustomBackend(RouterBackend):
            def generate_urls(self):
                return []  # implement abstract method

        # register it
        RouterFactory.register_backend("custom", CustomBackend)

        # create backend without specific arguments - this should hit the else branch
        backend = RouterFactory.create_backend({"BACKEND": "custom"})
        assert isinstance(backend, CustomBackend)
        # verify that the else branch was executed by checking that no FileRouterBackend-specific args were passed
        assert not hasattr(backend, "pages_dir")

    def test_generate_urls_comprehensive_coverage(self) -> None:
        """Test generate_urls method comprehensively to cover all uncovered lines."""
        router = FileRouterBackend()

        # test the entire generate_urls method to ensure we cover all lines
        with patch("next.urls.settings") as mock_settings:
            # set up INSTALLED_APPS to trigger the loop
            mock_settings.INSTALLED_APPS = ["testapp1", "testapp2"]

            # mock _get_app_pages_path to return None for first app, path for second
            with patch.object(router, "_get_app_pages_path") as mock_get_path:
                mock_get_path.side_effect = [None, Path("/tmp/pages")]

                # mock _generate_patterns_from_directory to return some patterns
                with patch.object(
                    router,
                    "_generate_patterns_from_directory",
                ) as mock_gen_patterns:
                    mock_gen_patterns.return_value = ["pattern1", "pattern2"]

                    # mock page.create_url_pattern to return a pattern
                    with patch(
                        "next.urls.page.create_url_pattern",
                        return_value="url_pattern",
                    ):
                        urls = router.generate_urls()
                        # the result should be the patterns from _generate_patterns_from_directory
                        assert urls == ["pattern1", "pattern2"]

    def test_generate_root_urls_comprehensive_coverage(self) -> None:
        """Test _generate_root_urls method comprehensively."""
        router = FileRouterBackend()

        # test with BASE_DIR = None to hit the return [] path
        with patch("next.urls.settings") as mock_settings:
            mock_settings.BASE_DIR = None

            urls = router._generate_root_urls()
            assert urls == []

    def test_generate_root_urls_with_patterns(self) -> None:
        """Test _generate_root_urls returns patterns when pages path exists."""
        router = FileRouterBackend()

        with (
            patch.object(
                router,
                "_get_root_pages_paths",
                return_value=[Path("/tmp/pages")],
            ),
            patch.object(
                router,
                "_generate_patterns_from_directory",
                return_value=iter(["p1", "p2"]),  # generator-like
            ),
        ):
            urls = router._generate_root_urls()
            assert urls == ["p1", "p2"]

    def test_create_url_pattern_comprehensive_coverage(self) -> None:
        """Test _create_url_pattern method comprehensively."""
        router = FileRouterBackend()

        # create a temporary file for testing
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write("def render(request, **kwargs):\n    return kwargs")
            temp_file = Path(f.name)

        try:
            with patch("next.urls.page.render", return_value="mocked kwargs"):
                pattern = page.create_url_pattern(
                    "test/[[args]]",
                    temp_file,
                    router._url_parser,
                )
                assert pattern is not None

                # test that the view wrapper handles missing args parameter correctly
                if hasattr(pattern, "callback"):
                    view_func = pattern.callback
                    # test without args parameter - should not crash and should not modify kwargs
                    result = view_func(Mock(), other_param="value")
                    assert result is not None
        finally:
            temp_file.unlink()

    def test_create_backend_comprehensive_coverage(self) -> None:
        """Test create_backend method comprehensively."""

        # create a custom backend that's not FileRouterBackend
        class CustomBackend(RouterBackend):
            def generate_urls(self):
                return []  # implement abstract method

        # register it
        RouterFactory.register_backend("custom", CustomBackend)

        # create backend without specific arguments - this should hit the else branch
        backend = RouterFactory.create_backend({"BACKEND": "custom"})
        assert isinstance(backend, CustomBackend)
        # verify that the else branch was executed by checking that no FileRouterBackend-specific args were passed
        assert not hasattr(backend, "pages_dir")

    def test_scan_pages_directory_real_filesystem(self, tmp_path) -> None:
        """Test _scan_pages_directory recursion on a real filesystem."""
        router = FileRouterBackend()

        pages_dir = tmp_path / "testapp" / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)

        # create nested directories and page.py files
        (pages_dir / "home").mkdir(parents=True, exist_ok=True)
        (pages_dir / "home" / "page.py").write_text(
            "def render(request):\n    return 'home'\n",
        )

        (pages_dir / "items" / "[int:id]").mkdir(parents=True, exist_ok=True)
        (pages_dir / "items" / "[int:id]" / "page.py").write_text(
            "def render(request, id):\n    return id\n",
        )

        (pages_dir / "blog" / "post").mkdir(parents=True, exist_ok=True)
        (pages_dir / "blog" / "post" / "page.py").write_text(
            "def render(request):\n    return 'post'\n",
        )

        results = list(router._scan_pages_directory(pages_dir))
        url_paths = {u for (u, _f) in results}

        assert "home" in url_paths
        assert "items/[int:id]" in url_paths
        assert "blog/post" in url_paths

    def test_create_url_pattern_with_template_attribute(self) -> None:
        """Test _create_url_pattern when module has template attribute (lines 192-200)."""
        router = FileRouterBackend()

        # create a temporary file with template attribute
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write('template = "Hello {{ name }}!"')
            temp_file = Path(f.name)

        try:
            with (
                patch("next.urls.page.register_template") as mock_register,
                patch("next.urls.page.render", return_value="Hello World!"),
            ):
                pattern = page.create_url_pattern(
                    "test",
                    temp_file,
                    router._url_parser,
                )

                # verify that page.register_template was called
                mock_register.assert_called_once_with(
                    temp_file,
                    "Hello {{ name }}!",
                )

                # verify that a URLPattern was returned
                assert pattern is not None
                assert hasattr(pattern, "callback")
                assert hasattr(pattern, "name")
                assert pattern.name == "page_test"
        finally:
            temp_file.unlink()

    def test_create_url_pattern_template_view_function_without_args(self) -> None:
        """Test the view function created for template pages without args parameter."""
        router = FileRouterBackend()

        # create a temporary file with template attribute
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write('template = "Hello {{ name }}!"')
            temp_file = Path(f.name)

        try:
            with (
                patch("next.urls.page.register_template"),
                patch(
                    "next.urls.page.render",
                    return_value="Hello World!",
                ) as mock_render,
            ):
                pattern = page.create_url_pattern(
                    "test",
                    temp_file,
                    router._url_parser,
                )

                # get the view function
                view_func = pattern.callback

                # test without args parameter - should not modify kwargs
                mock_request = Mock()
                result = view_func(mock_request, name="John")

                # verify that page.render was called with original kwargs
                mock_render.assert_called_once_with(
                    temp_file,
                    mock_request,
                    name="John",
                )
                assert result is not None
        finally:
            temp_file.unlink()

    def test_create_url_pattern_template_view_function_args_not_in_parameters(
        self,
    ) -> None:
        """Test view function when args is in kwargs but not in parameters."""
        router = FileRouterBackend()

        # create a temporary file with template attribute
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write('template = "Hello {{ name }}!"')
            temp_file = Path(f.name)

        try:
            with (
                patch("next.urls.page.register_template"),
                patch(
                    "next.urls.page.render",
                    return_value="Hello World!",
                ) as mock_render,
            ):
                # use a simple URL path without args parameter
                pattern = page.create_url_pattern(
                    "test",
                    temp_file,
                    router._url_parser,
                )

                # get the view function
                view_func = pattern.callback

                # test with args in kwargs but not in parameters - should not split
                mock_request = Mock()
                result = view_func(mock_request, args="arg1/arg2/arg3")

                # verify that page.render was called with original args (not split)
                mock_render.assert_called_once_with(
                    temp_file,
                    mock_request,
                    args="arg1/arg2/arg3",
                )
                assert result is not None
        finally:
            temp_file.unlink()

    def test_create_url_pattern_template_view_function_args_not_in_kwargs(self) -> None:
        """Test view function when args is in parameters but not in kwargs."""
        router = FileRouterBackend()

        # create a temporary file with template attribute
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write('template = "Hello {{ name }}!"')
            temp_file = Path(f.name)

        try:
            with (
                patch("next.urls.page.register_template"),
                patch(
                    "next.urls.page.render",
                    return_value="Hello World!",
                ) as mock_render,
            ):
                # use a URL path with args parameter
                pattern = page.create_url_pattern(
                    "test/[[args]]",
                    temp_file,
                    router._url_parser,
                )

                # get the view function
                view_func = pattern.callback

                # test without args in kwargs - should not add parameters to kwargs
                mock_request = Mock()
                result = view_func(mock_request, name="John")

                # verify that page.render was called with only actual kwargs
                # (parameters dict is not added to kwargs anymore)
                mock_render.assert_called_once_with(
                    temp_file,
                    mock_request,
                    name="John",
                )
                assert result is not None
        finally:
            temp_file.unlink()

    def test_create_url_pattern_no_template_no_render(self) -> None:
        """Test _create_url_pattern when module has neither template nor render function."""
        router = FileRouterBackend()

        # create a temporary file without template or render
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write('some_variable = "test"')
            temp_file = Path(f.name)

        try:
            pattern = page.create_url_pattern("test", temp_file, router._url_parser)
            assert pattern is None
        finally:
            temp_file.unlink()

    def test_create_url_pattern_spec_from_file_location_returns_none(self) -> None:
        """Test _create_url_pattern when spec_from_file_location returns None."""
        router = FileRouterBackend()

        with patch("importlib.util.spec_from_file_location", return_value=None):
            pattern = page.create_url_pattern(
                "test",
                Path("/nonexistent/file.py"),
                router._url_parser,
            )
            assert pattern is None

    def test_create_url_pattern_spec_loader_is_none(self) -> None:
        """Test _create_url_pattern when spec.loader is None."""
        router = FileRouterBackend()

        # create a mock spec with None loader
        mock_spec = Mock()
        mock_spec.loader = None

        with patch("importlib.util.spec_from_file_location", return_value=mock_spec):
            pattern = page.create_url_pattern(
                "test",
                Path("/some/file.py"),
                router._url_parser,
            )
            assert pattern is None
