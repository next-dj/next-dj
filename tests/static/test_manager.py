from __future__ import annotations

from typing import TYPE_CHECKING
from unittest import mock

from django.test import override_settings
from django.utils.functional import empty

from next.static import (
    ScriptInjectionPolicy,
    StaticAsset,
    StaticCollector,
    StaticFilesBackend,
    StaticManager,
    default_manager,
    reset_default_manager,
)
from next.static.collector import HEAD_CLOSE
from next.static.manager import DefaultStaticManager
from next.static.scripts import NextScriptBuilder


STYLES_PLACEHOLDER = "<!-- next:styles -->"
SCRIPTS_PLACEHOLDER = "<!-- next:scripts -->"


if TYPE_CHECKING:
    from pathlib import Path


CSS_URL = "https://cdn.example.com/a.css"
JS_URL = "https://cdn.example.com/a.js"


class TestEnsureBackends:
    def test_default_backend_is_static_files(
        self, fresh_manager: StaticManager
    ) -> None:
        assert isinstance(fresh_manager.default_backend, StaticFilesBackend)

    def test_len_equals_configured_count(self, fresh_manager: StaticManager) -> None:
        assert len(fresh_manager) == 1

    def test_page_roots_cached(self, fresh_manager: StaticManager) -> None:
        roots1 = fresh_manager.page_roots()
        roots2 = fresh_manager.page_roots()
        assert roots1 is roots2


class TestReloadConfig:
    def test_reload_rebuilds_backends(self) -> None:
        manager = StaticManager()
        manager._ensure_backends()
        initial = manager.default_backend
        manager._reload_config()
        assert manager.default_backend is not initial

    def test_reload_clears_discovery_cache(self) -> None:
        manager = StaticManager()
        _ = manager.discovery
        manager._reload_config()
        assert manager._discovery is None

    def test_reload_clears_script_builder(self) -> None:
        manager = StaticManager()
        manager._ensure_backends()
        with mock.patch(
            "next.static.manager.staticfiles_storage.url",
            return_value="/static/next/next.min.js",
        ):
            manager._next_script_builder()
        assert manager._script_builder is not None
        manager._reload_config()
        assert manager._script_builder is None

    def test_invalid_backend_falls_back(self) -> None:
        manager = StaticManager()
        with override_settings(
            NEXT_FRAMEWORK={"DEFAULT_STATIC_BACKENDS": [{"BACKEND": "builtins.dict"}]}
        ):
            manager._reload_config()
        assert isinstance(manager.default_backend, StaticFilesBackend)

    def test_empty_backends_seeds_default(self) -> None:
        manager = StaticManager()
        with override_settings(NEXT_FRAMEWORK={"DEFAULT_STATIC_BACKENDS": []}):
            manager._reload_config()
        assert len(manager) == 1


class TestInjectStyles:
    def test_replaces_styles_placeholder(self, fresh_manager: StaticManager) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        html = f"<head>{STYLES_PLACEHOLDER}</head><body/>"
        out = fresh_manager.inject(html, collector)
        assert f'<link rel="stylesheet" href="{CSS_URL}">' in out
        assert STYLES_PLACEHOLDER not in out

    def test_inline_style_emitted_verbatim(self, fresh_manager: StaticManager) -> None:
        collector = StaticCollector()
        collector.add(
            StaticAsset(url="", kind="css", inline="<style>body{color:red}</style>")
        )
        html = f"<head>{STYLES_PLACEHOLDER}</head>"
        out = fresh_manager.inject(html, collector)
        assert "<style>body{color:red}</style>" in out

    def test_empty_collector_empties_placeholder(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        html = f"<head>{STYLES_PLACEHOLDER}</head>"
        out = fresh_manager.inject(html, collector)
        assert STYLES_PLACEHOLDER not in out


class TestInjectScriptsAuto:
    """AUTO policy injects next.min.js and init script before user scripts."""

    def test_script_and_init_emitted(self, fresh_manager: StaticManager) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url=JS_URL, kind="js"))
        collector.add_js_context("user", "alice")

        html = f"<body>{SCRIPTS_PLACEHOLDER}</body>"
        with mock.patch(
            "next.static.manager.staticfiles_storage.url",
            return_value="/static/next/next.min.js",
        ):
            out = fresh_manager.inject(html, collector)

        assert "/static/next/next.min.js" in out
        assert 'Next._init({"user":"alice"})' in out
        assert f'<script src="{JS_URL}"></script>' in out
        assert SCRIPTS_PLACEHOLDER not in out

    def test_next_script_comes_before_user_scripts(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url=JS_URL, kind="js"))
        html = f"<body>{SCRIPTS_PLACEHOLDER}</body>"
        with mock.patch(
            "next.static.manager.staticfiles_storage.url",
            return_value="/static/next/next.min.js",
        ):
            out = fresh_manager.inject(html, collector)
        next_idx = out.index("/static/next/next.min.js")
        user_idx = out.index(JS_URL)
        assert next_idx < user_idx


class TestInjectScriptsDisabled:
    def test_disabled_policy_skips_injection(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url=JS_URL, kind="js"))
        fresh_manager._ensure_backends()
        fresh_manager._script_builder = NextScriptBuilder(
            "/static/next/next.min.js", policy=ScriptInjectionPolicy.DISABLED
        )

        html = f"<body>{SCRIPTS_PLACEHOLDER}</body>"
        out = fresh_manager.inject(html, collector)

        assert "/static/next/next.min.js" not in out
        assert "Next._init" not in out
        assert JS_URL in out

    def test_next_js_options_setting_honored(
        self, fresh_manager: StaticManager
    ) -> None:
        """`NEXT_JS_OPTIONS` from user settings controls the injection policy."""
        collector = StaticCollector()
        collector.add(StaticAsset(url=JS_URL, kind="js"))
        fresh_manager._ensure_backends()
        with (
            override_settings(
                NEXT_FRAMEWORK={"NEXT_JS_OPTIONS": {"policy": "disabled"}}
            ),
            mock.patch(
                "next.static.manager.staticfiles_storage.url",
                return_value="/static/next/next.min.js",
            ),
        ):
            html = f"<body>{SCRIPTS_PLACEHOLDER}</body>"
            out = fresh_manager.inject(html, collector)

        assert "/static/next/next.min.js" not in out
        assert "Next._init" not in out
        assert JS_URL in out


class TestInjectPreloadHint:
    def test_preload_prepended_before_head_close(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        html = f"<head>{HEAD_CLOSE}</head><body>{SCRIPTS_PLACEHOLDER}</body>"
        with mock.patch(
            "next.static.manager.staticfiles_storage.url",
            return_value="/static/next/next.min.js",
        ):
            out = fresh_manager.inject(html, collector)
        assert 'rel="preload"' in out
        preload_idx = out.index("preload")
        head_close_idx = out.index(HEAD_CLOSE)
        assert preload_idx < head_close_idx

    def test_no_head_close_means_no_preload(self, fresh_manager: StaticManager) -> None:
        collector = StaticCollector()
        html = "<body></body>"
        with mock.patch(
            "next.static.manager.staticfiles_storage.url",
            return_value="/static/next/next.min.js",
        ):
            out = fresh_manager.inject(html, collector)
        assert 'rel="preload"' not in out

    def test_disabled_policy_skips_preload(self, fresh_manager: StaticManager) -> None:
        collector = StaticCollector()
        fresh_manager._ensure_backends()
        fresh_manager._script_builder = NextScriptBuilder(
            "/static/next/next.min.js", policy=ScriptInjectionPolicy.DISABLED
        )
        html = f"<head>{HEAD_CLOSE}</head>"
        out = fresh_manager.inject(html, collector)
        assert 'rel="preload"' not in out


class TestInjectMissingPlaceholders:
    def test_missing_placeholders_leave_html_alone(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        html = "<html><body>plain</body></html>"
        out = fresh_manager.inject(html, collector)
        assert out == html


class TestInjectForwardsRequest:
    """`inject` forwards the active request to backend tag renderers."""

    def test_request_passed_to_render_link_tag(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        sentinel = object()
        with mock.patch.object(
            fresh_manager.default_backend,
            "render_link_tag",
            return_value="<link/>",
        ) as render:
            fresh_manager.inject(
                f"<head>{STYLES_PLACEHOLDER}</head>",
                collector,
                request=sentinel,  # type: ignore[arg-type]
            )
        render.assert_called_once_with(CSS_URL, request=sentinel)

    def test_request_passed_to_render_script_tag(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url=JS_URL, kind="js"))
        sentinel = object()
        with (
            mock.patch.object(
                fresh_manager,
                "_next_script_builder",
                return_value=NextScriptBuilder(
                    "/static/next/next.min.js",
                    policy=ScriptInjectionPolicy.DISABLED,
                ),
            ),
            mock.patch.object(
                fresh_manager.default_backend,
                "render_script_tag",
                return_value="<script/>",
            ) as render,
        ):
            fresh_manager.inject(
                f"<body>{SCRIPTS_PLACEHOLDER}</body>",
                collector,
                request=sentinel,  # type: ignore[arg-type]
            )
        render.assert_called_once_with(JS_URL, request=sentinel)

    def test_request_defaults_to_none(self, fresh_manager: StaticManager) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        with mock.patch.object(
            fresh_manager.default_backend,
            "render_link_tag",
            return_value="<link/>",
        ) as render:
            fresh_manager.inject(f"<head>{STYLES_PLACEHOLDER}</head>", collector)
        render.assert_called_once_with(CSS_URL, request=None)


class TestDiscoveryForwarding:
    def test_discover_page_assets_delegates(
        self,
        tmp_path: Path,
        fresh_manager: StaticManager,
    ) -> None:
        (tmp_path / "template.css").write_text("")
        page_path = tmp_path / "page.djx"
        page_path.write_text("")
        fresh_manager._cached_page_roots = (tmp_path.resolve(),)

        collector = StaticCollector()
        with mock.patch(
            "next.static.backends.staticfiles_storage.url",
            return_value="/static/next/index.css",
        ):
            fresh_manager.discover_page_assets(page_path, collector)
        assert [a.url for a in collector.assets_in_slot("styles")] == [
            "/static/next/index.css"
        ]


class TestDefaultManagerLazy:
    def test_resolves_to_static_manager(self, reset_default: None) -> None:
        reset_default_manager()
        assert isinstance(default_manager.default_backend, StaticFilesBackend)

    def test_is_lazy_object_class(self) -> None:
        assert isinstance(default_manager, DefaultStaticManager)

    def test_reset_drops_wrapped(self, reset_default: None) -> None:
        _ = default_manager.default_backend  # force eval
        assert default_manager._wrapped is not empty
        reset_default_manager()
        assert default_manager._wrapped is empty

    def test_setup_is_idempotent(self, reset_default: None) -> None:
        reset_default_manager()
        a = default_manager.default_backend
        b = default_manager.default_backend
        assert a is b


class TestSettingChangedReload:
    """Changing NEXT_FRAMEWORK triggers a default_manager reset via next.conf."""

    def test_override_settings_resets_manager(self, reset_default: None) -> None:
        _ = default_manager.default_backend  # warm up
        assert default_manager._wrapped is not empty

        with override_settings(
            NEXT_FRAMEWORK={
                "DEFAULT_STATIC_BACKENDS": [
                    {"BACKEND": "next.static.StaticFilesBackend"}
                ]
            }
        ):
            # override_settings fires setting_changed, which calls reload.
            # The first attribute access rebuilds the manager.
            assert isinstance(default_manager.default_backend, StaticFilesBackend)
