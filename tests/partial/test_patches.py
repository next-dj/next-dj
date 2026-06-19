import pytest

from next.partial import (
    Asset,
    Envelope,
    FormMeta,
    Patch,
    Patches,
    PatchResponse,
)
from next.partial.headers import CONTENT_TYPE


class TestPatchAsDict:
    """A patch serialises to an ordered mapping with verb first."""

    def test_html_verb(self) -> None:
        patch = Patch(op="replace", target={"zone": "list"}, html="<div></div>")
        assert patch.as_dict() == {
            "op": "replace",
            "target": {"zone": "list"},
            "html": "<div></div>",
        }

    def test_remove_has_no_html(self) -> None:
        patch = Patch(op="remove", target={"zone": "list"})
        assert patch.as_dict() == {"op": "remove", "target": {"zone": "list"}}

    def test_event_carries_extras(self) -> None:
        patch = Patch(op="event", extras={"name": "ping", "detail": {"x": 1}})
        assert patch.as_dict() == {
            "op": "event",
            "name": "ping",
            "detail": {"x": 1},
        }


class TestEnvelopeAsDict:
    """The envelope wire form carries ops and meta with stable keys."""

    def test_minimal_envelope(self) -> None:
        envelope = Envelope(version="v1")
        assert envelope.as_dict() == {
            "version": "v1",
            "ops": [],
            "assets": [],
            "form": None,
        }

    def test_full_envelope(self) -> None:
        envelope = Envelope(
            version="v1",
            ops=(Patch(op="remove", target={"zone": "row"}),),
            assets=(Asset(kind="css", url="/a.css"),),
            form=FormMeta(uid="ab12", valid=False, errors={"name": ["required"]}),
        )
        data = envelope.as_dict()
        assert data["ops"] == [{"op": "remove", "target": {"zone": "row"}}]
        assert data["assets"] == [{"kind": "css", "url": "/a.css"}]
        assert "defer" not in data
        assert data["form"] == {
            "uid": "ab12",
            "valid": False,
            "errors": {"name": ["required"]},
        }

    def test_csrf_and_request_id_present_only_when_set(self) -> None:
        envelope = Envelope(version="v1", csrf={"token": "t"}, request_id="r1")
        data = envelope.as_dict()
        assert data["csrf"] == {"token": "t"}
        assert data["request_id"] == "r1"

    def test_csrf_and_request_id_omitted_by_default(self) -> None:
        data = Envelope(version="v1").as_dict()
        assert "csrf" not in data
        assert "request_id" not in data


class TestPatchesBuilder:
    """The minimal builder emits HTML and HTML-less verbs in order."""

    def test_morph_is_the_default_verb(self) -> None:
        envelope = Patches("v1").morph({"zone": "list"}, "<div></div>").envelope()
        assert envelope.ops[0].as_dict() == {
            "op": "morph",
            "target": {"zone": "list"},
            "html": "<div></div>",
        }

    def test_morph_extract_marks_payload(self) -> None:
        envelope = (
            Patches("v1")
            .morph({"form": "ab12"}, "<html></html>", extract=True)
            .envelope()
        )
        assert envelope.ops[0].as_dict()["extract"] is True

    def test_morph_form_extract_morphs_by_uid(self) -> None:
        envelope = Patches("v1").morph_form("ab12", "<form></form>").envelope()
        assert envelope.ops[0].as_dict() == {
            "op": "morph",
            "target": {"form": "ab12"},
            "html": "<form></form>",
            "extract": True,
        }

    def test_morph_facade_form_delegates_to_morph_form(self) -> None:
        facade = Patches("v1").morph(form="ab12", html="<form></form>").envelope()
        direct = Patches("v1").morph_form("ab12", "<form></form>").envelope()
        assert facade.ops[0].as_dict() == direct.ops[0].as_dict()

    def test_morph_rejects_an_unknown_selector(self) -> None:
        with pytest.raises(TypeError, match="unexpected selector"):
            Patches("v1").morph(widget="ab12")

    def test_morph_rejects_conflicting_selectors(self) -> None:
        with pytest.raises(TypeError, match="conflicting selector"):
            Patches("v1").morph(zone="list", component="card")

    def test_replace(self) -> None:
        envelope = Patches("v1").replace({"zone": "list"}, "<div></div>").envelope()
        assert envelope.ops[0].as_dict() == {
            "op": "replace",
            "target": {"zone": "list"},
            "html": "<div></div>",
        }

    def test_inner(self) -> None:
        envelope = Patches("v1").inner({"zone": "list"}, "<p></p>").envelope()
        assert envelope.ops[0].op == "inner"
        assert envelope.ops[0].html == "<p></p>"

    def test_remove(self) -> None:
        envelope = Patches("v1").remove({"css": "#row-1"}).envelope()
        assert envelope.ops[0].as_dict() == {
            "op": "remove",
            "target": {"css": "#row-1"},
        }

    def test_event_default_detail(self) -> None:
        envelope = Patches("v1").event("saved").envelope()
        assert envelope.ops[0].as_dict() == {
            "op": "event",
            "name": "saved",
            "detail": {},
        }

    def test_event_with_detail(self) -> None:
        envelope = Patches("v1").event("saved", {"id": 7}).envelope()
        assert envelope.ops[0].as_dict()["detail"] == {"id": 7}

    def test_chaining_preserves_order(self) -> None:
        envelope = (
            Patches("v1")
            .replace({"zone": "a"}, "<a></a>")
            .remove({"zone": "b"})
            .event("done")
            .envelope()
        )
        assert [op.op for op in envelope.ops] == ["replace", "remove", "event"]

    def test_assets_and_form(self) -> None:
        form = FormMeta(uid="ab12", valid=True)
        envelope = Patches("v1").add_asset("css", "/x.css").set_form(form).envelope()
        assert envelope.assets[0] == Asset(kind="css", url="/x.css")
        assert envelope.form is form

    def test_version_carried(self) -> None:
        assert Patches("9f3c").envelope().version == "9f3c"


class TestPatchResponse:
    """`PatchResponse` is an HttpResponse carrying serialized bytes."""

    def test_default_content_type_and_body(self) -> None:
        response = PatchResponse(b'{"version":"v1"}')
        assert response["Content-Type"] == CONTENT_TYPE
        assert response.content == b'{"version":"v1"}'

    def test_version_header_set(self) -> None:
        response = PatchResponse(b"{}", version="9f3c")
        assert response["X-Next-Version"] == "9f3c"

    def test_vary_headers_stamped(self) -> None:
        response = PatchResponse(b"{}")
        assert "X-Next-Merge" in response["Vary"]

    def test_custom_status(self) -> None:
        response = PatchResponse(b"{}", status=409)
        assert response.status_code == 409
