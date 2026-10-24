from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory

from next.partial import Envelope, Patches
from next.realtime.tickets import TicketGrant, issue_ticket


ROOM_AUTHORIZER_REF = "tests.support.realtime.allow_room_topic"


def allow_room_topic(_user: object, topic: str) -> bool:
    """Grant every topic under the ``room:`` prefix and nothing else."""
    return topic.startswith("room:")


@dataclass(frozen=True, slots=True)
class SessionTicket:
    """A saved session and the WebSocket ticket issued against it."""

    session: SessionStore
    grant: TicketGrant


def issue_session_ticket(
    topics: tuple[str, ...] = ("room:42",),
    *,
    transform_ref: str | None = None,
    authorizer_ref: str = ROOM_AUTHORIZER_REF,
) -> SessionTicket:
    """Create a saved session and issue a WebSocket ticket bound to it."""
    session = SessionStore()
    session.create()
    request = RequestFactory().get("/updates/")
    request.session = session
    grant = issue_ticket(
        request, topics, transform_ref=transform_ref, authorizer_ref=authorizer_ref
    )
    return SessionTicket(session, grant)


def refresh_envelope() -> Envelope:
    """Build the topic-safe refresh envelope most broker tests publish."""
    return Patches(None).refresh(zone="results").envelope()


def event_envelope(name: str = "update") -> Envelope:
    """Build a topic-safe envelope carrying one named event without a detail."""
    return Envelope.from_dict(
        {
            "version": "v1",
            "ops": [{"op": "event", "name": name}],
            "assets": [],
            "form": None,
        }
    )


def message_payload(**overrides: object) -> dict[str, object]:
    """Build the JSON-safe mapping of one broker message with chosen fields replaced."""
    payload: dict[str, object] = {
        "topic": "poll:1",
        "seq": 3,
        "epoch": "e1",
        "envelope": refresh_envelope().as_dict(),
        "published_at": 1.5,
    }
    payload.update(overrides)
    return payload


HEARTBEAT_FRAME = b": heartbeat\n\n"


def next_frame(iterator: Iterator[bytes]) -> bytes:
    """Return the next SSE chunk that is not a heartbeat, whatever the timing."""
    frame = next(iterator)
    while frame == HEARTBEAT_FRAME:
        frame = next(iterator)
    return frame


async def anext_frame(iterator: AsyncIterator[bytes]) -> bytes:
    """Return the next async SSE chunk that is not a heartbeat."""
    frame = await anext(iterator)
    while frame == HEARTBEAT_FRAME:
        frame = await anext(iterator)
    return frame


async def anext_item[T](source: AsyncIterator[T | None]) -> T:
    """Return the next item of a topic source, skipping its heartbeat `None`."""
    item = await anext(source)
    while item is None:
        item = await anext(source)
    return item
