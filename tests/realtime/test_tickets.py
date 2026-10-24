import base64
import hashlib
from datetime import UTC, datetime
from unittest.mock import create_autospec

import pytest
import time_machine
from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.core import signing
from django.test import RequestFactory
from django.utils.crypto import salted_hmac

from next.realtime.errors import InvalidTicketError
from next.realtime.tickets import (
    _SALT,
    MAX_REFERENCE_LENGTH,
    issue_ticket,
    ticket_max_age,
    verify_ticket,
    websocket_path,
)
from next.testing import override_next_settings


_AUTHORIZER = "tests.realtime.test_tickets.allow_room"


def allow_room(_user: object, _topic: str) -> bool:
    return True


@pytest.mark.django_db()
def test_ticket_issued_for_a_session_verifies_its_topic_grant() -> None:
    request = RequestFactory().get("/updates/")
    request.session = SessionStore()
    grant = issue_ticket(
        request, ("room:3", "news"), transform_ref=None, authorizer_ref=_AUTHORIZER
    )

    claims = verify_ticket(grant.token, request.session)

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
        verify_ticket(grant.token, other)
    with pytest.raises(InvalidTicketError, match="invalid or expired"):
        verify_ticket(grant.token + "x", request.session)
    with pytest.raises(InvalidTicketError, match="non-empty"):
        verify_ticket(None, request.session)
    with pytest.raises(InvalidTicketError, match="no Django session"):
        verify_ticket(grant.token, object())


@pytest.mark.django_db()
def test_ticket_expired_or_malformed_signed_claims_are_rejected() -> None:
    request = RequestFactory().get("/updates/")
    request.session = SessionStore()
    grant = issue_ticket(
        request, ("room",), transform_ref=None, authorizer_ref=_AUTHORIZER
    )
    with (
        time_machine.travel(datetime(2100, 1, 1, tzinfo=UTC)),
        pytest.raises(InvalidTicketError, match="invalid or expired"),
    ):
        verify_ticket(grant.token, request.session)

    # Re-sign malformed payloads so structural validation is isolated from signature
    # failures.
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
            verify_ticket(token, request.session)


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
    claims = verify_ticket(grant.token, request.session)
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

    assert verify_ticket(grant.token, request.session).authorizer_ref == reference


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
