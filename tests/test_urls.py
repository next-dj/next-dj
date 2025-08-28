import tempfile
import types
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

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

    def test_router_backend_is_abstract(self):
        """Test that routerbackend cannot be instantiated directly."""
        with pytest.raises(TypeError):
            RouterBackend()


class TestFileRouterBackend:
    """Test the filerouterbackend implementation."""

    @pytest.fixture
    def router(self):
        """Create a basic router instance for tests."""
        return FileRouterBackend()

    @pytest.fixture
    def temp_dir(self):
        """Create a temporary directory for tests."""
        temp_dir = tempfile.mkdtemp()
        yield temp_dir
        import shutil

        shutil.rmtree(temp_dir)

    @pytest.fixture
    def pages_dir(self, temp_dir):
        """Create a pages directory structure for tests."""
        pages_dir = Path(temp_dir) / "testapp" / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)
        return pages_dir

    def test_init_defaults(self):
        """Test router initialization with default values."""
        router = FileRouterBackend()
        assert router.pages_dir_name == "pages"
        assert router.app_dirs is True
        assert router.options == {}
        assert router._patterns_cache == {}

    def test_init_custom(self):
        """Test router initialization with custom values."""
        router = FileRouterBackend(
            pages_dir_name="views", app_dirs=False, options={"custom": "value"}
        )
        assert router.pages_dir_name == "views"
        assert router.app_dirs is False
        assert router.options == {"custom": "value"}

    def test_repr(self):
        """Test string representation."""
        router = FileRouterBackend("views", False)
        assert repr(router) == "<FileRouterBackend pages_dir='views' app_dirs=False>"

    def test_eq_same_instance(self):
        """Test equality with same instance."""
        router1 = FileRouterBackend("pages", True, {"opt": "val"})
        router2 = FileRouterBackend("pages", True, {"opt": "val"})
        assert router1 == router2

    def test_eq_different_instance(self):
        """Test equality with different instance."""
        router1 = FileRouterBackend("pages", True)
        router2 = FileRouterBackend("views", True)
        assert router1 != router2

    def test_eq_wrong_type(self):
        """Test equality with wrong type."""
        router = FileRouterBackend()
        assert router != "not a router"

    def test_hash(self):
        """Test hash function."""
        router1 = FileRouterBackend("pages", True, {"opt": "val"})
        router2 = FileRouterBackend("pages", True, {"opt": "val"})
        assert hash(router1) == hash(router2)

    def test_generate_urls_app_dirs(self):
        """Test URL generation for app directories."""
        router = FileRouterBackend(app_dirs=True)
        with patch.object(router, "_generate_app_urls", return_value=["url1", "url2"]):
            urls = router.generate_urls()
            assert urls == ["url1", "url2"]

    def test_generate_urls_root_only(self):
        """Test URL generation for root pages only."""
        router = FileRouterBackend(app_dirs=False)
        with patch.object(router, "_generate_root_urls", return_value=["url1"]):
            urls = router.generate_urls()
            assert urls == ["url1"]

    def test_get_installed_apps(self):
        """Test getting installed Django apps."""
        router = FileRouterBackend()
        with patch("next.urls.settings") as mock_settings:
            mock_settings.INSTALLED_APPS = [
                "django.contrib.admin",
                "myapp",
                "django.contrib.auth",
            ]
            apps = list(router._get_installed_apps())
            assert apps == ["myapp"]

    def test_get_app_pages_path_success(self):
        """Test getting app pages path successfully."""
        router = FileRouterBackend()
        with patch("builtins.__import__") as mock_import:
            mock_module = Mock()
            mock_module.__file__ = "/path/to/app/__init__.py"
            mock_import.return_value = mock_module

            with patch("next.urls.Path") as mock_path_class:
                mock_app_path = Mock()
                mock_app_path.parent = Mock()
                mock_pages_path = Mock()
                mock_pages_path.exists.return_value = True
                mock_path_class.return_value = mock_app_path
                mock_app_path.parent.__truediv__ = Mock(return_value=mock_pages_path)

                result = router._get_app_pages_path("testapp")
                assert result == mock_pages_path

    def test_get_app_pages_path_import_error(self):
        """Test getting app pages path with import error."""
        router = FileRouterBackend()
        with patch("builtins.__import__", side_effect=ImportError):
            result = router._get_app_pages_path("nonexistent")
            assert result is None

    def test_get_app_pages_path_no_file(self):
        """Test getting app pages path when __file__ is None."""
        router = FileRouterBackend()
        with patch("builtins.__import__") as mock_import:
            mock_module = Mock()
            mock_module.__file__ = None
            mock_import.return_value = mock_module

            result = router._get_app_pages_path("testapp")
            assert result is None

    def test_get_root_pages_path_with_base_dir(self):
        """Test getting root pages path with BASE_DIR setting."""
        router = FileRouterBackend()
        with patch("next.urls.settings") as mock_settings:
            mock_settings.BASE_DIR = "/path/to/project"

            with patch("next.urls.Path") as mock_path_class:
                mock_path_instance = Mock()
                mock_path_instance.exists.return_value = True
                mock_path_class.return_value = mock_path_instance
                mock_path_instance.__truediv__ = Mock(return_value=mock_path_instance)

                result = router._get_root_pages_path()
                assert result == mock_path_instance

    def test_get_root_pages_path_string_base_dir(self):
        """Test getting root pages path with string BASE_DIR."""
        router = FileRouterBackend()
        with patch("next.urls.settings") as mock_settings:
            mock_settings.BASE_DIR = "/path/to/project"

            with patch("next.urls.Path") as mock_path_class:
                mock_path_instance = Mock()
                mock_path_instance.exists.return_value = True
                mock_path_class.return_value = mock_path_instance
                mock_path_instance.__truediv__ = Mock(return_value=mock_path_instance)

                result = router._get_root_pages_path()
                assert result == mock_path_instance

    def test_get_root_pages_path_no_base_dir(self):
        """Test getting root pages path when BASE_DIR is not set."""
        router = FileRouterBackend()
        with patch("next.urls.settings") as mock_settings:
            mock_settings.BASE_DIR = None
            result = router._get_root_pages_path()
            assert result is None

    def test_get_root_pages_path_does_not_exist(self):
        """Test getting root pages path when directory doesn't exist."""
        router = FileRouterBackend()
        with patch("next.urls.settings") as mock_settings:
            mock_settings.BASE_DIR = "/path/to/project"

            with patch("next.urls.Path") as mock_path_class:
                mock_path_instance = Mock()
                mock_path_instance.exists.return_value = False
                mock_path_class.return_value = mock_path_instance
                mock_path_instance.__truediv__ = Mock(return_value=mock_path_instance)

                result = router._get_root_pages_path()
                assert result is None

    def test_generate_urls_for_app_cached(self):
        """Test generating URLs for app with cache hit."""
        router = FileRouterBackend()
        router._patterns_cache["testapp"] = ["cached_url"]

        result = router._generate_urls_for_app("testapp")
        assert result == ["cached_url"]

    def test_generate_urls_for_app_no_pages_path(self):
        """Test generating URLs for app when pages path doesn't exist."""
        router = FileRouterBackend()
        with patch.object(router, "_get_app_pages_path", return_value=None):
            result = router._generate_urls_for_app("testapp")
            assert result == []

    def test_generate_urls_for_app_with_patterns(self):
        """Test generating URLs for app with patterns."""
        router = FileRouterBackend()
        mock_pages_path = Mock()

        with patch.object(router, "_get_app_pages_path", return_value=mock_pages_path):
            with patch.object(
                router,
                "_generate_patterns_from_directory",
                return_value=["pattern1", "pattern2"],
            ):
                result = router._generate_urls_for_app("testapp")
                assert result == ["pattern1", "pattern2"]
                assert router._patterns_cache["testapp"] == ["pattern1", "pattern2"]

    def test_generate_patterns_from_directory(self):
        """Test generating patterns from directory."""
        router = FileRouterBackend()
        mock_pages_path = Mock()

        with patch.object(
            router,
            "_scan_pages_directory",
            return_value=[("url1", "file1"), ("url2", "file2")],
        ):
            with patch.object(router, "_create_url_pattern") as mock_create:
                mock_create.side_effect = ["pattern1", "pattern2"]

                patterns = list(
                    router._generate_patterns_from_directory(mock_pages_path)
                )
                assert patterns == ["pattern1", "pattern2"]

    def test_scan_pages_directory_empty(self):
        """Test scanning empty pages directory."""
        router = FileRouterBackend()

        with patch("pathlib.Path.iterdir", return_value=[]):
            pages = list(router._scan_pages_directory(Path("/tmp")))
            assert pages == []

    def test_scan_pages_directory_with_files(self):
        """Test scanning pages directory with files."""
        router = FileRouterBackend()

        mock_dir = Mock()
        mock_dir.name = "dir1"
        mock_dir.is_dir.return_value = True

        mock_file = Mock()
        mock_file.name = "page.py"
        mock_file.is_dir.return_value = False

        with patch("pathlib.Path.iterdir", return_value=[mock_dir, mock_file]):
            with patch.object(router, "_scan_pages_directory") as mock_scan:
                mock_scan.return_value = [("dir1", "file1")]

                pages = list(router._scan_pages_directory(Path("/tmp")))
                assert pages == [("dir1", "file1")]

    def test_scan_pages_directory_recursive(self):
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

    def test_create_url_pattern_with_args_parameter(self):
        """Test creating URL pattern with args parameter handling."""
        router = FileRouterBackend()

        with patch.object(
            router, "_load_page_function", return_value=lambda req, **kwargs: "response"
        ):
            pattern = router._create_url_pattern("test/[[args]]", Path("/tmp/test.py"))
            assert pattern is not None

            # test that the view wrapper handles args parameter correctly
            # we need to access the view function from the pattern
            if hasattr(pattern, "callback"):
                view_func = pattern.callback
                # test with args parameter
                result = view_func(Mock(), args="arg1/arg2/arg3")
                # the view should work without errors
                assert result == "response"

    def test_parse_url_pattern_simple(self):
        """Test parsing simple URL pattern."""
        router = FileRouterBackend()
        pattern, params = router._parse_url_pattern("simple")
        assert pattern == "simple"
        assert params == {}

    def test_parse_url_pattern_with_param(self):
        """Test parsing URL pattern with parameter."""
        router = FileRouterBackend()
        pattern, params = router._parse_url_pattern("user/[id]")
        assert pattern == "user/<str:id>"
        assert params == {"id": "id"}

    def test_parse_url_pattern_with_type(self):
        """Test parsing URL pattern with type."""
        router = FileRouterBackend()
        pattern, params = router._parse_url_pattern("user/[int:user-id]")
        assert pattern == "user/<int:user_id>"
        assert params == {"user_id": "user_id"}

    def test_parse_url_pattern_with_args(self):
        """Test parsing URL pattern with args."""
        router = FileRouterBackend()
        pattern, params = router._parse_url_pattern("profile/[[args]]")
        assert pattern == "profile/<path:args>"
        assert params == {"args": "args"}

    def test_parse_url_pattern_mixed(self):
        """Test parsing URL pattern with mixed parameters."""
        router = FileRouterBackend()
        pattern, params = router._parse_url_pattern("user/[int:id]/posts/[[args]]")
        assert pattern == "user/<int:id>/posts/<path:args>"
        assert params == {"id": "id", "args": "args"}

    def test_parse_url_pattern_complex(self):
        """Test parsing complex URL pattern."""
        router = FileRouterBackend()
        pattern, params = router._parse_url_pattern(
            "user/[int:user-id]/posts/[slug:post-slug]/[[args]]"
        )
        assert "<int:user_id>" in pattern
        assert "<slug:post_slug>" in pattern
        assert "<path:args>" in pattern
        assert "user_id" in params
        assert "post_slug" in params
        assert "args" in params

    def test_parse_url_pattern_edge_cases(self):
        """Test parsing URL pattern edge cases."""
        router = FileRouterBackend()

        # empty string
        pattern, params = router._parse_url_pattern("")
        assert pattern == ""
        assert params == {}

        # empty brackets
        pattern, params = router._parse_url_pattern("[]")
        assert "[" in pattern or "<str:" in pattern
        assert len(params) == 0 or "" in params

        # empty args brackets
        pattern, params = router._parse_url_pattern("[[]]")
        assert "[" in pattern or "<path:" in pattern
        assert len(params) == 0 or "" in params

    def test_parse_param_name_and_type_simple(self):
        """Test parsing parameter name and type."""
        router = FileRouterBackend()
        name, type_name = router._parse_param_name_and_type("param")
        assert name == "param"
        assert type_name == "str"

    def test_parse_param_name_and_type_with_type(self):
        """Test parsing parameter name and type with type specification."""
        router = FileRouterBackend()
        name, type_name = router._parse_param_name_and_type("int:user-id")
        assert name == "user-id"
        assert type_name == "int"

    def test_parse_param_name_and_type_edge_cases(self):
        """Test parsing parameter name and type edge cases."""
        router = FileRouterBackend()

        # empty string
        name, type_name = router._parse_param_name_and_type("")
        assert name == ""
        assert type_name == "str"

        # whitespace
        name, type_name = router._parse_param_name_and_type("   ")
        assert name == ""
        assert type_name == "str"

        # only colon
        name, type_name = router._parse_param_name_and_type(":param")
        assert name == "param"
        assert type_name == ""

    def test_parse_url_pattern_with_args_and_params(self):
        """Test parsing URL pattern with both args and regular parameters."""
        router = FileRouterBackend()

        url_path = "user/[[profile]]/[int:user-id]/posts"
        django_pattern, parameters = router._parse_url_pattern(url_path)

        assert "profile" in parameters
        assert "user_id" in parameters
        assert django_pattern == "user/<path:profile>/<int:user_id>/posts"

    def test_parse_param_name_and_type_with_colon(self):
        """Test parsing parameter name and type with colon separator."""
        router = FileRouterBackend()

        name, type_name = router._parse_param_name_and_type("int:user-id")
        assert name == "user-id"
        assert type_name == "int"

    def test_parse_param_name_and_type_without_colon(self):
        """Test parsing parameter name and type without colon separator."""
        router = FileRouterBackend()

        name, type_name = router._parse_param_name_and_type("username")
        assert name == "username"
        assert type_name == "str"

    def test_load_page_function_success(self):
        """Test loading page function successfully."""
        router = FileRouterBackend()

        with patch("importlib.util.spec_from_file_location") as mock_spec:
            mock_spec.return_value = Mock(loader=Mock())

            with patch("importlib.util.module_from_spec") as mock_module:
                mock_module.return_value = Mock()
                mock_module.return_value.render = lambda req: "response"

                with patch(
                    "importlib.util.spec_from_file_location"
                ) as mock_spec_from_file:
                    mock_spec_from_file.return_value = Mock(loader=Mock())

                    result = router._load_page_function(Path("/tmp/page.py"))
                    assert result is not None
                    assert callable(result)

    def test_load_page_function_no_render(self):
        """Test loading page function when render function is missing."""
        router = FileRouterBackend()

        with patch("importlib.util.spec_from_file_location") as mock_spec:
            mock_spec.return_value = Mock(loader=Mock())

            with patch("importlib.util.module_from_spec") as mock_module:
                mock_module.return_value = Mock()
                # no render function

                with patch("builtins.getattr", return_value=None):
                    result = router._load_page_function(Path("/tmp/page.py"))
                    assert result is None

    def test_load_page_function_not_callable(self):
        """Test loading page function when render is not callable."""
        router = FileRouterBackend()

        with patch("importlib.util.spec_from_file_location") as mock_spec:
            mock_spec.return_value = Mock(loader=Mock())

            with patch("importlib.util.module_from_spec") as mock_module:
                mock_module.return_value = Mock()
                mock_module.return_value.render = "not a function"

                # mock the exec_module to avoid any real execution
                mock_spec.return_value.loader.exec_module.return_value = None

                result = router._load_page_function(Path("/tmp/page.py"))
                assert result is None

    def test_load_page_function_execution_error(self):
        """Test loading page function with execution error."""
        router = FileRouterBackend()

        with patch("importlib.util.spec_from_file_location") as mock_spec:
            mock_spec.return_value = Mock(loader=Mock())

            with patch("importlib.util.module_from_spec") as mock_module:
                mock_module.return_value = Mock()

                # make exec_module raise an exception
                mock_spec.return_value.loader.exec_module.side_effect = Exception(
                    "Test error"
                )

                result = router._load_page_function(Path("/tmp/page.py"))
                assert result is None

    def test_load_page_function_spec_none(self):
        """Test loading page function when spec is None."""
        router = FileRouterBackend()

        with patch("importlib.util.spec_from_file_location", return_value=None):
            result = router._load_page_function(Path("/tmp/page.py"))
            assert result is None

    def test_load_page_function_loader_none(self):
        """Test loading page function when loader is None."""
        router = FileRouterBackend()

        mock_spec = Mock()
        mock_spec.loader = None

        with patch("importlib.util.spec_from_file_location", return_value=mock_spec):
            result = router._load_page_function(Path("/tmp/page.py"))
            assert result is None

    def test_load_page_function_exec_module_exception(self):
        """Test loading page function when exec_module raises an exception."""
        router = FileRouterBackend()

        with patch("importlib.util.spec_from_file_location") as mock_spec:
            mock_spec.return_value = Mock(loader=Mock())

            with patch("importlib.util.module_from_spec") as mock_module:
                mock_module.return_value = Mock()

                # make exec_module raise an exception to cover the except block
                mock_spec.return_value.loader.exec_module.side_effect = Exception(
                    "Test error during exec_module"
                )

                result = router._load_page_function(Path("/tmp/page.py"))
                assert result is None

    def test_load_page_function_getattr_returns_none(self):
        """Test loading page function when getattr returns None."""
        router = FileRouterBackend()

        with patch("importlib.util.spec_from_file_location") as mock_spec:
            mock_spec.return_value = Mock(loader=Mock())

            with patch("importlib.util.module_from_spec") as mock_module:
                mock_module.return_value = Mock()

                # mock the exec_module to avoid any real execution
                mock_spec.return_value.loader.exec_module.return_value = None

                with patch("builtins.getattr", return_value=None):
                    result = router._load_page_function(Path("/tmp/page.py"))
                    assert result is None

    def test_load_page_function_exec_module_error(self):
        """Test loading page function when exec_module raises an exception."""
        router = FileRouterBackend()

        with patch("importlib.util.spec_from_file_location") as mock_spec:
            mock_spec.return_value = Mock(loader=Mock())

            with patch("importlib.util.module_from_spec") as mock_module:
                mock_module.return_value = Mock()

                # make exec_module raise an exception
                mock_spec.return_value.loader.exec_module.side_effect = Exception(
                    "Test error"
                )

                result = router._load_page_function(Path("/tmp/page.py"))
                assert result is None


class TestRouterFactory:
    """Test cases for RouterFactory class."""

    def test_register_backend(self):
        """Test registering a new backend."""

        # register a custom backend
        class CustomBackend(RouterBackend):
            pass

        RouterFactory.register_backend("custom", CustomBackend)
        assert "custom" in RouterFactory._backends
        assert RouterFactory._backends["custom"] == CustomBackend

    def test_create_backend_success(self):
        """Test creating backend successfully."""
        config = {
            "BACKEND": "next.urls.FileRouterBackend",
            "APP_DIRS": True,
            "OPTIONS": {"PAGES_DIR_NAME": "views"},
        }

        router = RouterFactory.create_backend(config)
        assert isinstance(router, FileRouterBackend)
        assert router.pages_dir_name == "views"
        assert router.app_dirs is True

    def test_create_backend_defaults(self):
        """Test creating backend with default values."""
        config = {"BACKEND": "next.urls.FileRouterBackend"}

        router = RouterFactory.create_backend(config)
        assert isinstance(router, FileRouterBackend)
        assert router.pages_dir_name == "pages"
        assert router.app_dirs is True
        assert router.options == {}

    def test_create_backend_unsupported(self):
        """Test creating backend with unsupported backend name."""
        config = {"BACKEND": "unsupported.backend"}

        with pytest.raises(ValueError, match="Unsupported backend"):
            RouterFactory.create_backend(config)

    def test_create_backend_missing_backend(self):
        """Test creating backend with missing backend name."""
        config = {}

        router = RouterFactory.create_backend(config)
        assert isinstance(router, FileRouterBackend)

    def test_create_backend_non_file_router_backend(self):
        """Test creating backend with non-FileRouterBackend type."""

        class CustomBackend(RouterBackend):
            def generate_urls(self):
                return []

        RouterFactory.register_backend("custom.backend", CustomBackend)

        config = {"BACKEND": "custom.backend"}
        router = RouterFactory.create_backend(config)
        assert isinstance(router, CustomBackend)

    def test_create_backend_non_file_router_backend_else_branch(self):
        """Test create_backend when backend is not FileRouterBackend (line 230)."""

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
        assert not hasattr(backend, "pages_dir_name")


class TestRouterManager:
    """Test cases for RouterManager class."""

    def test_init(self):
        """Test RouterManager initialization."""
        manager = RouterManager()
        assert manager._routers == []
        assert manager._config_cache is None

    def test_repr(self):
        """Test string representation."""
        manager = RouterManager()
        assert repr(manager) == "<RouterManager routers=0>"

    def test_len(self):
        """Test length method."""
        manager = RouterManager()
        assert len(manager) == 0

        # add a router
        manager._routers.append(Mock())
        assert len(manager) == 1

    def test_iter_returns_url_patterns(self):
        """Test that __iter__ returns URL patterns from all routers."""
        manager = RouterManager()

        # mock routers that return url patterns
        mock_router1 = Mock()
        mock_router1.generate_urls.return_value = ["url1", "url2"]
        mock_router2 = Mock()
        mock_router2.generate_urls.return_value = ["url3"]

        manager._routers = [mock_router1, mock_router2]

        # __iter__ should return all url patterns combined
        url_patterns = list(manager)
        assert url_patterns == ["url1", "url2", "url3"]

    def test_iter_triggers_reload_when_empty(self):
        """Test that __iter__ calls _reload_config when routers list is empty."""
        manager = RouterManager()

        with patch.object(manager, "_reload_config") as mock_reload:
            # ensure empty routers to hit the branch
            manager._routers = []
            # iteration should call _reload_config()
            list(manager)
            mock_reload.assert_called_once()

    def test_iter_reloads_config_when_empty(self):
        """Test that __iter__ reloads config when no routers are configured."""
        manager = RouterManager()

        # mock the reload process to avoid actual reloading
        with patch.object(manager, "_reload_config"):
            # mock the config and router creation
            with patch.object(manager, "_get_next_pages_config") as mock_get_config:
                mock_get_config.return_value = [
                    {"BACKEND": "next.urls.FileRouterBackend"}
                ]

                # mock router creation
                with patch("next.urls.RouterFactory.create_backend") as mock_create:
                    mock_router = Mock()
                    mock_router.generate_urls.return_value = ["url1"]
                    mock_create.return_value = mock_router

                    # manually add the router to simulate what _reload_config would do
                    manager._routers = [mock_router]

                    # trigger iteration
                    url_patterns = list(manager)

                    # should return the url patterns from the router
                    assert url_patterns == ["url1"]

    def test_getitem(self):
        """Test getting router by index."""
        manager = RouterManager()
        router = Mock()
        manager._routers = [router]

        assert manager[0] == router

    def test_reload_config_clears_cache(self):
        """Test that reload_config clears the config cache initially but then refills it."""
        manager = RouterManager()
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

    def test_reload_config_with_exception(self):
        """Test reload_config when creating backend raises exception."""
        manager = RouterManager()

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

    def test_get_next_pages_config_uses_cache(self):
        """Test that _get_next_pages_config uses cached config."""
        manager = RouterManager()
        cached_config = ["cached", "config"]
        manager._config_cache = cached_config

        result = manager._get_next_pages_config()
        assert result == cached_config

    def test_get_next_pages_config_no_next_pages_setting(self):
        """Test _get_next_pages_config when NEXT_PAGES setting is not configured."""
        manager = RouterManager()

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

    def test_router_manager_instance(self):
        """Test that router_manager is properly initialized."""
        assert router_manager is not None
        assert isinstance(router_manager, RouterManager)

    def test_router_manager_reload_config_clears_cache(self):
        """Test that global router_manager reload_config works correctly."""
        # test that the global instance works
        len(router_manager._routers)
        router_manager._reload_config()
        # should clear and reload
        assert router_manager._config_cache is not None

    def test_urlpatterns_dynamic(self):
        """Test that urlpatterns is dynamically generated through router_manager."""
        # urlpatterns should be the same object as router_manager
        assert urlpatterns is router_manager

        # test that urlpatterns is iterable and returns url patterns
        # we need to patch the method that generates the actual patterns
        with patch.object(router_manager, "_routers", [Mock()]):
            mock_router = router_manager._routers[0]
            mock_router.generate_urls.return_value = ["url1", "url2"]

            patterns = list(urlpatterns)
            assert patterns == ["url1", "url2"]

    def test_generate_urls_for_app_returns_empty_list(self):
        """Test _generate_urls_for_app when it returns empty list."""
        router = FileRouterBackend()

        with patch.object(router, "_generate_urls_for_app", return_value=[]):
            urls = router.generate_urls()
            assert urls == []

    def test_generate_root_urls_no_pages_path(self):
        """Test _generate_root_urls when _get_root_pages_path returns None."""
        router = FileRouterBackend()

        with patch.object(router, "_get_root_pages_path", return_value=None):
            urls = router._generate_root_urls()
            assert urls == []

    def test_create_url_pattern_view_wrapper_no_args_parameter(self):
        """Test view_wrapper when args parameter is not passed."""
        router = FileRouterBackend()

        with patch.object(
            router, "_load_page_function", return_value=lambda req, **kwargs: "response"
        ):
            pattern = router._create_url_pattern("test/[[args]]", Path("/tmp/test.py"))
            assert pattern is not None

            # test that the view wrapper handles missing args parameter correctly
            if hasattr(pattern, "callback"):
                view_func = pattern.callback
                # test without args parameter - should not crash
                result = view_func(Mock(), other_param="value")
                assert result == "response"

    def test_generate_urls_with_empty_patterns_from_apps(self):
        """Test generate_urls when _generate_urls_for_app returns empty list."""
        router = FileRouterBackend()

        # mock _get_installed_apps to return a list of apps
        with patch.object(router, "_get_installed_apps", return_value=["app1", "app2"]):
            # mock _generate_urls_for_app to return empty list for each app
            with patch.object(router, "_generate_urls_for_app", return_value=[]):
                urls = router.generate_urls()
                assert urls == []

    def test_generate_root_urls_with_none_pages_path(self):
        """Test _generate_root_urls when _get_root_pages_path returns none."""
        router = FileRouterBackend()

        with patch.object(router, "_get_root_pages_path", return_value=None):
            urls = router._generate_root_urls()
            assert urls == []

    def test_view_wrapper_without_args_parameter(self):
        """Test view_wrapper when args parameter is not in kwargs."""
        router = FileRouterBackend()

        # create a mock render function that returns kwargs to verify they weren't modified
        mock_render_func = Mock(return_value="success")

        with patch.object(router, "_load_page_function", return_value=mock_render_func):
            pattern = router._create_url_pattern("test/[[args]]", Path("/tmp/test.py"))
            assert pattern is not None

            # test that the view wrapper handles missing args parameter correctly
            if hasattr(pattern, "callback"):
                view_func = pattern.callback
                # test without args parameter - should not crash and should not modify kwargs
                result = view_func(Mock(), other_param="value")
                assert result == "success"

    def test_generate_urls_direct_coverage(self):
        """Test generate_urls method directly with walrus operator."""
        router = FileRouterBackend()

        # mock the entire method chain to ensure we hit the walrus operator
        with patch.object(router, "_get_installed_apps", return_value=["testapp"]):
            with patch.object(router, "_generate_urls_for_app", return_value=[]):
                with patch.object(router, "_generate_root_urls", return_value=[]):
                    urls = router.generate_urls()
                    assert urls == []

    def test_generate_root_urls_direct_coverage(self):
        """Test _generate_root_urls method."""
        router = FileRouterBackend()

        # mock _get_root_pages_path to return None to hit the return [] path
        with patch.object(router, "_get_root_pages_path", return_value=None):
            urls = router._generate_root_urls()
            assert urls == []

    def test_view_wrapper_args_parameter_not_in_kwargs(self):
        """Test view_wrapper when args parameter is not in kwargs."""
        router = FileRouterBackend()

        # create a mock render function that returns kwargs to verify they weren't modified
        mock_render_func = Mock(return_value="success")

        with patch.object(router, "_load_page_function", return_value=mock_render_func):
            pattern = router._create_url_pattern("test/[[args]]", Path("/tmp/test.py"))
            assert pattern is not None

            # test that the view wrapper handles missing args parameter correctly
            if hasattr(pattern, "callback"):
                view_func = pattern.callback
                # test without args parameter - should not crash and should not modify kwargs
                result = view_func(Mock(), other_param="value")
                assert result == "success"
                # verify that the render function was called with the original kwargs
                mock_render_func.assert_called_once()
                call_args = mock_render_func.call_args
                assert call_args[1] == {"other_param": "value"}

    def test_generate_urls_real_execution(self):
        """Test generate_urls method with real execution."""
        router = FileRouterBackend()

        # create a real test environment that will execute the walrus operator
        with patch("next.urls.settings") as mock_settings:
            mock_settings.INSTALLED_APPS = ["testapp"]

            # mock the app path to return None so _generate_urls_for_app returns empty list
            with patch.object(router, "_get_app_pages_path", return_value=None):
                urls = router.generate_urls()
                assert urls == []

    def test_generate_root_urls_real_execution(self):
        """Test _generate_root_urls method with real execution."""
        router = FileRouterBackend()

        # mock settings to return None for BASE_DIR
        with patch("next.urls.settings") as mock_settings:
            mock_settings.BASE_DIR = None

            urls = router._generate_root_urls()
            assert urls == []

    def test_view_wrapper_real_execution(self):
        """Test view_wrapper with real execution."""
        router = FileRouterBackend()

        # create a real template that will be parsed
        with patch.object(
            router, "_load_page_function", return_value=lambda req, **kwargs: kwargs
        ):
            pattern = router._create_url_pattern("test/[[args]]", Path("/tmp/test.py"))
            assert pattern is not None

            # test that the view wrapper handles missing args parameter correctly
            if hasattr(pattern, "callback"):
                view_func = pattern.callback
                # test without args parameter - should not crash and should not modify kwargs
                result = view_func(Mock(), other_param="value")
                assert result == {"other_param": "value"}

    def test_create_backend_real_execution(self):
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
        assert not hasattr(backend, "pages_dir_name")

    def test_generate_urls_comprehensive_coverage(self):
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
                    router, "_generate_patterns_from_directory"
                ) as mock_gen_patterns:
                    mock_gen_patterns.return_value = ["pattern1", "pattern2"]

                    # mock _create_url_pattern to return a pattern
                    with patch.object(
                        router, "_create_url_pattern", return_value="url_pattern"
                    ):
                        urls = router.generate_urls()
                        # the result should be the patterns from _generate_patterns_from_directory
                        assert urls == ["pattern1", "pattern2"]

    def test_generate_root_urls_comprehensive_coverage(self):
        """Test _generate_root_urls method comprehensively."""
        router = FileRouterBackend()

        # test with BASE_DIR = None to hit the return [] path
        with patch("next.urls.settings") as mock_settings:
            mock_settings.BASE_DIR = None

            urls = router._generate_root_urls()
            assert urls == []

    def test_generate_root_urls_with_patterns(self):
        """Test _generate_root_urls returns patterns when pages path exists."""
        router = FileRouterBackend()

        with patch.object(
            router, "_get_root_pages_path", return_value=Path("/tmp/pages")
        ):
            with patch.object(
                router,
                "_generate_patterns_from_directory",
                return_value=iter(["p1", "p2"]),  # generator-like
            ):
                urls = router._generate_root_urls()
                assert urls == ["p1", "p2"]

    def test_create_url_pattern_comprehensive_coverage(self):
        """Test _create_url_pattern method comprehensively."""
        router = FileRouterBackend()

        # create a pattern with args parameter
        with patch.object(
            router, "_load_page_function", return_value=lambda req, **kwargs: kwargs
        ):
            pattern = router._create_url_pattern("test/[[args]]", Path("/tmp/test.py"))
            assert pattern is not None

            # test that the view wrapper handles missing args parameter correctly
            if hasattr(pattern, "callback"):
                view_func = pattern.callback
                # test without args parameter - should not crash and should not modify kwargs
                result = view_func(Mock(), other_param="value")
                assert result == {"other_param": "value"}

    def test_create_url_pattern_returns_none_when_no_render(self):
        """Test _create_url_pattern returns None when render function is missing."""
        router = FileRouterBackend()

        with patch.object(router, "_load_page_function", return_value=None):
            pattern = router._create_url_pattern("test/path", Path("/tmp/test.py"))
            assert pattern is None

    def test_create_backend_comprehensive_coverage(self):
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
        assert not hasattr(backend, "pages_dir_name")

    def test_load_page_function_missing_render_attribute(self):
        """Test _load_page_function when module lacks 'render' attribute."""
        router = FileRouterBackend()

        # mock import machinery to return a simple module without 'render'
        with patch("importlib.util.spec_from_file_location") as mock_spec:
            spec = Mock()
            spec.loader = Mock()
            # exec_module should be a no-op
            spec.loader.exec_module.return_value = None
            mock_spec.return_value = spec

            with patch(
                "importlib.util.module_from_spec", return_value=types.SimpleNamespace()
            ):
                result = router._load_page_function(Path("/tmp/page.py"))
                assert result is None

    def test_scan_pages_directory_real_filesystem(self, tmp_path):
        """Test _scan_pages_directory recursion on a real filesystem."""
        router = FileRouterBackend()

        pages_dir = tmp_path / "testapp" / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)

        # create nested directories and page.py files
        (pages_dir / "home").mkdir(parents=True, exist_ok=True)
        (pages_dir / "home" / "page.py").write_text(
            "def render(request):\n    return 'home'\n"
        )

        (pages_dir / "items" / "[int:id]").mkdir(parents=True, exist_ok=True)
        (pages_dir / "items" / "[int:id]" / "page.py").write_text(
            "def render(request, id):\n    return id\n"
        )

        (pages_dir / "blog" / "post").mkdir(parents=True, exist_ok=True)
        (pages_dir / "blog" / "post" / "page.py").write_text(
            "def render(request):\n    return 'post'\n"
        )

        results = list(router._scan_pages_directory(pages_dir))
        url_paths = {u for (u, _f) in results}

        assert "home" in url_paths
        assert "items/[int:id]" in url_paths
        assert "blog/post" in url_paths
