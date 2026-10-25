"""Presence contract a broker implements and the window it announces snapshots on."""

import heapq
import json
import logging
import math
import os
import threading
import time
import weakref
from abc import ABC, abstractmethod
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from functools import partial
from typing import Protocol, runtime_checkable

from django.core.exceptions import ImproperlyConfigured

from next.conf.merge import FROZEN_DEFAULTS
from next.conf.scopes import scope_value
from next.partial.envelope import Envelope, Patch
from next.partial.manager import asset_version

from .errors import InvalidMemberError
from .messages import Message
from .signals import published


logger = logging.getLogger(__name__)

EVENT = "next:presence"
_DEFAULT_LIMITS: Mapping[str, int | float] = FROZEN_DEFAULTS["REALTIME"]["PRESENCE"]
PRESENCE_KEYS = frozenset(_DEFAULT_LIMITS)
MAX_WINDOW = 60
MAX_MEMBERS = 1000
MAX_MEMBER_BYTES = 4096

type Member = Mapping[str, object]


@dataclass(frozen=True, slots=True)
class Presence:
    """The attendees on a topic and the members identified among them.

    Connections with an equal member count once, a connection without one counts alone.
    """

    count: int
    members: tuple[Member, ...] = ()

    def as_detail(self) -> dict[str, object]:
        """Return the `next:presence` event detail, with members only when any exist."""
        if not self.members:
            return {"count": self.count}
        return {"count": self.count, "members": [dict(m) for m in self.members]}


@dataclass(frozen=True, slots=True)
class PresenceLimits:
    """The coalescing window in seconds and the two caps on what a snapshot carries."""

    window: float
    size: int
    member_bytes: int


@runtime_checkable
class PresenceBroker(Protocol):
    """A broker that keeps the attendance of every topic."""

    def join(
        self, topic: str, connection: str, member: Member | None, ttl: float
    ) -> None:
        """Record a connection on a topic until `ttl` seconds pass without a touch."""
        ...

    def leave(self, topic: str, connection: str) -> None:
        """Forget a connection on a topic."""
        ...

    def touch(self, topic: str, connection: str, ttl: float) -> bool:
        """Push a connection's deadline out, False when it holds no record to push."""
        ...

    def presence(self, topic: str) -> Presence:
        """Return the attendance snapshot of a topic, never with an expired attendee."""
        ...

    async def ajoin(
        self, topic: str, connection: str, member: Member | None, ttl: float
    ) -> None:
        """Record a connection through the asynchronous API."""
        ...

    async def aleave(self, topic: str, connection: str) -> None:
        """Forget a connection through the asynchronous API."""
        ...

    async def atouch(self, topic: str, connection: str, ttl: float) -> bool:
        """Push a connection's deadline out through the asynchronous API."""
        ...

    async def apresence(self, topic: str) -> Presence:
        """Return the attendance snapshot of a topic through the asynchronous API."""
        ...


def valid_window(value: object) -> bool:
    """Whether a `PRESENCE['WINDOW']` value is a positive number up to a minute.

    The Redis lock rounds it up to whole milliseconds, so any positive value holds one.
    """
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and 0 < value <= MAX_WINDOW
    )


def valid_snapshot_size(value: object) -> bool:
    """Whether a `PRESENCE['SIZE']` value is an integer from 0 up to `MAX_MEMBERS`."""
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= MAX_MEMBERS
    )


def valid_member_bytes(value: object) -> bool:
    """Whether a `PRESENCE['MEMBER_BYTES']` value is an integer from 1 up to the cap."""
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 < value <= MAX_MEMBER_BYTES
    )


def presence_limits(limits: Mapping[str, object] | None = None) -> PresenceLimits:
    """Return the limits of a mapping, `REALTIME['PRESENCE']` when it is None."""
    raw = scope_value("REALTIME", "PRESENCE") if limits is None else limits
    if not isinstance(raw, Mapping) or not set(raw) <= PRESENCE_KEYS:
        msg = (
            "REALTIME.PRESENCE must be a mapping with only WINDOW, SIZE and "
            "MEMBER_BYTES keys."
        )
        raise ImproperlyConfigured(msg)
    window = raw.get("WINDOW", _DEFAULT_LIMITS["WINDOW"])
    size = raw.get("SIZE", _DEFAULT_LIMITS["SIZE"])
    member_bytes = raw.get("MEMBER_BYTES", _DEFAULT_LIMITS["MEMBER_BYTES"])
    if not (
        valid_window(window)
        and valid_snapshot_size(size)
        and valid_member_bytes(member_bytes)
    ):
        msg = (
            f"REALTIME.PRESENCE needs a positive WINDOW of at most {MAX_WINDOW} "
            f"seconds, an integer SIZE from 0 to {MAX_MEMBERS} and an integer "
            f"MEMBER_BYTES from 1 to {MAX_MEMBER_BYTES}."
        )
        raise ImproperlyConfigured(msg)
    return PresenceLimits(float(window), size, member_bytes)


def encode_member(member: object, limit: int) -> str | None:
    """Return the canonical JSON a broker stores for a member, None for no member.

    Sorted keys make the encoding the member's identity, so equal members share it.
    """
    if member is None:
        return None
    if not isinstance(member, Mapping) or not all(isinstance(k, str) for k in member):
        reason = "it is not a mapping with string keys"
        raise InvalidMemberError(reason)
    try:
        encoded = json.dumps(
            dict(member),
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            default=_plain,
        )
    except (TypeError, ValueError) as exc:
        reason = "it holds a value JSON cannot carry"
        raise InvalidMemberError(reason) from exc
    size = len(encoded.encode())
    if size > limit:
        reason = f"it encodes to {size} bytes, above the limit of {limit}"
        raise InvalidMemberError(reason)
    return encoded


def _plain(value: object) -> object:
    """Encode a nested mapping as an object, refusing any other foreign value."""
    if isinstance(value, Mapping):
        return dict(value)
    reason = f"{type(value).__qualname__} is not JSON"
    raise TypeError(reason)


def decode_member(payload: object) -> Member | None:
    """Decode a stored member, logging and skipping one that no longer parses."""
    try:
        decoded = json.loads(payload) if isinstance(payload, str | bytes) else None
    except ValueError:
        decoded = None
    if not isinstance(decoded, dict):
        logger.warning("Skipped a malformed presence member from the broker.")
        return None
    return decoded


def presence_event(detail: object) -> Envelope:
    """Build the topic envelope of a `next:presence` event carrying any detail.

    The builder refuses framework event names, so the op is assembled here instead.
    """
    op = Patch(op="event", extras={"name": EVENT, "detail": detail})
    return Envelope(version=asset_version(), ops=(op,))


def presence_envelope(snapshot: Presence) -> Envelope:
    """Build the topic envelope of a snapshot, the `next:presence` event op."""
    return presence_event(snapshot.as_detail())


def snapshot_message(topic: str, snapshot: Presence, published_at: float) -> Message:
    """Return the unnumbered message that carries a snapshot to live subscribers."""
    return Message(topic, 0, presence_envelope(snapshot), published_at)


def counted(message: Message) -> Message:
    """Return the message with the members cut from every presence event it carries.

    An app may publish the event as well, so neither the seq nor the op count matters.
    """
    ops = message.envelope.ops
    if len(ops) == 1:
        if ops[0].extras.get("name") != EVENT:
            return message
        return _uncounted(message)
    for op in ops:
        if op.extras.get("name") == EVENT:
            return _uncounted(message)
    return message


def _uncounted(message: Message) -> Message:
    """Rebuild the envelope with the members cut from each presence event it holds."""
    ops = message.envelope.ops
    cut = tuple(_count_only(op) for op in ops)
    if all(new is op for new, op in zip(cut, ops, strict=True)):
        return message
    return replace(message, envelope=replace(message.envelope, ops=cut))


def _count_only(op: Patch) -> Patch:
    """Return a presence event op without its members, any other op unchanged."""
    detail = op.extras.get("detail")
    if (
        op.op != "event"
        or op.extras.get("name") != EVENT
        or not isinstance(detail, Mapping)
        or "members" not in detail
    ):
        return op
    kept = {key: value for key, value in detail.items() if key != "members"}
    return replace(op, extras={**op.extras, "detail": kept})


class _Announcing(Protocol):
    def presence(self, topic: str) -> Presence: ...

    def broadcast(self, topic: str, envelope: Envelope) -> Message: ...


def announce(broker: _Announcing, topic: str) -> None:
    """Broadcast the current snapshot of a topic to its live subscribers."""
    envelope = presence_envelope(broker.presence(topic))
    published(broker, broker.broadcast(topic, envelope))


def _no_presence(kind: type) -> ImproperlyConfigured:
    msg = (
        f"{kind.__qualname__} keeps no presence. Implement "
        "next.realtime.PresenceBroker on the broker or drop presence= from the "
        "stream."
    )
    return ImproperlyConfigured(msg)


def presence_broker(broker: object) -> PresenceBroker:
    """Return the broker as a `PresenceBroker`, refusing one without the capability."""
    if not isinstance(broker, PresenceBroker):
        raise _no_presence(type(broker))
    return broker


def require_presence(kind: type) -> None:
    """Refuse a broker class without presence before a stream starts answering."""
    if not issubclass(kind, PresenceBroker):
        raise _no_presence(kind)


class PresenceSchedule:
    """Publish each claimed topic's snapshot once its window closes.

    One daemon thread per broker serves WSGI and ASGI alike and outlives any request.
    """

    def __init__(
        self,
        publish: Callable[[str], object],
        *,
        clock: Callable[[], float] = time.monotonic,
        threaded: bool = True,
        retry: float = 1.0,
    ) -> None:
        """Hold the snapshot publisher without starting a thread.

        A failed snapshot waits `retry` seconds before its topic is tried again.
        """
        self._publish = publish
        self._clock = clock
        self._threaded = threaded
        self._retry = retry
        self._closed = False
        self.reset()
        _SCHEDULES.add(self)

    def reset(self) -> None:
        """Drop the pending topics, the lock and the thread, as a forked child must."""
        self._due: list[tuple[float, str]] = []
        self._condition = threading.Condition()
        self._thread: threading.Thread | None = None

    def schedule(self, topic: str, delay: float) -> None:
        """Publish the snapshot of a topic `delay` seconds from now."""
        with self._condition:
            if self._closed:
                return
            heapq.heappush(self._due, (self._clock() + delay, topic))
            if self._threaded and self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name="next-realtime-presence", daemon=True
                )
                self._thread.start()
            self._condition.notify()

    def pending(self) -> tuple[str, ...]:
        """Return the topics waiting for their window to close, soonest first."""
        with self._condition:
            return tuple(topic for _, topic in sorted(self._due))

    def run_due(self, now: float | None = None) -> int:
        """Publish every snapshot due by `now` and return how many were attempted.

        A failed topic is logged and queued again, so one topic cannot stop the others.
        """
        moment = self._clock() if now is None else now
        ripe: list[str] = []
        with self._condition:
            while self._due and self._due[0][0] <= moment:
                ripe.append(heapq.heappop(self._due)[1])
        for topic in ripe:
            try:
                self._publish(topic)
            except Exception:
                logger.exception("Could not publish the presence of %r.", topic)
                self._again(topic)
        return len(ripe)

    def _again(self, topic: str) -> None:
        """Queue a failed topic `retry` seconds out unless it is pending already."""
        with self._condition:
            if self._closed or any(queued == topic for _, queued in self._due):
                return
            heapq.heappush(self._due, (self._clock() + self._retry, topic))
            self._condition.notify()

    def close(self) -> None:
        """Publish what is pending right away and let the thread exit at its wakeup."""
        with self._condition:
            self._closed = True
            ripe = tuple(dict.fromkeys(topic for _, topic in sorted(self._due)))
            self._due.clear()
            self._condition.notify_all()
        for topic in ripe:
            try:
                self._publish(topic)
            except Exception:
                logger.exception("Could not publish the presence of %r.", topic)

    def _run(self) -> None:
        """Sleep until the soonest window closes, publish, and exit once idle."""
        while True:
            with self._condition:
                while True:
                    if self._closed or not self._due:
                        self._thread = None
                        return
                    wait = self._due[0][0] - self._clock()
                    if wait <= 0:
                        break
                    self._condition.wait(wait)
            self.run_due()


type Scheduler = Callable[[Callable[[str], object]], PresenceSchedule]


class PresenceWindow(ABC):
    """The limits, the schedule and the announce hook a presence broker shares.

    A change that claims its topic's window hands the topic to `schedule_presence`.
    """

    presence_limits: PresenceLimits
    presence_schedule: PresenceSchedule

    def keep_presence(
        self,
        limits: Mapping[str, object] | None,
        schedule: Scheduler | None,
        clock: Callable[[], float],
    ) -> None:
        """Read the limits and build the schedule that calls `announce_presence`."""
        self.presence_limits = presence_limits(limits)
        if schedule is None:
            schedule = partial(
                PresenceSchedule, clock=clock, retry=self.presence_limits.window
            )
        self.presence_schedule = schedule(self.announce_presence)

    def schedule_presence(self, topic: str) -> None:
        """Announce the snapshot of a topic once the window this process took closes."""
        self.presence_schedule.schedule(topic, self.presence_limits.window)

    @abstractmethod
    def announce_presence(self, topic: str) -> None:
        """Release the closed window of a topic and broadcast the snapshot it held."""


_SCHEDULES: weakref.WeakSet[PresenceSchedule] = weakref.WeakSet()


def _reset_schedules() -> None:
    """Give every schedule a forked child inherits a fresh lock and no thread."""
    for schedule in tuple(_SCHEDULES):
        schedule.reset()


def _without_fork(**_hooks: Callable[[], object]) -> None:
    """Stand in for os.register_at_fork on platforms that cannot fork."""


getattr(os, "register_at_fork", _without_fork)(after_in_child=_reset_schedules)


__all__ = [
    "Member",
    "Presence",
    "PresenceBroker",
    "PresenceLimits",
    "PresenceSchedule",
    "PresenceWindow",
    "Scheduler",
    "announce",
    "decode_member",
    "encode_member",
    "presence_limits",
]
