import pytest
from django.test import Client

from next.partial.envelope import Asset, Envelope, FormMeta, Patch
from next.testing import NextClient, PartialEnvelope, envelope_of
from tests.forms import actions


# `actions` registers the baseline form actions on import. Bind it so the
# module survives unused-import cleanup whatever the collection order is.
_BASELINE_ACTIONS = actions


class TestNextClient:
    """NextClient extends django.test.Client with form-action shortcuts."""

    def test_get_action_url_returns_known_url(self) -> None:
        client = NextClient()
        url = client.get_action_url("simple_form")
        assert "_next/form/" in url

    def test_post_action_dispatches_form(self) -> None:
        client = NextClient(enforce_csrf_checks=False)
        response = client.post_action("simple_form_redirect", {"name": "Carol"})
        assert response.status_code in (200, 302)

    @pytest.mark.django_db()
    def test_post_action_origin_fills_hidden_field(self) -> None:
        client = NextClient(enforce_csrf_checks=False)
        response = client.post_action("simple_form", {"name": ""}, origin="/")
        assert response.status_code == 200
        assert response["X-Next-Form"] == "invalid"

    def test_post_action_without_origin_keeps_protocol_raw(self) -> None:
        client = NextClient(enforce_csrf_checks=False)
        response = client.post_action("simple_form", {"name": ""})
        assert response.status_code == 400

    @pytest.mark.django_db()
    def test_post_action_data_origin_wins_over_keyword(self) -> None:
        client = NextClient(enforce_csrf_checks=False)
        response = client.post_action(
            "simple_form",
            {"name": "", "_next_form_origin": "/"},
            origin="/no/such/route/",
        )
        assert response.status_code == 200


class TestGetZones:
    """`get_zones` GETs a URL as a partial zone request."""

    def test_single_zone_returns_envelope(self) -> None:
        response = NextClient().get_zones("/zoned/", "alpha")
        assert response.status_code == 200
        assert envelope_of(response).zone_targets() == ["alpha"]

    def test_tuple_of_zones_batches(self) -> None:
        response = NextClient().get_zones("/zoned/", ("alpha", "beta"))
        assert envelope_of(response).zone_targets() == ["alpha", "beta"]

    def test_version_header_drives_conflict(self) -> None:
        response = NextClient().get_zones("/zoned/", "alpha", version="stale")
        assert response.status_code == 409

    def test_extra_headers_forwarded(self) -> None:
        response = NextClient().get_zones("/zoned/", "alpha", HTTP_X_CUSTOM="present")
        assert response.wsgi_request.headers["X-Custom"] == "present"


class TestPostActionPartialHeaders:
    """`post_action` stamps the partial, zone, and version headers."""

    @pytest.mark.django_db()
    def test_partial_post_returns_an_envelope(self) -> None:
        client = NextClient(enforce_csrf_checks=False)
        response = client.post_action(
            "simple_form", {"name": ""}, origin="/", partial=True
        )
        assert envelope_of(response).op_verbs() == ["morph"]

    @pytest.mark.django_db()
    def test_zones_header_reaches_the_request(self) -> None:
        client = NextClient(enforce_csrf_checks=False)
        response = client.post_action(
            "simple_form",
            {"name": ""},
            origin="/",
            partial=True,
            zones=("alpha", "beta"),
        )
        assert response.wsgi_request.headers["X-Next-Zone"] == "alpha,beta"

    @pytest.mark.django_db()
    def test_version_header_reaches_the_request(self) -> None:
        client = NextClient(enforce_csrf_checks=False)
        response = client.post_action(
            "simple_form", {"name": ""}, origin="/", partial=True, version="9f3c"
        )
        assert response.wsgi_request.headers["X-Next-Version"] == "9f3c"


class TestEnvelopeHelpers:
    """The structural envelope view answers questions about ops and targets."""

    def test_envelope_of_rejects_non_envelope(self) -> None:
        response = Client().get("/zoned/")
        with pytest.raises(AssertionError, match="not a patch envelope"):
            envelope_of(response)

    def test_toasts_filters_toast_ops(self) -> None:
        envelope = PartialEnvelope(
            {
                "version": "v1",
                "ops": [
                    {"op": "morph", "target": {"zone": "a"}, "html": "<div></div>"},
                    {"op": "toast", "text": "Saved", "variant": "success"},
                ],
            }
        )
        assert envelope.toasts() == [
            {"op": "toast", "text": "Saved", "variant": "success"}
        ]

    def test_version_op_verbs_and_targets(self) -> None:
        envelope = envelope_of(NextClient().get_zones("/zoned/", ("alpha", "beta")))
        assert isinstance(envelope, PartialEnvelope)
        assert envelope.version == "0"
        # The zoned page registers a serialize=True provider, so the batch
        # ends with the context op carrying its js-context delta.
        assert envelope.op_verbs() == ["morph", "morph", "context"]
        assert envelope.targets() == [{"zone": "alpha"}, {"zone": "beta"}, None]

    def test_assets_manifest_lists_co_located_css(self) -> None:
        envelope = envelope_of(NextClient().get_zones("/zoned/", "alpha"))
        assert {
            "kind": "css",
            "url": "/static/next/zoned.css",
            "load": "link",
        } in envelope.assets

    def test_html_for_zone_returns_payload(self) -> None:
        envelope = envelope_of(NextClient().get_zones("/zoned/", "alpha"))
        assert envelope.html_for_zone("alpha").startswith('<div data-next-zone="alpha"')

    def test_html_for_zone_raises_for_missing_zone(self) -> None:
        envelope = envelope_of(NextClient().get_zones("/zoned/", "alpha"))
        with pytest.raises(AssertionError, match="no op targets zone"):
            envelope.html_for_zone("absent")


class TestEnvelopeDecoding:
    """The view rebuilds the producer's own objects out of the wire mapping."""

    def test_decoding_is_the_inverse_of_the_producer(self) -> None:
        envelope = Envelope(
            version="v1",
            ops=(
                Patch(op="morph", target={"zone": "list"}, html="<div></div>"),
                Patch(op="toast", extras={"text": "Saved"}),
            ),
            assets=(
                Asset(kind="css", url="/a.css", load="link"),
                Asset(kind="js", url="", inline="console.log(1)"),
            ),
            form=FormMeta(uid="ab12", valid=False, errors={"name": ["required"]}),
            csrf={"token": "t"},
            request_id="r1",
        )
        data = envelope.as_dict()
        assert Envelope.from_dict(data) == envelope
        assert Envelope.from_dict(data).as_dict() == data

    def test_reads_every_accessor_off_the_rebuilt_objects(self) -> None:
        envelope = Envelope(
            version="v1",
            ops=(
                Patch(op="morph", target={"zone": "list"}, html="<div></div>"),
                Patch(op="morph", target={"form": "ab12"}, html="<form></form>"),
                Patch(op="toast", extras={"text": "Saved"}),
            ),
            assets=(Asset(kind="css", url="/a.css", load="link"),),
            form=FormMeta(uid="ab12", valid=True),
        )
        view = PartialEnvelope(envelope.as_dict())
        assert view.version == "v1"
        assert view.op_verbs() == ["morph", "morph", "toast"]
        assert view.targets() == [{"zone": "list"}, {"form": "ab12"}, None]
        assert view.zone_targets() == ["list"]
        assert view.form_targets() == ["ab12"]
        assert view.assets == [{"kind": "css", "url": "/a.css", "load": "link"}]
        assert view.form_meta() == {"uid": "ab12", "valid": True, "errors": {}}
        assert view.toasts() == [{"op": "toast", "text": "Saved"}]
        assert view.html_for_zone("list") == "<div></div>"

    def test_absent_collections_decode_to_empty(self) -> None:
        view = PartialEnvelope({"version": "v1"})
        assert view.ops == []
        assert view.assets == []
        assert view.form_meta() is None

    def test_null_form_decodes_to_none(self) -> None:
        view = PartialEnvelope({"version": "v1", "ops": [], "form": None})
        assert view.form_meta() is None

    def test_zone_op_without_html_answers_empty(self) -> None:
        view = PartialEnvelope(
            {"version": "v1", "ops": [{"op": "refresh", "target": {"zone": "a"}}]}
        )
        assert view.html_for_zone("a") == ""

    def test_returned_mappings_do_not_alias_the_payload(self) -> None:
        data = {
            "version": "v1",
            "ops": [{"op": "morph", "target": {"zone": "a"}, "html": "<p></p>"}],
        }
        view = PartialEnvelope(data)
        view.ops[0]["html"] = "mutated"
        view.targets()[0]["zone"] = "b"
        assert data["ops"] == [
            {"op": "morph", "target": {"zone": "a"}, "html": "<p></p>"}
        ]


class TestEnvelopeStrictness:
    """A payload that is not a whole envelope fails loudly instead of half answering."""

    def test_missing_version_raises(self) -> None:
        with pytest.raises(KeyError):
            _ = PartialEnvelope({"ops": []}).ops

    def test_op_without_a_verb_raises(self) -> None:
        with pytest.raises(KeyError):
            _ = PartialEnvelope({"version": "v1", "ops": [{"html": "<p></p>"}]}).ops

    def test_asset_without_a_url_raises(self) -> None:
        with pytest.raises(KeyError):
            _ = PartialEnvelope({"version": "v1", "assets": [{"kind": "css"}]}).assets

    def test_form_meta_without_errors_raises(self) -> None:
        view = PartialEnvelope(
            {"version": "v1", "form": {"uid": "ab12", "valid": True}}
        )
        with pytest.raises(KeyError):
            view.form_meta()

    def test_raw_payload_stays_readable(self) -> None:
        assert PartialEnvelope({"ops": []}).data == {"ops": []}
