import json
from pathlib import Path

import pytest
from django import forms
from django.test import RequestFactory

from next.forms.dispatch.responses import ActionOutcome, ActionOutcomeKind
from next.forms.manager import form_action_manager
from next.forms.uid import ORIGIN_FIELD_NAME
from next.partial import Patches, shape_partial
from next.partial.errors import ReservedContextKeyError, UnknownContextNameError
from next.partial.headers import partial_intent
from next.partial.shaping import ActionRef, shape_validate
from next.static.runtime import CSRF_PAYLOAD_KEY
from next.testing import NextClient, envelope_of
from tests.support import partial_request
from tests.support.partial_requests import partial_meta


ZONED_PAGE = Path(__file__).resolve().parent.parent / "site_pages" / "zoned" / "page.py"
FLAG_CONTEXT = {"op": "context", "data": {"flag": True}}


class _NameForm(forms.Form):
    name = forms.CharField()


def _zoned_action(zone: str = "alpha"):
    """Return a partial action POST from the zoned page naming one of its zones."""
    return RequestFactory().post(
        "/_next/form/x/",
        data={ORIGIN_FIELD_NAME: "/zoned/"},
        **partial_meta(zones=zone),
    )


def _ops(response) -> list[dict[str, object]]:
    return json.loads(response.content)["ops"]


class TestZoneMorphContext:
    """`morph(zone=...)` carries the zone's client context as a zone GET does."""

    def test_the_context_op_matches_the_zone_get(self) -> None:
        envelope = Patches(partial_request()).morph(zone="alpha").envelope()
        get = envelope_of(NextClient().get_zones("/zoned/", "alpha"))
        built = [op.as_dict() for op in envelope.ops]
        assert built[1:] == [op for op in get.ops if op["op"] == "context"]
        assert built[1:] == [FLAG_CONTEXT]

    def test_the_context_follows_the_morph(self) -> None:
        envelope = Patches(partial_request()).morph(zone="alpha").envelope()
        assert [op.op for op in envelope.ops] == ["morph", "context"]

    def test_context_false_sends_none(self) -> None:
        envelope = (
            Patches(partial_request()).morph(zone="alpha", context=False).envelope()
        )
        assert [op.op for op in envelope.ops] == ["morph"]

    def test_a_mapping_merges_over_the_zone_context_in_one_op(self) -> None:
        envelope = (
            Patches(partial_request())
            .morph(zone="alpha", context={"flag": False})
            .envelope()
        )
        assert [op.as_dict() for op in envelope.ops][1:] == [
            {"op": "context", "data": {"flag": False}}
        ]

    def test_an_explicit_context_right_after_merges_into_the_zone_op(self) -> None:
        envelope = (
            Patches(partial_request())
            .morph(zone="alpha")
            .context(flag=False)
            .envelope()
        )
        assert [op.as_dict() for op in envelope.ops][1:] == [
            {"op": "context", "data": {"flag": False}}
        ]

    def test_an_explicit_context_after_another_op_stays_separate(self) -> None:
        envelope = (
            Patches(partial_request())
            .morph(zone="alpha")
            .toast("saved")
            .context(flag=False)
            .envelope()
        )
        assert [op.op for op in envelope.ops] == [
            "morph",
            "context",
            "toast",
            "context",
        ]

    def test_a_merged_context_still_validates_its_names(self) -> None:
        patches = Patches(partial_request()).morph(zone="alpha")
        with pytest.raises(UnknownContextNameError):
            patches.context(nope=1)

    def test_a_mapping_naming_no_serialize_provider_raises(self) -> None:
        with pytest.raises(UnknownContextNameError):
            Patches(partial_request()).morph(zone="alpha", context={"greeting": "x"})

    def test_a_mapping_naming_a_reserved_key_raises(self) -> None:
        with pytest.raises(ReservedContextKeyError):
            Patches(partial_request()).morph(
                zone="alpha", context={CSRF_PAYLOAD_KEY: "x"}
            )

    def test_a_page_without_serialize_providers_sends_no_context(self) -> None:
        envelope = (
            Patches(partial_request(origin="/formzone/"))
            .morph(zone="editor")
            .envelope()
        )
        assert [op.op for op in envelope.ops] == ["morph"]

    def test_the_foreign_route_keeps_its_morph_alone(self) -> None:
        envelope = (
            Patches(partial_request()).morph(zone="alpha", page=ZONED_PAGE).envelope()
        )
        assert [op.op for op in envelope.ops] == ["morph"]


class TestContextKeywordRefusals:
    """`context=` belongs to the zone route and any other route refuses it."""

    @pytest.mark.parametrize("value", [False, {"flag": False}], ids=["off", "map"])
    def test_the_target_route_refuses_it(self, value: object) -> None:
        with pytest.raises(TypeError, match=r"\['context'\]"):
            Patches.versioned("v1").morph({"id": "x"}, "<p></p>", context=value)

    def test_the_form_route_refuses_it(self) -> None:
        with pytest.raises(TypeError, match=r"\['context'\]"):
            Patches.versioned("v1").morph(
                form="u1", html="<form></form>", context=False
            )

    def test_the_foreign_route_refuses_it(self) -> None:
        with pytest.raises(TypeError, match=r"\['context'\]"):
            Patches(partial_request()).morph(
                zone="alpha", page=ZONED_PAGE, context=False
            )

    def test_the_default_value_is_accepted_anywhere(self) -> None:
        envelope = (
            Patches.versioned("v1").morph({"id": "x"}, "<p></p>", context=True)
        ).envelope()
        assert [op.op for op in envelope.ops] == ["morph"]


class TestActionResponseContext:
    """An action answering with a zone morph carries the zone's client context."""

    def test_an_invalid_submission_carries_it(self) -> None:
        form = _NameForm(data={})
        form.is_valid()
        outcome = ActionOutcome(
            kind=ActionOutcomeKind.INVALID,
            action_name="x",
            uid="u1",
            form=form,
            page_path=ZONED_PAGE,
        )
        backend = form_action_manager.default_backend
        ops = _ops(shape_partial(backend, _zoned_action(), outcome))
        assert [op["op"] for op in ops] == ["morph", "context"]
        assert ops[1] == FLAG_CONTEXT

    def test_the_success_funnel_carries_it(self) -> None:
        outcome = ActionOutcome(kind=ActionOutcomeKind.RESULT, action_name="x")
        backend = form_action_manager.default_backend
        ops = _ops(shape_partial(backend, _zoned_action(), outcome))
        assert [op["op"] for op in ops] == ["morph", "context"]
        assert ops[1] == FLAG_CONTEXT

    def test_a_validate_pass_leaves_the_client_context_alone(self) -> None:
        request = _zoned_action()
        response = shape_validate(
            form_action_manager.default_backend,
            request,
            _NameForm(data={}),
            partial_intent(request),
            ActionRef(action_name="x", uid="u1"),
        )
        assert [op["op"] for op in _ops(response)] == ["morph"]
