from __future__ import annotations

import json
import logging
from contextvars import copy_context
from functools import partial
from typing import TYPE_CHECKING, Any
from unittest import mock

import pytest
from django.middleware.csrf import get_token
from django.test import RequestFactory, override_settings
from django.urls import NoReverseMatch

from next.csrf import defer_token
from next.defer.payload import partial_streams
from next.diagnostics import QUIET_PERIOD, degraded, watch_degraded
from next.ports import PortList, ScriptShare
from next.router.render import RouterRender, RouterScripts, router_render
from next.static import (
    StaticAsset,
    StaticCollector,
    StaticFilesBackend,
    StaticManager,
    default_kinds,
)
from next.static.collector import HEAD_CLOSE, default_placeholders
from next.static.inject import (
    SPLIT_NOTE,
    _dropped as dropped_bundles,
    _split_mark,
    split_of,
)
from next.static.manager import _failures as manager_failures
from next.static.runtime import (
    BUNDLE_NOTE,
    CSRF_PAYLOAD_KEY,
    DEV_PAYLOAD_KEY,
    html_finish,
)
from next.static.serializers import JsonJsContextSerializer
from next.testing import override_next_settings
from tests.support import (
    PREFIXED_BACKENDS,
    REQUEST_RECORDING_BACKENDS,
    PrefixingStaticBackend,
    restored_static_registries,
)


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping

    from django.http import HttpRequest


STYLES_PLACEHOLDER = "<!-- next:styles -->"
SCRIPTS_PLACEHOLDER = "<!-- next:scripts -->"
CSS_URL = "https://cdn.example.com/a.css"
JS_URL = "https://cdn.example.com/a.js"
NEXT_JS_URL = "/static/next/next.min.js"
LAZY_CHUNKS = (
    '"scripts":"/static/next/next.scripts.min.js",'
    '"stream":"/static/next/next.stream.min.js",'
    '"csrf":"/static/next/next.csrf.min.js",'
    '"poll":"/static/next/next.poll.min.js",'
    '"forms":"/static/next/next.forms.min.js",'
    '"layers":"/static/next/next.layers.min.js"'
)
CHUNKS = f'"$chunks":{{{LAZY_CHUNKS}}}'
DEV_CHUNKS = f'"$chunks":{{{LAZY_CHUNKS},"dev":"/static/next/next.dev.min.js"}}'
WIRE = ',"$version":"0","$protocol":2'

HASHED = {
    "next/next.min.js": "/static/next/next.min.1a2b.js",
    "next/next.scripts.min.js": "/static/next/next.scripts.min.3c4d.js",
    "next/next.stream.min.js": "/static/next/next.stream.min.7a8b.js",
    "next/next.ws.min.js": "/static/next/next.ws.min.8c9d.js",
    "next/next.csrf.min.js": "/static/next/next.csrf.min.9c0d.js",
    "next/next.poll.min.js": "/static/next/next.poll.min.1e2f.js",
    "next/next.dev.min.js": "/static/next/next.dev.min.5e6f.js",
    "next/next.forms.min.js": "/static/next/next.forms.min.2a3b.js",
    "next/next.layers.min.js": "/static/next/next.layers.min.4c5d.js",
}
HASHED_CHUNKS = (
    '"$chunks":{"scripts":"/static/next/next.scripts.min.3c4d.js",'
    '"stream":"/static/next/next.stream.min.7a8b.js",'
    '"csrf":"/static/next/next.csrf.min.9c0d.js",'
    '"poll":"/static/next/next.poll.min.1e2f.js",'
    '"forms":"/static/next/next.forms.min.2a3b.js",'
    '"layers":"/static/next/next.layers.min.4c5d.js"}'
)
REQUEST_ONLY_BACKENDS = {
    "STATIC_BACKENDS": [
        {"BACKEND": "tests.static.test_inject.RequestOnlyStaticBackend"}
    ]
}
COMPOSED_BACKENDS = {
    "STATIC_BACKENDS": [{"BACKEND": "tests.static.test_inject.ComposedStaticBackend"}]
}
DISABLED = {"NEXT_JS_OPTIONS": {"policy": "disabled"}}
MANUAL = {"NEXT_JS_OPTIONS": {"policy": "manual"}}
REWRITING_BACKENDS = pytest.mark.parametrize(
    ("backends", "prefix"),
    [
        pytest.param(PREFIXED_BACKENDS, "/pfx", id="override"),
        pytest.param(COMPOSED_BACKENDS, "/composed", id="composed"),
    ],
)


def _storage_url(name: str) -> str:
    return f"/static/{name}"


def _stamp_prefix(prefix: str, url: str, *, request: HttpRequest | None = None) -> str:
    if request is None or not url.startswith("/"):
        return url
    return f"{prefix}{url}"


class RequestOnlyStaticBackend(StaticFilesBackend):
    """Backend whose script renderer takes the request but not the nonce."""

    def render_script_tag(self, url: str, *, request: HttpRequest | None = None) -> str:
        """Render the tag without a nonce."""
        return f'<script src="{url}" data-old></script>'


class ComposedStaticBackend(StaticFilesBackend):
    """Backend that installs its rewrite on the instance instead of the class."""

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        """Bind the prefix into the hook the manager will find on the instance."""
        super().__init__(config)
        self.asset_url = partial(_stamp_prefix, "/composed")


class TestInjectStyles:
    def test_replaces_styles_placeholder(self, fresh_manager: StaticManager) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        html = f"<head>{STYLES_PLACEHOLDER}</head><body/>"
        out = fresh_manager.inject(html, collector)
        assert f'<link rel="stylesheet" href="{CSS_URL}">' in out
        assert STYLES_PLACEHOLDER not in out

    def test_inline_body_wrapped_by_kind(self, fresh_manager: StaticManager) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url="", kind="css", inline="body{color:red}"))
        collector.add(StaticAsset(url="", kind="js", inline="console.log(1)"))
        html = f"<head>{STYLES_PLACEHOLDER}</head><body>{SCRIPTS_PLACEHOLDER}</body>"

        out = fresh_manager.inject(html, collector)

        assert "<style>body{color:red}</style>" in out
        assert "<script>console.log(1)</script>" in out

    def test_inline_body_verbatim_when_kind_has_no_inline_tag(
        self, fresh_manager: StaticManager
    ) -> None:
        """A kind naming no element contributes its body as it stands."""
        with restored_static_registries():
            default_kinds.register(
                "raw", extension=".raw", slot="styles", renderer="render_link_tag"
            )
            collector = StaticCollector()
            collector.add(StaticAsset(url="", kind="raw", inline="<custom>x</custom>"))
            out = fresh_manager.inject(f"<head>{STYLES_PLACEHOLDER}</head>", collector)

        assert "<custom>x</custom>" in out
        assert "<style>" not in out

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
            "next.static.manager.staticfiles_storage.url", side_effect=_storage_url
        ):
            out = fresh_manager.inject(html, collector)

        assert "/static/next/next.min.js" in out
        assert f'Next._init({{"user":"alice",{CHUNKS}{WIRE}}})' in out
        assert f'<script src="{JS_URL}"></script>' in out
        assert SCRIPTS_PLACEHOLDER not in out

    def test_no_provider_adds_no_head_or_provider_entry(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        html = f"<head></head><body>{SCRIPTS_PLACEHOLDER}</body>"
        with (
            mock.patch(
                "next.static.manager.staticfiles_storage.url", side_effect=_storage_url
            ),
            mock.patch("next.static.inject.page_scripts", PortList()),
        ):
            out = fresh_manager.inject(html, collector)

        assert "/static/next/next.min.js" in out
        assert "<script" not in out.split("</head>")[0]
        assert f"Next._init({{{CHUNKS}}})" in out

    def test_next_script_comes_before_user_scripts(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url=JS_URL, kind="js"))
        html = f"<body>{SCRIPTS_PLACEHOLDER}</body>"
        with mock.patch(
            "next.static.manager.staticfiles_storage.url", side_effect=_storage_url
        ):
            out = fresh_manager.inject(html, collector)
        next_idx = out.index("/static/next/next.min.js")
        user_idx = out.index(JS_URL)
        assert next_idx < user_idx

    def _payload(
        self, manager: StaticManager, request: HttpRequest, *, streams: bool = False
    ) -> str:
        collector = StaticCollector()
        if streams:
            collector.note(SPLIT_NOTE, None)
        html = f"<body>{SCRIPTS_PLACEHOLDER}</body>"
        with mock.patch(
            "next.static.manager.staticfiles_storage.url", side_effect=_storage_url
        ):
            return manager.inject(html, collector, request=request)

    def test_a_request_that_read_no_token_names_the_endpoint(
        self, fresh_manager: StaticManager
    ) -> None:
        request = RequestFactory().get("/")
        out = self._payload(fresh_manager, request)
        assert (
            '"$csrf":{"header":"X-Csrftoken","url":"/_next/csrf/","cookie":"csrftoken"}'
            in out
        )
        assert not request.META.get("CSRF_COOKIE_NEEDS_UPDATE")

    def test_a_request_that_read_a_token_embeds_one(
        self, fresh_manager: StaticManager
    ) -> None:
        request = RequestFactory().get("/")
        get_token(request)
        out = self._payload(fresh_manager, request)
        assert '"$csrf":{"header":"X-Csrftoken","token":' in out

    def test_each_page_carries_its_own_entry_after_another(
        self, fresh_manager: StaticManager
    ) -> None:
        endpoint = '"$csrf":{"header":"X-Csrftoken","url":"/_next/csrf/"'
        first = self._payload(fresh_manager, RequestFactory().get("/"))
        again = self._payload(fresh_manager, RequestFactory().get("/"))
        minting = RequestFactory().get("/")
        get_token(minting)
        minted = self._payload(fresh_manager, minting)
        assert endpoint in first
        assert endpoint in again
        assert '"$csrf":{"header":"X-Csrftoken","token":' in minted

    def test_a_streamed_page_mints_before_its_headers(
        self, fresh_manager: StaticManager
    ) -> None:
        request = RequestFactory().get("/")
        out = self._payload(fresh_manager, request, streams=True)
        assert '"$csrf":{"header":"X-Csrftoken","token":' in out
        assert request.META["CSRF_COOKIE_NEEDS_UPDATE"]

    @pytest.mark.parametrize("delivery", ["eager", "lazy"])
    def test_the_other_modes_keep_their_rule(
        self, fresh_manager: StaticManager, delivery: str
    ) -> None:
        request = RequestFactory().get("/")
        with override_settings(NEXT_FRAMEWORK={"CSRF_DELIVERY": delivery}):
            if delivery == "lazy":
                defer_token(request)
            out = self._payload(fresh_manager, request)
        assert ('"token":' in out) is (delivery == "eager")

    def test_an_unrouted_endpoint_embeds_the_token(
        self, fresh_manager: StaticManager
    ) -> None:
        request = RequestFactory().get("/")
        with mock.patch("next.csrf.csrf_url", side_effect=NoReverseMatch):
            out = self._payload(fresh_manager, request)
        assert '"$csrf":{"header":"X-Csrftoken","token":' in out

    def test_missing_request_omits_csrf_payload(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        html = f"<body>{SCRIPTS_PLACEHOLDER}</body>"
        with mock.patch(
            "next.static.manager.staticfiles_storage.url", side_effect=_storage_url
        ):
            out = fresh_manager.inject(html, collector)
        assert "$csrf" not in out
        assert f"Next._init({{{CHUNKS}{WIRE}}})" in out


class TestScriptsChunkUrl:
    """Every init payload names the chunk the storage resolved, once per storage."""

    def test_a_payload_without_chunk_keys_names_the_hashed_chunk(
        self, fresh_manager: StaticManager
    ) -> None:
        html = f"<body>{SCRIPTS_PLACEHOLDER}</body>"
        with mock.patch(
            "next.static.manager.staticfiles_storage.url",
            side_effect=HASHED.__getitem__,
        ) as url:
            first = fresh_manager.inject(html, StaticCollector())
            second = fresh_manager.inject(html, StaticCollector())
        assert first == second
        assert f"Next._init({{{HASHED_CHUNKS}{WIRE}}})" in first
        assert url.call_count == 7

    def test_debug_names_the_hashed_dev_chunk_too(
        self, fresh_manager: StaticManager
    ) -> None:
        html = f"<body>{SCRIPTS_PLACEHOLDER}</body>"
        with (
            override_settings(DEBUG=True),
            mock.patch(
                "next.static.manager.staticfiles_storage.url",
                side_effect=HASHED.__getitem__,
            ),
        ):
            out = fresh_manager.inject(html, StaticCollector())
        assert (
            f'{HASHED_CHUNKS[:-1]},"dev":"/static/next/next.dev.min.5e6f.js"}}{WIRE}}})'
        ) in out


class _SpacedSerializer(JsonJsContextSerializer):
    """Encodes with default separators, so its bytes differ from the default one."""

    def dumps(self, value: object) -> str:
        return json.dumps(value)


class TestWebSocketChunk:
    """The WebSocket adapter is listed only while the `ws` transport is active."""

    HTML = f"<body>{SCRIPTS_PLACEHOLDER}</body>"

    def test_an_active_websocket_lists_the_adapter_and_its_offer(
        self, fresh_manager: StaticManager
    ) -> None:
        with (
            override_next_settings(REALTIME={"TRANSPORTS": ["ws", "sse"]}),
            mock.patch(
                "next.static.manager.staticfiles_storage.url", side_effect=_storage_url
            ),
        ):
            out = fresh_manager.inject(self.HTML, StaticCollector())
        assert '"ws":"/static/next/next.ws.min.js"' in out
        assert '"$realtime":{"protocol":2,"transports":["ws","sse"]' in out

    def test_the_stock_transports_leave_the_adapter_out(
        self, fresh_manager: StaticManager
    ) -> None:
        with mock.patch(
            "next.static.manager.staticfiles_storage.url", side_effect=_storage_url
        ):
            out = fresh_manager.inject(self.HTML, StaticCollector())
        assert "next.ws.min.js" not in out
        assert "$realtime" not in out


class TestChunksEncoding:
    """The `$chunks` entry is encoded again only once a URL or the serializer moves."""

    HTML = f"<body>{SCRIPTS_PLACEHOLDER}</body>"

    def inject(self, manager: StaticManager, *, debug: bool = False) -> str:
        with (
            override_settings(DEBUG=debug),
            mock.patch(
                "next.static.manager.staticfiles_storage.url", side_effect=_storage_url
            ),
        ):
            return manager.inject(self.HTML, StaticCollector())

    def test_an_unmoved_entry_is_encoded_once(
        self, fresh_manager: StaticManager
    ) -> None:
        with mock.patch.object(
            JsonJsContextSerializer,
            "dumps",
            autospec=True,
            side_effect=JsonJsContextSerializer.dumps,
        ) as dumps:
            first = self.inject(fresh_manager)
            second = self.inject(fresh_manager)
        assert first == second
        assert dumps.call_count == 1

    def test_debug_and_back_switches_the_entry_each_way(
        self, fresh_manager: StaticManager
    ) -> None:
        assert f"Next._init({{{CHUNKS}{WIRE}}})" in self.inject(fresh_manager)
        assert DEV_CHUNKS in self.inject(fresh_manager, debug=True)
        assert f"Next._init({{{CHUNKS}{WIRE}}})" in self.inject(fresh_manager)

    def test_a_rebuilt_storage_encodes_the_new_url(
        self, fresh_manager: StaticManager
    ) -> None:
        self.inject(fresh_manager)
        fresh_manager.forget_backend_urls()
        with mock.patch(
            "next.static.manager.staticfiles_storage.url",
            side_effect=HASHED.__getitem__,
        ):
            out = fresh_manager.inject(self.HTML, StaticCollector())
        assert f"{HASHED_CHUNKS}{WIRE}}})" in out

    def test_another_serializer_encodes_it_again(
        self, fresh_manager: StaticManager
    ) -> None:
        self.inject(fresh_manager)
        with mock.patch(
            "next.static.inject.resolve_serializer", return_value=_SpacedSerializer()
        ):
            out = self.inject(fresh_manager)
        assert '"$chunks":{"scripts": "/static/next/next.scripts.min.js", ' in out


class TestInjectDevPayload:
    """The `$dev` init key follows Django `DEBUG` and is absent otherwise."""

    def inject(
        self, manager: StaticManager, collector: StaticCollector, *, debug: bool
    ) -> str:
        html = f"<body>{SCRIPTS_PLACEHOLDER}</body>"
        with (
            override_settings(DEBUG=debug),
            mock.patch(
                "next.static.manager.staticfiles_storage.url", side_effect=_storage_url
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
                "next.static.manager.staticfiles_storage.url", side_effect=_storage_url
            ),
        ):
            return manager.inject(html, collector, request=request)

    def test_debug_adds_dev_key(self, fresh_manager: StaticManager) -> None:
        out = self.inject(fresh_manager, StaticCollector(), debug=True)
        assert f'Next._init({{"$dev":true,{DEV_CHUNKS}{WIRE}}})' in out

    def test_debug_appends_dev_key_after_user_context(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        collector.add_js_context("user", "alice")
        out = self.inject(fresh_manager, collector, debug=True)
        assert f'Next._init({{"user":"alice","$dev":true,{DEV_CHUNKS}{WIRE}}})' in out

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
        assert "next.dev.min.js" not in out
        assert f"Next._init({{{CHUNKS}{WIRE}}})" in out

    def test_no_debug_omits_dev_key_with_csrf_payload(
        self, fresh_manager: StaticManager
    ) -> None:
        out = self.inject_with_request(fresh_manager, StaticCollector(), debug=False)
        assert "$dev" not in out
        assert '"$csrf"' in out

    @pytest.mark.parametrize("options", [DISABLED, MANUAL], ids=["disabled", "manual"])
    def test_a_policy_without_injection_omits_dev_key(
        self, fresh_manager: StaticManager, options: dict
    ) -> None:
        with override_settings(NEXT_FRAMEWORK=options):
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
                "next.static.manager.staticfiles_storage.url", side_effect=_storage_url
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

    def test_user_dev_key_is_dropped_without_debug(
        self, fresh_manager: StaticManager
    ) -> None:
        """A reserved key belongs to the framework even when it writes nothing."""
        collector = StaticCollector()
        collector.add_js_context(DEV_PAYLOAD_KEY, False)
        out = self.inject(fresh_manager, collector, debug=False)
        assert DEV_PAYLOAD_KEY not in out
        assert f"Next._init({{{CHUNKS}{WIRE}}})" in out

    def test_user_csrf_key_is_dropped_without_a_token_minting_request(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        collector.add_js_context(CSRF_PAYLOAD_KEY, {"header": "X-App", "token": "app"})
        out = self.inject(fresh_manager, collector, debug=False)
        assert CSRF_PAYLOAD_KEY not in out
        assert "X-App" not in out

    def test_a_context_without_reserved_keys_is_untouched(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        collector.add_js_context("user", "alice")
        out = self.inject(fresh_manager, collector, debug=False)
        assert f'Next._init({{"user":"alice",{CHUNKS}{WIRE}}})' in out

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

        html = f"<body>{SCRIPTS_PLACEHOLDER}</body>"
        with override_settings(NEXT_FRAMEWORK=DISABLED):
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
                "next.static.manager.staticfiles_storage.url", side_effect=_storage_url
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
            "next.static.manager.staticfiles_storage.url", side_effect=_storage_url
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
            "next.static.manager.staticfiles_storage.url", side_effect=_storage_url
        ):
            out = fresh_manager.inject(html, collector)
        assert 'rel="preload"' not in out

    def test_no_scripts_placeholder_means_no_preload(
        self, fresh_manager: StaticManager
    ) -> None:
        """The runtime loads from the scripts placeholder alone, so none is hinted."""
        html = f"<head>{HEAD_CLOSE}</head><body></body>"
        with mock.patch(
            "next.static.manager.staticfiles_storage.url", side_effect=_storage_url
        ):
            out = fresh_manager.inject(html, StaticCollector())
        assert out == html

    def test_disabled_policy_skips_preload(self, fresh_manager: StaticManager) -> None:
        collector = StaticCollector()
        html = f"<head>{HEAD_CLOSE}</head>"
        with override_settings(NEXT_FRAMEWORK=DISABLED):
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

    def test_a_layout_without_the_scripts_slot_mints_no_csrf_token(
        self, fresh_manager: StaticManager
    ) -> None:
        """No token reaches a page without the slot, so no cookie or `Vary` follows."""
        request = RequestFactory().get("/")
        html = f"<html><head>{STYLES_PLACEHOLDER}</head><body>plain</body></html>"
        with mock.patch("next.csrf.get_token") as minted:
            out = fresh_manager.inject(html, StaticCollector(), request=request)
        assert out == "<html><head></head><body>plain</body></html>"
        minted.assert_not_called()

    def test_every_repeat_of_a_slot_token_is_filled(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        html = f"{STYLES_PLACEHOLDER}<p>{SCRIPTS_PLACEHOLDER}</p>{STYLES_PLACEHOLDER}"
        out = fresh_manager.inject(html, collector)
        assert out.count(CSS_URL) == 2
        assert "<!-- next:" not in out

    def test_a_comment_sharing_the_token_prefix_stays(
        self, fresh_manager: StaticManager
    ) -> None:
        html = f"<head><!-- next:other -->{STYLES_PLACEHOLDER}</head>"
        out = fresh_manager.inject(html, StaticCollector())
        assert out == "<head><!-- next:other --></head>"

    def test_a_token_without_the_shared_prefix_is_filled_too(
        self, fresh_manager: StaticManager
    ) -> None:
        with restored_static_registries():
            default_placeholders.register("late", token="[[late]]")
            default_kinds.register(
                "late", extension=".late", slot="late", renderer="render_link_tag"
            )
            collector = StaticCollector()
            collector.add(StaticAsset(url="", kind="late", inline="<i>late</i>"))
            collector.add(StaticAsset(url=CSS_URL, kind="css"))
            html = f"<p>[[late]]</p>{STYLES_PLACEHOLDER}<p>[[late]]</p>"
            out = fresh_manager.inject(html, collector)
        assert out.count("<i>late</i>") == 2
        assert CSS_URL in out
        assert "[[late]]" not in out
        assert STYLES_PLACEHOLDER not in out


class TestStreamSplit:
    """A streamed render asks where its chunks go, the end of the scripts slot."""

    def _split(self, manager: StaticManager, html: str) -> tuple[str, int | None]:
        collector = StaticCollector()
        collector.note(SPLIT_NOTE, None)
        with mock.patch(
            "next.static.manager.staticfiles_storage.url", side_effect=_storage_url
        ):
            out = manager.inject(html, collector)
        return out, split_of(collector)

    def test_the_split_follows_the_last_scripts_slot(
        self, fresh_manager: StaticManager
    ) -> None:
        html = f"<head></head><p>{SCRIPTS_PLACEHOLDER}</p><p>x</p>{SCRIPTS_PLACEHOLDER}"
        out, split = self._split(fresh_manager, html)
        assert split is not None
        assert out[:split].endswith("</script>\n")
        assert SCRIPTS_PLACEHOLDER not in out
        assert out.count("Next._init(") == 2
        assert out[split:] == ""

    def test_a_body_close_after_the_scripts_slot_takes_the_split(
        self, fresh_manager: StaticManager
    ) -> None:
        html = f"<head>{SCRIPTS_PLACEHOLDER}</head><body><p>x</p></body></html>"
        out, split = self._split(fresh_manager, html)
        assert out[split:] == "</body></html>"

    def test_a_page_without_a_body_close_splits_at_its_end(
        self, fresh_manager: StaticManager
    ) -> None:
        out, split = self._split(fresh_manager, "<p>x</p>")
        assert (out, split) == ("<p>x</p>", 8)

    def test_page_text_that_looks_like_a_mark_stays_and_no_mark_leaks(
        self, fresh_manager: StaticManager
    ) -> None:
        text = "user\x00next-split\x00text"
        html = f"<p>{text}</p>{SCRIPTS_PLACEHOLDER}<footer>{text}</footer></body>"
        out, split = self._split(fresh_manager, html)
        assert out.count(text) == 2
        assert "\x00next-split-" not in out
        assert split is not None
        assert out[split:] == "</body>"

    def test_an_unrouted_html_finishes_before_its_split(
        self, fresh_manager: StaticManager
    ) -> None:
        seen: list[str] = []

        def finish(html: str) -> str:
            seen.append(html)
            return html.replace("<nav>", '<nav data-x="1">')

        token = html_finish.set(finish)
        try:
            out, split = self._split(
                fresh_manager, f"<body><nav></nav>{SCRIPTS_PLACEHOLDER}</body>"
            )
        finally:
            html_finish.reset(token)
        assert "\x00next-split-" in seen[0]
        assert out.startswith('<body><nav data-x="1"></nav>')
        assert out[split:] == "</body>"

    def test_each_render_gets_its_own_mark(self) -> None:
        assert _split_mark() != _split_mark()

    def test_a_render_that_asks_nothing_gets_no_split(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        out = fresh_manager.inject(f"<body>{SCRIPTS_PLACEHOLDER}</body>", collector)
        assert split_of(collector) is None
        assert SCRIPTS_PLACEHOLDER not in out

    def test_a_split_asked_for_reads_none_until_the_injector_answers(self) -> None:
        collector = StaticCollector()
        collector.note(SPLIT_NOTE, None)
        assert split_of(collector) is None


class TestInjectForwardsRequest:
    """`inject` forwards the active request to the backend rendering each tag."""

    def _inject(self, html: str, collector: StaticCollector, request) -> str:
        """Inject through a manager whose backend names the request in its tags."""
        with override_settings(NEXT_FRAMEWORK=REQUEST_RECORDING_BACKENDS):
            return StaticManager().inject(html, collector, request=request)

    def test_a_style_tag_is_rendered_under_the_active_request(self) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        request = RequestFactory().get("/dashboard/")

        out = self._inject(f"<head>{STYLES_PLACEHOLDER}</head>", collector, request)

        assert f'<link href="{CSS_URL}" data-request="/dashboard/">' in out

    def test_a_script_tag_is_rendered_under_the_active_request(self) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url=JS_URL, kind="js"))
        request = RequestFactory().get("/dashboard/")

        out = self._inject(f"<body>{SCRIPTS_PLACEHOLDER}</body>", collector, request)

        assert f'<script src="{JS_URL}" data-request="/dashboard/"></script>' in out

    def test_a_render_without_a_request_says_so(self) -> None:
        """No request in scope is the caller's default, not a missing keyword."""
        collector = StaticCollector()
        collector.add(StaticAsset(url=CSS_URL, kind="css"))

        with override_settings(NEXT_FRAMEWORK=REQUEST_RECORDING_BACKENDS):
            out = StaticManager().inject(
                f"<head>{STYLES_PLACEHOLDER}</head>", collector
            )

        assert f'<link href="{CSS_URL}" data-request="none">' in out


class TestBackendRewritesEveryAssetUrl:
    """A backend that rewrites `asset_url` reaches every URL the page carries.

    The runtime bundle is no collected asset, so a composed hook must rewrite it too.
    """

    @staticmethod
    def _inject(backends, collector, request) -> str:
        html = (
            f"<head>{STYLES_PLACEHOLDER}{HEAD_CLOSE}</head>"
            f"<body>{SCRIPTS_PLACEHOLDER}</body>"
        )
        with (
            override_settings(NEXT_FRAMEWORK=backends),
            mock.patch(
                "next.static.manager.staticfiles_storage.url", return_value=NEXT_JS_URL
            ),
        ):
            return StaticManager().inject(html, collector, request=request)

    def test_the_configured_backend_is_the_prefixing_one(self) -> None:
        with override_settings(NEXT_FRAMEWORK=PREFIXED_BACKENDS):
            assert isinstance(StaticManager().default_backend, PrefixingStaticBackend)

    @REWRITING_BACKENDS
    def test_runtime_script_tag_carries_the_rewritten_url(
        self, backends: dict, prefix: str
    ) -> None:
        out = self._inject(backends, StaticCollector(), RequestFactory().get("/"))
        assert f'<script src="{prefix}{NEXT_JS_URL}"></script>' in out
        assert f'<script src="{NEXT_JS_URL}"></script>' not in out

    @REWRITING_BACKENDS
    def test_preload_hint_carries_the_rewritten_url(
        self, backends: dict, prefix: str
    ) -> None:
        out = self._inject(backends, StaticCollector(), RequestFactory().get("/"))
        assert f'<link rel="preload" as="script" href="{prefix}{NEXT_JS_URL}">' in out
        assert f'href="{NEXT_JS_URL}"' not in out

    @REWRITING_BACKENDS
    def test_collected_asset_urls_carry_the_rewritten_url(
        self, backends: dict, prefix: str
    ) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url="/static/next/a.css", kind="css"))
        collector.add(StaticAsset(url="/static/next/a.js", kind="js"))
        out = self._inject(backends, collector, RequestFactory().get("/"))
        assert f'href="{prefix}/static/next/a.css"' in out
        assert f'src="{prefix}/static/next/a.js"' in out

    @REWRITING_BACKENDS
    def test_without_a_request_the_urls_stay_untouched(
        self, backends: dict, prefix: str
    ) -> None:
        out = self._inject(backends, StaticCollector(), None)
        assert f'<script src="{NEXT_JS_URL}"></script>' in out
        assert prefix not in out

    def test_disabled_policy_never_asks_the_backend_for_the_runtime_url(self) -> None:
        with override_settings(NEXT_FRAMEWORK={**PREFIXED_BACKENDS, **DISABLED}):
            manager = StaticManager()
            with mock.patch.object(
                manager.default_backend,
                "asset_url",
                wraps=manager.default_backend.asset_url,
            ) as asset_url:
                manager.inject(
                    f"<head>{HEAD_CLOSE}</head><body>{SCRIPTS_PLACEHOLDER}</body>",
                    StaticCollector(),
                    request=RequestFactory().get("/"),
                )
        assert asset_url.call_count == 0

    def test_the_runtime_url_is_resolved_once_for_the_whole_render(self) -> None:
        """The tag and its preload share one call, the scripts chunk takes the other."""
        with override_settings(NEXT_FRAMEWORK=PREFIXED_BACKENDS):
            manager = StaticManager()
            with mock.patch.object(
                manager.default_backend,
                "asset_url",
                wraps=manager.default_backend.asset_url,
            ) as asset_url:
                out = manager.inject(
                    f"<head>{HEAD_CLOSE}</head><body>{SCRIPTS_PLACEHOLDER}</body>",
                    StaticCollector(),
                    request=RequestFactory().get("/"),
                )
        assert asset_url.call_args_list == [
            mock.call(NEXT_JS_URL, request=mock.ANY),
            mock.call("/static/next/next.scripts.min.js", request=mock.ANY),
            mock.call("/static/next/next.stream.min.js", request=mock.ANY),
            mock.call("/static/next/next.csrf.min.js", request=mock.ANY),
            mock.call("/static/next/next.poll.min.js", request=mock.ANY),
            mock.call("/static/next/next.forms.min.js", request=mock.ANY),
            mock.call("/static/next/next.layers.min.js", request=mock.ANY),
        ]
        assert f'<script src="/pfx{NEXT_JS_URL}"></script>' in out
        assert f'<link rel="preload" as="script" href="/pfx{NEXT_JS_URL}">' in out


def _hashed_without(*missing: str):
    def url(name: str) -> str:
        if name in missing:
            msg = f"Missing staticfiles manifest entry for '{name}'"
            raise ValueError(msg)
        return f"/static/{name}"

    return url


def _records(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "next.static.manager"]


class TestBrokenRuntimeConfiguration:
    """A runtime the storage lacks or an unknown policy still renders the page."""

    PAGE = f"<head></head><body>{SCRIPTS_PLACEHOLDER}</body>"

    @pytest.fixture(autouse=True)
    def _rearmed(self):
        manager_failures.clear()
        yield
        manager_failures.clear()

    def test_an_uncollected_runtime_renders_without_it_logged_once(
        self, caplog
    ) -> None:
        with mock.patch(
            "next.static.manager.staticfiles_storage.url",
            side_effect=_hashed_without("next/next.min.js"),
        ):
            pages = [
                StaticManager().inject(self.PAGE, StaticCollector()) for _ in range(2)
            ]
        assert all(
            "next.min.js" not in page and "Next._init" not in page for page in pages
        )
        [message] = _records(caplog)
        assert "has no next/next.min.js" in message
        assert "collectstatic" in message

    def test_an_uncollected_runtime_raises_under_debug(self) -> None:
        with (
            override_settings(DEBUG=True),
            mock.patch(
                "next.static.manager.staticfiles_storage.url",
                side_effect=_hashed_without("next/next.min.js"),
            ),
            pytest.raises(ValueError, match="manifest") as raised,
        ):
            StaticManager().inject(self.PAGE, StaticCollector())
        assert "collectstatic" in raised.value.__notes__[0]

    def test_an_uncollected_chunk_is_left_to_the_runtime(self, caplog) -> None:
        with mock.patch(
            "next.static.manager.staticfiles_storage.url",
            side_effect=_hashed_without("next/next.stream.min.js"),
        ):
            page = StaticManager().inject(self.PAGE, StaticCollector())
        assert '"stream":"/static/next/next.stream.min.js"' not in page
        assert '"poll":"/static/next/next.poll.min.js"' in page
        [message] = _records(caplog)
        assert "next/next.stream.min.js" in message

    def test_an_unknown_policy_injects_as_auto_logged_once(self, caplog) -> None:
        with override_next_settings(NEXT_JS_OPTIONS={"policy": "sometimes"}):
            pages = [
                StaticManager().inject(self.PAGE, StaticCollector()) for _ in range(2)
            ]
        assert all(NEXT_JS_URL in page for page in pages)
        [message] = _records(caplog)
        assert "NEXT_JS_OPTIONS['policy']" in message

    def test_an_unknown_policy_raises_under_debug(self) -> None:
        with (
            override_settings(DEBUG=True),
            override_next_settings(NEXT_JS_OPTIONS={"policy": "sometimes"}),
            pytest.raises(ValueError, match="Invalid NextScriptBuilder policy"),
        ):
            StaticManager().inject(self.PAGE, StaticCollector())


class TestRendererWithoutTheNonceKeyword:
    """A renderer written before the nonce keyword serves every page without one."""

    def _inject(self, nonce: str | None) -> str:
        collector = StaticCollector()
        collector.add(StaticAsset(url=JS_URL, kind="js"))
        with (
            override_settings(NEXT_FRAMEWORK={**REQUEST_ONLY_BACKENDS, **DISABLED}),
            mock.patch("next.static.inject.resolve_nonce", return_value=nonce),
        ):
            return StaticManager().inject(
                f"<body>{SCRIPTS_PLACEHOLDER}</body>",
                collector,
                request=RequestFactory().get("/"),
            )

    def test_a_page_without_a_nonce_renders(self) -> None:
        assert f'<script src="{JS_URL}" data-old></script>' in self._inject(None)

    def test_a_page_with_a_nonce_still_fails(self) -> None:
        with pytest.raises(TypeError, match="nonce"):
            self._inject("n0")


class TestTheNonceIsResolvedOnDemand:
    """The injector asks for the nonce only when it writes a tag that carries one."""

    def _resolved(self, html: str, collector: StaticCollector, **framework) -> int:
        with (
            override_settings(NEXT_FRAMEWORK=framework),
            mock.patch(
                "next.static.inject.resolve_nonce", return_value=None
            ) as resolve,
        ):
            StaticManager().inject(html, collector, request=RequestFactory().get("/"))
        return resolve.call_count

    def test_a_render_that_writes_no_tag_never_asks(self) -> None:
        html = f"<head>{STYLES_PLACEHOLDER}{HEAD_CLOSE}</head>{SCRIPTS_PLACEHOLDER}"
        assert self._resolved(html, StaticCollector(), **DISABLED) == 0

    def test_a_written_tag_asks(self) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        html = f"<head>{STYLES_PLACEHOLDER}</head>"
        assert self._resolved(html, collector, **DISABLED) == 1


class _NotASerializer:
    """A class `JS_CONTEXT_SERIALIZER` may name by mistake."""


class _Clock:
    """A monotonic clock a test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class TestMemoisedFallbacksDegradeEveryRender:
    """A fallback a contained failure built marks every render that reuses it.

    Each case renders twice, moves the clock past the quiet period, and renders again.
    """

    PAGE = f"<head>{HEAD_CLOSE}</head><body>{SCRIPTS_PLACEHOLDER}</body>"

    @pytest.fixture(autouse=True)
    def _rearmed(self):
        manager_failures.clear()
        yield
        manager_failures.clear()

    @pytest.fixture(autouse=True)
    def _clock(self, monkeypatch: pytest.MonkeyPatch) -> _Clock:
        held = _Clock()
        monkeypatch.setattr("next.diagnostics.monotonic", held)
        self.clock = held
        return held

    def _degraded_renders(self) -> list[bool]:
        """Inject through one manager, each render watched on its own."""
        manager = StaticManager()

        def render() -> bool:
            watch_degraded()
            manager.inject(self.PAGE, StaticCollector())
            return degraded()

        renders = [copy_context().run(render) for _ in range(2)]
        self.clock.now += QUIET_PERIOD
        return [*renders, copy_context().run(render)]

    @staticmethod
    def _assert_logged_again(caplog: pytest.LogCaptureFixture, reused: str) -> None:
        """Assert one error at build time and one warning after the quiet period."""
        records = [r for r in caplog.records if r.name.startswith("next.static")]
        assert [r.levelname for r in records] == ["ERROR", "WARNING"]
        assert reused in records[1].getMessage()
        assert records[1].suppressed > 0

    def test_a_sound_configuration_degrades_nothing(self, caplog) -> None:
        with mock.patch(
            "next.static.manager.staticfiles_storage.url", side_effect=_storage_url
        ):
            assert self._degraded_renders() == [False, False, False]
        assert not caplog.records

    def test_an_unknown_policy(self, caplog) -> None:
        with override_next_settings(NEXT_JS_OPTIONS={"policy": "sometimes"}):
            assert self._degraded_renders() == [True, True, True]
        self._assert_logged_again(caplog, "script builder still holds a default")

    def test_an_uncollected_runtime(self, caplog) -> None:
        with mock.patch(
            "next.static.manager.staticfiles_storage.url",
            side_effect=_hashed_without("next/next.min.js"),
        ):
            assert self._degraded_renders() == [True, True, True]
        self._assert_logged_again(caplog, "missing next/next.min.js")

    def test_an_uncollected_chunk_warns_without_degrading(self, caplog) -> None:
        """The HTML is complete without a lazy chunk, so the render stays shareable."""
        with mock.patch(
            "next.static.manager.staticfiles_storage.url",
            side_effect=_hashed_without("next/next.stream.min.js"),
        ):
            assert self._degraded_renders() == [False, False, False]
        records = [r for r in caplog.records if r.name.startswith("next.static")]
        assert [r.levelname for r in records] == ["WARNING", "WARNING"]
        assert "has no next/next.stream.min.js" in records[0].getMessage()
        assert records[1].suppressed > 0

    def test_a_broken_serializer(self, caplog) -> None:
        path = f"{__name__}._NotASerializer"
        with (
            override_next_settings(JS_CONTEXT_SERIALIZER=path),
            mock.patch(
                "next.static.manager.staticfiles_storage.url", side_effect=_storage_url
            ),
        ):
            assert self._degraded_renders() == [True, True, True]
        self._assert_logged_again(caplog, "still cannot serialize")

    def test_a_backend_tag_template_that_cannot_format(self, caplog) -> None:
        backends = [
            {
                "BACKEND": "next.static.StaticFilesBackend",
                "OPTIONS": {"js_tag": "<script src={url}{oops}>"},
            }
        ]
        with (
            override_next_settings(STATIC_BACKENDS=backends),
            mock.patch(
                "next.static.manager.staticfiles_storage.url", side_effect=_storage_url
            ),
        ):
            assert self._degraded_renders() == [True, True, True]
        self._assert_logged_again(caplog, "static backend list still holds a default")


class TestChunkUrlsWithoutARewrite:
    """Without a per-request rewrite the chunk URLs are built once per storage."""

    def test_a_later_render_asks_the_pipeline_only_for_the_runtime(self) -> None:
        manager = StaticManager()
        html = f"<body>{SCRIPTS_PLACEHOLDER}</body>"
        with (
            override_next_settings(STATIC_VERSION="v1"),
            mock.patch(
                "next.static.manager.staticfiles_storage.url", side_effect=_storage_url
            ),
        ):
            first = manager.inject(html, StaticCollector())
            with mock.patch.object(
                manager, "asset_url", wraps=manager.asset_url
            ) as asset_url:
                second = manager.inject(html, StaticCollector())
        assert second == first
        assert '"poll":"/static/next/next.poll.min.js?v=v1"' in second
        assert asset_url.call_args_list == [mock.call(NEXT_JS_URL, request=None)]


BOUNDARIES_URL = "/static/next/next.boundaries.min.js"
RELOAD_URL = "/static/next/next.reload.min.js"
MORE_URL = "/static/next/next.more.min.js"


class TestDeclaredBundles:
    """A needed bundle is declared by a tag between the runtime and the init script."""

    PAGE = f"<body>{SCRIPTS_PLACEHOLDER}</body>"

    @pytest.fixture(autouse=True)
    def _rearmed(self):
        manager_failures.clear()
        dropped_bundles.clear()
        yield
        manager_failures.clear()
        dropped_bundles.clear()

    def _inject(
        self,
        manager: StaticManager,
        needs: tuple[str, ...],
        *,
        urgent: bool = False,
        streams: bool = False,
        page: str = PAGE,
    ) -> tuple[StaticCollector, str]:
        with mock.patch(
            "next.static.manager.staticfiles_storage.url", side_effect=_storage_url
        ):
            collector = manager.create_collector()
            collector.add(StaticAsset(url=JS_URL, kind="js"))
            if streams:
                collector.note(SPLIT_NOTE, None)
            for name in needs:
                collector.require(name)
            if urgent:
                collector.require(needs[0], urgent=True)
            return collector, manager.inject(page, collector)

    def _marked(
        self,
        manager: StaticManager,
        needs: tuple[str, ...],
        *,
        urgent: bool = False,
        page: str = PAGE,
    ) -> tuple[StaticCollector, str]:
        token = partial_streams.set(True)
        try:
            return self._inject(manager, needs, urgent=urgent, page=page)
        finally:
            partial_streams.reset(token)

    def test_a_need_lands_after_the_runtime_and_before_the_init(
        self, fresh_manager: StaticManager
    ) -> None:
        _, out = self._inject(fresh_manager, ("boundaries",))
        runtime = f'<script src="{NEXT_JS_URL}"></script>\n'
        bundle = f'<script async fetchpriority="low" src="{BOUNDARIES_URL}"></script>\n'
        assert f"{runtime}{bundle}<script data-next-init>Next._init(" in out
        assert out.index("Next._init(") < out.index(JS_URL)
        assert out.count(BOUNDARIES_URL) == 1

    def test_urgency_defers_every_bundle_of_the_page(
        self, fresh_manager: StaticManager
    ) -> None:
        _, out = self._inject(
            fresh_manager, ("more", "boundaries", "reload"), urgent=True
        )
        assert (
            f'<script defer src="{MORE_URL}"></script>\n'
            f'<script defer src="{BOUNDARIES_URL}"></script>\n'
            f'<script defer src="{RELOAD_URL}"></script>\n'
            "<script data-next-init>Next._init("
        ) in out
        assert "async" not in out

    def test_a_streamed_page_loads_the_reader_and_urgent_bundles_at_once(
        self, fresh_manager: StaticManager
    ) -> None:
        _, out = self._inject(
            fresh_manager, ("boundaries", "reload"), urgent=True, streams=True
        )
        assert (
            f'<script async src="{BOUNDARIES_URL}"></script>\n'
            f'<script async src="{RELOAD_URL}"></script>\n'
            "<script data-next-init>Next._init("
        ) in out
        assert " defer" not in out

    def test_a_streamed_page_keeps_a_bundle_no_boundary_hurries_lazy(
        self, fresh_manager: StaticManager
    ) -> None:
        _, out = self._inject(fresh_manager, ("boundaries", "more"), streams=True)
        assert f'<script async src="{BOUNDARIES_URL}"></script>\n' in out
        assert f'<script async fetchpriority="low" src="{MORE_URL}"></script>' in out

    def test_a_page_marked_for_streamed_partials_requires_the_reader(
        self, fresh_manager: StaticManager
    ) -> None:
        collector, out = self._marked(fresh_manager, ("boundaries",), urgent=True)
        assert collector.notes(BUNDLE_NOTE) == [BOUNDARIES_URL]
        assert (
            f'<script defer src="{BOUNDARIES_URL}"></script>\n<script data-next-init>Next._init('
        ) in out
        assert '"$stream":true' in out

    def test_a_page_without_the_runtime_slot_requires_no_reader(
        self, fresh_manager: StaticManager
    ) -> None:
        collector, out = self._marked(fresh_manager, (), page="<body></body>")
        assert collector.notes(BUNDLE_NOTE) == ()
        assert "next.boundaries" not in out

    def test_a_manual_page_marked_for_streamed_partials_requires_no_reader(
        self, fresh_manager: StaticManager
    ) -> None:
        with override_settings(NEXT_FRAMEWORK=MANUAL):
            collector, out = self._marked(fresh_manager, ())
        assert collector.notes(BUNDLE_NOTE) == ()
        assert "next.boundaries" not in out

    def test_no_need_renders_the_scripts_slot_unchanged(
        self, fresh_manager: StaticManager
    ) -> None:
        _, out = self._inject(fresh_manager, ())
        baseline = StaticCollector()
        baseline.add(StaticAsset(url=JS_URL, kind="js"))
        with mock.patch(
            "next.static.manager.staticfiles_storage.url", side_effect=_storage_url
        ):
            expected = fresh_manager.inject(self.PAGE, baseline)
        assert out == expected
        assert "next.boundaries" not in out

    def test_the_payload_never_lists_a_declared_bundle(
        self, fresh_manager: StaticManager
    ) -> None:
        _, out = self._inject(fresh_manager, ("boundaries", "reload", "more"))
        assert f"Next._init({{{CHUNKS}{WIRE}}})" in out

    def test_the_tags_carry_the_page_nonce(self, fresh_manager: StaticManager) -> None:
        with mock.patch("next.static.inject.resolve_nonce", return_value="n0"):
            _, out = self._inject(fresh_manager, ("reload",))
        assert (
            f'<script async fetchpriority="low" src="{RELOAD_URL}" nonce="n0"></script>'
        ) in out

    def test_without_the_runtime_no_bundle_renders(
        self, fresh_manager: StaticManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG, logger="next.static.inject")
        with override_settings(NEXT_FRAMEWORK=DISABLED):
            _, out = self._inject(fresh_manager, ("boundaries",))
        assert "next.boundaries" not in out
        assert JS_URL in out
        [record] = [r for r in caplog.records if r.name == "next.static.inject"]
        assert record.levelno == logging.DEBUG
        assert BOUNDARIES_URL in record.getMessage()

    def test_a_manual_page_declares_them_before_its_scripts(
        self, fresh_manager: StaticManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG, logger="next.static.inject")
        with override_settings(NEXT_FRAMEWORK=MANUAL):
            _, out = self._inject(fresh_manager, ("boundaries",), urgent=True)
        assert out.startswith(f'<body><script defer src="{BOUNDARIES_URL}"></script>\n')
        assert out.index(BOUNDARIES_URL) < out.index(JS_URL)
        assert "Next._init(" not in out
        assert not [r for r in caplog.records if r.name == "next.static.inject"]

    def test_a_page_without_the_scripts_slot_logs_the_drop(
        self, fresh_manager: StaticManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.DEBUG, logger="next.static.inject")
        with mock.patch(
            "next.static.manager.staticfiles_storage.url", side_effect=_storage_url
        ):
            collector = fresh_manager.create_collector()
            collector.require("reload")
            out = fresh_manager.inject("<body></body>", collector)
        assert out == "<body></body>"
        [record] = [r for r in caplog.records if r.name == "next.static.inject"]
        assert RELOAD_URL in record.getMessage()

    def test_an_unbuilt_bundle_is_left_out(self, caplog) -> None:
        with mock.patch(
            "next.static.manager.staticfiles_storage.url",
            side_effect=_hashed_without("next/next.boundaries.min.js"),
        ):
            manager = StaticManager()
            collector = manager.create_collector()
            collector.require("boundaries", urgent=True)
            out = manager.inject(self.PAGE, collector)
        assert "next.boundaries" not in out
        assert "<script data-next-init>Next._init(" in out
        [message] = _records(caplog)
        assert "next/next.boundaries.min.js" in message


ROUTER_URL = "/static/next/next.router.min.js"
INTENT_URL = "/static/next/next.intent.min.js"


class _HeadScripts:
    name = "head"
    priority = 500
    keys = frozenset({"$head"})

    def applies(self) -> bool:
        return True

    def share(self, render: object) -> ScriptShare:
        return ScriptShare(head="<script data-h></script>")


class TestRoutedRender:
    """Under the client router the injector encloses its output and loads the router."""

    PAGE = f"<head>{STYLES_PLACEHOLDER}</head><body>{SCRIPTS_PLACEHOLDER}</body>"

    @pytest.fixture(autouse=True)
    def router_on(self) -> Iterator[None]:
        with override_next_settings(ROUTER={"ENABLED": True}):
            yield

    def _inject(
        self, manager: StaticManager, routed: RouterRender, *, streams: bool = False
    ) -> tuple[StaticCollector, str]:
        token = router_render.set(routed)
        try:
            with mock.patch(
                "next.static.manager.staticfiles_storage.url", side_effect=_storage_url
            ):
                collector = manager.create_collector()
                collector.add(StaticAsset(url=CSS_URL, kind="css"))
                if streams:
                    collector.note(SPLIT_NOTE, None)
                return collector, manager.inject(self.PAGE, collector)
        finally:
            router_render.reset(token)

    def test_every_filled_slot_is_enclosed(self, fresh_manager: StaticManager) -> None:
        finished: list[str] = []

        def finish(html: str) -> str:
            finished.append(html)
            return html.replace(routed.mark, "[]")

        routed = RouterRender("5ea1", '{"scope":"all"}', finish)
        collector, out = self._inject(fresh_manager, routed)
        assert "<!--nx:a 5ea1--><link" in out
        assert out.count("<!--/nx:a 5ea1-->") == 2
        assert f'<script async src="{ROUTER_URL}"></script>' in out
        assert (
            f'"$router":{{"scope":"all","boundaries":"{BOUNDARIES_URL}",'
            f'"intent":"{INTENT_URL}"}}}});</script>'
            '<script type="application/json" data-next-layouts>[]</script>'
        ) in out
        assert f'src="{BOUNDARIES_URL}"' not in out
        assert f'src="{INTENT_URL}"' not in out
        assert routed.boundaries == BOUNDARIES_URL
        assert routed.intent == INTENT_URL
        assert routed.collector is collector
        assert finished
        assert routed.mark in finished[0]

    @pytest.mark.parametrize(
        ("missing", "attr", "fields"),
        [
            pytest.param(
                "next/next.boundaries.min.js",
                "boundaries",
                f'"intent":"{INTENT_URL}"',
                id="boundaries",
            ),
            pytest.param(
                "next/next.intent.min.js",
                "intent",
                f'"boundaries":"{BOUNDARIES_URL}"',
                id="intent",
            ),
        ],
    )
    def test_a_storage_without_a_bundle_leaves_it_out(
        self, missing: str, attr: str, fields: str
    ) -> None:
        routed = RouterRender("5ea1", '{"scope":"all"}')
        token = router_render.set(routed)
        try:
            with mock.patch(
                "next.static.manager.staticfiles_storage.url",
                side_effect=_hashed_without(missing),
            ):
                manager = StaticManager()
                out = manager.inject(self.PAGE, manager.create_collector())
        finally:
            router_render.reset(token)
        assert f'"$router":{{"scope":"all",{fields}}}' in out
        assert [
            name for name in ("boundaries", "intent") if getattr(routed, name) is None
        ] == [attr]

    def test_a_collector_without_bundles_names_no_boundaries(
        self, fresh_manager: StaticManager
    ) -> None:
        routed = RouterRender("5ea1", '{"scope":"all"}')
        token = router_render.set(routed)
        try:
            with mock.patch(
                "next.static.manager.staticfiles_storage.url", side_effect=_storage_url
            ):
                out = fresh_manager.inject(self.PAGE, StaticCollector())
        finally:
            router_render.reset(token)
        assert routed.boundaries is None
        assert routed.intent is None
        assert '"$router":{"scope":"all"}' in out

    def test_a_page_out_of_snapshots_says_so_in_the_payload(
        self, fresh_manager: StaticManager
    ) -> None:
        routed = RouterRender("5ea1", '{"scope":"all"}', snapshot=False)
        _collector, out = self._inject(fresh_manager, routed)
        assert f'"intent":"{INTENT_URL}","snapshot":false}}' in out

    def test_a_stream_keeps_its_split_inside_the_enclosed_scripts(
        self, fresh_manager: StaticManager
    ) -> None:
        routed = RouterRender("5ea1", "{}")
        collector, out = self._inject(fresh_manager, routed, streams=True)
        split = split_of(collector)
        assert split is not None
        assert out[split:] == "</body>"
        assert out[:split].endswith("<!--/nx:a 5ea1-->")

    def test_the_unrouted_pass_stays_off_a_routed_render(
        self, fresh_manager: StaticManager
    ) -> None:
        unrouted: list[str] = []

        def finish(html: str) -> str:
            unrouted.append(html)
            return html

        token = html_finish.set(finish)
        try:
            self._inject(fresh_manager, RouterRender("5ea1", "{}", str.strip))
        finally:
            html_finish.reset(token)
        assert unrouted == []

    def test_an_empty_slot_is_left_bare(self, fresh_manager: StaticManager) -> None:
        routed = RouterRender("5ea1", "{}")
        token = router_render.set(routed)
        try:
            out = fresh_manager.inject("<p>x</p>", fresh_manager.create_collector())
        finally:
            router_render.reset(token)
        assert out == "<p>x</p>"

    def _routed(
        self, manager: StaticManager, routed: RouterRender, collector: StaticCollector
    ) -> str:
        token = router_render.set(routed)
        try:
            with mock.patch(
                "next.static.manager.staticfiles_storage.url", side_effect=_storage_url
            ):
                return manager.inject(self.PAGE, collector)
        finally:
            router_render.reset(token)

    def test_page_stylesheets_carry_the_asset_attribute(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        collector.add(StaticAsset(url="", kind="css", inline="p{color:red}"))
        collector.add(StaticAsset(url=JS_URL, kind="js"))
        out = self._routed(fresh_manager, RouterRender("5ea1", "{}"), collector)
        assert f'<link data-next-asset="page" rel="stylesheet" href="{CSS_URL}">' in out
        assert '<style data-next-asset="page">p{color:red}</style>' in out
        assert f'<script src="{JS_URL}"></script>' in out

    def test_a_tag_without_an_element_stays_as_rendered(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        with mock.patch.object(
            fresh_manager.default_backend, "render_link_tag", return_value="plain"
        ):
            out = self._routed(fresh_manager, RouterRender("5ea1", "{}"), collector)
        assert "<!--nx:a 5ea1-->plain<!--/nx:a 5ea1-->" in out

    def test_the_head_carries_the_speculation_rules(
        self, fresh_manager: StaticManager
    ) -> None:
        routed = RouterRender("5ea1", "{}", speculation='{"prefetch":[]}')
        out = self._routed(fresh_manager, routed, StaticCollector())
        head = out.split("</head>")[0]
        assert (
            '<!--nx:a 5ea1--><script type="speculationrules">{"prefetch":[]}</script>'
            "<!--/nx:a 5ea1-->"
        ) in head

    def test_the_rules_come_before_the_head_scripts(
        self, fresh_manager: StaticManager
    ) -> None:
        listed = PortList[Any]()
        listed.register(_HeadScripts())
        listed.register(RouterScripts())
        routed = RouterRender("5ea1", "{}", speculation="{}")
        with mock.patch("next.static.inject.page_scripts", listed):
            out = self._routed(fresh_manager, routed, StaticCollector())
        assert (
            '<script type="speculationrules">{}</script>\n<script data-h></script>'
        ) in out


class _Provider:
    """A page script provider handing back a fixed share, counted per call."""

    def __init__(
        self,
        name: str,
        priority: int,
        share: ScriptShare | None,
        *,
        applies: bool = True,
        keys: frozenset[str] = frozenset(),
    ) -> None:
        self.name = name
        self.priority = priority
        self.keys = keys
        self._share = share
        self._applies = applies
        self.applied = 0
        self.seen: list[object] = []

    def applies(self) -> bool:
        self.applied += 1
        return self._applies

    def share(self, render: object) -> ScriptShare | None:
        self.seen.append(render)
        return self._share


class TestPageScriptProviders:
    """The injector runs every provider in rank order and knows none of them."""

    PAGE = (
        f"<head>{STYLES_PLACEHOLDER}</head><body><p>x</p>{SCRIPTS_PLACEHOLDER}</body>"
    )

    def _inject(
        self,
        manager: StaticManager,
        *providers: _Provider,
        collector: StaticCollector | None = None,
        page: str = PAGE,
    ) -> str:
        listed = PortList[Any]()
        for provider in providers:
            listed.register(provider)
        with (
            mock.patch("next.static.inject.page_scripts", listed),
            mock.patch(
                "next.static.manager.staticfiles_storage.url", side_effect=_storage_url
            ),
        ):
            return manager.inject(page, collector or manager.create_collector())

    def test_the_parts_of_every_share_merge_in_rank_order(
        self, fresh_manager: StaticManager
    ) -> None:
        late = _Provider(
            "late",
            2,
            ScriptShare(
                head="<meta late>",
                entries={"$late": 2},
                shared='"$b":2',
                after=lambda nonce: f"<i late {nonce}>",
            ),
        )
        early = _Provider(
            "early",
            1,
            ScriptShare(
                head="<meta early>",
                entries={"$early": 1},
                shared='"$a":1',
                after="<i early>".format,
            ),
        )
        out = self._inject(fresh_manager, late, early)
        assert "<meta early>\n<meta late>\n" in out.split("</head>")[0]
        payload = out.split("Next._init(", 1)[1].split(");</script>", 1)[0]
        assert list(json.loads(payload)) == ["$early", "$late", "$chunks", "$a", "$b"]
        assert ");</script><i early><i late None>\n" in out

    def test_a_provider_that_does_not_apply_is_never_asked(
        self, fresh_manager: StaticManager
    ) -> None:
        off = _Provider("off", 1, ScriptShare(head="<meta off>"), applies=False)
        first = self._inject(fresh_manager, off)
        listed = PortList[Any]()
        listed.register(off)
        with mock.patch("next.static.inject.page_scripts", listed):
            fresh_manager.inject(self.PAGE, fresh_manager.create_collector())
            fresh_manager.inject(self.PAGE, fresh_manager.create_collector())
        assert "<meta off>" not in first
        assert off.seen == []
        assert off.applied == 2

    def test_a_render_every_provider_skips_keeps_the_bare_payload(
        self, fresh_manager: StaticManager
    ) -> None:
        idle = _Provider("idle", 1, None)
        out = self._inject(fresh_manager, idle)
        assert f"Next._init({{{CHUNKS}}})" in out
        assert len(idle.seen) == 1

    def test_the_render_names_the_page_and_its_runtime(
        self, fresh_manager: StaticManager
    ) -> None:
        reader = _Provider("reader", 1, None)
        collector = fresh_manager.create_collector()
        collector.note(SPLIT_NOTE, None)
        self._inject(fresh_manager, reader, collector=collector)
        [render] = reader.seen
        assert render.collector is collector
        assert render.runtime is True
        assert render.streams is True
        assert render.page_path is None
        assert render.request is None
        assert render.bundle_url("router") == ROUTER_URL

    def test_a_render_without_bundles_resolves_none(
        self, fresh_manager: StaticManager
    ) -> None:
        reader = _Provider("reader", 1, ScriptShare(eager=("router",)))
        collector = StaticCollector()
        out = self._inject(fresh_manager, reader, collector=collector)
        [render] = reader.seen
        assert render.bundle_url("router") is None
        assert "next.router" not in out

    def test_a_provider_key_is_kept_from_the_page_context(
        self, fresh_manager: StaticManager
    ) -> None:
        owner = _Provider(
            "owner", 1, ScriptShare(entries={"$own": "x"}), keys=frozenset({"$own"})
        )
        collector = fresh_manager.create_collector()
        collector.add_js_context("$own", "page")
        out = self._inject(fresh_manager, owner, collector=collector)
        assert '"$own":"x"' in out
        assert '"page"' not in out
        assert owner.seen[0].entries == {"$own": "x"}

    def test_the_eager_bundles_open_the_declared_ones(
        self, fresh_manager: StaticManager
    ) -> None:
        needs = _Provider("needs", 1, ScriptShare(bundles=("more",), eager=("router",)))
        out = self._inject(fresh_manager, needs)
        router = f'<script async src="{ROUTER_URL}"></script>\n'
        more = '<script async fetchpriority="low" src="/static/next/next.more.min.js">'
        assert f"{router}{more}" in out

    def test_a_listed_chunk_joins_the_chunks(
        self, fresh_manager: StaticManager
    ) -> None:
        lister = _Provider("lister", 1, ScriptShare(listed=frozenset({"ws"})))
        out = self._inject(fresh_manager, lister)
        assert '"ws":"/static/next/next.ws.min.js"' in out

    def test_enclosures_stylesheet_marks_and_passes_run_in_rank_order(
        self, fresh_manager: StaticManager
    ) -> None:
        def finish(tag: str) -> Callable[[str], str]:
            return lambda html: f"{html}<{tag}>"

        first = _Provider(
            "first",
            1,
            ScriptShare(enclose="[{}]".format, finish=finish("a"), stylesheet=" a"),
        )
        second = _Provider(
            "second",
            2,
            ScriptShare(enclose="({})".format, finish=finish("b"), stylesheet=" b"),
        )
        collector = fresh_manager.create_collector()
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        out = self._inject(fresh_manager, first, second, collector=collector)
        assert f'([<link a b rel="stylesheet" href="{CSS_URL}">])' in out
        assert out.endswith("</body><a><b>")

    def test_a_replaced_list_is_read_afresh(self, fresh_manager: StaticManager) -> None:
        one = _Provider("one", 1, ScriptShare(head="<meta one>"))
        two = _Provider("two", 1, ScriptShare(head="<meta two>"))
        assert "<meta one>" in self._inject(fresh_manager, one)
        assert "<meta two>" in self._inject(fresh_manager, two)

    def test_a_share_without_an_enclosure_leaves_the_slots_bare(
        self, fresh_manager: StaticManager
    ) -> None:
        marker = _Provider("marker", 1, ScriptShare(stylesheet=" m", finish=str.strip))
        collector = fresh_manager.create_collector()
        collector.add(StaticAsset(url=CSS_URL, kind="css"))
        out = self._inject(fresh_manager, marker, collector=collector)
        assert f'<link m rel="stylesheet" href="{CSS_URL}">' in out
        assert out == out.strip()
