import logging
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import RequestFactory, override_settings
from django.urls import Resolver404, URLResolver, include, path

from next.conf import next_framework_settings
from next.forms import ActionRegistration, RegistryFormActionBackend
from next.forms.manager import FormActionManager
from next.pages import page
from next.testing import override_next_settings
from next.urls import (
    FileRouterBackend,
    RouterBackend,
    RouterFactory,
    RouterManager,
    TrieURLResolver,
    router_manager,
    urlpatterns,
)
from next.urls.manager import _build_url_resolver, _LazyUrlPatterns
from tests.support import named_temp_py


lazy_urlpatterns = urlpatterns[0].urlconf_name


class _StubManager:
    """Iterable manager stub carrying the `version` cache token."""

    def __init__(self, items, version=0, on_iter=None) -> None:
        self.items = list(items)
        self.version = version
        self.builds = 0
        self._on_iter = on_iter

    def __iter__(self) -> Iterator[str]:
        self.builds += 1
        if self._on_iter is not None:
            self._on_iter()
        return iter(self.items)


class TestRouterManager:
    """RouterManager iteration, reload, and config access."""

    def test_init(self, manager) -> None:
        """Starts with empty routers, no config cache, and nothing loaded."""
        assert manager._backends == []
        assert manager._config_cache is None
        assert manager._loaded is False

    def test_repr(self, manager) -> None:
        """``repr`` shows router count and load state."""
        manager.reload()
        assert repr(manager) == "<RouterManager backends=1 loaded=True>"

    def test_repr_on_a_fresh_manager_does_not_load(self, manager) -> None:
        """Looking at a manager in a debugger never rebuilds the routes."""
        with patch.object(manager, "reload") as mock_reload:
            rendered = repr(manager)
        mock_reload.assert_not_called()
        assert rendered == "<RouterManager backends=0 loaded=False>"

    def test_backends_reports_the_loaded_list_in_order(self, manager) -> None:
        """``backends`` is the public read the other areas key their walks on."""
        first, second = Mock(), Mock()
        manager._backends = [first, second]
        manager._loaded = True
        assert manager.backends == (first, second)

    def test_backends_loads_the_configured_set_on_first_read(self, manager) -> None:
        """A read before the first resolve loads, instead of reporting nothing."""
        with patch.object(manager, "reload", wraps=manager.reload) as mock_reload:
            loaded = manager.backends
        mock_reload.assert_called_once()
        assert manager._loaded is True
        assert [type(backend) for backend in loaded] == [FileRouterBackend]

    def test_backends_reads_after_the_load_do_not_reload(self, manager) -> None:
        """The load happens once, later reads hand back the loaded list."""
        manager.reload()
        with patch.object(manager, "reload") as mock_reload:
            assert manager.backends == tuple(manager._backends)
            assert manager.backends == tuple(manager._backends)
            mock_reload.assert_not_called()

    def test_backends_snapshot_detaches_from_the_live_list(self, manager) -> None:
        """The returned tuple survives a later mutation of the live list."""
        snapshot = manager.backends
        manager._backends.append(Mock())
        assert len(snapshot) == len(manager._backends) - 1

    def test_empty_page_backends_loads_once_across_iterations(self, manager) -> None:
        """An empty ``PAGE_BACKENDS`` is a loaded state, not a reason to reload."""
        with (
            override_next_settings(PAGE_BACKENDS=[]),
            patch.object(manager, "reload", wraps=manager.reload) as mock_reload,
        ):
            assert list(manager) == []
            assert list(manager) == []
            assert mock_reload.call_count == 1

    @pytest.mark.parametrize(
        ("router_count", "expected_len"), [(0, 0), (1, 1)], ids=["empty", "one_router"]
    )
    def test_len_variations(self, manager, router_count, expected_len) -> None:
        """``len`` matches number of registered routers."""
        for _ in range(router_count):
            manager._backends.append(Mock())
        manager._loaded = True
        assert len(manager) == expected_len

    def test_len_loads_the_configured_set_on_first_read(self, manager) -> None:
        """``len`` before the first resolve agrees with ``backends``."""
        with patch.object(manager, "reload", wraps=manager.reload) as mock_reload:
            counted = len(manager)
        mock_reload.assert_called_once()
        assert counted == len(manager.backends)
        assert counted > 0

    def test_getitem_loads_the_configured_set_on_first_read(self, manager) -> None:
        """Indexing before the first resolve returns a backend, not ``IndexError``."""
        with patch.object(manager, "reload", wraps=manager.reload) as mock_reload:
            first = manager[0]
        mock_reload.assert_called_once()
        assert first is manager.backends[0]

    def test_iter_returns_url_patterns(self, manager) -> None:
        """Iteration concatenates generate_urls from each router."""
        mock_router1 = Mock()
        mock_router1.generate_urls.return_value = ["url1", "url2"]
        mock_router2 = Mock()
        mock_router2.generate_urls.return_value = ["url3"]

        manager._backends = [mock_router1, mock_router2]
        manager._loaded = True

        url_patterns = list(manager)
        assert url_patterns == ["url1", "url2", "url3"]

    def test_iter_triggers_reload_before_the_first_load(self, manager) -> None:
        """Iteration on a manager that never loaded triggers the load."""
        with patch.object(manager, "reload") as mock_reload:
            # Iterating through ``iter`` keeps ``list`` from reading ``__len__``,
            # which loads on its own.
            list(iter(manager))
            mock_reload.assert_called_once()

    def test_iter_returns_patterns_from_routers_created_by_reload(
        self, manager
    ) -> None:
        """After reload, iteration returns patterns from created routers."""
        with (
            patch.object(manager, "reload"),
            patch.object(manager, "_get_next_pages_config") as mock_get_config,
            patch("next.urls.RouterFactory.create_backend") as mock_create,
        ):
            mock_get_config.return_value = [
                {
                    "BACKEND": "next.urls.FileRouterBackend",
                    "PAGES_DIR": "pages",
                    "APP_DIRS": True,
                    "OPTIONS": {},
                }
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
        manager._loaded = True

        assert manager[0] == router

    def test_reload_clears_cache(self, manager) -> None:
        """Reload replaces cache and builds routers from default framework config."""
        manager._config_cache = ["some", "cached", "config"]

        manager.reload()
        assert manager._config_cache is not None
        assert len(manager._config_cache) == 1
        assert manager._config_cache[0]["BACKEND"] == "next.urls.FileRouterBackend"
        assert len(manager._backends) == 1
        assert isinstance(manager._backends[0], FileRouterBackend)

    def test_reload_with_exception(self, manager) -> None:
        """Backend creation failure leaves routers empty but cache is still set."""
        with patch(
            "next.urls.RouterFactory.create_backend",
            side_effect=ValueError("Test error"),
        ):
            manager.reload()
            assert len(manager._backends) == 0
            assert manager._config_cache is not None
            assert len(manager._config_cache) == 1
            assert manager._config_cache[0]["BACKEND"] == "next.urls.FileRouterBackend"

    @pytest.mark.parametrize(
        "exc_type",
        [ValueError, TypeError, KeyError, ImportError],
        ids=["value_error", "type_error", "key_error", "import_error"],
    )
    def test_reload_swallows_expected_config_errors(
        self, manager, caplog, exc_type
    ) -> None:
        """Each config-error type from backend creation is logged and swallowed."""
        with (
            patch(
                "next.urls.RouterFactory.create_backend", side_effect=exc_type("boom")
            ),
            caplog.at_level(logging.ERROR, logger="next.urls.manager"),
        ):
            manager.reload()
        assert manager._backends == []
        assert "error creating router from config" in caplog.text

    def test_reload_propagates_unexpected_errors(self, manager) -> None:
        """Exceptions outside the config-error set escape reload."""
        with (
            patch(
                "next.urls.RouterFactory.create_backend",
                side_effect=RuntimeError("boom"),
            ),
            pytest.raises(RuntimeError, match="boom"),
        ):
            manager.reload()

    def test_reload_bumps_version(self, manager) -> None:
        """Every reload increments the urlpatterns cache token."""
        before = manager.version
        manager.reload()
        manager.reload()
        assert manager.version == before + 2

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

    def test_router_manager_reload_clears_cache(self) -> None:
        """Global manager reload refreshes config cache."""
        len(router_manager._backends)
        router_manager.reload()
        assert router_manager._config_cache is not None

    def test_urlpatterns_dynamic(self) -> None:
        """``urlpatterns`` is one TrieURLResolver over the lazy pattern sequence."""
        assert isinstance(urlpatterns, list)
        assert len(urlpatterns) == 1
        assert isinstance(urlpatterns[0], TrieURLResolver)
        assert isinstance(urlpatterns[0].urlconf_name, _LazyUrlPatterns)

        with (
            patch.object(router_manager, "_backends", [Mock()]),
            patch.object(router_manager, "_loaded", True),
        ):
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
        router = FileRouterBackend()
        render_module_path = tmp_path / "page.py"
        render_module_path.write_text(file_content)

        pattern = page.create_url_pattern(
            "test/[[args]]", render_module_path, router._url_parser
        )
        assert pattern is not None

        view_func = pattern.callback
        response = view_func(RequestFactory().get("/"), other_param="value")
        assert response.status_code == 200
        assert response.content == b"success"

    def test_view_wrapper_render_returning_non_str_raises(self, tmp_path) -> None:
        """`render()` returning a dict (or any non-str non-HttpResponse) raises TypeError."""
        router = FileRouterBackend()
        render_module_path = tmp_path / "page.py"
        render_module_path.write_text(
            "def render(request, **kwargs):\n    return kwargs"
        )

        pattern = page.create_url_pattern(
            "test/[[args]]", render_module_path, router._url_parser
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
        with patch("next.utils.settings", mock_s):
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

        with (
            patch.object(
                router, "_get_installed_apps", return_value=["testapp1", "testapp2"]
            ),
            patch.object(router, "_get_app_pages_path") as mock_get_path,
        ):
            mock_get_path.side_effect = [None, Path("/tmp/pages")]

            with patch.object(
                router, "_generate_patterns_from_directory"
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
                router, "_get_root_pages_paths", return_value=[Path("/tmp/pages")]
            ),
            patch.object(
                router,
                "_generate_patterns_from_directory",
                return_value=iter(["p1", "p2"]),
            ),
        ):
            urls = router._generate_root_urls()
            assert urls == ["p1", "p2"]

    def test_scan_pages_directory_real_filesystem(self, tmp_path) -> None:
        """Nested page.py files produce URL path segments on disk."""
        router = FileRouterBackend()

        pages_dir = tmp_path / "testapp" / "pages"
        pages_dir.mkdir(parents=True, exist_ok=True)

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

    def test_create_url_pattern_with_template_attribute(self) -> None:
        """Template only module gets a named pattern and callback."""
        router = FileRouterBackend()

        with named_temp_py('template = "Hello {{ name }}!"') as temp_file:
            pattern = page.create_url_pattern("test", temp_file, router._url_parser)
            assert pattern is not None
            assert hasattr(pattern, "callback")
            assert hasattr(pattern, "name")
            assert pattern.name == "page_test"

    def test_create_url_pattern_template_view_function_without_args(self) -> None:
        """Template view renders the module's `template` attribute with kwargs."""
        router = FileRouterBackend()

        with named_temp_py('template = "Hello {{ name }}!"') as temp_file:
            pattern = page.create_url_pattern("test", temp_file, router._url_parser)

            view_func = pattern.callback
            response = view_func(RequestFactory().get("/"), name="John")

            assert response.status_code == 200
            assert response.content == b"Hello John!"

    def test_create_url_pattern_template_view_function_args_not_in_parameters(
        self,
    ) -> None:
        """Args passed as keyword flow through to the rendered template."""
        router = FileRouterBackend()

        with named_temp_py('template = "Hello {{ name }}!"') as temp_file:
            pattern = page.create_url_pattern("test", temp_file, router._url_parser)

            view_func = pattern.callback
            response = view_func(
                RequestFactory().get("/"), args="arg1/arg2/arg3", name="Mia"
            )

            assert response.status_code == 200
            assert response.content == b"Hello Mia!"

    def test_create_url_pattern_template_view_function_args_not_in_kwargs(self) -> None:
        """[[args]] in path without an `args` call-kwarg still renders the template."""
        router = FileRouterBackend()

        with named_temp_py('template = "Hello {{ name }}!"') as temp_file:
            pattern = page.create_url_pattern(
                "test/[[args]]", temp_file, router._url_parser
            )

            view_func = pattern.callback
            response = view_func(RequestFactory().get("/"), name="John")

            assert response.status_code == 200
            assert response.content == b"Hello John!"

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
                "test", Path("/nonexistent/file.py"), router._url_parser
            )
            assert pattern is None

    def test_create_url_pattern_spec_loader_is_none(self) -> None:
        """Spec with no loader returns no pattern."""
        router = FileRouterBackend()

        mock_spec = Mock()
        mock_spec.loader = None

        with patch("importlib.util.spec_from_file_location", return_value=mock_spec):
            pattern = page.create_url_pattern(
                "test", Path("/some/file.py"), router._url_parser
            )
            assert pattern is None


class TestLazyUrlPatterns:
    """Sequence protocol, laziness, and the versioned concat cache."""

    @pytest.fixture(autouse=True)
    def _reset_urlpatterns_cache(self):
        """Keep the module-level concat and trie index caches empty around each test."""
        lazy_urlpatterns._cache = None
        urlpatterns[0]._index_cache = None
        yield
        lazy_urlpatterns._cache = None
        urlpatterns[0]._index_cache = None

    def test_sequence_protocol_without_list_inheritance(self) -> None:
        """Iteration, len, indexing, slicing, and reversed work without list."""
        with (
            patch("next.urls.manager.router_manager", _StubManager(["r1", "r2"])),
            patch("next.urls.manager.form_action_manager", _StubManager(["f1"])),
        ):
            lazy = _LazyUrlPatterns()
            assert not isinstance(lazy, list)
            assert list(lazy) == ["r1", "r2", "f1"]
            assert len(lazy) == 3
            assert lazy[0] == "r1"
            assert lazy[-1] == "f1"
            assert lazy[1:] == ["r2", "f1"]
            assert list(reversed(lazy)) == ["f1", "r2", "r1"]

    def test_reversed_override_builds_patterns_once(self) -> None:
        """Explicit ``__reversed__`` walks one ``_patterns()`` build, not one per index."""
        assert "__reversed__" in type(lazy_urlpatterns).__dict__
        with patch.object(
            type(lazy_urlpatterns), "_patterns", return_value=["r1", "r2", "f1"]
        ) as mock_patterns:
            assert list(reversed(lazy_urlpatterns)) == ["f1", "r2", "r1"]
        assert mock_patterns.call_count == 1

    def test_cache_hit_builds_once(self) -> None:
        """Two accesses with stable versions expand the routers once."""
        with patch.object(
            RouterManager, "__iter__", side_effect=lambda *args: iter([])
        ) as mock_iter:
            first = list(lazy_urlpatterns)
            second = list(lazy_urlpatterns)
        assert mock_iter.call_count == 1
        assert second == first

    def test_invalidated_by_router_reload(self) -> None:
        """`router_manager.reload()` bumps the version and forces a rebuild."""
        with patch.object(
            RouterManager, "__iter__", side_effect=lambda *args: iter([])
        ) as mock_iter:
            list(lazy_urlpatterns)
            router_manager.reload()
            list(lazy_urlpatterns)
        assert mock_iter.call_count == 2

    def test_late_action_appears_after_forms_version_bump(self) -> None:
        """A bumped forms version rebuilds the concat, so late actions appear."""
        router = _StubManager([])
        forms = _StubManager(["f1"])
        with (
            patch("next.urls.manager.router_manager", router),
            patch("next.urls.manager.form_action_manager", forms),
        ):
            lazy = _LazyUrlPatterns()
            assert list(lazy) == ["f1"]
            assert list(lazy) == ["f1"]
            assert forms.builds == 1
            forms.items.append("f2")
            forms.version += 1
            assert list(lazy) == ["f1", "f2"]
            assert forms.builds == 2

    def test_invalidated_by_register_action(self) -> None:
        """`FormActionManager.register_action` invalidates the cached concat."""
        router = _StubManager(["r1"])
        forms = FormActionManager(backends=[RegistryFormActionBackend()])
        with (
            patch("next.urls.manager.router_manager", router),
            patch("next.urls.manager.form_action_manager", forms),
        ):
            lazy = _LazyUrlPatterns()
            list(lazy)
            list(lazy)
            assert router.builds == 1
            forms.register_action(
                ActionRegistration(
                    name="late_lazy_action",
                    file_path="/fake/app/forms.py",
                    scope="shared",
                    handler=lambda: None,
                )
            )
            list(lazy)
            assert router.builds == 2

    def test_invalidated_by_clear_registries(self) -> None:
        """`FormActionManager.clear_registries` invalidates the cached concat."""
        router = _StubManager(["r1"])
        forms = FormActionManager(backends=[RegistryFormActionBackend()])
        with (
            patch("next.urls.manager.router_manager", router),
            patch("next.urls.manager.form_action_manager", forms),
        ):
            lazy = _LazyUrlPatterns()
            list(lazy)
            assert router.builds == 1
            forms.clear_registries()
            list(lazy)
            assert router.builds == 2

    def test_registration_during_build_keeps_cache_valid(self) -> None:
        """Actions registered while pages expand do not stale the cache."""
        forms = _StubManager(["f1"])

        def register_during_expand() -> None:
            forms.items = ["f1", "f2"]
            forms.version += 1

        router = _StubManager(["r1"], on_iter=register_during_expand)
        with (
            patch("next.urls.manager.router_manager", router),
            patch("next.urls.manager.form_action_manager", forms),
        ):
            lazy = _LazyUrlPatterns()
            assert list(lazy) == ["r1", "f1", "f2"]
            assert list(lazy) == ["r1", "f1", "f2"]
            assert router.builds == 1
            assert forms.builds == 1

    def test_sequence_reads_share_one_cached_build(self) -> None:
        """reversed, len, indexing, and slicing all read the cached concat."""
        router = _StubManager(["r1", "r2"])
        forms = _StubManager(["f1"])
        with (
            patch("next.urls.manager.router_manager", router),
            patch("next.urls.manager.form_action_manager", forms),
        ):
            lazy = _LazyUrlPatterns()
            assert list(reversed(lazy)) == ["f1", "r2", "r1"]
            assert len(lazy) == 3
            assert lazy[0] == "r1"
            assert lazy[1:] == ["r2", "f1"]
            assert list(lazy) == ["r1", "r2", "f1"]
            assert router.builds == 1
            assert forms.builds == 1

    def test_include_defers_materialisation_until_first_resolve(self) -> None:
        """``include()`` does not iterate patterns, the first resolve does."""
        with patch.object(
            RouterManager, "__iter__", side_effect=lambda *args: iter([])
        ) as mock_iter:
            included = include("next.urls")
            mock_iter.assert_not_called()
            resolver = path("lazy/", included)
            mock_iter.assert_not_called()
            patterns = resolver.url_patterns
            mock_iter.assert_not_called()
            with pytest.raises(Resolver404):
                resolver.resolve("lazy/miss/")
            mock_iter.assert_called_once()
        assert patterns is urlpatterns


class TestBuildUrlResolver:
    """The URL_RESOLVER factory behind urlpatterns[0]."""

    def test_default_short_circuits_the_import_helper(self) -> None:
        """The default dotted path binds TrieURLResolver without importing."""
        with patch("next.urls.manager.import_class_cached") as import_helper:
            resolver = _build_url_resolver()
        import_helper.assert_not_called()
        assert type(resolver) is TrieURLResolver
        assert isinstance(resolver.urlconf_name, _LazyUrlPatterns)

    def test_explicit_default_string_override_builds_trie_resolver(self) -> None:
        """A user override naming the default class still yields the trie."""
        with override_next_settings(URL_RESOLVER="next.urls.TrieURLResolver"):
            assert type(urlpatterns[0]) is TrieURLResolver

    def test_invalid_dotted_path_raises_improperly_configured(self) -> None:
        """An unimportable dotted path fails loudly at build time."""
        mock_nf = SimpleNamespace(URL_RESOLVER="no_such_module_zzz.Resolver")
        with (
            patch("next.urls.manager.next_framework_settings", mock_nf),
            pytest.raises(ImproperlyConfigured, match="could not be imported"),
        ):
            _build_url_resolver()

    @pytest.mark.parametrize(
        "dotted",
        ["next.urls.RouterManager", "next.urls.manager.urlpatterns"],
        ids=["class_not_a_resolver", "not_a_class"],
    )
    def test_non_resolver_target_raises_improperly_configured(self, dotted) -> None:
        """Importable targets outside URLResolver subclasses are rejected."""
        mock_nf = SimpleNamespace(URL_RESOLVER=dotted)
        with (
            patch("next.urls.manager.next_framework_settings", mock_nf),
            pytest.raises(ImproperlyConfigured, match="URLResolver subclass"),
        ):
            _build_url_resolver()

    def test_settings_reload_swaps_urlpatterns_head_in_place(self) -> None:
        """A reload replaces urlpatterns[0] while the list keeps its identity."""
        head_before = urlpatterns[0]
        with override_next_settings(URL_RESOLVER="django.urls.resolvers.URLResolver"):
            head_during = urlpatterns[0]
            assert head_during is not head_before
            assert type(head_during) is URLResolver
            assert not isinstance(head_during, TrieURLResolver)
            assert isinstance(head_during.urlconf_name, _LazyUrlPatterns)
        assert type(urlpatterns[0]) is TrieURLResolver


class TestRouterManagerNextPagesConfig:
    """``RouterManager._get_next_pages_config`` defensive branches."""

    def test_non_list_default_page_backends_returns_empty_cached(self) -> None:
        """When ``PAGE_BACKENDS`` is not a list, config is empty and cached."""
        mock_nf = SimpleNamespace(PAGE_BACKENDS="not-a-list")
        with patch("next.urls.manager.next_framework_settings", mock_nf):
            mgr = RouterManager()
            assert mgr._get_next_pages_config() == []
            assert mgr._get_next_pages_config() == []
