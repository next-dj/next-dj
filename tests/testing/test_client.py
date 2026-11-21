import re
from pathlib import Path
from typing import override

import pytest
from django.conf import settings
from django.http import HttpRequest, HttpResponse
from django.test import Client, override_settings

from next.csrf import csrf_url
from next.forms.uid import ORIGIN_FIELD_NAME
from next.partial.backends import JsonPartialProtocolBackend
from next.partial.envelope import Asset, Envelope, FormMeta, Patch
from next.testing import (
    NextClient,
    PartialEnvelope,
    envelope_of,
    override_next_settings,
)
from tests.forms import actions
from tests.pages.checks.metadata.trees import COMPONENTS
from tests.support import TWIN_ACTION, routed, write_page
from tests.support.forms import post_without_origin


# Importing `actions` registers the baseline form actions, so keep it bound.
_BASELINE_ACTIONS = actions


class ReversedJsonBackend(JsonPartialProtocolBackend):
    """A wire format nothing but this backend can read, named from settings."""

    @override
    def serialize_envelope(self, envelope: Envelope) -> bytes:
        return super().serialize_envelope(envelope)[::-1]

    @override
    def deserialize_envelope(self, body: bytes) -> Envelope:
        return super().deserialize_envelope(body[::-1])


class TestNextClientByPage:
    """`page` routes a shared action name to the page the test means."""

    @pytest.mark.django_db()
    @pytest.mark.parametrize("label", ["create", "edit"])
    def test_post_action_reaches_the_named_page(self, twins: Path, label: str) -> None:
        response = NextClient().post_action(
            TWIN_ACTION, origin=f"/{label}/", page=twins / label
        )
        assert response.content == label.encode()

    @pytest.mark.django_db()
    def test_get_action_url_names_the_page_of_post_action(self, twins: Path) -> None:
        client = NextClient()
        url = client.get_action_url(TWIN_ACTION, page=twins / "edit")
        assert client.post(url, {ORIGIN_FIELD_NAME: "/edit/"}).content == b"edit"

    def test_a_bare_name_is_refused(self, twins: Path) -> None:
        with pytest.raises(LookupError, match="several files"):
            NextClient().post_action(TWIN_ACTION, origin="/create/")


class TestNextClient:
    """NextClient extends django.test.Client with form-action shortcuts."""

    def test_get_action_url_returns_known_url(self) -> None:
        client = NextClient()
        url = client.get_action_url("simple_form")
        assert "_next/form/" in url

    @pytest.mark.django_db()
    def test_post_action_dispatches_form(self) -> None:
        client = NextClient(enforce_csrf_checks=False)
        response = client.post_action(
            "simple_form_redirect", {"name": "Carol"}, origin="/"
        )
        assert response.status_code == 302
        assert response["Location"] == "/done/"

    @pytest.mark.django_db()
    def test_post_action_origin_fills_hidden_field(self) -> None:
        client = NextClient(enforce_csrf_checks=False)
        response = client.post_action("simple_form", {"name": ""}, origin="/")
        assert response.status_code == 200
        assert response["X-Next-Form"] == "invalid"

    @pytest.mark.django_db()
    def test_a_post_without_an_origin_is_refused_before_dispatch(self) -> None:
        client = NextClient(enforce_csrf_checks=False)
        response = post_without_origin(client, "simple_form", {"name": ""})
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
        assert response["X-Next-Form"] == "invalid"
        assert b"<p>test page</p>" in response.content


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

    def test_headers_mapping_travels_beside_the_partial_ones(self) -> None:
        response = NextClient().get_zones(
            "/zoned/", "alpha", headers={"X-Custom": "present"}
        )
        assert response.wsgi_request.headers["X-Custom"] == "present"
        assert response.wsgi_request.headers["X-Next-Zone"] == "alpha"


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
            "simple_form", {"name": ""}, origin="/", partial=("alpha", "beta")
        )
        assert response.wsgi_request.headers["X-Next-Zone"] == "alpha,beta"

    @pytest.mark.django_db()
    def test_a_single_zone_name_reaches_the_request(self) -> None:
        client = NextClient(enforce_csrf_checks=False)
        response = client.post_action(
            "simple_form", {"name": ""}, origin="/", partial="alpha"
        )
        assert response.wsgi_request.headers["X-Next-Zone"] == "alpha"
        assert response.wsgi_request.headers["X-Next-Request"] == "1"

    @pytest.mark.django_db()
    def test_version_header_reaches_the_request(self) -> None:
        client = NextClient(enforce_csrf_checks=False)
        response = client.post_action(
            "simple_form", {"name": ""}, origin="/", partial=True, version="9f3c"
        )
        assert response.wsgi_request.headers["X-Next-Version"] == "9f3c"

    @pytest.mark.django_db()
    def test_headers_mapping_travels_beside_the_partial_ones(self) -> None:
        client = NextClient(enforce_csrf_checks=False)
        response = client.post_action(
            "simple_form",
            {"name": ""},
            origin="/",
            partial=True,
            headers={"X-Custom": "present"},
        )
        assert response.wsgi_request.headers["X-Custom"] == "present"
        assert response.wsgi_request.headers["X-Next-Request"] == "1"


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

    def test_zone_targets_read_the_zone_a_refresh_names_on_itself(self) -> None:
        envelope = PartialEnvelope(
            {
                "version": "v1",
                "ops": [
                    {"op": "morph", "target": {"zone": "a"}, "html": "<div></div>"},
                    {"op": "refresh", "zone": "b"},
                    {"op": "layer.open", "zone": "c"},
                    {"op": "refresh"},
                ],
            }
        )
        assert envelope.zone_targets() == ["a", "b"]

    def test_version_op_verbs_and_targets(self) -> None:
        envelope = envelope_of(NextClient().get_zones("/zoned/", ("alpha", "beta")))
        assert isinstance(envelope, PartialEnvelope)
        assert envelope.version == "0"
        # The zoned page registers a serialize=True provider, so a context op ends it.
        assert envelope.op_verbs() == ["morph", "morph", "context"]
        assert envelope.targets() == [{"zone": "alpha"}, {"zone": "beta"}, None]

    def test_assets_manifest_lists_co_located_css(self) -> None:
        envelope = envelope_of(NextClient().get_zones("/zoned/", "alpha"))
        assert {
            "kind": "css",
            "url": "/static/next/tests/site_pages/zoned.css",
            "load": "link",
        } in envelope.assets

    def test_html_for_zone_returns_payload(self) -> None:
        envelope = envelope_of(NextClient().get_zones("/zoned/", "alpha"))
        assert envelope.html_for_zone("alpha").startswith('<div data-next-zone="alpha"')

    def test_html_for_zone_raises_for_missing_zone(self) -> None:
        envelope = envelope_of(NextClient().get_zones("/zoned/", "alpha"))
        with pytest.raises(AssertionError, match="no op targets zone"):
            envelope.html_for_zone("absent")


class TestEnvelopeReadsThroughTheBackend:
    """The view decodes with the backend that wrote the body, not with JSON."""

    def test_a_replaced_wire_format_still_reads(self) -> None:
        with override_next_settings(
            PARTIAL_BACKENDS=[
                {"BACKEND": "tests.testing.test_client.ReversedJsonBackend"}
            ]
        ):
            response = NextClient().get_zones("/zoned/", "alpha")

            assert response.content.startswith(b"}")
            assert envelope_of(response).zone_targets() == ["alpha"]


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
        view = PartialEnvelope.from_envelope(envelope)
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
        with pytest.raises(KeyError, match="version"):
            _ = PartialEnvelope({"ops": []}).ops

    def test_op_without_a_verb_raises(self) -> None:
        with pytest.raises(KeyError, match="op"):
            _ = PartialEnvelope({"version": "v1", "ops": [{"html": "<p></p>"}]}).ops

    def test_asset_without_a_url_raises(self) -> None:
        with pytest.raises(KeyError, match="url"):
            _ = PartialEnvelope({"version": "v1", "assets": [{"kind": "css"}]}).assets

    def test_form_meta_without_errors_raises(self) -> None:
        view = PartialEnvelope(
            {"version": "v1", "form": {"uid": "ab12", "valid": True}}
        )
        with pytest.raises(KeyError, match="errors"):
            view.form_meta()

    def test_from_envelope_data_equals_as_dict(self) -> None:
        envelope = Envelope(
            version="v1", ops=(Patch(op="toast", extras={"text": "x"}),)
        )
        assert PartialEnvelope.from_envelope(envelope).data == envelope.as_dict()

    def test_from_envelope_data_is_memoised_across_reads(self) -> None:
        view = PartialEnvelope(envelope=Envelope(version="v1", ops=()))
        assert view.data is view.data
        view.data["extra"] = 1
        assert view.data["extra"] == 1

    def test_envelope_of_skips_the_dict_round_trip(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[object] = []
        original = Envelope.from_dict

        def spy(data: dict[str, object]) -> Envelope:
            calls.append(data)
            return original(data)

        monkeypatch.setattr(Envelope, "from_dict", spy)
        response = NextClient().get_zones("/zoned/", ("alpha",))
        assert envelope_of(response).version == "0"
        assert len(calls) == 1

    def test_raw_payload_stays_readable(self) -> None:
        assert PartialEnvelope({"ops": []}).data == {"ops": []}


_CARD = (
    'from next.components import component\n\ncomponent.defer("load", props=("sku",))\n'
)


@pytest.fixture()
def trip_pages(tmp_path: Path) -> Path:
    root = tmp_path / "pages"
    folder = root / "_components" / "card"
    folder.mkdir(parents=True)
    (folder / "component.py").write_text(_CARD)
    (folder / "component.djx").write_text("<p>card {{ sku }}</p>")
    (root / "layout.djx").write_text("<html><body>{% template %}</body></html>")
    write_page(
        root,
        "shop",
        "from next import page\n\npage.public()\n",
        body='{% component "card" sku="a" %}',
    )
    return root


@pytest.mark.django_db()
class TestGetBoundary:
    """`get_boundary` mints a token and reloads the component through the view."""

    @pytest.mark.parametrize(
        "transport",
        [None, "next.suspense.QueryTransport", "next.suspense.BodyTransport"],
    )
    def test_the_component_reloads_through_each_transport(
        self, trip_pages: Path, transport: str | None
    ) -> None:
        options = {} if transport is None else {"TRANSPORT": transport}
        with routed(
            trip_pages,
            COMPONENT_BACKENDS=COMPONENTS,
            DEFER={"TOKENS": {"OPTIONS": options}},
        ):
            envelope = NextClient().get_boundary("/shop/", "card", sku="b")
        (op,) = envelope.ops
        assert op["html"] == "<p>card b</p>"
        assert op["s"] == "ready"

    def test_an_unknown_component_raises(self, trip_pages: Path) -> None:
        with (
            routed(trip_pages, COMPONENT_BACKENDS=COMPONENTS),
            pytest.raises(LookupError, match="ghost"),
        ):
            NextClient().boundary_token("/shop/", "ghost")

    def test_a_url_without_a_page_raises(self, trip_pages: Path) -> None:
        with (
            routed(trip_pages, COMPONENT_BACKENDS=COMPONENTS),
            pytest.raises(LookupError, match="no page view"),
        ):
            NextClient().boundary_token(csrf_url(), "card")


def carried(request: HttpRequest) -> str:
    """Bind what the request carries, so a session or user the view lacks refuses."""
    return f"session={hasattr(request, 'session')} user={hasattr(request, 'user')}"


_CARRIED = {"TOKENS": {"OPTIONS": {"BINDING": f"{__name__}.carried"}}}
_SESSION_MIDDLEWARE = "django.contrib.sessions.middleware.SessionMiddleware"
_AUTH_MIDDLEWARE = "django.contrib.auth.middleware.AuthenticationMiddleware"
_MESSAGE_MIDDLEWARE = "django.contrib.messages.middleware.MessageMiddleware"


@pytest.mark.django_db()
class TestBoundaryTokenWithoutContribApps:
    """The minted token carries only what the view of a slimmer project sees."""

    @pytest.mark.parametrize(
        ("app", "middleware"),
        [
            (
                "django.contrib.sessions",
                {_SESSION_MIDDLEWARE, _AUTH_MIDDLEWARE, _MESSAGE_MIDDLEWARE},
            ),
            ("django.contrib.auth", {_AUTH_MIDDLEWARE}),
        ],
    )
    def test_the_component_reloads_without_the_app(
        self, trip_pages: Path, app: str, middleware: set[str]
    ) -> None:
        installed = [name for name in settings.INSTALLED_APPS if name != app]
        layers = [name for name in settings.MIDDLEWARE if name not in middleware]
        client = NextClient()
        with (
            override_settings(INSTALLED_APPS=installed, MIDDLEWARE=layers),
            routed(trip_pages, COMPONENT_BACKENDS=COMPONENTS, DEFER=_CARRIED),
        ):
            envelope = client.get_boundary("/shop/", "card", sku="b")
        (op,) = envelope.ops
        assert op["html"] == "<p>card b</p>"
        assert (settings.SESSION_COOKIE_NAME in client.cookies) is (
            app != "django.contrib.sessions"
        )


_TRIP_WRAPPER = re.compile(
    r'id="(?P<id>b-[0-9a-f]+-[0-9]+)" data-next-boundary="(?P=id)" '
    r'data-next-boundary-key="(?P<key>[^"]*)"'
)


@pytest.mark.django_db()
class TestFollowBoundary:
    """`follow_boundary` reloads a wrapper of a rendered page with its own token."""

    @pytest.mark.parametrize(
        "transport",
        [None, "next.suspense.QueryTransport", "next.suspense.BodyTransport"],
    )
    def test_the_rendered_token_reloads_through_each_transport(
        self, trip_pages: Path, transport: str | None
    ) -> None:
        options = {} if transport is None else {"TRANSPORT": transport}
        with routed(
            trip_pages,
            COMPONENT_BACKENDS=COMPONENTS,
            DEFER={"TOKENS": {"OPTIONS": options}},
        ):
            client = NextClient()
            document = client.get("/shop/?q=1")
            sent: list[dict[str, object]] = []
            original = client.request

            def spy(**request: object):
                sent.append(request)
                return original(**request)

            client.request = spy
            envelope = client.follow_boundary(document, "card")
        [found] = _TRIP_WRAPPER.finditer(document.content.decode())
        (op,) = envelope.ops
        assert op["target"] == {"boundary": found["id"]}
        assert op["html"] == "<p>card a</p>"
        assert sent[0]["PATH_INFO"] == "/shop/"
        assert str(sent[0]["QUERY_STRING"]).startswith("q=1")
        assert sent[0]["HTTP_X_NEXT_ZONE"] == found["id"]

    @pytest.mark.parametrize("field", ["id", "key"])
    def test_the_wrapper_is_found_by_its_wire_id_or_stable_key(
        self, trip_pages: Path, field: str
    ) -> None:
        with routed(trip_pages, COMPONENT_BACKENDS=COMPONENTS):
            client = NextClient()
            document = client.get("/shop/")
            [found] = _TRIP_WRAPPER.finditer(document.content.decode())
            envelope = client.follow_boundary(document, found[field])
        assert envelope.ops[0]["html"] == "<p>card a</p>"

    def test_following_mints_nothing_and_reads_no_session(
        self, trip_pages: Path, django_assert_num_queries
    ) -> None:
        with routed(trip_pages, COMPONENT_BACKENDS=COMPONENTS):
            client = NextClient()
            document = client.get("/shop/")
            with django_assert_num_queries(0):
                envelope = client.follow_boundary(document, "card")
        assert envelope.ops[0]["s"] == "ready"

    def test_an_unknown_boundary_raises(self, trip_pages: Path) -> None:
        with routed(trip_pages, COMPONENT_BACKENDS=COMPONENTS):
            client = NextClient()
            document = client.get("/shop/")
            with pytest.raises(LookupError, match="ghost"):
                client.follow_boundary(document, "ghost")

    def test_a_response_no_client_sent_is_refused(self) -> None:
        with pytest.raises(TypeError, match="test client"):
            NextClient().follow_boundary(HttpResponse("<p>x</p>"), "card")

    @pytest.mark.parametrize("read", ["response", "envelope"])
    def test_a_zone_refresh_is_followed_through_its_envelope(
        self, trip_pages: Path, read: str
    ) -> None:
        write_page(
            trip_pages,
            "zoned",
            "from next import page\n\npage.public()\n",
            body='{% zone "list" %}{% component "card" sku="z" %}{% endzone %}',
        )
        with routed(trip_pages, COMPONENT_BACKENDS=COMPONENTS):
            client = NextClient()
            response = client.get_zones("/zoned/", "list")
            source = envelope_of(response) if read == "envelope" else response
            envelope = client.follow_boundary(source, "card")
        assert envelope.ops[0]["html"] == "<p>card z</p>"

    def test_a_zone_refresh_without_the_boundary_raises(self, trip_pages: Path) -> None:
        write_page(
            trip_pages,
            "zoned",
            "from next import page\n\npage.public()\n",
            body='{% zone "list" %}<p>empty</p>{% endzone %}',
        )
        with routed(trip_pages, COMPONENT_BACKENDS=COMPONENTS):
            client = NextClient()
            response = client.get_zones("/zoned/", "list")
            with pytest.raises(LookupError, match="card"):
                client.follow_boundary(response, "card")

    def test_a_hand_built_envelope_is_refused(self) -> None:
        with pytest.raises(TypeError, match="test client"):
            NextClient().follow_boundary(PartialEnvelope({"ops": []}), "card")
