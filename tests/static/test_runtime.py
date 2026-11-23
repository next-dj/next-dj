from __future__ import annotations

import json
from decimal import Decimal
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any
from unittest import mock

import pytest
from django.test import RequestFactory

from next.partial.manager import partial_backend_manager
from next.static import NextScriptBuilder, ScriptInjectionPolicy
from next.static.collector import StaticCollector
from next.static.runtime import (
    CHUNK_STATIC_PATHS,
    CSRF_PAYLOAD_KEY,
    DECLARED_CHUNK_KEYS,
    DEV_PAYLOAD_KEY,
    LISTED_CHUNK_KEYS,
    NEXT_JS_STATIC_PATH,
    REALTIME_PAYLOAD_KEY,
    RESERVED_PAYLOAD_KEYS,
    STREAM_PAYLOAD_KEY,
    BundleLoad,
    RealtimeOffer,
    RouterRender,
    _failures as runtime_failures,
    bundle_load,
    csrf_payload_for,
    forget_realtime_offer,
    nonce_attr,
    partial_streams,
    payload_in_script,
    realtime_offer,
    router_render,
    speculation_tag,
)
from next.static.serializers import resolve_serializer
from next.testing import override_next_settings
from tests.support.codecs import TextStreamCodec


if TYPE_CHECKING:
    from collections.abc import Mapping

    from next.static.serializers import JsContextSerializer


URL = "/static/next/next.min.js"
BREAKOUT_URL = '/static/next.min.js"><script>alert(1)</script>'


def _reference_init_payload(
    js_context: Mapping[str, Any],
    key_serializers: Mapping[str, JsContextSerializer] | None,
) -> str:
    """Reproduce the whole-dict or per-key init payload for byte-parity checks."""
    if not key_serializers:
        return resolve_serializer().dumps(dict(js_context))
    default = resolve_serializer()
    fragments: list[str] = []
    for k, v in js_context.items():
        serializer = key_serializers.get(k, default)
        encoded_key = json.dumps(k, separators=(",", ":"))
        fragments.append(f"{encoded_key}:{serializer.dumps(v)}")
    return "{" + ",".join(fragments) + "}"


class _MarkSerializer:
    """Compact per-key serializer that wraps values under a marker."""

    def dumps(self, value: object) -> str:
        """Return the value wrapped in a marker object as compact JSON."""
        return json.dumps({"mark": value}, separators=(",", ":"))


class _RawSeparatorSerializer:
    """Per-key serializer that leaves non-ASCII raw via `ensure_ascii=False`."""

    def dumps(self, value: object) -> str:
        """Return compact JSON with raw non-ASCII characters."""
        return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


class TestScriptInjectionPolicy:
    """Enum values drive conditional injection in StaticManager."""

    def test_values(self) -> None:
        assert ScriptInjectionPolicy.AUTO.value == "auto"
        assert ScriptInjectionPolicy.DISABLED.value == "disabled"
        assert ScriptInjectionPolicy.MANUAL.value == "manual"

    def test_all_members(self) -> None:
        assert {p.value for p in ScriptInjectionPolicy} == {
            "auto",
            "disabled",
            "manual",
        }


class TestNextScriptBuilderDefaults:
    """Default templates match the classic behavior."""

    def test_preload_link(self) -> None:
        builder = NextScriptBuilder(URL)
        assert builder.preload_link() == (
            f'<link rel="preload" as="script" href="{URL}">'
        )

    def test_script_tag(self) -> None:
        builder = NextScriptBuilder(URL)
        assert builder.script_tag() == f'<script src="{URL}"></script>'

    def test_init_script_passes_payload(self) -> None:
        builder = NextScriptBuilder(URL)
        out = builder.init_script({"user": "alice"})
        assert out == '<script>Next._init({"user":"alice"});</script>'

    def test_init_script_serializes_django_types(self) -> None:

        builder = NextScriptBuilder(URL)
        out = builder.init_script({"price": Decimal("1.00")})
        assert '"1.00"' in out

    def test_default_policy_is_auto(self) -> None:
        builder = NextScriptBuilder(URL)
        assert builder.policy is ScriptInjectionPolicy.AUTO

    def test_url_exposed(self) -> None:
        assert NextScriptBuilder(URL).url == URL


class TestNextScriptBuilderUrlOverride:
    """A caller may pass the URL a request-aware backend resolved."""

    def test_preload_link_takes_an_explicit_url(self) -> None:
        builder = NextScriptBuilder(URL)
        assert builder.preload_link("/pfx/next.min.js") == (
            '<link rel="preload" as="script" href="/pfx/next.min.js">'
        )

    def test_script_tag_takes_an_explicit_url(self) -> None:
        builder = NextScriptBuilder(URL)
        assert builder.script_tag("/pfx/next.min.js") == (
            '<script src="/pfx/next.min.js"></script>'
        )

    def test_explicit_url_keeps_the_custom_template(self) -> None:
        builder = NextScriptBuilder(
            URL, script_tag_template='<script defer src="{url}"></script>'
        )
        assert builder.script_tag("/pfx/next.min.js") == (
            '<script defer src="/pfx/next.min.js"></script>'
        )


class TestNextScriptBuilderNonce:
    """Every runtime tag carries the nonce of the render, escaped."""

    def test_each_default_tag_carries_it(self) -> None:
        builder = NextScriptBuilder(URL)
        assert builder.preload_link(nonce="n0") == (
            f'<link rel="preload" as="script" href="{URL}" nonce="n0">'
        )
        assert builder.script_tag(nonce="n0") == (
            f'<script src="{URL}" nonce="n0"></script>'
        )
        assert builder.init_script({}, nonce="n0") == (
            '<script nonce="n0">Next._init({});</script>'
        )

    def test_the_nonce_is_escaped(self) -> None:
        assert nonce_attr('a"b') == ' nonce="a&quot;b"'
        assert nonce_attr(None) == ""
        assert nonce_attr("") == ""

    def test_a_template_without_the_placeholder_drops_it(self) -> None:
        builder = NextScriptBuilder(
            URL, script_tag_template='<script defer src="{url}"></script>'
        )
        assert builder.script_tag(nonce="n0") == f'<script defer src="{URL}"></script>'


class TestRuntimeTagsEscapeTheUrl:
    """The runtime tags reach the page past the engine, so the URL is escaped."""

    @pytest.mark.parametrize("builder_tag", ["preload_link", "script_tag"])
    def test_a_url_closing_the_attribute_cannot_open_an_element(
        self, builder_tag
    ) -> None:
        builder = NextScriptBuilder(URL)

        rendered = getattr(builder, builder_tag)(BREAKOUT_URL)

        assert "<script>alert(1)</script>" not in rendered
        assert "&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;" in rendered

    @pytest.mark.parametrize("builder_tag", ["preload_link", "script_tag"])
    def test_the_resolved_runtime_url_is_escaped_too(self, builder_tag) -> None:
        """A backend answering the URL at construction reaches the same escape."""
        builder = NextScriptBuilder(BREAKOUT_URL)

        assert "<script>alert(1)</script>" not in getattr(builder, builder_tag)()

    @pytest.mark.parametrize("builder_tag", ["preload_link", "script_tag"])
    def test_a_non_str_url_renders_as_its_str(self, builder_tag) -> None:
        builder = NextScriptBuilder(URL)

        rendered = getattr(builder, builder_tag)(PurePosixPath("/pfx/next.min.js"))

        assert '"/pfx/next.min.js"' in rendered

    def test_a_query_ampersand_renders_as_an_entity(self) -> None:
        builder = NextScriptBuilder(URL)

        assert builder.script_tag("/static/next.min.js?v=1&x=2") == (
            '<script src="/static/next.min.js?v=1&amp;x=2"></script>'
        )


class TestNextScriptBuilderCustomTemplates:
    """Every template is an instance attribute, pluggable without subclassing."""

    def test_custom_preload(self) -> None:
        builder = NextScriptBuilder(
            URL,
            preload_template='<link data-next rel="preload" as="script" href="{url}">',
        )
        assert "data-next" in builder.preload_link()

    def test_custom_script_tag(self) -> None:
        builder = NextScriptBuilder(
            URL, script_tag_template='<script defer src="{url}"></script>'
        )
        assert builder.script_tag() == f'<script defer src="{URL}"></script>'

    def test_custom_init_template(self) -> None:
        builder = NextScriptBuilder(
            URL, init_template="<script>window.MyNext.boot({payload})</script>"
        )
        out = builder.init_script({"x": 1})
        assert out == '<script>window.MyNext.boot({"x":1})</script>'

    def test_custom_policy(self) -> None:
        builder = NextScriptBuilder(URL, policy=ScriptInjectionPolicy.DISABLED)
        assert builder.policy is ScriptInjectionPolicy.DISABLED


class TestNextScriptBuilderFromOptions:
    """OPTIONS mapping drives per-builder configuration."""

    def test_empty_options_yields_defaults(self) -> None:
        builder = NextScriptBuilder.from_options(URL, {})
        assert builder.policy is ScriptInjectionPolicy.AUTO
        assert builder.preload_link() == (
            f'<link rel="preload" as="script" href="{URL}">'
        )

    def test_none_options(self) -> None:
        builder = NextScriptBuilder.from_options(URL, None)
        assert builder.policy is ScriptInjectionPolicy.AUTO

    def test_policy_as_enum(self) -> None:
        builder = NextScriptBuilder.from_options(
            URL, {"policy": ScriptInjectionPolicy.MANUAL}
        )
        assert builder.policy is ScriptInjectionPolicy.MANUAL

    def test_policy_as_string(self) -> None:
        builder = NextScriptBuilder.from_options(URL, {"policy": "disabled"})
        assert builder.policy is ScriptInjectionPolicy.DISABLED

    def test_invalid_policy_string_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid NextScriptBuilder policy"):
            NextScriptBuilder.from_options(URL, {"policy": "bogus"})

    def test_custom_templates_via_options(self) -> None:
        builder = NextScriptBuilder.from_options(
            URL,
            {
                "preload_template": '<link data-x href="{url}">',
                "script_tag_template": '<script async src="{url}"></script>',
                "init_template": "<script>boot({payload})</script>",
            },
        )
        assert "data-x" in builder.preload_link()
        assert "async" in builder.script_tag()
        payload = json.dumps({"a": 1}, separators=(",", ":"))
        assert builder.init_script({"a": 1}) == f"<script>boot({payload})</script>"


class TestChunkScript:
    """A streamed chunk pushes its payload through the chunk template, escaped."""

    def test_the_default_template_pushes_onto_the_queue(self) -> None:
        builder = NextScriptBuilder(URL)
        assert builder.chunk_script("0", nonce="n") == (
            '<script nonce="n">(self.$nx||=[]).push(0)</script>'
        )

    def test_markup_and_line_separators_are_escaped(self) -> None:
        text = "</script><!--" + chr(0x2028)
        payload = json.dumps({"h": text}, ensure_ascii=False)
        script = NextScriptBuilder(URL).chunk_script(payload)
        body = script.removeprefix("<script>").removesuffix("</script>")
        assert "<" not in body
        assert chr(0x2028) not in body
        assert "\\u003C/script\\u003E" in body

    def test_a_chunk_template_comes_from_the_options(self) -> None:
        builder = NextScriptBuilder.from_options(
            URL, {"chunk_template": "<script{nonce_attr}>q.push({payload})</script>"}
        )
        assert builder.chunk_script("1") == "<script>q.push(1)</script>"

    def test_a_broken_chunk_template_falls_back_to_the_default(self) -> None:
        runtime_failures.clear()
        builder = NextScriptBuilder(URL, chunk_template="<script>{oops}</script>")
        assert builder.chunk_script("1") == "<script>(self.$nx||=[]).push(1)</script>"

    def test_a_payload_outside_script_text_falls_back_to_the_default(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        runtime_failures.clear()
        template = "<script{nonce_attr} data-c='{payload}'>{payload}</script>"
        with caplog.at_level("WARNING", logger="next.static.runtime"):
            builder = NextScriptBuilder(URL, chunk_template=template)
        assert builder.chunk_script("1") == "<script>(self.$nx||=[]).push(1)</script>"
        assert "outside the text of a <script> element" in caplog.text


class TestPayloadInScript:
    """A chunk payload is safe as the text of a script element alone."""

    @pytest.mark.parametrize(
        "template",
        [
            "<script{nonce_attr}>(self.$nx||=[]).push({payload})</script>",
            "<script>a({payload})</script><script>b({payload})</script>",
            "<script>{oops}</script>",
            "<SCRIPT>{payload}</Script >",
            "<script\n>{payload}</script/>",
            "<script>{payload}</script\t\n bar>",
        ],
    )
    def test_script_text_holds_the_payload(self, template: str) -> None:
        assert payload_in_script(template) is True

    @pytest.mark.parametrize(
        "template",
        [
            "<script data-x='{payload}'></script>",
            "<p>{payload}</p>",
            "<script>{payload}",
            "<script>a</script foo>{payload}<script>b</script>",
            "<script>a</SCRIPT\t\n bar>{payload}</script>",
            "<script>a</script/>{payload}</script>",
            "<script-x>{payload}</script>",
        ],
    )
    def test_an_attribute_or_markup_does_not(self, template: str) -> None:
        assert payload_in_script(template) is False


class TestInitScriptGoldenParity:
    """Fragment-assembled init payload stays byte-identical to the whole-dict dump.

    Each case builds its collector the way the static manager does, for each serializer.
    """

    def _assert_parity(
        self,
        js_context: Mapping[str, Any],
        *,
        key_serializers: Mapping[str, JsContextSerializer],
        encoded: Mapping[str, str],
    ) -> str:
        builder = NextScriptBuilder(URL)
        new = builder.init_script(
            js_context, key_serializers=key_serializers, encoded=encoded
        )
        expected = _reference_init_payload(js_context, key_serializers)
        assert new == f"<script>Next._init({expected});</script>"
        return new

    def test_default_serializer_simple_value(self) -> None:
        collector = StaticCollector()
        collector.add_js_context("user", "alice")
        self._assert_parity(
            collector.js_context(),
            key_serializers=collector.js_context_serializers(),
            encoded=collector.js_context_encoded(),
        )

    def test_empty_js_context(self) -> None:
        collector = StaticCollector()
        out = self._assert_parity(
            collector.js_context(),
            key_serializers=collector.js_context_serializers(),
            encoded=collector.js_context_encoded(),
        )
        assert out == "<script>Next._init({});</script>"

    def test_per_key_serializer_override(self) -> None:
        collector = StaticCollector()
        collector.add_js_context("user", "alice")
        collector.add_js_context("flags", [1, 2], serializer=_MarkSerializer())
        out = self._assert_parity(
            collector.js_context(),
            key_serializers=collector.js_context_serializers(),
            encoded=collector.js_context_encoded(),
        )
        assert '"flags":{"mark":[1,2]}' in out

    def test_nested_structure_value(self) -> None:
        collector = StaticCollector()
        collector.add_js_context("data", {"a": [1, 2], "b": {"c": 3}})
        self._assert_parity(
            collector.js_context(),
            key_serializers=collector.js_context_serializers(),
            encoded=collector.js_context_encoded(),
        )

    def test_externally_added_csrf_key_falls_back(self) -> None:
        collector = StaticCollector()
        collector.add_js_context("user", "alice")
        csrf = {"header": "X-CSRFToken", "token": "tok"}
        js_context = {**collector.js_context(), CSRF_PAYLOAD_KEY: csrf}
        out = self._assert_parity(
            js_context,
            key_serializers=collector.js_context_serializers(),
            encoded=collector.js_context_encoded(),
        )
        assert '"$csrf":{"header":"X-CSRFToken","token":"tok"}' in out


class TestInitScriptEscaping:
    """The assembled payload is escaped for the inline `<script>` context.

    A `serialize=True` value with `</script>`, `<!--`, or line separators must not leak.
    """

    def _payload(self, out: str) -> str:
        """Return the `Next._init` argument stripped of the script wrapper."""
        prefix = "<script>Next._init("
        suffix = ");</script>"
        assert out.startswith(prefix)
        assert out.endswith(suffix)
        return out[len(prefix) : -len(suffix)]

    def test_script_close_sequence_cannot_break_out(self) -> None:
        builder = NextScriptBuilder(URL)
        payload = self._payload(builder.init_script({"x": "</script><b>pwn"}))
        assert "</script>" not in payload
        assert "\\u003C/script\\u003E" in payload

    def test_angle_and_amp_each_escape(self) -> None:
        builder = NextScriptBuilder(URL)
        payload = self._payload(builder.init_script({"x": "<&>"}))
        assert "<" not in payload
        assert ">" not in payload
        assert "&" not in payload
        assert "\\u003C\\u0026\\u003E" in payload

    def test_line_separator_from_custom_serializer_is_escaped(self) -> None:
        builder = NextScriptBuilder(URL)
        payload = self._payload(
            builder.init_script(
                {"x": "a" + chr(0x2028) + "b" + chr(0x2029) + "c"},
                key_serializers={"x": _RawSeparatorSerializer()},
            )
        )
        assert chr(0x2028) not in payload
        assert chr(0x2029) not in payload
        assert "\\u2028" in payload
        assert "\\u2029" in payload

    def test_clean_payload_is_byte_identical_to_compact_dump(self) -> None:
        builder = NextScriptBuilder(URL)
        value = {"user": "alice", "score": 42}
        out = builder.init_script(value)
        expected = resolve_serializer().dumps(value)
        assert out == f"<script>Next._init({expected});</script>"


class TestNextJsStaticPath:
    def test_namespace_prefix(self) -> None:
        assert NEXT_JS_STATIC_PATH.startswith("next/")
        assert NEXT_JS_STATIC_PATH.endswith(".js")


class TestDeclaredBundles:
    """The bundles a page declares by tag ship like any chunk, never in `$chunks`."""

    def test_they_ship_and_are_never_listed(self) -> None:
        assert {
            "defer",
            "reload",
            "more",
            "reader",
            "router",
            "history",
            "intent",
        } == DECLARED_CHUNK_KEYS
        assert set(CHUNK_STATIC_PATHS) >= DECLARED_CHUNK_KEYS
        assert set(LISTED_CHUNK_KEYS).isdisjoint(DECLARED_CHUNK_KEYS)
        assert set(LISTED_CHUNK_KEYS) | DECLARED_CHUNK_KEYS == set(CHUNK_STATIC_PATHS)

    @pytest.mark.parametrize(
        ("reader", "streams", "urgent", "load"),
        [
            (True, True, False, BundleLoad.EAGER),
            (True, True, True, BundleLoad.EAGER),
            (True, False, False, BundleLoad.LAZY),
            (True, False, True, BundleLoad.LAZY),
            (False, True, True, BundleLoad.EAGER),
            (False, True, False, BundleLoad.LAZY),
            (False, False, True, BundleLoad.DEFERRED),
            (False, False, False, BundleLoad.LAZY),
        ],
    )
    def test_the_load_follows_the_bundle_the_stream_and_the_urgency(
        self, *, reader: bool, streams: bool, urgent: bool, load: BundleLoad
    ) -> None:
        assert bundle_load(reader=reader, streams=streams, urgent=urgent) is load

    def test_each_load_names_its_attributes(self) -> None:
        assert {load: load.value for load in BundleLoad} == {
            BundleLoad.LAZY: ' async fetchpriority="low"',
            BundleLoad.EAGER: " async",
            BundleLoad.DEFERRED: " defer",
        }

    def test_a_lazy_tag_loads_async_at_low_priority(self) -> None:
        tag = NextScriptBuilder(URL).bundle_tag(
            "/a&b.js", load=BundleLoad.LAZY, nonce="n"
        )
        assert (
            tag
            == '<script async fetchpriority="low" src="/a&amp;b.js" nonce="n"></script>'
        )

    def test_an_eager_tag_loads_async_at_once(self) -> None:
        tag = NextScriptBuilder(URL).bundle_tag("/s/a.js", load=BundleLoad.EAGER)
        assert tag == '<script async src="/s/a.js"></script>'

    def test_a_deferred_tag_waits_for_the_document(self) -> None:
        tag = NextScriptBuilder(URL).bundle_tag("/s/a.js", load=BundleLoad.DEFERRED)
        assert tag == '<script defer src="/s/a.js"></script>'

    def test_the_script_tag_template_renders_it(self) -> None:
        builder = NextScriptBuilder(
            URL,
            script_tag_template=(
                '<SCRIPT crossorigin="anonymous" src="{url}"{nonce_attr}></SCRIPT>'
            ),
        )
        assert builder.bundle_tag("/s/a.js", load=BundleLoad.DEFERRED, nonce="n") == (
            '<SCRIPT defer crossorigin="anonymous" src="/s/a.js" nonce="n"></SCRIPT>'
        )

    def test_a_custom_element_named_like_script_takes_the_default(self) -> None:
        builder = NextScriptBuilder(
            URL, script_tag_template='<script-loader src="{url}"></script-loader>'
        )
        assert builder.bundle_tag("/s/a.js", load=BundleLoad.EAGER) == (
            '<script async src="/s/a.js"></script>'
        )

    def test_a_template_without_a_script_element_takes_the_default(self) -> None:
        builder = NextScriptBuilder(
            URL, script_tag_template='<link rel="modulepreload" href="{url}">'
        )
        assert builder.bundle_tag("/s/a.js", load=BundleLoad.LAZY) == (
            '<script async fetchpriority="low" src="/s/a.js"></script>'
        )


TRANSPORTS = "next.realtime.ports.active_transports"
PARTIAL_BACKEND = "next.partial.manager.partial_backend_manager.get"
TURBO = "text/vnd.turbo-stream.html"
WEBSOCKET = {"TRANSPORTS": ["ws", "sse"]}
BOTH_TRANSPORTS = f'"{REALTIME_PAYLOAD_KEY}":{{"protocol":2,"transports":["ws","sse"],'


class TestRealtimePayload:
    """The `$realtime` entry is sent only where it moves off the runtime defaults."""

    def test_the_stock_configuration_sends_no_entry(self) -> None:
        out = NextScriptBuilder(URL).init_script({"$chunks": {"stream": "/s.js"}})
        assert out == '<script>Next._init({"$chunks":{"stream":"/s.js"}});</script>'
        assert realtime_offer() == RealtimeOffer("", websocket=False)

    def test_an_active_websocket_advertises_both_transports(self) -> None:
        with override_next_settings(REALTIME=WEBSOCKET):
            out = NextScriptBuilder(URL).init_script({"$chunks": {}})
            assert realtime_offer().websocket is True
        assert (
            f'{BOTH_TRANSPORTS}"content_type":"application/vnd.next.patches+json"}}'
        ) in out

    def test_changing_the_transports_drops_the_memoised_entry(self) -> None:
        builder = NextScriptBuilder(URL)
        before = builder.init_script({"$chunks": {}})
        with override_next_settings(REALTIME=WEBSOCKET):
            during = builder.init_script({"$chunks": {}})
        after = builder.init_script({"$chunks": {}})
        assert REALTIME_PAYLOAD_KEY not in before
        assert BOTH_TRANSPORTS in during
        assert after == before

    @pytest.mark.usefixtures("forgotten_realtime_offer")
    def test_another_partial_backend_names_its_content_type(self) -> None:
        with mock.patch(PARTIAL_BACKEND) as get_backend:
            get_backend.return_value.content_type = TURBO
            out = NextScriptBuilder(URL).init_script({"$chunks": {}})
            assert realtime_offer().websocket is False
        assert (
            f'"{REALTIME_PAYLOAD_KEY}":{{"protocol":2,"transports":["sse"],'
            f'"content_type":"{TURBO}"}}'
        ) in out

    def test_a_payload_without_chunks_carries_no_entry(self) -> None:
        with override_next_settings(REALTIME=WEBSOCKET):
            out = NextScriptBuilder(URL).init_script({"user": "alice"})
        assert REALTIME_PAYLOAD_KEY not in out

    @pytest.mark.usefixtures("forgotten_realtime_offer")
    def test_a_reset_partial_backend_drops_the_memoised_entry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert realtime_offer().entry == ""
        monkeypatch.setattr(
            partial_backend_manager,
            "_select_config",
            lambda: {"BACKEND": "tests.support.codecs.TextStreamCodec"},
        )
        partial_backend_manager.reset()
        try:
            entry = realtime_offer().entry
            assert isinstance(partial_backend_manager.get(), TextStreamCodec)
        finally:
            monkeypatch.undo()
            partial_backend_manager.reset()
        assert '"content_type":"text/plain"' in entry
        assert realtime_offer().entry == ""

    @pytest.mark.usefixtures("forgotten_realtime_offer")
    def test_the_offer_is_resolved_once_until_it_is_forgotten(self) -> None:
        with mock.patch(TRANSPORTS, return_value=("sse",)) as transports:
            first = realtime_offer()
            assert realtime_offer() is first
            forget_realtime_offer()
            realtime_offer()
        assert transports.call_count == 2


class TestStreamMarker:
    """The init payload says partial requests may stream inside a page that may."""

    def test_the_flag_follows_the_page_under_render(self) -> None:
        builder = NextScriptBuilder(URL)
        assert STREAM_PAYLOAD_KEY == "$stream"
        assert '"$stream"' not in builder.init_script({"$chunks": {}})
        token = partial_streams.set(True)
        try:
            marked = builder.init_script({"$chunks": {}})
            bare = builder.init_script({"user": "a"})
        finally:
            partial_streams.reset(token)
        assert marked == '<script>Next._init({"$chunks":{},"$stream":true});</script>'
        assert '"$stream"' not in bare


class TestRouterEntry:
    """The `$router` entry adds the bundle URLs and a snapshot refusal per page."""

    ENTRY = '{"scope":"all"}'

    def test_a_page_adding_nothing_sends_the_shared_entry(self) -> None:
        routed = RouterRender("5ea1", self.ENTRY)
        assert routed.snapshot is True
        assert routed.speculation == ""
        assert routed.encoded() is self.ENTRY

    @pytest.mark.parametrize(
        ("reader", "history", "intent", "snapshot", "expected"),
        [
            pytest.param(
                "/r.js",
                None,
                None,
                True,
                {"scope": "all", "reader": "/r.js"},
                id="reader",
            ),
            pytest.param(
                None,
                "/h.js",
                None,
                True,
                {"scope": "all", "history": "/h.js"},
                id="history",
            ),
            pytest.param(
                None,
                None,
                "/i.js",
                True,
                {"scope": "all", "intent": "/i.js"},
                id="intent",
            ),
            pytest.param(
                None,
                None,
                None,
                False,
                {"scope": "all", "snapshot": False},
                id="refusal",
            ),
            pytest.param(
                "/r.js",
                "/h.js",
                "/i.js",
                False,
                {
                    "scope": "all",
                    "reader": "/r.js",
                    "history": "/h.js",
                    "intent": "/i.js",
                    "snapshot": False,
                },
                id="all",
            ),
        ],
    )
    def test_the_additions(
        self,
        reader: str | None,
        history: str | None,
        intent: str | None,
        expected: dict[str, object],
        *,
        snapshot: bool,
    ) -> None:
        routed = RouterRender("5ea1", self.ENTRY, snapshot=snapshot)
        routed.reader = reader
        routed.history = history
        routed.intent = intent
        encoded = routed.encoded()
        assert json.loads(encoded) == expected
        assert list(json.loads(encoded)) == list(expected)

    def test_the_init_payload_carries_the_additions(self) -> None:
        routed = RouterRender("5ea1", self.ENTRY, snapshot=False)
        token = router_render.set(routed)
        try:
            script = NextScriptBuilder(URL).init_script({"$chunks": {}})
        finally:
            router_render.reset(token)
        assert '"$router":{"scope":"all","snapshot":false}' in script


class TestSpeculationTag:
    """The rules travel in one script element, nonced when the response has one."""

    def test_the_tag(self) -> None:
        assert speculation_tag("{}", None) == (
            '<script type="speculationrules">{}</script>'
        )
        assert speculation_tag("{}", "n1") == (
            '<script type="speculationrules" nonce="n1">{}</script>'
        )


class TestReservedPayloadKeys:
    """The reserved set is the single source of truth for framework-owned keys."""

    def test_holds_every_framework_wire_name(self) -> None:
        assert (
            frozenset(
                {
                    "$csrf",
                    "$dev",
                    "$chunks",
                    "$realtime",
                    "$stream",
                    "$scripts",
                    "$consent",
                    "$router",
                    "$layouts",
                }
            )
            == RESERVED_PAYLOAD_KEYS
        )

    def test_key_constants_carry_the_wire_names(self) -> None:
        assert (CSRF_PAYLOAD_KEY, DEV_PAYLOAD_KEY) == ("$csrf", "$dev")


class TestCsrfPayload:
    """The `$csrf` payload of `Next._init` needs a request that can mint a token."""

    def test_payload_for_real_request_returns_payload(self) -> None:
        request = RequestFactory().get("/")
        payload = csrf_payload_for(request)
        assert payload is not None
        assert "header" in payload
        assert "token" in payload

    def test_payload_for_none_request_returns_none(self) -> None:
        assert csrf_payload_for(None) is None

    def test_payload_for_request_without_meta_mapping_returns_none(self) -> None:
        class FakeRequest:
            META = object()

        assert csrf_payload_for(FakeRequest()) is None  # type: ignore[arg-type]


class TestUnformattableTemplates:
    """A template `.format` cannot fill gives way to the default, logged once."""

    @pytest.fixture(autouse=True)
    def _rearmed(self):
        runtime_failures.clear()
        yield
        runtime_failures.clear()

    @pytest.mark.parametrize(
        ("option", "template", "rendered"),
        [
            ("preload_template", "<link {rel} href='{url}'>", "preload_link"),
            ("script_tag_template", "<script src='{url}'>{</script>", "script_tag"),
            ("init_template", "<script>{0}({payload})</script>", "init_script"),
        ],
    )
    def test_the_default_renders_and_the_template_is_named(
        self, caplog, option, template, rendered
    ) -> None:
        builders = [NextScriptBuilder("/n.js", **{option: template}) for _ in range(2)]
        default = NextScriptBuilder("/n.js")
        if rendered == "init_script":
            assert builders[1].init_script({}) == default.init_script({})
        else:
            assert getattr(builders[1], rendered)() == getattr(default, rendered)()
        [record] = [r for r in caplog.records if r.name == "next.static.runtime"]
        assert f"NEXT_JS_OPTIONS['{option}']" in record.getMessage()

    def test_a_broken_template_raises_under_debug(self, settings) -> None:
        settings.DEBUG = True
        with pytest.raises(KeyError) as raised:
            NextScriptBuilder("/n.js", preload_template="<link {rel}>")
        assert "Double every literal brace" in raised.value.__notes__[0]

    def test_doubled_braces_format(self) -> None:
        builder = NextScriptBuilder(
            "/n.js", script_tag_template="<script data-x='{{}}' src='{url}'></script>"
        )
        assert builder.script_tag() == "<script data-x='{}' src='/n.js'></script>"
