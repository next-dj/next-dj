"""Replay contract that lets a reconnecting subscriber catch up from a broker log."""

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from django.core.exceptions import ImproperlyConfigured

from next.conf.merge import FROZEN_DEFAULTS
from next.conf.scopes import scope_value
from next.utils import int_between, positive_number

from .errors import BrokerConnectionError
from .messages import Message, Position


_MAX_EVENT_ID = 40
_EVENT_ID = re.compile(r"([0-9a-f]{16}):(0|[1-9][0-9]{0,18})")
_DEFAULT_WINDOW: Mapping[str, int | float] = FROZEN_DEFAULTS["REALTIME"]["REPLAY"]
REPLAY_KEYS = frozenset(_DEFAULT_WINDOW)
MAX_SIZE = 10_000
MAX_TTL = 365 * 24 * 60 * 60


@dataclass(frozen=True, slots=True)
class Replay:
    """The logged messages after a cursor and whether they close the whole gap."""

    messages: tuple[Message, ...]
    complete: bool


INCOMPLETE = Replay((), complete=False)
"""The answer of a broker whose log cannot close the gap after a cursor."""


@runtime_checkable
class ReplayingBroker(Protocol):
    """A broker that keeps a bounded log of recent messages on every topic."""

    def replay(self, topic: str, after: Position) -> Replay:
        """Return the messages numbered after `after` in ascending order."""

    async def areplay(self, topic: str, after: Position) -> Replay:
        """Return the messages after `after` through the asynchronous API."""


def valid_size(value: object) -> bool:
    """Whether a `REPLAY['SIZE']` value is an integer from zero up to `MAX_SIZE`.

    A replay reads and copies the whole log of a topic, so the cap bounds that read.
    """
    return int_between(value, 0, MAX_SIZE)


def valid_ttl(value: object) -> bool:
    """Whether a `REPLAY['TTL']` value is a positive number of seconds up to a year.

    The cap keeps the Redis `PEXPIRE` milliseconds far inside what Redis accepts.
    """
    return positive_number(value, MAX_TTL)


def replay_window(window: Mapping[str, object] | None = None) -> tuple[int, float]:
    """Return the size and TTL of a window, `REALTIME['REPLAY']` when it is None."""
    raw = scope_value("REALTIME", "REPLAY") if window is None else window
    if not isinstance(raw, Mapping) or not set(raw) <= REPLAY_KEYS:
        msg = "REALTIME.REPLAY must be a mapping with only SIZE and TTL keys."
        raise ImproperlyConfigured(msg)
    size = raw.get("SIZE", _DEFAULT_WINDOW["SIZE"])
    ttl = raw.get("TTL", _DEFAULT_WINDOW["TTL"])
    if not valid_size(size) or not valid_ttl(ttl):
        msg = (
            f"REALTIME.REPLAY needs an integer SIZE from 0 to {MAX_SIZE} and a "
            f"positive TTL of at most {MAX_TTL} seconds."
        )
        raise ImproperlyConfigured(msg)
    return size, float(ttl)


def replay_after(
    after: Position, current: Position, logged: Sequence[Message]
) -> Replay:
    """Cut an ascending log down to the messages after a cursor, or report the gap."""
    if after.epoch != current.epoch or after.seq > current.seq:
        return INCOMPLETE
    if after.seq == current.seq:
        return Replay((), complete=True)
    messages = tuple(
        message
        for message in logged
        if message.seq > after.seq and message.epoch == current.epoch
    )
    if len(messages) != current.seq - after.seq or any(
        message.seq != after.seq + offset
        for offset, message in enumerate(messages, start=1)
    ):
        return INCOMPLETE
    return Replay(messages, complete=True)


def parse_event_id(value: object) -> Position | None:
    """Read an SSE `Last-Event-ID` as an `<epoch>:<seq>` cursor, None for anything else.

    The epoch is the sixteen lower-case hex digits both built-in brokers generate.
    """
    if not isinstance(value, str) or len(value) > _MAX_EVENT_ID:
        return None
    match = _EVENT_ID.fullmatch(value)
    if match is None:
        return None
    return Position(match[1], int(match[2]))


def format_event_id(epoch: str, seq: int) -> str | None:
    """Return the SSE event id of a message, None when its cursor would not parse."""
    value = f"{epoch}:{seq}"
    return value if _EVENT_ID.fullmatch(value) is not None else None


def catch_up(
    broker: object, cursors: Mapping[str, Position], positions: Mapping[str, Position]
) -> tuple[Message, ...]:
    """Return what each cursor missed up to the position snapshot, nothing on a gap."""
    if not cursors or not isinstance(broker, ReplayingBroker):
        return ()
    missed: list[Message] = []
    for topic, cursor, position in _pending(cursors, positions):
        try:
            replay = broker.replay(topic, cursor)
        except BrokerConnectionError:
            continue
        missed.extend(_until(replay, position))
    return tuple(missed)


async def acatch_up(
    broker: object, cursors: Mapping[str, Position], positions: Mapping[str, Position]
) -> tuple[Message, ...]:
    """Return what each cursor missed through the broker's asynchronous replay."""
    if not cursors or not isinstance(broker, ReplayingBroker):
        return ()
    missed: list[Message] = []
    for topic, cursor, position in _pending(cursors, positions):
        try:
            replay = await broker.areplay(topic, cursor)
        except BrokerConnectionError:
            continue
        missed.extend(_until(replay, position))
    return tuple(missed)


def _pending(
    cursors: Mapping[str, Position], positions: Mapping[str, Position]
) -> Iterator[tuple[str, Position, Position]]:
    """Yield every topic whose cursor trails its snapshot within the same epoch."""
    for topic, cursor in cursors.items():
        position = positions.get(topic)
        if (
            position is not None
            and cursor.epoch == position.epoch
            and cursor.seq < position.seq
        ):
            yield topic, cursor, position


def _until(replay: Replay, position: Position) -> tuple[Message, ...]:
    """Keep a complete replay up to the snapshot hello announces, else nothing.

    Later messages already wait in the subscription queue.
    """
    if not replay.complete:
        return ()
    messages = tuple(
        message
        for message in replay.messages
        if message.seq <= position.seq and message.epoch == position.epoch
    )
    if not messages or messages[-1].seq != position.seq:
        return ()
    return messages


__all__ = [
    "INCOMPLETE",
    "MAX_SIZE",
    "MAX_TTL",
    "REPLAY_KEYS",
    "Replay",
    "ReplayingBroker",
    "acatch_up",
    "catch_up",
    "format_event_id",
    "parse_event_id",
    "replay_after",
    "replay_window",
    "valid_size",
    "valid_ttl",
]
