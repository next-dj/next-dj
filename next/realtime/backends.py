"""Broker contracts and the process-local in-memory implementation."""

import asyncio
import itertools
import json
import math
import secrets
import threading
import time
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from collections.abc import (
    AsyncIterator,
    Callable,
    Iterable,
    Iterator,
    Mapping,
    Sequence,
)
from contextlib import (
    AbstractAsyncContextManager,
    AbstractContextManager,
    asynccontextmanager,
    contextmanager,
)
from typing import override

from next.partial.envelope import Envelope

from .messages import Message, Position, validate_topic
from .replay import INCOMPLETE, Replay, replay_after, replay_window
from .roster import (
    Member,
    Presence,
    PresenceWindow,
    Scheduler,
    encode_member,
    presence_envelope,
)
from .signals import published


_NEVER_PUBLISHED = Position(epoch="", seq=0)


class BrokerBackend(ABC):
    """Fans published envelopes out to subscribers of one or more topics."""

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        """Read backend options from its settings entry."""
        options = {} if config is None else config.get("OPTIONS", {})
        self.options = options if isinstance(options, Mapping) else {}

    def validate_publication(self, topic: str, envelope: object) -> Envelope:
        """Check the topic name and envelope type before a broker numbers anything."""
        validate_topic(topic)
        if not isinstance(envelope, Envelope):
            msg = "BrokerBackend.publish() requires a partial Envelope."
            raise TypeError(msg)
        return envelope

    @abstractmethod
    def publish(self, topic: str, envelope: Envelope) -> Message:
        """Publish an envelope and return its broker-assigned message."""

    @abstractmethod
    async def apublish(self, topic: str, envelope: Envelope) -> Message:
        """Publish an envelope through the asynchronous backend API."""

    def broadcast(self, topic: str, envelope: Envelope) -> Message:
        """Deliver an envelope to live subscribers only, unnumbered and never logged.

        Presence snapshots need it, so a broker that keeps presence overrides it.
        """
        msg = f"{type(self).__qualname__} has no unnumbered fan-out for broadcast()."
        raise NotImplementedError(msg)

    @abstractmethod
    def subscribe(
        self, topics: Sequence[str], *, timeout: float
    ) -> AbstractContextManager[Iterator[Message | None]]:
        """Subscribe until the context exits, yielding `None` on timeout."""

    @abstractmethod
    def asubscribe(
        self, topics: Sequence[str], *, timeout: float
    ) -> AbstractAsyncContextManager[AsyncIterator[Message | None]]:
        """Subscribe asynchronously until the context exits."""

    @abstractmethod
    def positions(self, topics: Sequence[str]) -> dict[str, Position]:
        """Return the latest position of every topic in one batched read."""

    @abstractmethod
    async def apositions(self, topics: Sequence[str]) -> dict[str, Position]:
        """Return the latest topic positions through the asynchronous API."""

    def close(self) -> None:
        """Release connections and threads held by the backend."""
        return

    async def aclose(self) -> None:
        """Release backend resources from an event loop."""
        self.close()


class _SyncSubscriber:
    def __init__(self, size: int) -> None:
        self.queue: deque[Message] = deque(maxlen=size)
        self.condition = threading.Condition()


class _AsyncSubscriber:
    def __init__(self, loop: asyncio.AbstractEventLoop, size: int) -> None:
        self.loop = loop
        self.queue: deque[Message] = deque(maxlen=size)
        self.lock = threading.Lock()
        self.wake = asyncio.Event()
        self.wake_scheduled = False

    def enqueue(self, message: Message) -> bool:
        """Buffer one message and schedule at most one loop wakeup."""
        with self.lock:
            self.queue.append(message)
            if self.wake_scheduled:
                return True
            self.wake_scheduled = True
        try:
            self.loop.call_soon_threadsafe(self._notify)
        except RuntimeError:
            with self.lock:
                self.wake_scheduled = False
            return False
        return True

    def _notify(self) -> None:
        """Wake the async reader when a publisher has buffered messages."""
        with self.lock:
            self.wake_scheduled = False
            if self.queue:
                self.wake.set()

    async def get(self, wait_seconds: float) -> Message | None:
        """Return the oldest buffered message, or `None` when the wait expires."""
        try:
            async with asyncio.timeout(wait_seconds):
                await self.wake.wait()
        except TimeoutError:
            return None
        with self.lock:
            message = self.queue.popleft() if self.queue else None
            if not self.queue:
                self.wake.clear()
            return message


def _since_idle(
    logged: tuple[Message, ...], now: float, ttl: float
) -> tuple[Message, ...]:
    """Keep the messages written since the log last sat a whole TTL without a write.

    A write after such a pause restarts the log, as the expiry of a Redis key does.
    """
    if not logged or now - logged[-1].published_at >= ttl:
        return ()
    for index in range(len(logged) - 1, 0, -1):
        if logged[index].published_at - logged[index - 1].published_at >= ttl:
            return logged[index:]
    return logged


class _Roster:
    """The live attendance of one topic and the earliest moment a record can expire.

    Connections sharing an encoded member are one attendee, one without a member alone.
    Attendees keep the order of their first join, and `version` counts the changes.
    """

    __slots__ = (
        "anonymous",
        "attendees",
        "holders",
        "identified",
        "prune_at",
        "published",
        "version",
    )

    def __init__(self) -> None:
        self.anonymous: dict[str, float] = {}
        self.identified: dict[str, tuple[float, str]] = {}
        self.attendees: dict[str, float] = {}
        self.holders: dict[str, set[str]] = {}
        self.prune_at = math.inf
        self.version = 0
        self.published = 0

    def __bool__(self) -> bool:
        return bool(self.anonymous or self.identified)

    def put(self, connection: str, deadline: float, identity: str | None) -> bool:
        """Store a connection under its encoded member, True when it is new or moved."""
        if identity is None:
            fresh = connection not in self.anonymous
            if fresh:
                self.remove(connection)
            self.anonymous[connection] = deadline
        else:
            previous = self.identified.get(connection)
            fresh = previous is None or previous[1] != identity
            if fresh:
                self.remove(connection)
                self.holders.setdefault(identity, set()).add(connection)
            self.identified[connection] = (deadline, identity)
            before = None if previous is None or fresh else previous[0]
            self._rescore(identity, deadline, before)
        self.prune_at = min(self.prune_at, deadline)
        return fresh

    def refresh(self, connection: str, deadline: float) -> bool:
        """Move a connection's deadline, False when it holds no record."""
        if connection in self.anonymous:
            self.anonymous[connection] = deadline
        else:
            entry = self.identified.get(connection)
            if entry is None:
                return False
            before, identity = entry
            self.identified[connection] = (deadline, identity)
            if deadline >= self.attendees[identity]:
                self.attendees[identity] = deadline
            else:
                self._rescore(identity, deadline, before)
        self.prune_at = min(self.prune_at, deadline)
        return True

    def _rescore(self, identity: str, deadline: float, before: float | None) -> None:
        """Keep an attendee's deadline the latest of its connections after one moved."""
        held = self.attendees.get(identity)
        if held is None or deadline >= held:
            self.attendees[identity] = deadline
        elif before is not None and before >= held:
            holders = self.holders[identity]
            self.attendees[identity] = max(self.identified[h][0] for h in holders)

    def snapshot(self, now: float, size: int) -> tuple[int, list[str]]:
        """Count the unexpired attendees and list the first identified ones.

        Before the earliest deadline nothing has lapsed, so no record is scanned.
        """
        if now < self.prune_at:
            listed = list(itertools.islice(self.attendees, size))
            return len(self.attendees) + len(self.anonymous), listed
        live = [
            identity for identity, deadline in self.attendees.items() if deadline > now
        ]
        unnamed = sum(1 for deadline in self.anonymous.values() if deadline > now)
        return len(live) + unnamed, live[:size]

    def remove(self, connection: str) -> bool:
        """Forget a connection, True when its attendee left with it."""
        if self.anonymous.pop(connection, None) is not None:
            return True
        entry = self.identified.pop(connection, None)
        if entry is None:
            return False
        identity = entry[1]
        holders = self.holders[identity]
        holders.discard(connection)
        if not holders:
            del self.holders[identity], self.attendees[identity]
            return True
        self.attendees[identity] = max(self.identified[h][0] for h in holders)
        return False

    def prune(self, now: float) -> bool:
        """Drop the expired connections once the earliest deadline passed."""
        if now < self.prune_at:
            return False
        expired = [
            connection
            for connection, deadline in self.anonymous.items()
            if deadline <= now
        ]
        expired.extend(
            connection
            for connection, (deadline, _) in self.identified.items()
            if deadline <= now
        )
        changed = False
        for connection in expired:
            changed = self.remove(connection) or changed
        self.prune_at = min(
            itertools.chain(
                self.anonymous.values(),
                (deadline for deadline, _ in self.identified.values()),
            ),
            default=math.inf,
        )
        return changed


class MemoryBroker(BrokerBackend, PresenceWindow):
    """Deliver messages within one process using bounded subscriber queues.

    A replay log expires `REPLAY['TTL']` seconds after its last `published_at` stamp.
    """

    def __init__(
        self,
        config: Mapping[str, object] | None = None,
        *,
        clock: Callable[[], float] = time.time,
        replay: Mapping[str, object] | None = None,
        presence: Mapping[str, object] | None = None,
        schedule: Scheduler | None = None,
    ) -> None:
        """Create the broker and validate its queue size, replay and presence limits.

        `schedule` builds the presence schedule from the announce hook it is handed.
        """
        super().__init__(config)
        size = self.options.get("QUEUE_SIZE", 256)
        if not isinstance(size, int) or isinstance(size, bool) or size < 1:
            msg = "REALTIME.BROKER.OPTIONS.QUEUE_SIZE must be a positive integer."
            raise ValueError(msg)
        self._queue_size = size
        self.clock = clock
        self.replay_size, self.replay_ttl = replay_window(replay)
        self._lock = threading.RLock()
        self._positions: dict[str, Position] = {}
        self._logs: defaultdict[str, deque[Message]] = defaultdict(self._new_log)
        self._sweep_at = -math.inf
        self._subscribers: dict[str, set[_SyncSubscriber | _AsyncSubscriber]] = (
            defaultdict(set)
        )
        self._rosters: dict[str, _Roster] = {}
        self._windows: dict[str, float] = {}
        self.keep_presence(presence, schedule, clock)

    @override
    def publish(self, topic: str, envelope: Envelope) -> Message:
        """Assign a topic sequence and notify all local subscribers."""
        envelope = self.validate_publication(topic, envelope)
        with self._lock:
            current = self._positions.get(topic)
            position = (
                Position(secrets.token_hex(8), 1)
                if current is None
                else Position(current.epoch, current.seq + 1)
            )
            self._positions[topic] = position
            now = self.clock()
            message = Message(topic, position.seq, envelope, now, position.epoch)
            if self.replay_size:
                if now >= self._sweep_at:
                    self._sweep(now)
                self._logs[topic].append(message)
            self._fan_out(topic, message)
        return message

    @override
    def broadcast(self, topic: str, envelope: Envelope) -> Message:
        """Notify the local subscribers without numbering or logging the envelope."""
        envelope = self.validate_publication(topic, envelope)
        with self._lock:
            message = Message(topic, 0, envelope, self.clock())
            self._fan_out(topic, message)
        return message

    def _fan_out(self, topic: str, message: Message) -> None:
        """Queue a message for every subscriber of its topic, under the broker lock."""
        for subscriber in tuple(self._subscribers.get(topic, ())):
            if isinstance(subscriber, _SyncSubscriber):
                with subscriber.condition:
                    subscriber.queue.append(message)
                    subscriber.condition.notify()
            elif not subscriber.enqueue(message):
                self._remove_subscriber(subscriber)

    def _sweep(self, now: float) -> None:
        """Drop the expired logs on the first publish a TTL after the last sweep."""
        ttl = self.replay_ttl
        self._logs = defaultdict(
            self._new_log,
            {
                topic: log
                for topic, log in self._logs.items()
                if now - log[-1].published_at < ttl
            },
        )
        self._sweep_at = now + ttl

    def _new_log(self) -> deque[Message]:
        """Start an empty topic log bounded by `REPLAY['SIZE']`."""
        return deque(maxlen=self.replay_size)

    @override
    async def apublish(self, topic: str, envelope: Envelope) -> Message:
        """Publish with the same ordering and fan-out contract as `publish`."""
        return self.publish(topic, envelope)

    def replay(self, topic: str, after: Position) -> Replay:
        """Return the logged messages after a cursor, incomplete on any gap."""
        validate_topic(topic)
        if not self.replay_size:
            return INCOMPLETE
        with self._lock:
            current = self._positions.get(topic, _NEVER_PUBLISHED)
            log = self._logs.get(topic)
            logged = () if log is None else tuple(log)
            now = self.clock()
        return replay_after(after, current, _since_idle(logged, now, self.replay_ttl))

    async def areplay(self, topic: str, after: Position) -> Replay:
        """Replay without leaving the event loop, the log lives in this process."""
        return self.replay(topic, after)

    def join(
        self, topic: str, connection: str, member: Member | None, ttl: float
    ) -> None:
        """Record a connection and claim the window while a change is unannounced."""
        validate_topic(topic)
        encoded = encode_member(member, self.presence_limits.member_bytes)
        with self._lock:
            now = self.clock()
            roster = self._rosters.setdefault(topic, _Roster())
            pruned = roster.prune(now)
            if roster.put(connection, now + ttl, encoded) or pruned:
                roster.version += 1
            claimed = roster.version != roster.published and self._claim(topic, now)
        if claimed:
            self.schedule_presence(topic)

    def touch(self, topic: str, connection: str, ttl: float) -> bool:
        """Move a connection's deadline, pruning the topic's expired ones first."""
        validate_topic(topic)
        with self._lock:
            now = self.clock()
            roster = self._rosters.get(topic)
            if roster is None:
                return False
            if roster.prune(now):
                roster.version += 1
            present = roster.refresh(connection, now + ttl)
            claimed = roster.version != roster.published and self._claim(topic, now)
        if claimed:
            self.schedule_presence(topic)
        return present

    def leave(self, topic: str, connection: str) -> None:
        """Forget a connection and claim the window while a change is unannounced."""
        validate_topic(topic)
        with self._lock:
            now = self.clock()
            roster = self._rosters.get(topic)
            if roster is None:
                return
            pruned = roster.prune(now)
            if roster.remove(connection) or pruned:
                roster.version += 1
            claimed = roster.version != roster.published and self._claim(topic, now)
        if claimed:
            self.schedule_presence(topic)

    def presence(self, topic: str) -> Presence:
        """Return the unexpired attendees of a topic without pruning them.

        The members decode from their stored JSON in one call, fresh for each caller.
        """
        validate_topic(topic)
        with self._lock:
            roster = self._rosters.get(topic)
            if roster is None:
                return Presence(0)
            count, listed = roster.snapshot(self.clock(), self.presence_limits.size)
        if not listed:
            return Presence(count)
        return Presence(count, tuple(json.loads(f"[{','.join(listed)}]")))

    async def ajoin(
        self, topic: str, connection: str, member: Member | None, ttl: float
    ) -> None:
        """Join without leaving the event loop, the roster lives in this process."""
        self.join(topic, connection, member, ttl)

    async def atouch(self, topic: str, connection: str, ttl: float) -> bool:
        """Touch without leaving the event loop."""
        return self.touch(topic, connection, ttl)

    async def aleave(self, topic: str, connection: str) -> None:
        """Leave without leaving the event loop."""
        self.leave(topic, connection)

    async def apresence(self, topic: str) -> Presence:
        """Read the presence without leaving the event loop."""
        return self.presence(topic)

    def _claim(self, topic: str, now: float) -> bool:
        """Take the free window of a topic whose last change is still unannounced."""
        if now < self._windows.get(topic, -math.inf):
            return False
        self._windows[topic] = now + self.presence_limits.window
        return True

    @override
    def announce_presence(self, topic: str) -> None:
        """Release a closed window and broadcast the snapshot it coalesced.

        The read and the fan-out share the lock, so snapshots leave in change order.
        """
        with self._lock:
            if self._windows.get(topic, math.inf) <= self.clock():
                del self._windows[topic]
            roster = self._rosters.get(topic)
            if roster is None or roster.version == roster.published:
                return
            snapshot = self.presence(topic)
            roster.published = roster.version
            if not roster:
                del self._rosters[topic]
            message = self.broadcast(topic, presence_envelope(snapshot))
        published(self, message)

    @override
    def close(self) -> None:
        """Broadcast the pending snapshots and stop the presence schedule."""
        self.presence_schedule.close()

    @override
    def subscribe(
        self, topics: Sequence[str], *, timeout: float
    ) -> AbstractContextManager[Iterator[Message | None]]:
        """Return a context manager for a blocking subscription iterator."""
        selected = tuple(dict.fromkeys(validate_topic(topic) for topic in topics))
        if not selected:
            msg = "A broker subscription needs at least one topic."
            raise ValueError(msg)
        subscriber = _SyncSubscriber(self._queue_size)

        @contextmanager
        def subscription() -> Iterator[Iterator[Message | None]]:
            self._register(selected, subscriber)
            try:
                yield self._sync_messages(subscriber, timeout)
            finally:
                self._unregister(selected, subscriber)

        return subscription()

    def _sync_messages(
        self, subscriber: _SyncSubscriber, timeout: float
    ) -> Iterator[Message | None]:
        while True:
            with subscriber.condition:
                if not subscriber.queue:
                    subscriber.condition.wait(timeout)
                message = subscriber.queue.popleft() if subscriber.queue else None
            yield message

    @override
    def asubscribe(
        self, topics: Sequence[str], *, timeout: float
    ) -> AbstractAsyncContextManager[AsyncIterator[Message | None]]:
        """Return a context manager for a bounded asynchronous subscription."""
        selected = tuple(dict.fromkeys(validate_topic(topic) for topic in topics))
        if not selected:
            msg = "A broker subscription needs at least one topic."
            raise ValueError(msg)
        loop = asyncio.get_running_loop()
        subscriber = _AsyncSubscriber(loop, self._queue_size)

        @asynccontextmanager
        async def subscription() -> AsyncIterator[AsyncIterator[Message | None]]:
            self._register(selected, subscriber)
            try:
                yield self._async_messages(subscriber, timeout)
            finally:
                self._unregister(selected, subscriber)

        return subscription()

    async def _async_messages(
        self, subscriber: _AsyncSubscriber, wait_seconds: float
    ) -> AsyncIterator[Message | None]:
        while True:
            yield await subscriber.get(wait_seconds)

    def _register(
        self, topics: Iterable[str], subscriber: _SyncSubscriber | _AsyncSubscriber
    ) -> None:
        with self._lock:
            for topic in topics:
                self._subscribers[topic].add(subscriber)

    def _unregister(
        self, topics: Iterable[str], subscriber: _SyncSubscriber | _AsyncSubscriber
    ) -> None:
        with self._lock:
            for topic in topics:
                subscribers = self._subscribers.get(topic)
                if subscribers is None:
                    continue
                subscribers.discard(subscriber)
                if not subscribers:
                    self._subscribers.pop(topic, None)

    def _remove_subscriber(
        self, subscriber: _SyncSubscriber | _AsyncSubscriber
    ) -> None:
        for subscribed_topic, subscribers in tuple(self._subscribers.items()):
            subscribers.discard(subscriber)
            if not subscribers:
                self._subscribers.pop(subscribed_topic, None)

    @override
    def positions(self, topics: Sequence[str]) -> dict[str, Position]:
        """Return the local topic positions, empty for topics never published."""
        selected = tuple(dict.fromkeys(validate_topic(topic) for topic in topics))
        with self._lock:
            return {
                topic: self._positions.get(topic, _NEVER_PUBLISHED)
                for topic in selected
            }

    @override
    async def apositions(self, topics: Sequence[str]) -> dict[str, Position]:
        """Return the local topic positions without leaving the event loop."""
        return self.positions(topics)


__all__ = ["BrokerBackend", "MemoryBroker"]
