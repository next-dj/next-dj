from next.partial import Asset, Envelope, FormMeta, Patch, Patches, keys
from next.partial.view import _patch_zone
from next.testing.client import PartialEnvelope


class TestWireKeyConstants:
    """The wire-key constants carry the frozen literal strings."""

    def test_reserved_patch_keys(self) -> None:
        assert frozenset({"op", "target", "html"}) == keys.RESERVED_PATCH_KEYS

    def test_envelope_key_values(self) -> None:
        assert keys.VERSION == "version"
        assert keys.OPS == "ops"
        assert keys.ASSETS == "assets"
        assert keys.FORM == "form"
        assert keys.CSRF == "csrf"
        assert keys.REQUEST_ID == "request_id"

    def test_patch_key_values(self) -> None:
        assert keys.OP == "op"
        assert keys.TARGET == "target"
        assert keys.HTML == "html"

    def test_asset_key_values(self) -> None:
        assert keys.KIND == "kind"
        assert keys.URL == "url"

    def test_form_meta_key_values(self) -> None:
        assert keys.UID == "uid"
        assert keys.VALID == "valid"
        assert keys.ERRORS == "errors"

    def test_target_selector_values(self) -> None:
        assert keys.ZONE == "zone"
        assert keys.FORM_SELECTOR == "form"


class TestSerializerEnvelopeShareKeys:
    """The serializer and the test envelope view read the same wire keys."""

    def test_round_trip_through_partial_envelope(self) -> None:
        envelope = Envelope(
            version="v1",
            ops=(Patch(op="morph", target={"zone": "list"}, html="<div></div>"),),
            assets=(Asset(kind="css", url="/a.css"),),
            form=FormMeta(uid="ab12", valid=False, errors={"name": ["required"]}),
            csrf={"token": "t"},
            request_id="r1",
        )
        view = PartialEnvelope(envelope.as_dict())
        assert view.version == "v1"
        assert view.op_verbs() == ["morph"]
        assert view.zone_targets() == ["list"]
        assert view.assets == [{"kind": "css", "url": "/a.css"}]
        assert view.form_meta() == {
            "uid": "ab12",
            "valid": False,
            "errors": {"name": ["required"]},
        }

    def test_form_selector_round_trips(self) -> None:
        envelope = Envelope(
            version="v1",
            ops=(Patch(op="morph", target={"form": "ab12"}, html="<form></form>"),),
        )
        view = PartialEnvelope(envelope.as_dict())
        assert view.form_targets() == ["ab12"]

    def test_csrf_and_request_id_keys_match(self) -> None:
        data = Envelope(version="v1", csrf={"token": "t"}, request_id="r1").as_dict()
        assert data[keys.CSRF] == {"token": "t"}
        assert data[keys.REQUEST_ID] == "r1"


class TestProducersWriteTargetSelectorKeys:
    """Every target/extras producer writes the wire keys from keys.py."""

    def test_view_zone_patch_targets_zone_key(self) -> None:
        patches = Patches("v1")
        _patch_zone(patches, "list", "<ul></ul>", None)
        view = PartialEnvelope(patches.envelope().as_dict())
        assert view.zone_targets() == ["list"]
        assert view.targets() == [{keys.ZONE: "list"}]

    def test_morph_form_targets_form_selector_key(self) -> None:
        patches = Patches("v1").morph_form("ab12", "<form></form>")
        view = PartialEnvelope(patches.envelope().as_dict())
        assert view.form_targets() == ["ab12"]
        assert view.targets() == [{keys.FORM_SELECTOR: "ab12"}]

    def test_layer_open_seeds_zone_extras_key(self) -> None:
        patches = Patches("v1").layer_open(zone="cart")
        view = PartialEnvelope(patches.envelope().as_dict())
        assert view.ops[0][keys.ZONE] == "cart"
