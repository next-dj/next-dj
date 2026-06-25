from __future__ import annotations

import json
from decimal import Decimal

import pytest
from django.test import RequestFactory, override_settings

from next.static import NextScriptBuilder, ScriptInjectionPolicy
from next.static.scripts import (
    CSRF_PAYLOAD_KEY,
    NEXT_JS_STATIC_PATH,
    csrf_header_name,
    csrf_payload,
    csrf_payload_for,
)


URL = "/static/next/next.min.js"


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
