from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from next.pages import page
from next.urls import (
    FileRouterBackend,
    RouterBackend,
    RouterFactory,
)
from tests.support import file_router_backend_from_params


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
            (
                "custom",
                "views",
                False,
                {"custom": "value"},
                "views",
                False,
                {},
            ),
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

    def test_get_installed_apps(self, router, mock_settings) -> None:
        """Yields only project apps, skipping Django contrib packages."""
        mock_settings.INSTALLED_APPS = [
            "django.contrib.admin",
            "myapp",
            "django.contrib.auth",
        ]
        apps = list(router._get_installed_apps())
        assert apps == ["myapp"]

    def test_get_app_pages_path_returns_cached_entry_without_reimporting(
        self, router
    ) -> None:
        """A second lookup returns the cached value without hitting `__import__`."""
        sentinel = Path("/sentinel/pages")
        router._app_pages_path_cache["cached_app"] = sentinel
        with patch("builtins.__import__") as mock_import:
            result = router._get_app_pages_path("cached_app")
        assert result is sentinel
        mock_import.assert_not_called()

    @pytest.mark.parametrize(
        ("test_case", "import_side_effect", "file_path", "expected_result"),
        [
            ("success", None, "/path/to/app/__init__.py", "mock_pages_path"),
            ("import_error", ImportError, None, None),
            ("no_file", None, None, None),
        ],
        ids=["success", "import_error", "no_file"],
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
                    with patch("next.urls.backends.Path") as mock_path_class:
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
        ids=["with_base_dir", "string_base_dir", "no_base_dir", "does_not_exist"],
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
            mock_pages_path = Mock()
            mock_pages_path.exists.return_value = exists
            mock_base = Mock()
            mock_base.__truediv__ = Mock(return_value=mock_pages_path)
            with patch(
                "next.urls.backends.resolve_base_dir",
                return_value=mock_base,
            ):
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
            extra_root_paths=[
                Path("/nonexistent/path"),
                Path("/also/nonexistent"),
            ],
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

    def test_generate_urls_includes_root_when_app_dirs_and_extra_roots(
        self, tmp_path
    ) -> None:
        """With app_dirs and extra root paths, root directory patterns are generated."""
        router = FileRouterBackend(app_dirs=True, extra_root_paths=[tmp_path])
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
            patch("next.urls.backends.page.create_url_pattern") as mock_create,
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

    def test_create_url_pattern_with_args_parameter(self, tmp_path) -> None:
        """View wrapper accepts args string when URL pattern includes [[args]]."""
        router = FileRouterBackend()

        page_py = tmp_path / "page.py"
        page_py.write_text(
            "def render(request, args):\n    return 'response-' + args\n"
        )

        pattern = page.create_url_pattern(
            "test/[[args]]",
            page_py,
            router._url_parser,
        )
        assert pattern is not None
        assert pattern.callback is not None
        response = pattern.callback(Mock(), args="arg1/arg2/arg3")
        assert response.content == b"response-arg1/arg2/arg3"


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
            ),
        ],
        ids=["success"],
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
        with (
            patch("next.urls.backends.settings", mock_s),
            patch(
                "next.utils.settings",
                mock_s,
            ),
        ):
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
        with (
            patch("next.urls.backends.settings", mock_s),
            patch(
                "next.utils.settings",
                mock_s,
            ),
        ):
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
        self,
        config,
        missing_key,
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
        self,
        custom_backend_class,
    ) -> None:
        """Minimal config dict hits the non FileRouterBackend branch."""
        RouterFactory.register_backend("custom", custom_backend_class)

        backend = RouterFactory.create_backend({"BACKEND": "custom"})
        assert isinstance(backend, custom_backend_class)
        assert not hasattr(backend, "pages_dir")

    def test_is_filesystem_discovery_router_accepts_none(self) -> None:
        """Duck-typing helper returns false for a null router reference."""
        assert RouterFactory.is_filesystem_discovery_router(None) is False

    def test_is_filesystem_discovery_router_class_rejects_non_type(self) -> None:
        """Only type objects are treated as router classes."""
        assert RouterFactory.is_filesystem_discovery_router_class(object()) is False

    def test_resolve_components_folder_name_from_first_component_backend(
        self,
    ) -> None:
        """Skip-folder name comes from the first ``DEFAULT_COMPONENT_BACKENDS`` entry."""
        with patch("next.urls.backends.next_framework_settings") as nfs:
            nfs.DEFAULT_COMPONENT_BACKENDS = [{"COMPONENTS_DIR": "custom_comp"}]
            assert FileRouterBackend._resolve_components_folder_name() == "custom_comp"

    def test_resolve_components_folder_name_raises_when_unavailable(self) -> None:
        """Missing COMPONENTS_DIR and no valid component backend entry raises KeyError."""
        with patch("next.urls.backends.next_framework_settings") as nfs:
            nfs.DEFAULT_COMPONENT_BACKENDS = []
            with pytest.raises(KeyError, match="COMPONENTS_DIR"):
                FileRouterBackend._resolve_components_folder_name()

    def test_resolve_components_folder_name_raises_when_first_entry_invalid(
        self,
    ) -> None:
        """First component backend dict must contain COMPONENTS_DIR."""
        with patch("next.urls.backends.next_framework_settings") as nfs:
            nfs.DEFAULT_COMPONENT_BACKENDS = [{}]
            with pytest.raises(KeyError, match="COMPONENTS_DIR"):
                FileRouterBackend._resolve_components_folder_name()

    def test_is_filesystem_discovery_router_class_requires_tree_api(self) -> None:
        """Router subclasses must expose the filesystem page-tree hooks on the class."""

        class LooseRouter(RouterBackend):
            def generate_urls(self):
                return []

        assert RouterFactory.is_filesystem_discovery_router_class(LooseRouter) is False

    def test_is_filesystem_discovery_router_class_accepts_file_router(self) -> None:
        """The built-in file router class is always treated as a filesystem router."""
        assert (
            RouterFactory.is_filesystem_discovery_router_class(FileRouterBackend)
            is True
        )

    def test_is_filesystem_discovery_router_class_rejects_non_router_type(
        self,
    ) -> None:
        """Types that are not URL router backends are rejected."""

        class NotRouter:
            pass

        assert RouterFactory.is_filesystem_discovery_router_class(NotRouter) is False
