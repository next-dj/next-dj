import pytest
from django.test import RequestFactory

from next.partial import Patches, PatchResponse, register_patch_op
from next.partial.headers import CONTENT_TYPE
from next.partial.patches import (
    BuiltinPatchOpError,
    ReservedPatchKeyError,
    UnknownContextNameError,
    UnknownPatchOpError,
)
from next.partial.registry import patch_op_registry
from tests.support import partial_request, plain_request


@pytest.fixture()
def custom_op():
    """Register a custom patch verb for the test and drop it afterwards."""
    register_patch_op("confetti")
    yield "confetti"
    patch_op_registry._ops.discard("confetti")
    patch_op_registry._custom.discard("confetti")


class TestMorphZone:
    """`morph(zone=)` renders the named zone and lists its assets."""

    def test_zone_body_morphs_in_place(self) -> None:
        envelope = Patches(partial_request()).morph(zone="alpha").envelope()
        op = envelope.ops[0].as_dict()
        assert op["op"] == "morph"
        assert op["target"] == {"zone": "alpha"}
        assert op["html"] == '<div data-next-zone="alpha"><p>alpha hi</p></div>'

    def test_zone_asset_manifest_travels_in_the_envelope(self) -> None:
        envelope = Patches(partial_request()).morph(zone="alpha").envelope()
        assert {"kind": "css", "url": "/static/next/zoned.css"} in [
            asset.as_dict() for asset in envelope.assets
        ]

    def test_overrides_reach_the_zone_body(self) -> None:
        envelope = (
            Patches(partial_request())
            .morph(zone="alpha", overrides={"greeting": "swapped"})
            .envelope()
        )
        assert "swapped" in envelope.ops[0].html


class TestMorphFormAndHtml:
    """`morph(form=)` extract-morphs and `morph(html=)` morphs ready HTML."""

    def test_form_target_extract_morphs(self) -> None:
        envelope = Patches("v1").morph(form="ab12", html="<form></form>").envelope()
        op = envelope.ops[0].as_dict()
        assert op["target"] == {"form": "ab12"}
        assert op["extract"] is True

    def test_ready_html_morphs_the_passed_target(self) -> None:
        envelope = Patches("v1").morph({"zone": "list"}, "<ul></ul>").envelope()
        assert envelope.ops[0].as_dict() == {
            "op": "morph",
            "target": {"zone": "list"},
            "html": "<ul></ul>",
        }


class TestStandaloneVerbs:
    """Each builder verb records its own patch in order."""

    def test_append_carries_dedupe(self) -> None:
        envelope = Patches("v1").append({"zone": "feed"}, "<li></li>").envelope()
        assert envelope.ops[0].as_dict() == {
            "op": "append",
            "target": {"zone": "feed"},
            "html": "<li></li>",
            "dedupe": "key",
        }

    def test_append_accepts_a_custom_dedupe(self) -> None:
        envelope = (
            Patches("v1").append({"zone": "feed"}, "<li></li>", dedupe="id").envelope()
        )
        assert envelope.ops[0].as_dict()["dedupe"] == "id"

    def test_prepend_carries_dedupe(self) -> None:
        envelope = Patches("v1").prepend({"zone": "feed"}, "<li></li>").envelope()
        assert envelope.ops[0].as_dict() == {
            "op": "prepend",
            "target": {"zone": "feed"},
            "html": "<li></li>",
            "dedupe": "key",
        }

    def test_prepend_accepts_a_custom_dedupe(self) -> None:
        envelope = (
            Patches("v1").prepend({"zone": "feed"}, "<li></li>", dedupe="id").envelope()
        )
        assert envelope.ops[0].as_dict()["dedupe"] == "id"

    def test_refresh_names_the_zone(self) -> None:
        envelope = Patches("v1").refresh(zone="results").envelope()
        assert envelope.ops[0].as_dict() == {"op": "refresh", "zone": "results"}

    def test_layer_close_with_result(self) -> None:
        envelope = Patches("v1").layer_close(result={"id": 7}).envelope()
        assert envelope.ops[0].as_dict() == {
            "op": "layer.close",
            "result": {"id": 7},
        }

    def test_layer_close_with_dismiss(self) -> None:
        envelope = Patches("v1").layer_close(dismiss="cancel").envelope()
        assert envelope.ops[0].as_dict() == {
            "op": "layer.close",
            "dismiss": "cancel",
        }

    def test_layer_open_is_empty_without_seeds(self) -> None:
        envelope = Patches("v1").layer_open().envelope()
        assert envelope.ops[0].as_dict() == {"op": "layer.open"}

    def test_layer_open_seeds_a_zone(self) -> None:
        envelope = Patches("v1").layer_open(zone="cart").envelope()
        assert envelope.ops[0].as_dict() == {"op": "layer.open", "zone": "cart"}

    def test_layer_open_validates_the_href_same_site(self) -> None:
        envelope = (
            Patches(partial_request())
            .layer_open(href="https://evil.example.com/x")
            .envelope()
        )
        assert envelope.ops[0].as_dict() == {"op": "layer.open", "href": "/zoned/"}

    def test_toast_default_variant(self) -> None:
        envelope = Patches("v1").toast("Saved").envelope()
        assert envelope.ops[0].as_dict() == {
            "op": "toast",
            "text": "Saved",
            "variant": "info",
        }

    def test_event_carries_detail(self) -> None:
        envelope = Patches("v1").event("ping", {"x": 1}).envelope()
        assert envelope.ops[0].as_dict()["detail"] == {"x": 1}

    def test_push_url_validates_same_site(self) -> None:
        envelope = Patches(partial_request()).push_url("/next/").envelope()
        assert envelope.ops[0].as_dict() == {
            "op": "url",
            "action": "push",
            "href": "/next/",
        }

    def test_push_url_falls_back_for_external_host(self) -> None:
        envelope = (
            Patches(partial_request()).push_url("https://evil.example.com/x").envelope()
        )
        assert envelope.ops[0].as_dict()["href"] == "/zoned/"

    def test_redirect_internal_is_validated(self) -> None:
        envelope = Patches(partial_request()).redirect("/safe/").envelope()
        assert envelope.ops[0].as_dict() == {"op": "visit", "href": "/safe/"}

    def test_redirect_external_carries_marker(self) -> None:
        envelope = (
            Patches(partial_request())
            .redirect("https://oauth.example.com/x", external=True)
            .envelope()
        )
        assert envelope.ops[0].as_dict() == {
            "op": "visit",
            "href": "https://oauth.example.com/x",
            "external": True,
        }


class TestCustomOp:
    """`op()` emits a registered custom verb and rejects unknown ones."""

    def test_registered_verb_emits_a_patch(self, custom_op: str) -> None:
        envelope = Patches("v1").op(custom_op, origin="button").envelope()
        assert envelope.ops[0].as_dict() == {
            "op": "confetti",
            "origin": "button",
        }

    def test_unregistered_verb_raises_unknown_op(self) -> None:
        with pytest.raises(UnknownPatchOpError) as exc:
            Patches("v1").op("nope")
        assert exc.value.name == "nope"

    def test_built_in_verb_is_refused_on_the_generic_channel(self) -> None:
        with pytest.raises(BuiltinPatchOpError) as exc:
            Patches("v1").op("morph")
        assert exc.value.name == "morph"

    def test_payload_naming_a_reserved_wire_key_raises(self, custom_op: str) -> None:
        with pytest.raises(ReservedPatchKeyError) as exc:
            Patches("v1").op(custom_op, target="spoof").envelope().ops[0].as_dict()
        assert exc.value.keys == frozenset({"target"})


class TestContextPatch:
    """`context()` accepts only registered serialize providers."""

    def test_registered_serialize_name_serialises(self) -> None:
        envelope = Patches(partial_request()).context(flag=True).envelope()
        assert envelope.ops[0].as_dict() == {
            "op": "context",
            "data": {"flag": True},
        }

    def test_unregistered_name_raises_unknown_context(self) -> None:
        with pytest.raises(UnknownContextNameError) as exc:
            Patches(partial_request()).context(secret="leak")
        assert exc.value.name == "secret"


class TestResponse:
    """`response()` returns an envelope on partial requests, else a 303."""

    def test_partial_request_returns_an_envelope(self) -> None:
        response = Patches(partial_request()).toast("hi").response()
        assert isinstance(response, PatchResponse)
        assert response["Content-Type"] == CONTENT_TYPE

    def test_no_runtime_default_redirects_to_origin(self) -> None:
        response = Patches(plain_request()).response()
        assert response.status_code == 303
        assert response["Location"] == "/zoned/"

    def test_no_runtime_with_fallback_redirects_to_it(self) -> None:
        response = Patches(plain_request()).response(fallback="/after/")
        assert response.status_code == 303
        assert response["Location"] == "/after/"

    def test_origin_falls_back_to_request_path_without_match(self) -> None:
        request = RequestFactory().post("/loose/", data={})
        response = Patches(request).response()
        assert response["Location"] == "/loose/"


class TestVersionBuilderCompatibility:
    """The bare-version builder stays a low-level assembler."""

    def test_version_builder_keeps_positional_morph(self) -> None:
        envelope = Patches("9f3c").morph({"zone": "x"}, "<div></div>").envelope()
        assert envelope.version == "9f3c"
        assert envelope.ops[0].html == "<div></div>"

    def test_version_builder_response_redirects_to_root(self) -> None:
        response = Patches("9f3c").response()
        assert response.status_code == 303
        assert response["Location"] == "/"

    def test_version_builder_render_helpers_require_a_request(self) -> None:
        with pytest.raises(RuntimeError):
            Patches("9f3c").morph(zone="x")
