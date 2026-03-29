from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.test import override_settings

from next.conf import next_framework_settings
from next.pages import get_pages_directories_for_watch, page
from next.urls import (
    FileRouterBackend,
    RouterBackend,
    RouterFactory,
    RouterManager,
    _scan_pages_directory,
    router_manager,
    urlpatterns,
)
from tests.support import file_router_backend_from_params, named_temp_py


class TestRouterBackend:
    """Abstract RouterBackend cannot be instantiated."""

    def test_router_backend_is_abstract(self) -> None:
        """Direct instantiation raises TypeError."""
        with pytest.raises(TypeError):
            RouterBackend()


class TestFileRouterBackend:
    """FileRouterBackend initialization, paths, and URL generation."""

    @pytest.fixture()
    def router(self):
        """Fresh FileRouterBackend instance."""
        return FileRouterBackend()

    @pytest.fixture()
    def mock_settings(self):
        """Patch ``next.urls.settings``."""
        with patch("next.urls.settings") as mock_settings:
            yield mock_settings

    @pytest.fixture()
    def temp_file(self):
        """Temporary ``page.py`` with a minimal render function."""
        with named_temp_py(
            "def render(request, **kwargs):\n    return 'response'"
        ) as path:
            yield path

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
    )
    def test_equality_variations(
        self,
        test_case,
        router1_params,
        router2_params,
        expected_equal,
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
    )
    def test_generate_urls_variations(
        self, app_dirs, method_to_patch, expected_urls
    ) -> None:
        """Delegates to app or root URL generators based on app_dirs."""
        router = FileRouterBackend(app_dirs=app_dirs)
        with patch.object(router, method_to_patch, return_value=expected_urls):
            urls = router.generate_urls()
            assert urls == expected_urls

    def test_get_installed_apps(self, router, mock_settings) -> None:
        """Yields only project apps, skipping Django contrib packages."""
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
        """Resolves app package path or returns None on import error or missing file."""
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
        """Root pages paths from BASE_DIR when directory exists or missing."""
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

    def test_get_root_pages_paths_from_pages_dirs(self, tmp_path) -> None:
        """OPTIONS.PAGES_DIRS adds resolved paths."""
        router = FileRouterBackend(options={"PAGES_DIRS": [tmp_path]})
        result = router._get_root_pages_paths()
        assert len(result) == 1
        assert result[0] == tmp_path

    def test_get_root_pages_paths_from_pages_dir(self, tmp_path) -> None:
        """OPTIONS.PAGES_DIR adds a single path."""
        router = FileRouterBackend(options={"PAGES_DIR": tmp_path})
        result = router._get_root_pages_paths()
        assert len(result) == 1
        assert result[0] == tmp_path

    def test_get_root_pages_paths_skips_nonexistent(self) -> None:
        """Nonexistent PAGES_DIRS entries are omitted."""
        router = FileRouterBackend(
            options={"PAGES_DIRS": ["/nonexistent/path", "/also/nonexistent"]},
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

    def test_get_root_pages_paths_empty_when_app_dirs_true_no_options(self) -> None:
        """With app_dirs True and no PAGES_DIRS or PAGES_DIR, returns an empty list."""
        router = FileRouterBackend(app_dirs=True)
        result = router._get_root_pages_paths()
        assert result == []

    def test_generate_urls_includes_root_when_app_dirs_and_pages_dirs(
        self, tmp_path
    ) -> None:
        """With app_dirs and PAGES_DIRS, root directory patterns are generated."""
        router = FileRouterBackend(app_dirs=True, options={"PAGES_DIRS": [tmp_path]})
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
        """Builds URL patterns from scan results via create_url_pattern."""
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
        """View wrapper accepts args string when URL pattern includes [[args]]."""
        router = FileRouterBackend()

        with (
            named_temp_py(
                "def render(request, **kwargs):\n    return 'response'"
            ) as temp_file,
            patch("next.urls.page.render", return_value="mocked response"),
        ):
            pattern = page.create_url_pattern(
                "test/[[args]]",
                temp_file,
                router._url_parser,
            )
            assert pattern is not None

            if hasattr(pattern, "callback"):
                view_func = pattern.callback
                result = view_func(Mock(), args="arg1/arg2/arg3")
                assert result is not None


class TestRouterFactory:
    """RouterFactory.register_backend and create_backend."""

    @pytest.fixture()
    def custom_backend_class(self):
        """Minimal concrete RouterBackend for registration tests."""

        class CustomBackend(RouterBackend):
            def generate_urls(self):
                return []

        return CustomBackend

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
                    "OPTIONS": {},
                },
                FileRouterBackend,
                {"pages_dir": "pages", "app_dirs": True, "options": {}},
            ),
        ],
    )
    def test_create_backend_variations(
        self,
        test_case,
        config,
        expected_type,
        expected_attrs,
    ) -> None:
        """Valid FileRouterBackend config produces a router with expected attributes."""
        router = RouterFactory.create_backend(config)
        assert isinstance(router, expected_type)

        for attr, expected_value in expected_attrs.items():
            assert getattr(router, attr) == expected_value

    @pytest.mark.parametrize(
        ("config", "missing_key"),
        [
            ({}, "BACKEND"),
            ({"BACKEND": "next.urls.FileRouterBackend"}, "PAGES_DIR"),
            (
                {
                    "BACKEND": "next.urls.FileRouterBackend",
                    "PAGES_DIR": "pages",
                },
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
        ],
    )
    def test_create_backend_keyerror_when_required_key_missing(
        self,
        config,
        missing_key,
    ) -> None:
        """FileRouterBackend config must list BACKEND, PAGES_DIR, APP_DIRS, OPTIONS explicitly."""
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
        self,
        custom_backend_class,
    ) -> None:
        """Minimal config dict hits the non FileRouterBackend branch."""
        RouterFactory.register_backend("custom", custom_backend_class)

        backend = RouterFactory.create_backend({"BACKEND": "custom"})
        assert isinstance(backend, custom_backend_class)
        assert not hasattr(backend, "pages_dir")


class TestRouterManager:
    """RouterManager iteration, reload, and config access."""

    @pytest.fixture()
    def manager(self):
        """Fresh RouterManager."""
        return RouterManager()

    def test_init(self, manager) -> None:
        """Starts with empty routers and no config cache."""
        assert manager._routers == []
        assert manager._config_cache is None

    def test_repr(self, manager) -> None:
        """``repr`` shows router count."""
        assert repr(manager) == "<RouterManager routers=0>"

    @pytest.mark.parametrize(
        ("router_count", "expected_len"),
        [
            (0, 0),
            (1, 1),
        ],
    )
    def test_len_variations(self, manager, router_count, expected_len) -> None:
        """``len`` matches number of registered routers."""
        for _ in range(router_count):
            manager._routers.append(Mock())
        assert len(manager) == expected_len

    def test_iter_returns_url_patterns(self, manager) -> None:
        """Iteration concatenates generate_urls from each router."""
        mock_router1 = Mock()
        mock_router1.generate_urls.return_value = ["url1", "url2"]
        mock_router2 = Mock()
        mock_router2.generate_urls.return_value = ["url3"]

        manager._routers = [mock_router1, mock_router2]

        # __iter__ should return all url patterns combined
        url_patterns = list(manager)
        assert url_patterns == ["url1", "url2", "url3"]

    def test_iter_triggers_reload_when_empty(self, manager) -> None:
        """Empty routers triggers _reload_config on iteration."""
        with patch.object(manager, "_reload_config") as mock_reload:
            manager._routers = []
            list(manager)
            mock_reload.assert_called_once()

    def test_iter_reloads_config_when_empty(self, manager) -> None:
        """After reload, iteration returns patterns from created routers."""
        with (
            patch.object(manager, "_reload_config"),
            patch.object(manager, "_get_next_pages_config") as mock_get_config,
            patch("next.urls.RouterFactory.create_backend") as mock_create,
        ):
            mock_get_config.return_value = [
                {
                    "BACKEND": "next.urls.FileRouterBackend",
                    "PAGES_DIR": "pages",
                    "APP_DIRS": True,
                    "OPTIONS": {},
                },
            ]
            mock_router = Mock()
            mock_router.generate_urls.return_value = ["url1"]
            mock_create.return_value = mock_router

            manager._routers = [mock_router]

            url_patterns = list(manager)

            assert url_patterns == ["url1"]

    def test_getitem(self, manager) -> None:
        """Index access returns the router at that position."""
        router = Mock()
        manager._routers = [router]

        assert manager[0] == router

    def test_reload_config_clears_cache(self, manager) -> None:
        """Reload replaces cache and builds routers from default framework config."""
        manager._config_cache = ["some", "cached", "config"]

        manager._reload_config()
        assert manager._config_cache is not None
        assert len(manager._config_cache) == 1
        assert manager._config_cache[0]["BACKEND"] == "next.urls.FileRouterBackend"
        assert len(manager._routers) == 1
        assert isinstance(manager._routers[0], FileRouterBackend)

    def test_reload_config_with_exception(self, manager) -> None:
        """Backend creation failure leaves routers empty but cache is still set."""
        with patch(
            "next.urls.RouterFactory.create_backend",
            side_effect=Exception("Test error"),
        ):
            manager._reload_config()
            assert len(manager._routers) == 0
            assert manager._config_cache is not None
            assert len(manager._config_cache) == 1
            assert manager._config_cache[0]["BACKEND"] == "next.urls.FileRouterBackend"

    def test_get_next_pages_config_uses_cache(self, manager) -> None:
        """Returns cached list when present."""
        cached_config = ["cached", "config"]
        manager._config_cache = cached_config

        result = manager._get_next_pages_config()
        assert result == cached_config

    def test_get_next_pages_config_no_next_setting(self, manager) -> None:
        """When ``NEXT`` is unset, merged framework defaults include ``ROUTERS``."""
        with override_settings(NEXT_FRAMEWORK=None):
            next_framework_settings.reload()
            manager._config_cache = None
            result = manager._get_next_pages_config()
            assert len(result) == 1
            assert result[0]["BACKEND"] == "next.urls.FileRouterBackend"


class TestGlobalInstances:
    """Module level router_manager, urlpatterns, and integration style coverage."""

    def test_router_manager_instance(self) -> None:
        """``router_manager`` is a RouterManager instance."""
        assert router_manager is not None
        assert isinstance(router_manager, RouterManager)

    def test_router_manager_reload_config_clears_cache(self) -> None:
        """Global manager reload refreshes config cache."""
        len(router_manager._routers)
        router_manager._reload_config()
        assert router_manager._config_cache is not None

    def test_urlpatterns_dynamic(self) -> None:
        """``urlpatterns`` is a list. Iteration delegates to ``router_manager``."""
        assert isinstance(urlpatterns, list)

        with patch.object(router_manager, "_routers", [Mock()]):
            mock_router = router_manager._routers[0]
            mock_router.generate_urls.return_value = ["url1", "url2"]

            patterns = list(router_manager)
            assert patterns == ["url1", "url2"]

    def test_generate_urls_for_app_returns_empty_list(self) -> None:
        """Empty per app URLs yield empty generate_urls."""
        router = FileRouterBackend()

        with patch.object(router, "_generate_urls_for_app", return_value=[]):
            urls = router.generate_urls()
            assert urls == []

    def test_generate_root_urls_returns_empty_when_no_pages_path(self) -> None:
        """No root pages paths means no root URL patterns."""
        router = FileRouterBackend()
        with patch.object(router, "_get_root_pages_paths", return_value=[]):
            urls = router._generate_root_urls()
            assert urls == []

    def test_generate_urls_with_empty_patterns_from_apps(self) -> None:
        """Apps with empty per app patterns still run the app loop."""
        router = FileRouterBackend()

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
            (
                "view_returns_kwargs",
                "def render(request, **kwargs):\n    return kwargs",
                "mocked kwargs",
            ),
        ],
        ids=["without_args_parameter", "args_not_in_kwargs", "view_returns_kwargs"],
    )
    def test_view_wrapper_scenarios(
        self, tmp_path, test_case, file_content, expected_result
    ) -> None:
        """View callback behavior when args is absent or kwargs vary."""
        router = FileRouterBackend()
        render_module_path = tmp_path / "page.py"
        render_module_path.write_text(file_content)

        with patch("next.urls.page.render", return_value=expected_result):
            pattern = page.create_url_pattern(
                "test/[[args]]",
                render_module_path,
                router._url_parser,
            )
            assert pattern is not None

            if hasattr(pattern, "callback"):
                view_func = pattern.callback
                result = view_func(Mock(), other_param="value")
                assert result is not None

    def test_generate_root_urls_returns_empty_when_base_dir_none(self) -> None:
        """BASE_DIR None yields no root URLs."""
        router = FileRouterBackend()
        with patch("next.urls.settings") as mock_settings:
            mock_settings.BASE_DIR = None
            urls = router._generate_root_urls()
            assert urls == []

    def test_create_backend_real_execution(self) -> None:
        """Registered custom backend instantiates without pages_dir."""

        class CustomBackend(RouterBackend):
            def generate_urls(self):
                return []

        RouterFactory.register_backend("custom", CustomBackend)

        backend = RouterFactory.create_backend({"BACKEND": "custom"})
        assert isinstance(backend, CustomBackend)
        assert not hasattr(backend, "pages_dir")

    def test_generate_urls_comprehensive_coverage(self) -> None:
        """generate_urls walks apps and collects patterns from existing pages paths."""
        router = FileRouterBackend()

        with patch("next.urls.settings") as mock_settings:
            mock_settings.INSTALLED_APPS = ["testapp1", "testapp2"]

            with patch.object(router, "_get_app_pages_path") as mock_get_path:
                mock_get_path.side_effect = [None, Path("/tmp/pages")]

                with patch.object(
                    router,
                    "_generate_patterns_from_directory",
                ) as mock_gen_patterns:
                    mock_gen_patterns.return_value = ["pattern1", "pattern2"]

                    with patch(
                        "next.urls.page.create_url_pattern",
                        return_value="url_pattern",
                    ):
                        urls = router.generate_urls()
                        assert urls == ["pattern1", "pattern2"]

    def test_generate_root_urls_with_patterns(self) -> None:
        """Root patterns come from _generate_patterns_from_directory."""
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

    def test_scan_pages_directory_real_filesystem(self, tmp_path) -> None:
        """Nested page.py files produce URL path segments on disk."""
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
        """Template only module gets a named pattern and callback."""
        router = FileRouterBackend()

        with named_temp_py('template = "Hello {{ name }}!"') as temp_file:
            pattern = page.create_url_pattern(
                "test",
                temp_file,
                router._url_parser,
            )
            assert pattern is not None
            assert hasattr(pattern, "callback")
            assert hasattr(pattern, "name")
            assert pattern.name == "page_test"

    def test_create_url_pattern_template_view_function_without_args(self) -> None:
        """Template view forwards kwargs to render when URL has no [[args]]."""
        router = FileRouterBackend()

        with (
            named_temp_py('template = "Hello {{ name }}!"') as temp_file,
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

            view_func = pattern.callback

            mock_request = Mock()
            result = view_func(mock_request, name="John")

            mock_render.assert_called_once_with(
                temp_file,
                mock_request,
                name="John",
            )
            assert result is not None

    def test_create_url_pattern_template_view_function_args_not_in_parameters(
        self,
    ) -> None:
        """Args passed as keyword are forwarded without path splitting."""
        router = FileRouterBackend()

        with (
            named_temp_py('template = "Hello {{ name }}!"') as temp_file,
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

            view_func = pattern.callback

            mock_request = Mock()
            result = view_func(mock_request, args="arg1/arg2/arg3")

            mock_render.assert_called_once_with(
                temp_file,
                mock_request,
                args="arg1/arg2/arg3",
            )
            assert result is not None

    def test_create_url_pattern_template_view_function_args_not_in_kwargs(self) -> None:
        """[[args]] in path without args in call still calls render with given kwargs."""
        router = FileRouterBackend()

        with (
            named_temp_py('template = "Hello {{ name }}!"') as temp_file,
            patch("next.urls.page.register_template"),
            patch(
                "next.urls.page.render",
                return_value="Hello World!",
            ) as mock_render,
        ):
            pattern = page.create_url_pattern(
                "test/[[args]]",
                temp_file,
                router._url_parser,
            )

            view_func = pattern.callback

            mock_request = Mock()
            result = view_func(mock_request, name="John")

            mock_render.assert_called_once_with(
                temp_file,
                mock_request,
                name="John",
            )
            assert result is not None

    def test_create_url_pattern_no_template_no_render(self) -> None:
        """Neither template nor render returns no pattern."""
        router = FileRouterBackend()

        with named_temp_py('some_variable = "test"') as temp_file:
            pattern = page.create_url_pattern("test", temp_file, router._url_parser)
            assert pattern is None

    def test_create_url_pattern_spec_from_file_location_returns_none(self) -> None:
        """Missing import spec yields no pattern."""
        router = FileRouterBackend()

        with patch("importlib.util.spec_from_file_location", return_value=None):
            pattern = page.create_url_pattern(
                "test",
                Path("/nonexistent/file.py"),
                router._url_parser,
            )
            assert pattern is None

    def test_create_url_pattern_spec_loader_is_none(self) -> None:
        """Spec with no loader returns no pattern."""
        router = FileRouterBackend()

        mock_spec = Mock()
        mock_spec.loader = None

        with patch("importlib.util.spec_from_file_location", return_value=mock_spec):
            pattern = page.create_url_pattern(
                "test",
                Path("/some/file.py"),
                router._url_parser,
            )
            assert pattern is None


class TestRouterManagerNextPagesConfig:
    """``RouterManager._get_next_pages_config`` defensive branches."""

    def test_non_list_default_page_routers_returns_empty_cached(self) -> None:
        """When ``DEFAULT_PAGE_ROUTERS`` is not a list, config is empty and cached."""
        mock_nf = SimpleNamespace(DEFAULT_PAGE_ROUTERS="not-a-list")
        with patch("next.urls.next_framework_settings", mock_nf):
            mgr = RouterManager()
            assert mgr._get_next_pages_config() == []
            assert mgr._get_next_pages_config() == []


class TestGetPagesDirectoriesForWatch:
    """Watch list from next.pages.get_pages_directories_for_watch (used by utils and apps)."""

    def test_returns_empty_when_routers_not_list(self) -> None:
        """When ``ROUTERS`` is not a list, returns []."""
        mock_nf = SimpleNamespace(DEFAULT_PAGE_ROUTERS={})
        with patch("next.pages.next_framework_settings", mock_nf):
            assert get_pages_directories_for_watch() == []

    def test_skips_non_dict_config(self) -> None:
        """List entries that are not dicts are skipped."""
        with override_settings(
            NEXT_FRAMEWORK={"DEFAULT_PAGE_ROUTERS": ["not a dict", None]},
        ):
            next_framework_settings.reload()
            assert get_pages_directories_for_watch() == []

    def test_swallows_backend_creation_error(self) -> None:
        """Invalid backend entry is skipped, valid entries still contribute."""
        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_PAGE_ROUTERS": [
                    {"BACKEND": "nonexistent.Backend"},
                    {
                        "BACKEND": "next.urls.FileRouterBackend",
                        "PAGES_DIR": "pages",
                        "APP_DIRS": False,
                        "OPTIONS": {
                            "BASE_DIR": str(
                                Path(__file__).parent.parent / "tests" / "pages",
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
                NEXT_FRAMEWORK={"DEFAULT_PAGE_ROUTERS": [{"BACKEND": "other.Backend"}]},
            ):
                next_framework_settings.reload()
                assert get_pages_directories_for_watch() == []

    def test_includes_root_and_app_paths_from_backend(self, tmp_path) -> None:
        """Backend root paths and app pages paths are both included."""
        app_pages = tmp_path / "myapp_pages"
        app_pages.mkdir()
        with patch("next.urls.RouterFactory.create_backend") as mock_create:
            mock_backend = Mock(spec=FileRouterBackend)
            mock_backend._get_root_pages_paths = Mock(
                return_value=[tmp_path / "root_pages"],
            )
            mock_backend._get_installed_apps = Mock(return_value=["myapp"])
            mock_backend._get_app_pages_path = Mock(return_value=app_pages)
            mock_create.return_value = mock_backend
            with override_settings(
                NEXT_FRAMEWORK={
                    "DEFAULT_PAGE_ROUTERS": [
                        {
                            "BACKEND": "next.urls.FileRouterBackend",
                            "PAGES_DIR": "pages",
                            "APP_DIRS": True,
                            "OPTIONS": {},
                        },
                    ],
                },
            ):
                next_framework_settings.reload()
                result = get_pages_directories_for_watch()
                assert (tmp_path / "root_pages").resolve() in result
                assert app_pages.resolve() in result


# Module function next.urls._scan_pages_directory (see also FileRouterBackend delegation in test_pages).
class TestScanPagesDirectory:
    """Edge cases for the standalone scan helper including skip_dir_names."""

    def test_oserror_on_iterdir_returns_nothing(self, tmp_path) -> None:
        """OSError from iterdir produces no routes."""
        with patch.object(Path, "iterdir", side_effect=OSError):
            result = list(_scan_pages_directory(tmp_path))
        assert result == []

    def test_virtual_page_template_djx_only(self, tmp_path) -> None:
        """template.djx without page.py yields a synthetic page path at root."""
        (tmp_path / "template.djx").write_text("<h1>Hi</h1>")
        result = list(_scan_pages_directory(tmp_path))
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
        result = list(_scan_pages_directory(tmp_path))
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
        result = list(_scan_pages_directory(tmp_path, skip_dir_names=("_components",)))
        url_paths = {r[0] for r in result}
        assert "" in url_paths
        assert "home" in url_paths
        assert "_components" not in url_paths
        assert "_components/nested" not in url_paths
        assert len(result) == 2
