from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.test import override_settings

from next.conf import next_framework_settings
from next.urls import (
    FileRouterBackend,
    RouterFactory,
    RouterManager,
    router_manager,
    urlpatterns,
)
from tests.support import named_temp_py


class TestRouterManager:
    """RouterManager iteration, reload, and config access."""

    def test_init(self, manager) -> None:
        """Starts with empty routers and no config cache."""
        assert manager._backends == []
        assert manager._config_cache is None

    def test_repr(self, manager) -> None:
        """``repr`` shows router count."""
        assert repr(manager) == "<RouterManager backends=0>"

    @pytest.mark.parametrize(
        ("router_count", "expected_len"),
        [
            (0, 0),
            (1, 1),
        ],
        ids=["empty", "one_router"],
    )
    def test_len_variations(self, manager, router_count, expected_len) -> None:
        """``len`` matches number of registered routers."""
        for _ in range(router_count):
            manager._backends.append(Mock())
        assert len(manager) == expected_len

    def test_iter_returns_url_patterns(self, manager) -> None:
        """Iteration concatenates generate_urls from each router."""
        mock_router1 = Mock()
        mock_router1.generate_urls.return_value = ["url1", "url2"]
        mock_router2 = Mock()
        mock_router2.generate_urls.return_value = ["url3"]

        manager._backends = [mock_router1, mock_router2]

        # __iter__ should return all url patterns combined
        url_patterns = list(manager)
        assert url_patterns == ["url1", "url2", "url3"]

    def test_iter_triggers_reload_when_empty(self, manager) -> None:
        """Empty routers triggers _reload_config on iteration."""
        with patch.object(manager, "_reload_config") as mock_reload:
            manager._backends = []
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

            manager._backends = [mock_router]

            url_patterns = list(manager)

            assert url_patterns == ["url1"]

    def test_getitem(self, manager) -> None:
        """Index access returns the router at that position."""
        router = Mock()
        manager._backends = [router]

        assert manager[0] == router

    def test_reload_config_clears_cache(self, manager) -> None:
        """Reload replaces cache and builds routers from default framework config."""
        manager._config_cache = ["some", "cached", "config"]

        manager._reload_config()
        assert manager._config_cache is not None
        assert len(manager._config_cache) == 1
        assert manager._config_cache[0]["BACKEND"] == "next.urls.FileRouterBackend"
        assert len(manager._backends) == 1
        assert isinstance(manager._backends[0], FileRouterBackend)

    def test_reload_config_with_exception(self, manager) -> None:
        """Backend creation failure leaves routers empty but cache is still set."""
        with patch(
            "next.urls.RouterFactory.create_backend",
            side_effect=Exception("Test error"),
        ):
            manager._reload_config()
            assert len(manager._backends) == 0
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
        len(router_manager._backends)
        router_manager._reload_config()
        assert router_manager._config_cache is not None

    def test_urlpatterns_dynamic(self) -> None:
        """``urlpatterns`` is a list. Iteration delegates to ``router_manager``."""
        assert isinstance(urlpatterns, list)
        assert len(urlpatterns) >= 1
        assert urlpatterns[0] is not None

        with patch.object(router_manager, "_backends", [Mock()]):
            mock_router = router_manager._backends[0]
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
        ("test_case", "file_content"),
        [
            (
                "without_args_parameter",
                "def render(request, **kwargs):\n    return 'success'",
            ),
            (
                "args_parameter_not_in_kwargs",
                "def render(request, **kwargs):\n    return 'success'",
            ),
        ],
        ids=["without_args_parameter", "args_not_in_kwargs"],
    )
    def test_view_wrapper_scenarios(self, tmp_path, test_case, file_content) -> None:
        """View callback behavior when `render()` returns a string body."""
        from next.pages import page

        router = FileRouterBackend()
        render_module_path = tmp_path / "page.py"
        render_module_path.write_text(file_content)

        pattern = page.create_url_pattern(
            "test/[[args]]",
            render_module_path,
            router._url_parser,
        )
        assert pattern is not None

        view_func = pattern.callback
        response = view_func(Mock(), other_param="value")
        assert response.status_code == 200
        assert response.content == b"success"

    def test_view_wrapper_render_returning_non_str_raises(self, tmp_path) -> None:
        """`render()` returning a dict (or any non-str non-HttpResponse) raises TypeError."""
        from next.pages import page

        router = FileRouterBackend()
        render_module_path = tmp_path / "page.py"
        render_module_path.write_text(
            "def render(request, **kwargs):\n    return kwargs"
        )

        pattern = page.create_url_pattern(
            "test/[[args]]",
            render_module_path,
            router._url_parser,
        )
        assert pattern is not None

        view_func = pattern.callback
        with pytest.raises(TypeError, match="must return str or HttpResponse"):
            view_func(Mock(), other_param="value")

    def test_generate_root_urls_returns_empty_when_base_dir_none(self) -> None:
        """BASE_DIR None yields no root URLs."""
        router = FileRouterBackend()
        mock_s = Mock()
        mock_s.BASE_DIR = None
        with (
            patch("next.urls.backends.settings", mock_s),
            patch(
                "next.utils.settings",
                mock_s,
            ),
        ):
            urls = router._generate_root_urls()
            assert urls == []

    def test_create_backend_real_execution(self) -> None:
        """Registered custom backend instantiates without pages_dir."""
        from next.urls import RouterBackend

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

        mock_s = Mock()
        mock_s.INSTALLED_APPS = ["testapp1", "testapp2"]
        with (
            patch("next.urls.backends.settings", mock_s),
            patch(
                "next.utils.settings",
                mock_s,
            ),
            patch.object(router, "_get_app_pages_path") as mock_get_path,
        ):
            mock_get_path.side_effect = [None, Path("/tmp/pages")]

            with patch.object(
                router,
                "_generate_patterns_from_directory",
            ) as mock_gen_patterns:
                mock_gen_patterns.return_value = ["pattern1", "pattern2"]

                with patch(
                    "next.urls.backends.page.create_url_pattern",
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
        from next.pages import page

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
        """Template view renders the module's `template` attribute with kwargs."""
        from next.pages import page

        router = FileRouterBackend()

        with named_temp_py('template = "Hello {{ name }}!"') as temp_file:
            pattern = page.create_url_pattern(
                "test",
                temp_file,
                router._url_parser,
            )

            view_func = pattern.callback
            response = view_func(Mock(), name="John")

            assert response.status_code == 200
            assert response.content == b"Hello John!"

    def test_create_url_pattern_template_view_function_args_not_in_parameters(
        self,
    ) -> None:
        """Args passed as keyword flow through to the rendered template."""
        from next.pages import page

        router = FileRouterBackend()

        with named_temp_py('template = "Hello {{ name }}!"') as temp_file:
            pattern = page.create_url_pattern(
                "test",
                temp_file,
                router._url_parser,
            )

            view_func = pattern.callback
            response = view_func(Mock(), args="arg1/arg2/arg3", name="Mia")

            assert response.status_code == 200
            assert response.content == b"Hello Mia!"

    def test_create_url_pattern_template_view_function_args_not_in_kwargs(self) -> None:
        """[[args]] in path without an `args` call-kwarg still renders the template."""
        from next.pages import page

        router = FileRouterBackend()

        with named_temp_py('template = "Hello {{ name }}!"') as temp_file:
            pattern = page.create_url_pattern(
                "test/[[args]]",
                temp_file,
                router._url_parser,
            )

            view_func = pattern.callback
            response = view_func(Mock(), name="John")

            assert response.status_code == 200
            assert response.content == b"Hello John!"

    def test_create_url_pattern_no_template_no_render(self) -> None:
        """Neither template nor render returns no pattern."""
        from next.pages import page

        router = FileRouterBackend()

        with named_temp_py('some_variable = "test"') as temp_file:
            pattern = page.create_url_pattern("test", temp_file, router._url_parser)
            assert pattern is None

    def test_create_url_pattern_spec_from_file_location_returns_none(self) -> None:
        """Missing import spec yields no pattern."""
        from next.pages import page

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
        from next.pages import page

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

    def test_non_list_default_page_backends_returns_empty_cached(self) -> None:
        """When ``DEFAULT_PAGE_BACKENDS`` is not a list, config is empty and cached."""
        mock_nf = SimpleNamespace(DEFAULT_PAGE_BACKENDS="not-a-list")
        with patch("next.urls.manager.next_framework_settings", mock_nf):
            mgr = RouterManager()
            assert mgr._get_next_pages_config() == []
            assert mgr._get_next_pages_config() == []
