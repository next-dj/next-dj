import json
import logging
from datetime import timedelta
from typing import Any

import pytest
from django.contrib.auth import get_user_model, login
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.http import HttpRequest
from django.test import Client, RequestFactory, override_settings
from django.urls import include, path, reverse
from django.utils import timezone

from next.realtime import TicketIntrospectionView
from next.realtime.messages import Message
from next.realtime.tickets import TicketClaims
from next.testing.realtime import SocketTicket, issue_socket_ticket
from tests.support.realtime import ROOM_AUTHORIZER_REF


TOKEN = "gateway-secret"
AUTHORIZATION = f"Bearer {TOKEN}"
DENIED = {"error": "denied"}


class HeaderTokenView(TicketIntrospectionView):
    def authorize(self, request: HttpRequest) -> bool:
        return request.headers.get("X-Gateway") == "trusted"


class TransformServingView(TicketIntrospectionView):
    def grant_transformed(
        self, claims: TicketClaims, user: object, expires_in: int | None
    ) -> dict[str, Any] | None:
        if claims.transform_ref != f"{__name__}.skip_everything":
            return None
        body = self.grant(claims, user, expires_in)
        return {**body, "transform": claims.transform_ref}


class BodyOnlyView(TicketIntrospectionView):
    def grant(
        self, claims: TicketClaims, user: object, expires_in: int | None
    ) -> dict[str, Any]:
        del user, expires_in
        return {"rooms": list(claims.topics)}


def skip_everything(message: Message) -> None:
    del message


def keep_everything(message: Message) -> Message:
    return message


def name_guest(_user: object, _topic: str) -> dict[str, object]:
    return {"name": "guest"}


urlpatterns = [
    path("gateway/", include("next.realtime.urls")),
    path("custom/", HeaderTokenView.as_view(), name="custom"),
    path("transforms/", TransformServingView.as_view(), name="transforms"),
    path("body/", BodyOnlyView.as_view(), name="body"),
]

pytestmark = [pytest.mark.django_db(transaction=True), pytest.mark.urls(__name__)]


@pytest.fixture(autouse=True)
def _gateway_token():
    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"GATEWAY_TOKEN": TOKEN}}):
        yield


@pytest.fixture()
def ticket() -> SocketTicket:
    return issue_socket_ticket(
        ("room:42", "room:7"), authorizer_ref=ROOM_AUTHORIZER_REF
    )


def _cookie(session_key: str) -> str:
    return f"csrftoken=abc; sessionid={session_key}; theme=dark"


def _introspect(
    body: object,
    *,
    authorization: str | None = AUTHORIZATION,
    url: str | None = None,
    **headers: str,
):
    if authorization is not None:
        headers["Authorization"] = authorization
    return Client(enforce_csrf_checks=True).post(
        url or reverse("next_realtime:ticket"),
        data=body if isinstance(body, str) else json.dumps(body),
        content_type="application/json",
        headers=headers,
    )


def _body(ticket: SocketTicket) -> dict[str, str]:
    return {"ticket": ticket.grant.token, "cookie": _cookie(ticket.session_key)}


def _assert_denied(response) -> None:
    assert response.status_code == 403
    assert response.json() == DENIED
    assert "no-store" in response["Cache-Control"]


def test_returns_the_grant_of_an_anonymous_session(ticket: SocketTicket) -> None:
    response = _introspect(_body(ticket))
    assert response.status_code == 200
    payload = response.json()
    assert payload["topics"] == ["room:42", "room:7"]
    assert payload["user"] is None
    assert 0 < payload["expires_in"] <= SessionStore().get_session_cookie_age()
    assert set(payload) == {"topics", "user", "expires_in"}
    assert "no-store" in response["Cache-Control"]
    assert ticket.session_key not in response.content.decode()


def test_expires_in_is_what_the_stored_row_has_left(ticket: SocketTicket) -> None:
    Session.objects.filter(session_key=ticket.session_key).update(
        expire_date=timezone.now() + timedelta(seconds=10)
    )
    assert 0 < _introspect(_body(ticket)).json()["expires_in"] <= 10


def test_a_store_without_a_readable_deadline_leaves_expires_in_out() -> None:
    with override_settings(SESSION_ENGINE="django.contrib.sessions.backends.cache"):
        ticket = issue_socket_ticket(("room:1",), authorizer_ref=ROOM_AUTHORIZER_REF)
        response = _introspect(_body(ticket))
    assert response.status_code == 200
    assert set(response.json()) == {"topics", "user"}


def test_returns_the_user_id_of_a_logged_in_session() -> None:
    user = get_user_model().objects.create_user("ada", password="pw")
    request = RequestFactory().get("/")
    request.session = SessionStore()
    login(request, user)
    request.session.save()
    ticket = issue_socket_ticket(
        ("room:1",), authorizer_ref=ROOM_AUTHORIZER_REF, session=request.session
    )
    response = _introspect(_body(ticket))
    assert response.status_code == 200
    assert response.json()["user"] == str(user.pk)


@pytest.mark.parametrize(
    "authorization",
    [None, "", "Bearer", "Bearer wrong", f"Basic {TOKEN}", f"Token {TOKEN}"],
)
def test_refuses_a_caller_without_the_bearer(
    ticket: SocketTicket, authorization: str | None
) -> None:
    _assert_denied(_introspect(_body(ticket), authorization=authorization))


def test_accepts_a_lowercase_scheme(ticket: SocketTicket) -> None:
    response = _introspect(_body(ticket), authorization=f"bearer {TOKEN}")
    assert response.status_code == 200


@pytest.mark.parametrize("token", [None, "", 42])
def test_refuses_every_caller_without_a_configured_token(
    ticket: SocketTicket, token: object
) -> None:
    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"GATEWAY_TOKEN": token}}):
        _assert_denied(_introspect(_body(ticket)))


@pytest.mark.parametrize("token", [None, ""])
def test_an_empty_bearer_never_matches_a_missing_token(
    ticket: SocketTicket, token: str | None
) -> None:
    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"GATEWAY_TOKEN": token}}):
        _assert_denied(_introspect(_body(ticket), authorization="Bearer "))


def test_refuses_every_caller_under_the_stock_default(ticket: SocketTicket) -> None:
    with override_settings(NEXT_FRAMEWORK={"REALTIME": {}}):
        _assert_denied(_introspect(_body(ticket), authorization=""))


def test_refusals_share_one_answer(ticket: SocketTicket) -> None:
    other = SessionStore()
    other.create()
    responses = [
        _introspect(_body(ticket), authorization="Bearer wrong"),
        _introspect({"ticket": "forged", "cookie": _cookie(ticket.session_key)}),
        _introspect(
            {"ticket": ticket.grant.token, "cookie": _cookie(other.session_key)}
        ),
        _introspect({"ticket": ticket.grant.token, "cookie": "theme=dark"}),
    ]
    for response in responses:
        _assert_denied(response)
    assert len({response.content for response in responses}) == 1


def test_refuses_a_ticket_whose_session_is_gone(ticket: SocketTicket) -> None:
    ticket.session.delete()
    _assert_denied(_introspect(_body(ticket)))


def test_refuses_a_topic_the_authorizer_denies() -> None:
    ticket = issue_socket_ticket(
        ("room:1", "poll:1"), authorizer_ref=ROOM_AUTHORIZER_REF
    )
    _assert_denied(_introspect(_body(ticket)))


def test_refuses_a_ticket_naming_a_transform() -> None:
    ticket = issue_socket_ticket(
        ("room:1",),
        authorizer_ref=ROOM_AUTHORIZER_REF,
        transform_ref=f"{__name__}.skip_everything",
    )
    _assert_denied(_introspect(_body(ticket)))


def test_refuses_a_ticket_whose_authorizer_no_longer_imports() -> None:
    ticket = issue_socket_ticket(("room:1",), authorizer_ref=f"{__name__}.missing")
    _assert_denied(_introspect(_body(ticket)))


def test_logs_the_reason_it_hides_from_the_caller(
    ticket: SocketTicket, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="next.realtime.views"):
        _introspect({"ticket": "forged", "cookie": _cookie(ticket.session_key)})
    assert "invalid or expired" in caplog.text


@pytest.mark.parametrize(
    "body",
    [
        "not json",
        "\xff",
        pytest.param("[" * 200_000, id="deeply-nested"),
        [],
        {"ticket": "t"},
        {"cookie": "sessionid=x"},
        {"ticket": 1, "cookie": "sessionid=x"},
    ],
)
def test_an_authorized_caller_with_a_malformed_body_gets_400(body: object) -> None:
    response = _introspect(body)
    assert response.status_code == 400
    assert response.json() == {"error": "invalid"}
    assert "no-store" in response["Cache-Control"]


def test_a_malformed_body_without_the_bearer_is_still_denied() -> None:
    _assert_denied(_introspect("not json", authorization="Bearer wrong"))


@pytest.mark.parametrize("method", ["get", "head", "put", "options", "delete"])
def test_only_post_is_allowed(method: str) -> None:
    response = getattr(Client(), method)(reverse("next_realtime:ticket"))
    assert response.status_code == 405
    assert response["Allow"] == "POST"


def test_skips_csrf_and_the_login_middleware(ticket: SocketTicket) -> None:
    with override_settings(
        MIDDLEWARE=[
            "django.contrib.sessions.middleware.SessionMiddleware",
            "django.middleware.csrf.CsrfViewMiddleware",
            "django.contrib.auth.middleware.AuthenticationMiddleware",
            "django.contrib.auth.middleware.LoginRequiredMiddleware",
        ]
    ):
        response = _introspect(_body(ticket))
    assert response.status_code == 200


def test_a_subclass_swaps_the_caller_check(ticket: SocketTicket) -> None:
    url = reverse("custom")
    _assert_denied(
        _introspect(_body(ticket), authorization=None, url=url, **{"X-Gateway": "x"})
    )
    response = _introspect(
        _body(ticket), authorization=None, url=url, **{"X-Gateway": "trusted"}
    )
    assert response.status_code == 200


def _transform_ticket(transform: str = "skip_everything") -> SocketTicket:
    return issue_socket_ticket(
        ("room:1",),
        authorizer_ref=ROOM_AUTHORIZER_REF,
        transform_ref=f"{__name__}.{transform}",
    )


def test_a_subclass_serves_a_transform_ticket_in_its_own_shape() -> None:
    ticket = _transform_ticket()
    response = _introspect(_body(ticket), url=reverse("transforms"))
    assert response.status_code == 200
    payload = response.json()
    assert payload["transform"] == f"{__name__}.skip_everything"
    assert payload["topics"] == ["room:1"]
    assert "no-store" in response["Cache-Control"]


def test_a_subclass_refuses_a_transform_it_does_not_serve(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ticket = _transform_ticket("keep_everything")
    with caplog.at_level(logging.INFO, logger="next.realtime.views"):
        _assert_denied(_introspect(_body(ticket), url=reverse("transforms")))
    assert "transform" in caplog.text


def test_a_served_transform_still_needs_a_live_session() -> None:
    ticket = _transform_ticket()
    ticket.session.delete()
    _assert_denied(_introspect(_body(ticket), url=reverse("transforms")))


def test_a_ticket_without_a_transform_skips_the_transform_hook(
    ticket: SocketTicket,
) -> None:
    response = _introspect(_body(ticket), url=reverse("transforms"))
    assert response.status_code == 200
    assert "transform" not in response.json()


def test_a_subclass_changing_the_body_still_refuses_a_transform() -> None:
    plain = issue_socket_ticket(("room:1",), authorizer_ref=ROOM_AUTHORIZER_REF)
    response = _introspect(_body(plain), url=reverse("body"))
    assert response.json() == {"rooms": ["room:1"]}
    _assert_denied(_introspect(_body(_transform_ticket()), url=reverse("body")))


@pytest.mark.parametrize(
    ("presence", "mode"),
    [(True, "count"), (f"{__name__}.name_guest", "members")],
    ids=["count", "members"],
)
def test_grant_names_the_presence_mode_but_not_its_reference(
    presence: object, mode: str
) -> None:
    ticket = issue_socket_ticket(
        ("room:1",), authorizer_ref=ROOM_AUTHORIZER_REF, presence=presence
    )
    response = _introspect(_body(ticket))
    assert response.status_code == 200
    assert response.json()["presence"] == mode
    assert "name_guest" not in response.content.decode()


def test_ticket_with_an_unimportable_identify_is_denied() -> None:
    ticket = issue_socket_ticket(
        ("room:1",), authorizer_ref=ROOM_AUTHORIZER_REF, presence=f"{__name__}.gone"
    )
    _assert_denied(_introspect(_body(ticket)))
