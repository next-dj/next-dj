import pytest

from next.testing import NextClient, envelope_of


class TestZoneGetInlineAssets:
    """A zone GET ships the inline assets its body collected."""

    @pytest.mark.parametrize(
        ("zone", "kind", "body", "load"),
        [
            ("styled", "css", ".zone-styled { color: crimson; }", "link"),
            ("scripted", "js", 'console.log("zone scripted");', "script"),
        ],
        ids=["css", "js"],
    )
    def test_inline_asset_travels_with_its_body(
        self, zone: str, kind: str, body: str, load: str
    ) -> None:
        response = NextClient().get_zones("/zoned_inline/", zone)
        inline = [a for a in envelope_of(response).assets if a["url"] == ""]
        assert inline == [{"kind": kind, "url": "", "inline": body, "load": load}]

    def test_inline_asset_manifest_is_not_empty(self) -> None:
        response = NextClient().get_zones("/zoned_inline/", "styled")
        assert envelope_of(response).assets


class TestZoneGetUrlAssets:
    """URL-form assets keep their byte-stable wire shape."""

    def test_url_form_asset_travels(self) -> None:
        response = NextClient().get_zones("/zoned/", "alpha")
        assert envelope_of(response).assets == [
            {
                "kind": "css",
                "url": "/static/next/tests/site_pages/zoned.css",
                "load": "link",
            }
        ]

    def test_url_form_asset_carries_no_inline_key(self) -> None:
        response = NextClient().get_zones("/zoned/", "alpha")
        assert "inline" not in envelope_of(response).assets[0]


class TestZoneGetContextDelta:
    """A serialize provider is sent as a context op with wire values."""

    def test_serialize_provider_emits_context_op(self) -> None:
        response = NextClient().get_zones("/zoned/", "alpha")
        context_ops = [op for op in envelope_of(response).ops if op["op"] == "context"]
        assert context_ops == [{"op": "context", "data": {"flag": True}}]

    def test_context_op_is_last_after_the_morphs(self) -> None:
        response = NextClient().get_zones("/zoned/", ("alpha", "beta"))
        assert envelope_of(response).op_verbs() == ["morph", "morph", "context"]

    def test_context_values_are_wire_ready(self) -> None:
        response = NextClient().get_zones("/zoned_inline/", "styled")
        ops = envelope_of(response).ops
        data = next(op["data"] for op in ops if op["op"] == "context")
        assert data == {"seen": 7}


class TestZoneGetWithoutSerializeProvider:
    """A page with no serialize provider never emits an empty context op."""

    def test_no_context_op_when_delta_is_empty(self) -> None:
        response = NextClient().get_zones("/counted/", "alpha")
        assert "context" not in envelope_of(response).op_verbs()

    def test_no_assets_and_only_a_morph(self) -> None:
        response = NextClient().get_zones("/counted/", "alpha")
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["morph"]
        assert envelope.assets == []


class TestZoneGetMixedManifest:
    """One zone GET carries inline, URL, and js-context together."""

    def test_inline_url_and_context_in_one_envelope(self) -> None:
        response = NextClient().get_zones("/zoned_inline/", "mixed")
        envelope = envelope_of(response)
        assert {
            "kind": "css",
            "url": "",
            "inline": ".zone-mixed { color: navy; }",
            "load": "link",
        } in envelope.assets
        assert {
            "kind": "css",
            "url": "/static/next/tests/site_pages/zoned_inline.css",
            "load": "link",
        } in envelope.assets
        context_ops = [op for op in envelope.ops if op["op"] == "context"]
        assert context_ops == [{"op": "context", "data": {"seen": 7}}]


RELOAD_ASSET = {
    "kind": "js",
    "url": "/static/next/next.reload.min.js",
    "load": "script",
}


class TestZoneGetBundleNeeds:
    """A zone GET lists the declared bundle its zone needs among its assets."""

    def test_a_loading_zone_needs_the_reload_bundle(self) -> None:
        response = NextClient().get_zones("/zoned/", "later")
        assert RELOAD_ASSET in envelope_of(response).assets

    def test_a_keep_zone_needs_none(self) -> None:
        response = NextClient().get_zones("/zoned/", "alpha")
        assert RELOAD_ASSET not in envelope_of(response).assets

    def test_the_own_page_zone_carries_no_skeleton(self) -> None:
        response = NextClient().get_zones("/zoned/", "later")
        morph = envelope_of(response).ops[0]
        assert "data-next-loading" not in morph["html"]
