from __future__ import annotations

import json
from functools import partial
from typing import TYPE_CHECKING, Any
from unittest import mock

import pytest
from django.test import RequestFactory, override_settings

from next.static import (
    StaticAsset,
    StaticCollector,
    StaticFilesBackend,
    StaticManager,
    default_kinds,
)
from next.static.collector import HEAD_CLOSE
from next.static.runtime import CSRF_PAYLOAD_KEY, DEV_PAYLOAD_KEY
from next.static.serializers import JsonJsContextSerializer
from tests.support import (
    PREFIXED_BACKENDS,
    REQUEST_RECORDING_BACKENDS,
    PrefixingStaticBackend,
    restored_static_registries,
)


if TYPE_CHECKING:
    from collections.abc import Mapping

    from django.http import HttpRequest


STYLES_PLACEHOLDER = "<!-- next:styles -->"
SCRIPTS_PLACEHOLDER = "<!-- next:scripts -->"
CSS_URL = "https://cdn.example.com/a.css"
JS_URL = "https://cdn.example.com/a.js"
NEXT_JS_URL = "/static/next/next.min.js"
CHUNKS = '"$chunks":{"scripts":"/static/next/next.scripts.min.js"}'
DEV_CHUNKS = (
    '"$chunks":{"scripts":"/static/next/next.scripts.min.js",'
    '"dev":"/static/next/next.dev.min.js"}'
)
HASHED = {
    "next/next.min.js": "/static/next/next.min.1a2b.js",
    "next/next.scripts.min.js": "/static/next/next.scripts.min.3c4d.js",
    "next/next.dev.min.js": "/static/next/next.dev.min.5e6f.js",
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
        assert f'Next._init({{"user":"alice",{CHUNKS}}})' in out
        assert f'<script src="{JS_URL}"></script>' in out
        assert SCRIPTS_PLACEHOLDER not in out

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

    def test_real_request_injects_csrf_payload(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        html = f"<body>{SCRIPTS_PLACEHOLDER}</body>"
        request = RequestFactory().get("/")
        with mock.patch(
            "next.static.manager.staticfiles_storage.url", side_effect=_storage_url
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
            "next.static.manager.staticfiles_storage.url", side_effect=_storage_url
        ):
            out = fresh_manager.inject(html, collector)
        assert "$csrf" not in out
        assert f"Next._init({{{CHUNKS}}})" in out


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
        assert (
            'Next._init({"$chunks":{"scripts":"/static/next/next.scripts.min.3c4d.js"}})'
        ) in first
        assert url.call_count == 2

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
            '"$chunks":{"scripts":"/static/next/next.scripts.min.3c4d.js",'
            '"dev":"/static/next/next.dev.min.5e6f.js"}'
        ) in out


class _SpacedSerializer(JsonJsContextSerializer):
    """Encodes with default separators, so its bytes differ from the default one."""

    def dumps(self, value: object) -> str:
        return json.dumps(value)


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
        assert f"Next._init({{{CHUNKS}}})" in self.inject(fresh_manager)
        assert DEV_CHUNKS in self.inject(fresh_manager, debug=True)
        assert f"Next._init({{{CHUNKS}}})" in self.inject(fresh_manager)

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
        assert '"$chunks":{"scripts":"/static/next/next.scripts.min.3c4d.js"}' in out

    def test_another_serializer_encodes_it_again(
        self, fresh_manager: StaticManager
    ) -> None:
        self.inject(fresh_manager)
        with mock.patch(
            "next.static.inject.resolve_serializer", return_value=_SpacedSerializer()
        ):
            out = self.inject(fresh_manager)
        assert '"$chunks":{"scripts": "/static/next/next.scripts.min.js"}' in out


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
        assert f'Next._init({{"$dev":true,{DEV_CHUNKS}}})' in out

    def test_debug_appends_dev_key_after_user_context(
        self, fresh_manager: StaticManager
    ) -> None:
        collector = StaticCollector()
        collector.add_js_context("user", "alice")
        out = self.inject(fresh_manager, collector, debug=True)
        assert f'Next._init({{"user":"alice","$dev":true,{DEV_CHUNKS}}})' in out

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
        assert f"Next._init({{{CHUNKS}}})" in out

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
        assert f"Next._init({{{CHUNKS}}})" in out

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
        assert f'Next._init({{"user":"alice",{CHUNKS}}})' in out

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

    The runtime bundle isn't a collected asset, so a composed hook must
    rewrite it just like a class override does.
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
        ]
        assert f'<script src="/pfx{NEXT_JS_URL}"></script>' in out
        assert f'<link rel="preload" as="script" href="/pfx{NEXT_JS_URL}">' in out
