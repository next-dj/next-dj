"""Short-lived, session-bound grants for realtime WebSocket subscriptions."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

from django.conf import settings
from django.core import signing
from django.urls import get_script_prefix
from django.utils.crypto import constant_time_compare, salted_hmac
from django.utils.module_loading import import_string

from next.conf import next_framework_settings

from .binding import ticket_binding
from .errors import InvalidTicketError
from .messages import validate_topic


if TYPE_CHECKING:
    from django.http import HttpRequest

    from next.partial.envelope import Envelope
    from next.partial.patches import Patches

    from .access import TopicAuthorizer
    from .attendance import Identify
    from .messages import Message

    type TopicTransform = Callable[[Message], Patches | Envelope | None]


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
        "presence": "\U0010ffff" * MAX_REFERENCE_LENGTH,
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
    """Validated ticket grants a WebSocket gateway may act on after its handshake.

    `presence` is False outside presence, True for a count, or the identify reference.
    """

    topics: tuple[str, ...]
    transform_ref: str | None
    authorizer_ref: str
    presence: bool | str = False


def issue_ticket(
    request: HttpRequest,
    topics: tuple[str, ...],
    *,
    transform_ref: str | None,
    authorizer_ref: str,
    presence: bool | str = False,
) -> TicketGrant:
    """Sign the exact topic grant after the guarded stream view has run.

    The grant is held to the verifier's caps, and `TICKET_BINDING` names its subject.
    """
    presence_ref = presence if isinstance(presence, str) else None
    _require_grant(topics, (transform_ref, authorizer_ref, presence_ref))
    subject = ticket_binding().issue(request)
    age = ticket_max_age()
    signer = signing.TimestampSigner(salt=_SALT)
    payload: dict[str, object] = {
        "session": _session_digest(subject, settings.SECRET_KEY),
        "topics": list(topics),
        "transform": transform_ref,
        "authorizer": authorizer_ref,
    }
    if presence is not False:
        payload["presence"] = presence
    token = signer.sign_object(payload, compress=True)
    url = f"{get_script_prefix().rstrip('/')}{websocket_path()}"
    return TicketGrant(token, topics, url, age)


def _require_grant(topics: tuple[str, ...], references: tuple[str | None, ...]) -> None:
    """Refuse a grant the verifier would reject."""
    if not topics or len(topics) > _MAX_TOPICS or len(set(topics)) != len(topics):
        msg = f"A WebSocket ticket grants 1 to {_MAX_TOPICS} unique topics."
        raise ValueError(msg)
    for topic in topics:
        validate_topic(topic)
    for reference in references:
        if reference is not None and not 0 < len(reference) <= MAX_REFERENCE_LENGTH:
            msg = (
                f"A WebSocket ticket reference holds 1 to {MAX_REFERENCE_LENGTH} "
                "characters."
            )
            raise ValueError(msg)


def verify_ticket(token: object, credential: str) -> TicketClaims:
    """Verify signature, age, binding, and the signed topic grant.

    The credential is what the ticket binding read from the handshake cookies.
    """
    if not isinstance(token, str) or not token or len(token) > _MAX_TICKET_LENGTH:
        msg = "A non-empty WebSocket ticket is required."
        raise InvalidTicketError(msg)
    if not isinstance(credential, str) or not credential:
        msg = "The WebSocket connection carries no ticket credential."
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
    subject = ticket_binding().subject(credential)
    if subject is None or not _bound(payload.get("session"), subject):
        msg = "The WebSocket ticket belongs to another session."
        raise InvalidTicketError(msg)
    return _ticket_claims(payload)


def resolve_claims(
    claims: TicketClaims,
) -> tuple[TopicTransform | None, TopicAuthorizer]:
    """Import the transform and authorizer a verified ticket names.

    A reference that no longer imports a callable fails like a bad ticket.
    """
    ref = claims.transform_ref
    transform = None if ref is None else _resolve(ref)
    authorizer = _resolve(claims.authorizer_ref)
    return (
        cast("TopicTransform | None", transform),
        cast("TopicAuthorizer", authorizer),
    )


def resolve_identify(claims: TicketClaims) -> Identify | None:
    """Import the identify callable a verified ticket names, None for a bare count.

    A reference that no longer imports a callable fails like a bad ticket.
    """
    if not isinstance(claims.presence, str):
        return None
    return cast("Identify", _resolve(claims.presence))


def import_callable(path: str) -> Callable[..., Any]:
    """Import the callable a dotted path names, `TypeError` for another value."""
    target = import_string(path)
    if not callable(target):
        msg = f"{path!r} does not name a callable."
        raise TypeError(msg)
    return cast("Callable[..., Any]", target)


def _resolve(ref: str) -> Callable[..., Any]:
    """Import a callable a verified ticket names, failing like a bad ticket."""
    try:
        return import_callable(ref)
    except (ImportError, AttributeError, TypeError) as exc:
        msg = "The WebSocket ticket names a callable that does not import."
        raise InvalidTicketError(msg) from exc


def _ticket_claims(payload: Mapping[str, Any]) -> TicketClaims:
    """Validate the signed topics and the references the grant names."""
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
    presence = payload.get("presence", False)
    if not isinstance(presence, bool) and (
        not isinstance(presence, str)
        or not presence
        or len(presence) > MAX_REFERENCE_LENGTH
    ):
        msg = "The WebSocket ticket has an invalid presence."
        raise InvalidTicketError(msg)
    return TicketClaims(topics, transform_ref, authorizer_ref, presence)


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
    """Return the configured same-origin WebSocket route below the script prefix."""
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


def _session_digest(subject: str, secret: str | bytes) -> str:
    """Bind a ticket without exposing the raw binding subject in its payload."""
    return salted_hmac(_SALT, subject, secret=secret, algorithm="sha256").hexdigest()


def _bound(digest: object, subject: str) -> bool:
    """Whether a signed digest names the subject under the key or a fallback key."""
    if not isinstance(digest, str):
        return False
    keys = (settings.SECRET_KEY, *settings.SECRET_KEY_FALLBACKS)
    return any(
        constant_time_compare(digest, _session_digest(subject, key)) for key in keys
    )


__all__ = [
    "MAX_REFERENCE_LENGTH",
    "TicketClaims",
    "TicketGrant",
    "import_callable",
    "issue_ticket",
    "resolve_claims",
    "resolve_identify",
    "ticket_max_age",
    "verify_ticket",
    "websocket_path",
]
