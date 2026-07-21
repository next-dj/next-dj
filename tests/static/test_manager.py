from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest import mock

from django.test import RequestFactory, override_settings
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
from next.static.scripts import CSRF_PAYLOAD_KEY, DEV_PAYLOAD_KEY, NextScriptBuilder


STYLES_PLACEHOLDER = "<!-- next:styles -->"
SCRIPTS_PLACEHOLDER = "<!-- next:scripts -->"


if TYPE_CHECKING:
    from pathlib import Path

    from next.components import ComponentInfo


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
            NEXT_FRAMEWORK={"STATIC_BACKENDS": [{"BACKEND": "builtins.dict"}]}
        ):
            manager._reload_config()
        assert isinstance(manager.default_backend, StaticFilesBackend)

    def test_empty_backends_seeds_default(self) -> None:
        manager = StaticManager()
        with override_settings(NEXT_FRAMEWORK={"STATIC_BACKENDS": []}):
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

    def test_inline_body_wrapped_by_kind(self, fresh_manager: StaticManager) -> None:
        fresh_manager._ensure_backends()
        backend = fresh_manager.default_backend
        css = StaticAsset(url="", kind="css", inline="body{color:red}")
        js = StaticAsset(url="", kind="js", inline="console.log(1)")
        assert fresh_manager._render_one(css, backend, None) == (
            "<style>body{color:red}</style>"
        )
        assert fresh_manager._render_one(js, backend, None) == (
            "<script>console.log(1)</script>"
        )

    def test_inline_body_verbatim_when_kind_has_no_inline_tag(
        self, fresh_manager: StaticManager
    ) -> None:
        fresh_manager._ensure_backends()
        asset = StaticAsset(url="", kind="raw", inline="<custom>x</custom>")
        with mock.patch(
            "next.static.manager.default_kinds.inline_tag", return_value=None
        ):
            rendered = fresh_manager._render_one(
                asset, fresh_manager.default_backend, None
            )
        assert rendered == "<custom>x</custom>"

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

    def test_real_request_injects_csrf_payload(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        html = f"<body>{SCRIPTS_PLACEHOLDER}</body>"
        request = RequestFactory().get("/")
        with mock.patch(
            "next.static.manager.staticfiles_storage.url",
            return_value="/static/next/next.min.js",
        ):
            out = fresh_manager.inject(html, collector, request=request)
        assert '"$csrf"' in out
        assert '"header":"X-Csrftoken"' in out
        assert '"token":' in out

    def test_missing_request_omits_csrf_payload(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        html = f"<body>{SCRIPTS_PLACEHOLDER}</body>"
        with mock.patch(
            "next.static.manager.staticfiles_storage.url",
            return_value="/static/next/next.min.js",
        ):
            out = fresh_manager.inject(html, collector)
        assert "$csrf" not in out
        assert "Next._init({})" in out


class TestInjectDevPayload:
    """The `$dev` init key follows Django `DEBUG` and is absent otherwise."""

    def inject(
        self, manager: StaticManager, collector: StaticCollector, *, debug: bool
    ) -> str:
        html = f"<body>{SCRIPTS_PLACEHOLDER}</body>"
        with (
            override_settings(DEBUG=debug),
            mock.patch(
                "next.static.manager.staticfiles_storage.url",
                return_value="/static/next/next.min.js",
            ),
        ):
            return manager.inject(html, collector)

    def inject_with_request(
        self, manager: StaticManager, collector: StaticCollector, *, debug: bool
    ) -> str:
        html = f"<body>{SCRIPTS_PLACEHOLDER}</body>"
        request = RequestFactory().get("/")
        with (
            override_settings(DEBUG=debug),
            mock.patch(
                "next.static.manager.staticfiles_storage.url",
                return_value="/static/next/next.min.js",
            ),
        ):
            return manager.inject(html, collector, request=request)

    def test_debug_adds_dev_key(self, fresh_manager: StaticManager) -> None:
        out = self.inject(fresh_manager, StaticCollector(), debug=True)
        assert 'Next._init({"$dev":true})' in out

    def test_debug_appends_dev_key_after_user_context(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        collector.add_js_context("user", "alice")
        out = self.inject(fresh_manager, collector, debug=True)
        assert 'Next._init({"user":"alice","$dev":true})' in out

    def test_debug_adds_dev_key_next_to_csrf_payload(
        self, fresh_manager: StaticManager
    ) -> None:
        out = self.inject_with_request(fresh_manager, StaticCollector(), debug=True)
        assert '"$csrf"' in out
        assert '"$dev":true' in out

    def test_debug_leaves_collector_context_unmutated(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        collector.add_js_context("user", "alice")
        out = self.inject(fresh_manager, collector, debug=True)
        assert f'"{DEV_PAYLOAD_KEY}":true' in out
        assert DEV_PAYLOAD_KEY not in collector.js_context()
        assert DEV_PAYLOAD_KEY not in collector.js_context_wire()

    def test_no_debug_omits_dev_key(self, fresh_manager: StaticManager) -> None:
        out = self.inject(fresh_manager, StaticCollector(), debug=False)
        assert "$dev" not in out
        assert "Next._init({})" in out

    def test_no_debug_omits_dev_key_with_csrf_payload(
        self, fresh_manager: StaticManager
    ) -> None:
        out = self.inject_with_request(fresh_manager, StaticCollector(), debug=False)
        assert "$dev" not in out
        assert '"$csrf"' in out

    def test_disabled_policy_omits_dev_key(self, fresh_manager: StaticManager) -> None:
        fresh_manager._ensure_backends()
        fresh_manager._script_builder = NextScriptBuilder(
            "/static/next/next.min.js", policy=ScriptInjectionPolicy.DISABLED
        )
        out = self.inject(fresh_manager, StaticCollector(), debug=True)
        assert "$dev" not in out
        assert "Next._init" not in out

    def test_manual_policy_omits_dev_key(self, fresh_manager: StaticManager) -> None:
        fresh_manager._ensure_backends()
        fresh_manager._script_builder = NextScriptBuilder(
            "/static/next/next.min.js", policy=ScriptInjectionPolicy.MANUAL
        )
        out = self.inject(fresh_manager, StaticCollector(), debug=True)
        assert "$dev" not in out
        assert "Next._init" not in out


class _MarkSerializer:
    """Per-key serializer that wraps a value under a marker."""

    def dumps(self, value: object) -> str:
        """Return the value wrapped in a marker object as compact JSON."""
        return json.dumps({"mark": value}, separators=(",", ":"))


class TestReservedInitPayloadKeys:
    """A js-context key colliding with a reserved key loses to the framework."""

    def inject(
        self,
        manager: StaticManager,
        collector: StaticCollector,
        *,
        debug: bool,
        with_request: bool = False,
    ) -> str:
        html = f"<body>{SCRIPTS_PLACEHOLDER}</body>"
        request = RequestFactory().get("/") if with_request else None
        with (
            override_settings(DEBUG=debug),
            mock.patch(
                "next.static.manager.staticfiles_storage.url",
                return_value="/static/next/next.min.js",
            ),
        ):
            return manager.inject(html, collector, request=request)

    def test_framework_csrf_payload_beats_a_user_fragment(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        collector.add_js_context(CSRF_PAYLOAD_KEY, {"header": "X-App", "token": "app"})
        out = self.inject(fresh_manager, collector, debug=False, with_request=True)
        assert '"header":"X-Csrftoken"' in out
        assert "X-App" not in out
        assert out.count(f'"{CSRF_PAYLOAD_KEY}"') == 1

    def test_framework_dev_flag_beats_a_user_fragment(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        collector.add_js_context(DEV_PAYLOAD_KEY, False)
        out = self.inject(fresh_manager, collector, debug=True)
        assert f'"{DEV_PAYLOAD_KEY}":true' in out
        assert f'"{DEV_PAYLOAD_KEY}":false' not in out

    def test_user_dev_key_survives_without_debug(
        self, fresh_manager: StaticManager
    ) -> None:
        """Without DEBUG the framework injects nothing, so `next.W075` is the guard."""
        collector = StaticCollector()
        collector.add_js_context(DEV_PAYLOAD_KEY, False)
        out = self.inject(fresh_manager, collector, debug=False)
        assert f'"{DEV_PAYLOAD_KEY}":false' in out

    def test_key_serializer_override_does_not_encode_the_framework_value(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        collector.add_js_context(DEV_PAYLOAD_KEY, False, serializer=_MarkSerializer())
        out = self.inject(fresh_manager, collector, debug=True)
        assert f'"{DEV_PAYLOAD_KEY}":true' in out
        assert "mark" not in out

    def test_collision_leaves_the_collector_untouched(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        collector.add_js_context(CSRF_PAYLOAD_KEY, {"header": "X-App", "token": "app"})
        self.inject(fresh_manager, collector, debug=True, with_request=True)
        assert collector.js_context()[CSRF_PAYLOAD_KEY] == {
            "header": "X-App",
            "token": "app",
        }
        assert "X-App" in collector.js_context_encoded()[CSRF_PAYLOAD_KEY]

    def test_other_keys_keep_their_cached_fragments(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        collector.add_js_context("user", "alice", serializer=_MarkSerializer())
        out = self.inject(fresh_manager, collector, debug=True)
        assert '"user":{"mark":"alice"}' in out


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
        sentinel = RequestFactory().get("/")
        with mock.patch.object(
            fresh_manager.default_backend, "render_link_tag", return_value="<link/>"
        ) as render:
            fresh_manager.inject(
                f"<head>{STYLES_PLACEHOLDER}</head>", collector, request=sentinel
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
                    "/static/next/next.min.js", policy=ScriptInjectionPolicy.DISABLED
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
            fresh_manager.default_backend, "render_link_tag", return_value="<link/>"
        ) as render:
            fresh_manager.inject(f"<head>{STYLES_PLACEHOLDER}</head>", collector)
        render.assert_called_once_with(CSS_URL, request=None)


class TestDiscoveryForwarding:
    def test_discover_page_assets_delegates(
        self, tmp_path: Path, fresh_manager: StaticManager
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

    def test_discover_component_assets_delegates(
        self, composite_component: ComponentInfo, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        with mock.patch(
            "next.static.backends.staticfiles_storage.url",
            return_value="/static/next/components/widget.css",
        ):
            fresh_manager.discover_component_assets(composite_component, collector)
        style_urls = [a.url for a in collector.assets_in_slot("styles")]
        assert "/static/next/components/widget.css" in style_urls
        assert "https://cdn.example.com/extra.css" in style_urls


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
                "STATIC_BACKENDS": [{"BACKEND": "next.static.StaticFilesBackend"}]
            }
        ):
            # override_settings fires setting_changed, which calls reload.
            # The first attribute access rebuilds the manager.
            assert isinstance(default_manager.default_backend, StaticFilesBackend)
