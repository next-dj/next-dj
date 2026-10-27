import base64
import hashlib
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import create_autospec

import pytest
import time_machine
from django.conf import settings
from django.contrib.auth import (
    BACKEND_SESSION_KEY,
    HASH_SESSION_KEY,
    SESSION_KEY,
    get_user_model,
)
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.backends.file import SessionStore as FileStore
from django.contrib.sessions.middleware import SessionMiddleware
from django.core import signing
from django.db import connection
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils.crypto import salted_hmac

from next.realtime.errors import InvalidTicketError
from next.realtime.tickets import (
    _SALT,
    MAX_REFERENCE_LENGTH,
    TicketClaims,
    issue_ticket,
    resolve_claims,
    resolve_identify,
    ticket_max_age,
    verify_ticket,
    websocket_path,
)
from next.testing import override_next_settings


_AUTHORIZER = "tests.realtime.test_tickets.allow_room"
_TRANSFORM = "tests.realtime.test_tickets.drop_message"
_IDENTIFY = "tests.realtime.test_tickets.name_guest"

NOT_CALLABLE = "a constant"


def allow_room(_user: object, _topic: str) -> bool:
    return True


def drop_message(_message: object) -> None:
    return None


def name_guest(_user: object, _topic: str) -> dict[str, object]:
    return {"name": "guest"}


@pytest.mark.django_db()
def test_ticket_issued_for_a_session_verifies_its_topic_grant() -> None:
    request = RequestFactory().get("/updates/")
    request.session = SessionStore()
    grant = issue_ticket(
        request, ("room:3", "news"), transform_ref=None, authorizer_ref=_AUTHORIZER
    )

    claims = verify_ticket(grant.token, request.session.session_key)

    assert grant.topics == ("room:3", "news")
    assert grant.websocket_path == "/_next/ws/"
    assert grant.expires_in == 60
    assert claims.topics == grant.topics
    assert claims.transform_ref is None
    assert claims.authorizer_ref == _AUTHORIZER
    assert request.session.session_key is not None
    assert request.session.modified is True


@pytest.mark.django_db()
def test_ticket_other_session_or_tampered_token_is_rejected() -> None:
    request = RequestFactory().get("/updates/")
    request.session = SessionStore()
    grant = issue_ticket(
        request, ("room",), transform_ref=None, authorizer_ref=_AUTHORIZER
    )
    other = SessionStore()
    other.create()

    with pytest.raises(InvalidTicketError, match="another session"):
        verify_ticket(grant.token, other.session_key)
    with pytest.raises(InvalidTicketError, match="invalid or expired"):
        verify_ticket(grant.token + "x", request.session.session_key)
    with pytest.raises(InvalidTicketError, match="non-empty"):
        verify_ticket(None, request.session.session_key)
    with pytest.raises(InvalidTicketError, match="no ticket credential"):
        verify_ticket(grant.token, "")


@pytest.mark.django_db()
def test_ticket_expired_or_malformed_claims_resigned_validly_are_rejected() -> None:
    request = RequestFactory().get("/updates/")
    request.session = SessionStore()
    grant = issue_ticket(
        request, ("room",), transform_ref=None, authorizer_ref=_AUTHORIZER
    )
    with (
        time_machine.travel(datetime(2100, 1, 1, tzinfo=UTC)),
        pytest.raises(InvalidTicketError, match="invalid or expired"),
    ):
        verify_ticket(grant.token, request.session.session_key)

    assert request.session.session_key is not None
    session_digest = salted_hmac(
        _SALT,
        request.session.session_key,
        secret=settings.SECRET_KEY,
        algorithm="sha256",
    ).hexdigest()
    signer = signing.TimestampSigner(salt=_SALT)
    invalid = [
        {
            "session": session_digest,
            "topics": [],
            "transform": None,
            "authorizer": _AUTHORIZER,
        },
        {
            "session": session_digest,
            "topics": ["next:internal"],
            "transform": None,
            "authorizer": _AUTHORIZER,
        },
        {
            "session": session_digest,
            "topics": ["room", "room"],
            "transform": None,
            "authorizer": _AUTHORIZER,
        },
        {
            "session": session_digest,
            "topics": ["room"],
            "transform": 42,
            "authorizer": _AUTHORIZER,
        },
        {"session": session_digest, "topics": ["room"], "transform": None},
        [],
    ]
    for payload in invalid:
        token = signer.sign_object(payload)
        with pytest.raises(InvalidTicketError):
            verify_ticket(token, request.session.session_key)


def test_ticket_invalid_settings_fall_back_to_safe_defaults() -> None:
    assert ticket_max_age() == 60
    assert websocket_path() == "/_next/ws/"
    with override_next_settings(REALTIME={"TICKET_MAX_AGE": 0, "WS_PATH": "bad"}):
        assert ticket_max_age() == 60
        assert websocket_path() == "/_next/ws/"
    with override_next_settings(
        REALTIME={"TICKET_MAX_AGE": 15, "WS_PATH": "/custom/ws/"}
    ):
        assert ticket_max_age() == 15
        assert websocket_path() == "/custom/ws/"


def test_ticket_without_session_middleware_raises() -> None:
    request = RequestFactory().get("/updates/")
    with pytest.raises(InvalidTicketError, match="SessionMiddleware"):
        issue_ticket(request, ("room",), transform_ref=None, authorizer_ref=_AUTHORIZER)


def test_ticket_session_backend_without_a_persisted_key_raises() -> None:
    session = create_autospec(SessionStore, instance=True)
    session.session_key = None
    session.modified = False

    def create() -> None:
        session.session_key = ""

    session.create.side_effect = create
    request = RequestFactory().get("/updates/")
    request.session = session
    with pytest.raises(InvalidTicketError, match="persistent Django session"):
        issue_ticket(request, ("room",), transform_ref=None, authorizer_ref=_AUTHORIZER)


@pytest.mark.django_db()
def test_ticket_at_max_topics_and_length_round_trips() -> None:
    noise = base64.urlsafe_b64encode(
        hashlib.shake_256(b"next.realtime.tickets").digest(150 * 65 + 384)
    ).decode()
    topics = tuple(noise[index * 200 : (index + 1) * 200] for index in range(64))
    reference = noise[-MAX_REFERENCE_LENGTH:]
    request = RequestFactory().get("/updates/")
    request.session = SessionStore()

    grant = issue_ticket(
        request, topics, transform_ref=reference, authorizer_ref=reference
    )

    assert len(grant.token) > 8192
    claims = verify_ticket(grant.token, request.session.session_key)
    assert claims.topics == topics
    assert claims.transform_ref == reference


@pytest.mark.django_db()
def test_ticket_non_bmp_references_still_verify() -> None:
    reference = "\U0010ffff" * MAX_REFERENCE_LENGTH
    request = RequestFactory().get("/updates/")
    request.session = SessionStore()
    topics = tuple(f"{index:0>200}" for index in range(64))

    grant = issue_ticket(
        request, topics, transform_ref=reference, authorizer_ref=reference
    )

    assert (
        verify_ticket(grant.token, request.session.session_key).authorizer_ref
        == reference
    )


@pytest.mark.parametrize(
    ("topics", "transform_ref", "authorizer_ref"),
    [
        ((), None, _AUTHORIZER),
        (tuple(f"room:{index}" for index in range(65)), None, _AUTHORIZER),
        (("room", "room"), None, _AUTHORIZER),
        (("bad topic",), None, _AUTHORIZER),
        (("room",), "", _AUTHORIZER),
        (("room",), None, "x" * (MAX_REFERENCE_LENGTH + 1)),
    ],
)
def test_issue_ticket_refuses_a_grant_the_verifier_would_reject(
    topics: tuple[str, ...], transform_ref: str | None, authorizer_ref: str
) -> None:
    request = RequestFactory().get("/updates/")
    with pytest.raises(ValueError, match=r"WebSocket ticket|Invalid realtime topic"):
        issue_ticket(
            request, topics, transform_ref=transform_ref, authorizer_ref=authorizer_ref
        )


@pytest.mark.django_db()
def test_ticket_for_a_persisted_session_keeps_its_key() -> None:
    request = RequestFactory().get("/updates/")
    request.session = SessionStore()
    request.session.create()
    key = request.session.session_key
    request.session.modified = False

    grant = issue_ticket(
        request, ("room",), transform_ref=None, authorizer_ref=_AUTHORIZER
    )

    assert request.session.session_key == key
    assert request.session.modified is False
    assert verify_ticket(grant.token, key).topics == ("room",)


@pytest.mark.parametrize("credential", [None, 42, ""])
def test_verify_ticket_without_a_string_credential_is_rejected(
    credential: object,
) -> None:
    with pytest.raises(InvalidTicketError, match="no ticket credential"):
        verify_ticket("token", credential)


@pytest.mark.parametrize(
    ("transform_ref", "expected"), [(None, None), (_TRANSFORM, drop_message)]
)
def test_resolve_claims_imports_the_named_callables(
    transform_ref: str | None, expected: object
) -> None:
    claims = TicketClaims(("room",), transform_ref, _AUTHORIZER)
    transform, authorizer = resolve_claims(claims)
    assert transform is expected
    assert authorizer is allow_room


@pytest.mark.parametrize(
    ("transform_ref", "authorizer_ref"),
    [
        (None, "tests.realtime.missing_module.allow"),
        (None, "tests.realtime.test_tickets.missing_name"),
        (None, "no_dots"),
        (None, "tests.realtime.test_tickets.NOT_CALLABLE"),
        ("tests.realtime.missing_module.drop", _AUTHORIZER),
        ("tests.realtime.test_tickets.NOT_CALLABLE", _AUTHORIZER),
    ],
)
def test_resolve_claims_unimportable_or_non_callable_reference_is_a_bad_ticket(
    transform_ref: str | None, authorizer_ref: str
) -> None:
    claims = TicketClaims(("room",), transform_ref, authorizer_ref)
    with pytest.raises(InvalidTicketError, match="does not import"):
        resolve_claims(claims)


_STORED = tuple(
    f"django.contrib.sessions.backends.{name}"
    for name in ("db", "cache", "cached_db", "file")
)
_SIGNED = "django.contrib.sessions.backends.signed_cookies"


def _engine_settings(
    engine: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> override_settings:
    monkeypatch.setattr(FileStore, "_storage_path", str(tmp_path), raising=False)
    return override_settings(SESSION_ENGINE=engine, SESSION_FILE_PATH=str(tmp_path))


@pytest.fixture(params=(*_STORED, _SIGNED))
def any_engine(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[str]:
    with _engine_settings(request.param, tmp_path, monkeypatch):
        yield request.param


@pytest.fixture(params=_STORED)
def stored_engine(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[str]:
    with _engine_settings(request.param, tmp_path, monkeypatch):
        yield request.param


def _write_session(request: HttpRequest) -> HttpResponse:
    request.session["visit"] = 1
    return HttpResponse()


def _issue(request: HttpRequest) -> HttpResponse:
    grant = issue_ticket(
        request, ("room",), transform_ref=None, authorizer_ref=_AUTHORIZER
    )
    return HttpResponse(grant.token)


def _respond(view, cookie: str | None) -> tuple[HttpResponse, str | None]:
    request = RequestFactory().get("/updates/")
    if cookie is not None:
        request.COOKIES[settings.SESSION_COOKIE_NAME] = cookie
    response = SessionMiddleware(view)(request)
    morsel = response.cookies.get(settings.SESSION_COOKIE_NAME)
    return response, cookie if morsel is None else morsel.value


@pytest.mark.django_db(transaction=True)
def test_ticket_for_an_established_session_keeps_the_cookie_it_binds(
    any_engine: str,
) -> None:
    del any_engine
    _response, cookie = _respond(_write_session, None)
    assert cookie is not None
    response, held = _respond(_issue, cookie)
    assert settings.SESSION_COOKIE_NAME not in response.cookies
    assert held == cookie
    assert verify_ticket(response.content.decode(), held).topics == ("room",)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("cookie", [None, "stale-session-key-0001"])
def test_ticket_without_a_live_session_binds_the_cookie_it_sets(
    stored_engine: str, cookie: str | None
) -> None:
    del stored_engine
    response, held = _respond(_issue, cookie)
    assert held is not None
    assert held != cookie
    assert verify_ticket(response.content.decode(), held).topics == ("room",)


@override_settings(SESSION_ENGINE=_SIGNED)
def test_ticket_without_a_signed_cookie_session_raises() -> None:
    with pytest.raises(InvalidTicketError, match="persistent Django session"):
        _respond(_issue, None)


@override_settings(SESSION_ENGINE=_SIGNED)
@pytest.mark.parametrize(
    "cookie",
    ["forged:value:unsigned", signing.dumps({"a": 1})],
    ids=["forged", "foreign-salt"],
)
def test_ticket_for_a_signed_cookie_the_handshake_rejects_raises(cookie: str) -> None:
    with pytest.raises(InvalidTicketError, match="persistent Django session"):
        _respond(_issue, cookie)


@pytest.mark.django_db()
@override_settings(SESSION_ENGINE="django.contrib.sessions.backends.db")
def test_ticket_for_an_expired_stored_session_binds_a_fresh_one() -> None:
    expired = SessionStore()
    expired["visit"] = 1
    expired.set_expiry(datetime(2000, 1, 1, tzinfo=UTC))
    expired.save()
    response, held = _respond(_issue, expired.session_key)
    assert held not in {None, expired.session_key}
    assert verify_ticket(response.content.decode(), held).topics == ("room",)


@pytest.mark.django_db()
@override_settings(SESSION_ENGINE="django.contrib.sessions.backends.db")
def test_ticket_for_a_signed_in_session_never_loads_its_user() -> None:
    user = get_user_model().objects.create_user(username="reader", password="x")
    stored = SessionStore()
    stored[SESSION_KEY] = str(user.pk)
    stored[HASH_SESSION_KEY] = user.get_session_auth_hash()
    stored[BACKEND_SESSION_KEY] = settings.AUTHENTICATION_BACKENDS[0]
    stored.save()
    request = RequestFactory().get("/updates/")
    request.session = SessionStore(stored.session_key)
    with CaptureQueriesContext(connection) as queries:
        grant = issue_ticket(
            request, ("room",), transform_ref=None, authorizer_ref=_AUTHORIZER
        )
    assert verify_ticket(grant.token, stored.session_key).topics == ("room",)
    tables = " ".join(query["sql"] for query in queries.captured_queries)
    assert "django_session" in tables
    assert "auth_user" not in tables


def _signed_presence(session_key: str, **presence: object) -> str:
    digest = salted_hmac(
        _SALT, session_key, secret=settings.SECRET_KEY, algorithm="sha256"
    ).hexdigest()
    payload = {
        "session": digest,
        "topics": ["room"],
        "transform": None,
        "authorizer": _AUTHORIZER,
        **presence,
    }
    return signing.TimestampSigner(salt=_SALT).sign_object(payload, compress=True)


@pytest.mark.django_db()
@pytest.mark.parametrize("presence", [False, True, _IDENTIFY])
def test_ticket_carries_the_presence_mode_of_its_grant(presence: object) -> None:
    request = RequestFactory().get("/updates/")
    request.session = SessionStore()
    grant = issue_ticket(
        request,
        ("room",),
        transform_ref=None,
        authorizer_ref=_AUTHORIZER,
        presence=presence,
    )
    claims = verify_ticket(grant.token, request.session.session_key)
    assert claims.presence == presence
    payload = signing.TimestampSigner(salt=_SALT).unsign_object(grant.token)
    assert ("presence" in payload) is (presence is not False)


@pytest.mark.django_db()
def test_ticket_without_a_presence_key_verifies_outside_presence() -> None:
    session = SessionStore()
    session.create()
    token = _signed_presence(session.session_key)
    assert verify_ticket(token, session.session_key).presence is False


@pytest.mark.django_db()
@pytest.mark.parametrize("presence", [42, None, "", "x" * (MAX_REFERENCE_LENGTH + 1)])
def test_ticket_with_a_malformed_presence_is_rejected(presence: object) -> None:
    session = SessionStore()
    session.create()
    token = _signed_presence(session.session_key, presence=presence)
    with pytest.raises(InvalidTicketError, match="invalid presence"):
        verify_ticket(token, session.session_key)


def test_issue_ticket_refuses_an_oversized_presence_reference() -> None:
    request = RequestFactory().get("/updates/")
    with pytest.raises(ValueError, match="WebSocket ticket reference"):
        issue_ticket(
            request,
            ("room",),
            transform_ref=None,
            authorizer_ref=_AUTHORIZER,
            presence="x" * (MAX_REFERENCE_LENGTH + 1),
        )


@pytest.mark.parametrize(
    ("presence", "expected"), [(False, None), (True, None), (_IDENTIFY, name_guest)]
)
def test_resolve_identify_imports_only_a_named_callable(
    presence: object, expected: object
) -> None:
    claims = TicketClaims(("room",), None, _AUTHORIZER, presence)
    assert resolve_identify(claims) is expected


@pytest.mark.parametrize(
    "presence",
    ["tests.realtime.missing_module.name", "tests.realtime.test_tickets.NOT_CALLABLE"],
)
def test_resolve_identify_unusable_reference_is_a_bad_ticket(presence: str) -> None:
    claims = TicketClaims(("room",), None, _AUTHORIZER, presence)
    with pytest.raises(InvalidTicketError, match="does not import"):
        resolve_identify(claims)
