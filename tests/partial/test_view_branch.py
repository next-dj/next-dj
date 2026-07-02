from django.test import Client

from next.partial.headers import CONTENT_TYPE
from next.testing import NextClient, envelope_of


class TestZoneGetEnvelope:
    """A zone GET returns one envelope morphing every named zone."""

    def test_batch_returns_morphs_for_both_zones(self) -> None:
        response = NextClient().get_zones("/zoned/", ("alpha", "beta"))
        assert response.status_code == 200
        assert response["Content-Type"] == CONTENT_TYPE
        envelope = envelope_of(response)
        # The zoned page registers a serialize=True provider, so the batch
        # ends with the context op carrying its js-context delta.
        assert envelope.op_verbs() == ["morph", "morph", "context"]
        assert envelope.zone_targets() == ["alpha", "beta"]

    def test_zone_html_is_wrapped_body(self) -> None:
        response = NextClient().get_zones("/zoned/", "alpha")
        envelope = envelope_of(response)
        assert envelope.html_for_zone("alpha") == (
            '<div data-next-zone="alpha"><p>alpha hi</p></div>'
        )

    def test_envelope_stamps_version(self) -> None:
        response = NextClient().get_zones("/zoned/", "alpha")
        assert response["X-Next-Version"] == envelope_of(response).version

    def test_assets_manifest_present(self) -> None:
        response = NextClient().get_zones("/zoned/", "alpha")
        assert isinstance(envelope_of(response).assets, list)

    def test_partial_vary_headers_set(self) -> None:
        response = NextClient().get_zones("/zoned/", "alpha")
        vary = response.get("Vary", "")
        assert "X-Next-Request" in vary
        assert "X-Next-Zone" in vary


class TestZoneGetBadRequests:
    """An unknown zone or a zone in a dynamic body is a 400 before render."""

    def test_unknown_zone_is_bad_request(self) -> None:
        response = NextClient().get_zones("/zoned/", "ghost")
        assert response.status_code == 400
        assert response.content == b"unknown zone"

    def test_unknown_zone_body_hides_declared_zones(self) -> None:
        response = NextClient().get_zones("/zoned/", "ghost")
        assert b"ghost" not in response.content
        assert b"alpha" not in response.content

    def test_zone_in_dynamic_body_is_bad_request(self) -> None:
        response = NextClient().get_zones("/dynamic/", "ghost")
        assert response.status_code == 400
        assert response.content == b"zone in dynamic body"


class TestZoneGetVersionConflict:
    """A stale version is a 409 on safe methods and is executed on unsafe ones."""

    def test_stale_version_on_get_is_conflict(self) -> None:
        response = NextClient().get_zones("/zoned/", "alpha", version="stale")
        assert response.status_code == 409
        assert response.content == b""

    def test_empty_version_is_not_a_conflict(self) -> None:
        # A browser opening a zone before it has learned a version sends an
        # empty X-Next-Version, which asserts nothing and must render.
        response = NextClient().get(
            "/zoned/",
            HTTP_X_NEXT_REQUEST="1",
            HTTP_X_NEXT_ZONE="alpha",
            HTTP_X_NEXT_VERSION="",
        )
        assert response.status_code == 200
        assert envelope_of(response).zone_targets() == ["alpha"]

    def test_matching_version_renders(self) -> None:
        client = NextClient()
        version = envelope_of(client.get_zones("/zoned/", "alpha")).version
        response = client.get_zones("/zoned/", "alpha", version=version)
        assert response.status_code == 200

    def test_stale_version_on_post_is_executed(self) -> None:
        client = NextClient(enforce_csrf_checks=False)
        response = client.post(
            "/zoned/",
            data={},
            HTTP_X_NEXT_REQUEST="1",
            HTTP_X_NEXT_ZONE="alpha",
            HTTP_X_NEXT_VERSION="stale",
        )
        assert response.status_code == 200
        assert envelope_of(response).zone_targets() == ["alpha"]


class TestZoneGetMergeIntent:
    """A merge header turns a zone GET into a server-authored merge patch."""

    def test_append_intent_yields_an_append_patch(self) -> None:
        response = NextClient().get_zones(
            "/zoned/", "alpha", HTTP_X_NEXT_MERGE="append"
        )
        assert response.status_code == 200
        envelope = envelope_of(response)
        assert envelope.op_verbs() == ["append", "context"]
        assert envelope.zone_targets() == ["alpha"]

    def test_append_patch_carries_key_dedupe(self) -> None:
        response = NextClient().get_zones(
            "/zoned/", "alpha", HTTP_X_NEXT_MERGE="append"
        )
        assert envelope_of(response).ops[0]["dedupe"] == "key"

    def test_prepend_intent_yields_a_prepend_patch(self) -> None:
        response = NextClient().get_zones(
            "/zoned/", "alpha", HTTP_X_NEXT_MERGE="prepend"
        )
        assert envelope_of(response).op_verbs() == ["prepend", "context"]

    def test_unknown_merge_value_falls_back_to_morph(self) -> None:
        response = NextClient().get_zones("/zoned/", "alpha", HTTP_X_NEXT_MERGE="bogus")
        assert envelope_of(response).op_verbs() == ["morph", "context"]

    def test_merge_response_varies_on_merge_header(self) -> None:
        response = NextClient().get_zones(
            "/zoned/", "alpha", HTTP_X_NEXT_MERGE="append"
        )
        assert "X-Next-Merge" in response.get("Vary", "")


class TestFullRenderRegression:
    """Without the partial switch the page render stays byte-for-byte."""

    def test_full_render_keeps_zones_inline(self) -> None:
        response = Client().get("/zoned/")
        assert response.status_code == 200
        assert response["Content-Type"] == "text/html; charset=utf-8"
        body = response.content
        assert b'data-next-zone="alpha"' in body
        assert b'data-next-zone="beta"' in body

    def test_full_render_shows_lazy_placeholder(self) -> None:
        response = Client().get("/zoned/")
        body = response.content.decode()
        assert "loading" in body
        assert "later hi" not in body

    def test_full_render_has_no_partial_headers(self) -> None:
        response = Client().get("/zoned/")
        assert "X-Next-Version" not in response
        assert response["Content-Type"] != CONTENT_TYPE

    def test_partial_switch_without_zone_falls_through(self) -> None:
        response = Client().get("/zoned/", HTTP_X_NEXT_REQUEST="1")
        assert response.status_code == 200
        assert response["Content-Type"] == "text/html; charset=utf-8"
