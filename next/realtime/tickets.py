"""Short-lived, session-bound grants for realtime WebSocket subscriptions."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core import signing
from django.utils.crypto import constant_time_compare, salted_hmac

from next.conf import next_framework_settings

from .errors import InvalidTicketError
from .topics import validate_topic


if TYPE_CHECKING:
    from django.http import HttpRequest


_SALT = "next.realtime.websocket.ticket.v1"
_DEFAULT_AGE = 60
_MAX_TOPICS = 64
_MAX_TOPIC_LENGTH = 200
MAX_REFERENCE_LENGTH = 512
_MAX_TICKET_AGE = 600
_SIGNATURE_OVERHEAD = 128


def _max_ticket_length() -> int:
    """Bound a token by its largest grant so every issued ticket verifies.

    A reference character outside the BMP costs a twelve-character JSON escape pair.
    """
    payload = {
        "session": "0" * 64,
        "topics": ["t" * _MAX_TOPIC_LENGTH] * _MAX_TOPICS,
        "transform": "\U0010ffff" * MAX_REFERENCE_LENGTH,
        "authorizer": "\U0010ffff" * MAX_REFERENCE_LENGTH,
    }
    encoded = json.dumps(payload, separators=(",", ":"))
    return 4 * math.ceil((len(encoded) + 1) / 3) + _SIGNATURE_OVERHEAD


_MAX_TICKET_LENGTH = _max_ticket_length()


@dataclass(frozen=True, slots=True)
class TicketGrant:
    """A ticket returned by the guarded HTTP negotiation endpoint."""

    token: str
    topics: tuple[str, ...]
    websocket_path: str
    expires_in: int

    def as_dict(self) -> dict[str, Any]:
        """Return the JSON-safe negotiation body."""
        return {
            "protocol": 2,
            "transport": "ws",
            "ticket": self.token,
            "topics": list(self.topics),
            "url": self.websocket_path,
            "expires_in": self.expires_in,
        }


@dataclass(frozen=True, slots=True)
class TicketClaims:
    """Validated ticket grants, safe for a consumer to use after handshake."""

    topics: tuple[str, ...]
    transform_ref: str | None
    authorizer_ref: str


def issue_ticket(
    request: HttpRequest,
    topics: tuple[str, ...],
    *,
    transform_ref: str | None,
    authorizer_ref: str,
) -> TicketGrant:
    """Sign the exact topic grant after the guarded stream view has run.

    The grant is held to the verifier's caps, so a ticket it signs always verifies.
    """
    _require_grant(topics, transform_ref, authorizer_ref)
    session = getattr(request, "session", None)
    if session is None:
        msg = "WebSocket ticket negotiation requires Django SessionMiddleware."
        raise InvalidTicketError(msg)
    session_key = session.session_key
    if session_key is None or not session.exists(session_key):
        # A stale cookie gets a fresh session, as a first visit without one does.
        session.create()
        session.modified = True
        session_key = session.session_key
    if not isinstance(session_key, str) or not session_key:
        msg = "A persistent Django session is required for WebSocket tickets."
        raise InvalidTicketError(msg)
    age = ticket_max_age()
    signer = signing.TimestampSigner(salt=_SALT)
    payload = {
        "session": _session_digest(session_key),
        "topics": list(topics),
        "transform": transform_ref,
        "authorizer": authorizer_ref,
    }
    token = signer.sign_object(payload, compress=True)
    return TicketGrant(token, topics, websocket_path(), age)


def _require_grant(
    topics: tuple[str, ...], transform_ref: str | None, authorizer_ref: str
) -> None:
    """Refuse a grant the verifier would reject."""
    if not topics or len(topics) > _MAX_TOPICS or len(set(topics)) != len(topics):
        msg = f"A WebSocket ticket grants 1 to {_MAX_TOPICS} unique topics."
        raise ValueError(msg)
    for topic in topics:
        validate_topic(topic)
    for reference in (transform_ref, authorizer_ref):
        if reference is not None and not 0 < len(reference) <= MAX_REFERENCE_LENGTH:
            msg = (
                f"A WebSocket ticket reference holds 1 to {MAX_REFERENCE_LENGTH} "
                "characters."
            )
            raise ValueError(msg)


def verify_ticket(token: object, session: object) -> TicketClaims:
    """Verify signature, age, session binding, and the signed topic grant."""
    if not isinstance(token, str) or not token or len(token) > _MAX_TICKET_LENGTH:
        msg = "A non-empty WebSocket ticket is required."
        raise InvalidTicketError(msg)
    session_key = getattr(session, "session_key", None)
    if not isinstance(session_key, str) or not session_key:
        msg = "The WebSocket connection has no Django session."
        raise InvalidTicketError(msg)
    try:
        payload = signing.TimestampSigner(salt=_SALT).unsign_object(
            token, max_age=ticket_max_age()
        )
    except (signing.BadSignature, TypeError, ValueError) as exc:
        msg = "The WebSocket ticket is invalid or expired."
        raise InvalidTicketError(msg) from exc
    if not isinstance(payload, Mapping):
        msg = "The WebSocket ticket has an invalid payload."
        raise InvalidTicketError(msg)
    digest = payload.get("session")
    if not isinstance(digest, str) or not constant_time_compare(
        digest, _session_digest(session_key)
    ):
        msg = "The WebSocket ticket belongs to another session."
        raise InvalidTicketError(msg)
    return _ticket_claims(payload)


def _ticket_claims(payload: Mapping[str, Any]) -> TicketClaims:
    """Validate the signed topic, transform, and authorization references."""
    raw_topics = payload.get("topics")
    if (
        not isinstance(raw_topics, list)
        or not raw_topics
        or len(raw_topics) > _MAX_TOPICS
    ):
        msg = "The WebSocket ticket has an invalid topic grant."
        raise InvalidTicketError(msg)
    try:
        topics = tuple(validate_topic(topic) for topic in raw_topics)
    except (TypeError, ValueError) as exc:
        msg = "The WebSocket ticket has an invalid topic grant."
        raise InvalidTicketError(msg) from exc
    if len(set(topics)) != len(topics):
        msg = "The WebSocket ticket has duplicate topics."
        raise InvalidTicketError(msg)
    transform_ref = payload.get("transform")
    if transform_ref is not None and (
        not isinstance(transform_ref, str)
        or not transform_ref
        or len(transform_ref) > MAX_REFERENCE_LENGTH
    ):
        msg = "The WebSocket ticket has an invalid transform."
        raise InvalidTicketError(msg)
    authorizer_ref = payload.get("authorizer")
    if (
        not isinstance(authorizer_ref, str)
        or not authorizer_ref
        or len(authorizer_ref) > MAX_REFERENCE_LENGTH
    ):
        msg = "The WebSocket ticket has no valid topic authorizer."
        raise InvalidTicketError(msg)
    return TicketClaims(topics, transform_ref, authorizer_ref)


def ticket_max_age() -> int:
    """Return the configured ticket lifetime after validating its safe shape."""
    realtime = getattr(next_framework_settings, "REALTIME", {})
    value = (
        realtime.get("TICKET_MAX_AGE", _DEFAULT_AGE)
        if isinstance(realtime, Mapping)
        else _DEFAULT_AGE
    )
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not 1 <= value <= _MAX_TICKET_AGE
    ):
        return _DEFAULT_AGE
    return value


def websocket_path() -> str:
    """Return the configured same-origin WebSocket route."""
    realtime = getattr(next_framework_settings, "REALTIME", {})
    path = (
        realtime.get("WS_PATH", "/_next/ws/")
        if isinstance(realtime, Mapping)
        else "/_next/ws/"
    )
    if (
        not isinstance(path, str)
        or not path.startswith("/")
        or "?" in path
        or "#" in path
    ):
        return "/_next/ws/"
    return path


def _session_digest(session_key: str) -> str:
    """Bind a ticket without exposing the raw session identifier in its payload."""
    return salted_hmac(
        _SALT, session_key, secret=settings.SECRET_KEY, algorithm="sha256"
    ).hexdigest()


__all__ = [
    "MAX_REFERENCE_LENGTH",
    "TicketClaims",
    "TicketGrant",
    "issue_ticket",
    "ticket_max_age",
    "verify_ticket",
    "websocket_path",
]
