from __future__ import annotations

import json
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest
from django.test import RequestFactory, override_settings

from next.static import NextScriptBuilder, ScriptInjectionPolicy
from next.static.collector import StaticCollector
from next.static.scripts import (
    CSRF_PAYLOAD_KEY,
    NEXT_JS_STATIC_PATH,
    csrf_header_name,
    csrf_payload,
    csrf_payload_for,
)
from next.static.serializers import resolve_serializer


if TYPE_CHECKING:
    from collections.abc import Mapping

    from next.static.serializers import JsContextSerializer


URL = "/static/next/next.min.js"


def _legacy_init_payload(
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


class TestNextScriptBuilderCustomTemplates:
    """Every template is an instance attribute — pluggable without subclassing."""

    def test_custom_preload(self) -> None:
        builder = NextScriptBuilder(
            URL,
            preload_template='<link data-next rel="preload" as="script" href="{url}">',
        )
        assert "data-next" in builder.preload_link()

    def test_custom_script_tag(self) -> None:
        builder = NextScriptBuilder(
            URL,
            script_tag_template='<script defer src="{url}"></script>',
        )
        assert builder.script_tag() == f'<script defer src="{URL}"></script>'

    def test_custom_init_template(self) -> None:
        builder = NextScriptBuilder(
            URL,
            init_template="<script>window.MyNext.boot({payload})</script>",
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


class TestInitScriptGoldenParity:
    """Fragment-assembled init payload stays byte-identical to the whole-dict dump.

    Each case builds a collector the way the static manager does, then asserts
    the new `encoded`-driven `init_script` output equals the legacy payload for
    the compact serializers the framework ships.
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
        legacy = _legacy_init_payload(js_context, key_serializers)
        assert new == f"<script>Next._init({legacy});</script>"
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


class TestNextJsStaticPath:
    def test_namespace_prefix(self) -> None:
        assert NEXT_JS_STATIC_PATH.startswith("next/")
        assert NEXT_JS_STATIC_PATH.endswith(".js")


class TestCsrfPayload:
    """CSRF header name and token feed the `$csrf` payload of `Next._init`."""

    def test_payload_key_is_dollar_csrf(self) -> None:
        assert CSRF_PAYLOAD_KEY == "$csrf"

    def test_header_name_is_http_wire_form(self) -> None:
        with override_settings(CSRF_HEADER_NAME="HTTP_X_CSRFTOKEN"):
            assert csrf_header_name() == "X-Csrftoken"

    def test_header_name_honours_custom_setting(self) -> None:
        with override_settings(CSRF_HEADER_NAME="HTTP_X_MY_TOKEN"):
            assert csrf_header_name() == "X-My-Token"

    def test_header_name_unmangles_unprefixed_setting(self) -> None:
        with override_settings(CSRF_HEADER_NAME="X_MY_TOKEN"):
            assert csrf_header_name() == "X-My-Token"

    def test_payload_carries_header_and_token(self) -> None:
        request = RequestFactory().get("/")
        payload = csrf_payload(request)
        assert payload["header"] == csrf_header_name()
        assert isinstance(payload["token"], str)
        assert payload["token"]

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
