import pytest

from next.partial import Asset, Envelope, FormMeta, Patch
from next.partial.errors import ReservedPatchKeyError


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
        assert patch.as_dict() == {"op": "event", "name": "ping", "detail": {"x": 1}}


class TestAssetAsDict:
    """An asset serialises with its inline body only when one is present."""

    def test_url_form_asset_carries_no_inline_key(self) -> None:
        assert Asset(kind="css", url="/a.css").as_dict() == {
            "kind": "css",
            "url": "/a.css",
        }

    def test_inline_form_asset_carries_its_body(self) -> None:
        asset = Asset(kind="css", url="", inline=".x { color: red; }")
        assert asset.as_dict() == {
            "kind": "css",
            "url": "",
            "inline": ".x { color: red; }",
        }

    def test_load_travels_when_set(self) -> None:
        asset = Asset(kind="module", url="/a.mjs", load="module")
        assert asset.as_dict() == {"kind": "module", "url": "/a.mjs", "load": "module"}


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


class TestPatchReservedKeys:
    """A reserved structural key in a patch payload is refused at construction."""

    def test_constructor_refuses_reserved_extras_key(self) -> None:
        with pytest.raises(ReservedPatchKeyError) as exc:
            Patch(op="x", extras={"target": 1})
        assert exc.value.keys == frozenset({"target"})

    def test_valid_patch_as_dict_does_not_raise(self) -> None:
        patch = Patch(op="event", extras={"name": "ping"})
        assert patch.as_dict() == {"op": "event", "name": "ping"}

    def test_multiple_reserved_keys_are_sorted_in_message(self) -> None:
        with pytest.raises(ReservedPatchKeyError) as exc:
            Patch(op="x", extras={"target": 1, "op": 2, "html": 3})
        assert exc.value.keys == frozenset({"op", "target", "html"})
        assert "html, op, target" in str(exc.value)
