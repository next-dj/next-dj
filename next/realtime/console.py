"""Publish to realtime topics and read their state from a terminal.

`manage.py realtime` wraps these, so a framework CLI can reach the broker the same way.
"""

import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager

from django.core.exceptions import ImproperlyConfigured

from next.partial.errors import ReservedEventNameError
from next.partial.patches import Patches

from .backends import BrokerBackend, MemoryBroker
from .delivery import publish
from .errors import (
    BrokerConnectionError,
    ConsoleError,
    InvalidTopicError,
    TopicPayloadError,
)
from .manager import broker_manager
from .messages import Message, Position, validate_topic
from .replay import Replay, ReplayingBroker, parse_event_id
from .roster import Presence, PresenceBroker


_REFUSED = (
    ImproperlyConfigured,
    InvalidTopicError,
    ReservedEventNameError,
    TopicPayloadError,
)
_PROCESS_LOCAL = (
    "MemoryBroker keeps its topics inside this process, so a message published from "
    "here reaches no server. Configure a shared broker such as RedisBroker."
)


@contextmanager
def refused() -> Iterator[None]:
    """Turn a refused topic, payload or broker into a `ConsoleError` with its reason."""
    try:
        yield
    except _REFUSED as exc:
        raise ConsoleError(str(exc)) from exc
    except BrokerConnectionError as exc:
        msg = f"The realtime broker is unreachable. {exc}"
        raise ConsoleError(msg) from exc


def broker() -> BrokerBackend:
    """Return the configured broker, refusing a misconfigured one."""
    with refused():
        return broker_manager.get()


def process_local() -> bool:
    """Whether the broker keeps its topics in this process, out of a server's reach."""
    return isinstance(broker(), MemoryBroker)


def publish_patches(
    topic: str,
    *,
    zones: Sequence[str] = (),
    event: str | None = None,
    data: str | None = None,
) -> Message:
    """Publish zone refreshes or one event at once and return the numbered message.

    A process-local broker is refused, since nothing outside this process would hear.
    """
    if process_local():
        raise ConsoleError(_PROCESS_LOCAL)
    patches = Patches(None)
    for zone in zones:
        if not zone:
            msg = "A zone to refresh needs a name."
            raise ConsoleError(msg)
        patches.refresh(zone=zone)
    with refused():
        if event is not None:
            patches.event(event, _detail(data))
        elif data is not None:
            msg = "--data needs --event."
            raise ConsoleError(msg)
        return publish(topic, patches, on_commit=False)


def _detail(data: str | None) -> dict[str, object] | None:
    """Decode the event detail, a JSON object."""
    if data is None:
        return None
    try:
        detail = json.loads(data)
    except json.JSONDecodeError as exc:
        msg = f"--data is not JSON. {exc}."
        raise ConsoleError(msg) from exc
    if not isinstance(detail, dict):
        msg = "--data must be a JSON object."
        raise ConsoleError(msg)
    return detail


def read_positions(topics: Sequence[str]) -> dict[str, Position]:
    """Return the latest position of each topic, an empty epoch when never published."""
    backend = broker()
    with refused():
        return backend.positions(topics)


def read_presence(topic: str) -> Presence:
    """Return the attendance of a topic, refusing a broker that keeps none."""
    backend = broker()
    with refused():
        validate_topic(topic)
        if not isinstance(backend, PresenceBroker):
            msg = f"{type(backend).__qualname__} keeps no presence."
            raise ConsoleError(msg)
        return backend.presence(topic)


def read_replay(topic: str, after: str) -> Replay:
    """Return what the broker log holds after an `<epoch>:<seq>` cursor."""
    cursor = parse_event_id(after)
    if cursor is None:
        msg = (
            f"--after takes an <epoch>:<seq> cursor, sixteen hex digits, a colon and a "
            f"sequence, not {after!r}."
        )
        raise ConsoleError(msg)
    backend = broker()
    with refused():
        validate_topic(topic)
        if not isinstance(backend, ReplayingBroker):
            msg = f"{type(backend).__qualname__} keeps no replay log."
            raise ConsoleError(msg)
        return backend.replay(topic, cursor)


__all__ = [
    "broker",
    "process_local",
    "publish_patches",
    "read_positions",
    "read_presence",
    "read_replay",
    "refused",
]
