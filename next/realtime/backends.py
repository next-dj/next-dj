"""Broker contracts and the process-local in-memory implementation."""

import asyncio
import secrets
import threading
import time
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Iterable, Iterator, Mapping, Sequence
from contextlib import (
    AbstractAsyncContextManager,
    AbstractContextManager,
    asynccontextmanager,
    contextmanager,
)
from typing import override

from next.partial.envelope import Envelope

from .messages import Message, Position
from .topics import validate_topic


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


class MemoryBroker(BrokerBackend):
    """Deliver messages within one process using bounded subscriber queues."""

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        """Create the broker and validate its per-subscriber queue size."""
        super().__init__(config)
        size = self.options.get("QUEUE_SIZE", 256)
        if not isinstance(size, int) or isinstance(size, bool) or size < 1:
            msg = "REALTIME.BROKER.OPTIONS.QUEUE_SIZE must be a positive integer."
            raise ValueError(msg)
        self._queue_size = size
        self._lock = threading.RLock()
        self._positions: dict[str, Position] = {}
        self._subscribers: dict[str, set[_SyncSubscriber | _AsyncSubscriber]] = (
            defaultdict(set)
        )

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
            message = Message(
                topic, position.seq, envelope, time.time(), position.epoch
            )
            for subscriber in tuple(self._subscribers.get(topic, ())):
                if isinstance(subscriber, _SyncSubscriber):
                    with subscriber.condition:
                        subscriber.queue.append(message)
                        subscriber.condition.notify()
                elif not subscriber.enqueue(message):
                    self._remove_subscriber(subscriber)
        return message

    @override
    async def apublish(self, topic: str, envelope: Envelope) -> Message:
        """Publish with the same ordering and fan-out contract as `publish`."""
        return self.publish(topic, envelope)

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
