"""Realtime broker backed by a Django Channels channel layer and shared cache."""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import importlib
import logging
import os
import re
import secrets
import threading
import time
import weakref
from collections import deque
from collections.abc import (
    AsyncIterator,
    Callable,
    Coroutine,
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
from typing import TYPE_CHECKING, Any, override

from django.core.cache import caches
from django.core.exceptions import ImproperlyConfigured

from next.realtime.access import run_detached
from next.realtime.backends import BrokerBackend
from next.realtime.errors import BrokerConnectionError
from next.realtime.messages import Message, Position, validate_topic


if TYPE_CHECKING:
    from types import TracebackType

    from next.partial.envelope import Envelope


logger = logging.getLogger(__name__)

_DEFAULT_PREFIX = "next.rt"
_DEFAULT_GROUP_EXPIRY = 86_400
_MAX_GROUP_RENEWAL = 3_600
_EVENT_TYPE = "next.message"
_PREFIX_RE = re.compile(r"[A-Za-z0-9.-]{1,40}")
_SYNC_QUEUE_SIZE = 256
_SHUTDOWN_TIMEOUT = 5.0
_HANDOFF_SECONDS = 3_600
_ALLOCATION_ATTEMPTS = 3


def _optional_errors(module: str, *names: str) -> tuple[type[Exception], ...]:
    """Return exception classes from an optional dependency, or none when absent."""
    try:
        found = importlib.import_module(module)
    except ImportError:
        return ()
    return tuple(getattr(found, name) for name in names)


def _transport_errors() -> tuple[type[Exception], ...]:
    """Collect the transport errors raised by layers and caches that are installed."""
    return (
        OSError,
        *_optional_errors("redis.exceptions", "ConnectionError", "TimeoutError"),
        *_optional_errors("pymemcache.exceptions", "MemcacheError"),
        *_optional_errors("pylibmc", "Error"),
    )


_LAYER_FAILURES = _transport_errors()


class _LayerFailures:
    """Re-raise a channel layer or cache transport error as a broker error."""

    def __enter__(self) -> None:
        """Enter a guarded layer or cache call."""

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Translate a transport failure so callers catch one broker error type."""
        if isinstance(error, _LAYER_FAILURES) and not isinstance(
            error, BrokerConnectionError
        ):
            msg = f"The Channels broker transport failed: {error}"
            raise BrokerConnectionError(msg) from error


_GUARD = _LayerFailures()
_CLEANUP_FAILURES: tuple[type[BaseException], ...] = (
    TimeoutError,
    concurrent.futures.CancelledError,
    *_LAYER_FAILURES,
)


class _Inbox:
    """A bounded buffer a bridge-loop pump fills for one blocking subscriber."""

    def __init__(self) -> None:
        self.queue: deque[Message] = deque(maxlen=_SYNC_QUEUE_SIZE)
        self.condition = threading.Condition()

    def put(self, message: Message) -> None:
        """Buffer a message, dropping the oldest once the subscriber falls behind."""
        with self.condition:
            self.queue.append(message)
            self.condition.notify()

    def wake(self, _future: object = None) -> None:
        """Wake the subscriber so it notices that the pump has stopped."""
        with self.condition:
            self.condition.notify()


class _Bridge:
    """One daemon thread running the event loop that owns every sync layer call."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._pending: set[concurrent.futures.Future[Any]] = set()
        _BRIDGES.add(self)

    def submit[T](
        self, coroutine: Coroutine[Any, Any, T]
    ) -> concurrent.futures.Future[T]:
        """Schedule a coroutine on the bridge loop, starting the thread on demand."""
        with self._lock:
            loop = self._loop
            if loop is None:
                loop = asyncio.new_event_loop()
                thread = threading.Thread(
                    target=loop.run_forever, name="next-realtime-channels", daemon=True
                )
                thread.start()
                self._loop, self._thread = loop, thread
            future = asyncio.run_coroutine_threadsafe(coroutine, loop)
            self._pending.add(future)
        future.add_done_callback(self._pending.discard)
        return future

    def run[T](self, coroutine: Coroutine[Any, Any, T]) -> T:
        """Run a coroutine on the bridge loop and wait for its result."""
        try:
            return self.submit(coroutine).result()
        except concurrent.futures.CancelledError as exc:
            msg = "The Channels broker bridge closed during the call."
            raise BrokerConnectionError(msg) from exc

    def run_if_open(self, coroutine: Coroutine[Any, Any, None]) -> None:
        """Run cleanup on a live bridge loop with a bounded wait, logging failures."""
        with self._lock:
            if self._loop is None:
                coroutine.close()
                return
            future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
            self._pending.add(future)
        future.add_done_callback(self._pending.discard)
        try:
            future.result(_SHUTDOWN_TIMEOUT)
        except _CLEANUP_FAILURES:
            logger.warning("Channels broker cleanup did not finish.", exc_info=True)

    def close(self) -> None:
        """Cancel outstanding work, stop the loop and join its thread, never raising."""
        with self._lock:
            loop, thread = self._loop, self._thread
            pending = tuple(self._pending)
            self._loop = self._thread = None
            self._pending = set()
        if loop is None or thread is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(_cancel_pending(), loop).result(
                _SHUTDOWN_TIMEOUT
            )
        except TimeoutError:
            logger.warning("Channels broker tasks ignored cancellation on close.")
        loop.call_soon_threadsafe(loop.stop)
        thread.join(_SHUTDOWN_TIMEOUT)
        for future in pending:
            future.cancel()
        if thread.is_alive():
            logger.warning("Channels broker bridge thread did not stop in time.")
            return
        loop.close()

    def forget(self) -> None:
        """Drop the state a forked child inherits, since the bridge thread is gone."""
        self._lock = threading.Lock()
        self._loop = self._thread = None
        self._pending = set()


_BRIDGES: weakref.WeakSet[_Bridge] = weakref.WeakSet()


def _forget_bridges() -> None:
    """Reset every bridge in a forked child so it starts its own loop on demand."""
    for bridge in tuple(_BRIDGES):
        bridge.forget()


def _without_fork(**_hooks: Callable[[], object]) -> None:
    """Stand in for os.register_at_fork on platforms that cannot fork."""


getattr(os, "register_at_fork", _without_fork)(after_in_child=_forget_bridges)


async def _cancel_pending() -> None:
    """Cancel every task on the running loop except the caller and await them."""
    current = asyncio.current_task()
    tasks = [task for task in asyncio.all_tasks() if task is not current]
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


class ChannelsLayerBroker(BrokerBackend):
    """Fan out topic messages through Channels groups and sequence them in cache.

    The cache must never evict realtime keys, so use noeviction or a dedicated cache.
    """

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        """Resolve an optional channel layer and validate its bounded queue size."""
        super().__init__(config)
        alias = self.options.get("CHANNEL_LAYER", "default")
        cache_alias = self.options.get("CACHE_ALIAS", "default")
        prefix = self.options.get("PREFIX", _DEFAULT_PREFIX)
        if not isinstance(alias, str) or not alias:
            msg = "CHANNEL_LAYER must name a channel layer."
            raise ImproperlyConfigured(msg)
        if not isinstance(cache_alias, str) or not cache_alias:
            msg = "CACHE_ALIAS must name a Django cache."
            raise ImproperlyConfigured(msg)
        if not isinstance(prefix, str) or _PREFIX_RE.fullmatch(prefix) is None:
            msg = "PREFIX must contain 1-40 safe ASCII characters."
            raise ImproperlyConfigured(msg)
        try:
            channel_layers = importlib.import_module("channels.layers")
            self._layer = channel_layers.get_channel_layer(alias)
        except ImportError as exc:
            msg = "ChannelsLayerBroker requires next.dj[channels]."
            raise ImproperlyConfigured(msg) from exc
        if self._layer is None:
            msg = f"No Channels layer is configured for {alias!r}."
            raise ImproperlyConfigured(msg)
        self._cache = caches[cache_alias]
        self._prefix = prefix
        self._bridge = _Bridge()
        expiry = getattr(self._layer, "group_expiry", _DEFAULT_GROUP_EXPIRY)
        if (
            not isinstance(expiry, int | float)
            or isinstance(expiry, bool)
            or expiry <= 0
        ):
            expiry = _DEFAULT_GROUP_EXPIRY
        self._group_renewal_seconds = min(_MAX_GROUP_RENEWAL, expiry / 2)

    @override
    def publish(self, topic: str, envelope: Envelope) -> Message:
        """Assign a cache-backed position and publish through the bridge loop."""
        message = self._allocate(topic, envelope)
        self._bridge.run(self._send(message))
        return message

    @override
    async def apublish(self, topic: str, envelope: Envelope) -> Message:
        """Publish without blocking an async request on cache or channel I/O."""
        message = await run_detached(self._allocate, topic, envelope)
        await self._send(message)
        return message

    async def _send(self, message: Message) -> None:
        """Fan out after allocation, a separate step whose gaps clients recover."""
        with _GUARD:
            await self._layer.group_send(
                self._group(message.topic),
                {"type": _EVENT_TYPE, "payload": message.as_dict()},
            )

    @override
    def subscribe(
        self, topics: Sequence[str], *, timeout: float
    ) -> AbstractContextManager[Iterator[Message | None]]:
        """Subscribe synchronously through the bridge loop that owns every receive."""
        selected = self._topics(topics)

        @contextmanager
        def subscription() -> Iterator[Iterator[Message | None]]:
            groups = tuple(self._group(topic) for topic in selected)
            channel = self._bridge.run(self._new_channel())
            inbox = _Inbox()
            pump: concurrent.futures.Future[None] | None = None
            try:
                self._bridge.run(self._join(groups, channel))
                pump = self._bridge.submit(self._pump(channel, groups, inbox))
                pump.add_done_callback(inbox.wake)
                yield self._sync_messages(inbox, pump, timeout)
            finally:
                if pump is not None:
                    pump.cancel()
                self._bridge.run_if_open(self._leave(groups, channel))

        return subscription()

    async def _new_channel(self) -> str:
        with _GUARD:
            channel: str = await self._layer.new_channel("next.realtime")
        return channel

    async def _join(self, groups: tuple[str, ...], channel: str) -> None:
        with _GUARD:
            for group in groups:
                await self._layer.group_add(group, channel)

    async def _leave(self, groups: tuple[str, ...], channel: str) -> None:
        with _GUARD:
            for group in groups:
                await self._layer.group_discard(group, channel)

    async def _pump(self, channel: str, groups: tuple[str, ...], inbox: _Inbox) -> None:
        """Move layer events into the inbox and renew group membership on time."""
        renewed_at = time.monotonic()
        while True:
            event = await self._receive(channel, self._group_renewal_seconds)
            if time.monotonic() - renewed_at >= self._group_renewal_seconds:
                await self._join(groups, channel)
                renewed_at = time.monotonic()
            message = self._decode(event)
            if message is not None:
                inbox.put(message)

    def _sync_messages(
        self, inbox: _Inbox, pump: concurrent.futures.Future[None], wait_seconds: float
    ) -> Iterator[Message | None]:
        while True:
            with inbox.condition:
                if not inbox.queue and not pump.done():
                    inbox.condition.wait(wait_seconds)
                message = inbox.queue.popleft() if inbox.queue else None
            if message is None and pump.done():
                if not pump.cancelled():
                    pump.result()
                return
            yield message

    @override
    def asubscribe(
        self, topics: Sequence[str], *, timeout: float
    ) -> AbstractAsyncContextManager[AsyncIterator[Message | None]]:
        """Subscribe asynchronously and discard every group on exit or cancellation."""
        selected = self._topics(topics)

        @asynccontextmanager
        async def subscription() -> AsyncIterator[AsyncIterator[Message | None]]:
            channel = await self._new_channel()
            groups = tuple(self._group(topic) for topic in selected)
            try:
                await self._join(groups, channel)
                yield self._async_messages(channel, groups, timeout)
            finally:
                await self._leave(groups, channel)

        return subscription()

    async def _async_messages(
        self, channel: str, groups: tuple[str, ...], wait_seconds: float
    ) -> AsyncIterator[Message | None]:
        renewed_at = time.monotonic()
        while True:
            event = await self._receive(
                channel, min(wait_seconds, self._group_renewal_seconds)
            )
            if time.monotonic() - renewed_at >= self._group_renewal_seconds:
                await self._join(groups, channel)
                renewed_at = time.monotonic()
            yield self._decode(event)

    async def _receive(
        self, channel: str, wait_seconds: float
    ) -> Mapping[str, Any] | None:
        try:
            event = await asyncio.wait_for(
                self._layer.receive(channel), timeout=wait_seconds
            )
        except TimeoutError:
            return None
        except _LAYER_FAILURES as exc:
            msg = f"The Channels layer receive failed: {exc}"
            raise BrokerConnectionError(msg) from exc
        return event if isinstance(event, Mapping) else None

    def _decode(self, event: Mapping[str, Any] | None) -> Message | None:
        if event is None or event.get("type") != _EVENT_TYPE:
            return None
        payload = event.get("payload")
        if not isinstance(payload, Mapping):
            return None
        try:
            return Message.from_dict(payload)
        except (TypeError, ValueError):
            logger.warning("Skipped a malformed realtime message from the layer.")
            return None

    def _allocate(self, topic: str, envelope: Envelope) -> Message:
        """Take the next seq from the counter that belongs to the topic's epoch.

        Two racing rotations may briefly flip the stored epoch back, never a seq.
        """
        envelope = self.validate_publication(topic, envelope)
        epoch = self._current_epoch(topic)
        for _ in range(_ALLOCATION_ATTEMPTS):
            try:
                with _GUARD:
                    seq = self._cache.incr(self._counter_key(topic, epoch))
            except ValueError:
                epoch = self._rotate_epoch(topic, epoch)
                continue
            return Message(topic, seq, envelope, time.time(), epoch)
        msg = "The Channels broker cache keeps losing the topic counter."
        raise BrokerConnectionError(msg)

    def _current_epoch(self, topic: str) -> str:
        """Return the stored epoch, creating one together with its counter if absent."""
        epoch_key = self._epoch_key(topic)
        with _GUARD:
            stored = self._cache.get(epoch_key)
        if isinstance(stored, str):
            return stored
        return self._claim(topic, epoch_key, timeout=None)

    def _rotate_epoch(self, topic: str, lost: str) -> str:
        """Agree on one successor epoch after the counter of `lost` disappeared."""
        epoch = self._claim(
            topic, self._handoff_key(topic, lost), timeout=_HANDOFF_SECONDS
        )
        with _GUARD:
            self._cache.set(self._epoch_key(topic), epoch, timeout=None)
        return epoch

    def _claim(self, topic: str, key: str, *, timeout: int | None) -> str:
        """Create a counter first, then race to publish its epoch under `key`."""
        candidate = secrets.token_hex(8)
        counter = self._counter_key(topic, candidate)
        with _GUARD:
            self._cache.add(counter, 0, timeout=None)
            if self._cache.add(key, candidate, timeout=timeout):
                return candidate
            self._cache.delete(counter)
            winner = self._cache.get(key)
        if isinstance(winner, str):
            return winner
        msg = "The Channels broker cache dropped a freshly stored epoch."
        raise BrokerConnectionError(msg)

    @override
    def positions(self, topics: Sequence[str]) -> dict[str, Position]:
        """Read the topic epochs, then their counters, in two batched cache reads."""
        selected = tuple(dict.fromkeys(validate_topic(topic) for topic in topics))
        epoch_keys = {topic: self._epoch_key(topic) for topic in selected}
        with _GUARD:
            epochs = self._cache.get_many(list(epoch_keys.values()))
        current = {
            topic: epoch
            for topic, key in epoch_keys.items()
            if isinstance(epoch := epochs.get(key), str)
        }
        counter_keys = {
            topic: self._counter_key(topic, epoch) for topic, epoch in current.items()
        }
        with _GUARD:
            counters = self._cache.get_many(list(counter_keys.values()))
        positions: dict[str, Position] = {}
        for topic in selected:
            epoch = current.get(topic, "")
            seq = counters.get(counter_keys.get(topic, ""))
            if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
                seq = 0
            positions[topic] = Position(epoch, seq)
        return positions

    @override
    async def apositions(self, topics: Sequence[str]) -> dict[str, Position]:
        """Read the positions on the detached pool, closing stale DB connections."""
        return await run_detached(self.positions, topics)

    @override
    def close(self) -> None:
        """Stop the bridge loop, ending every blocking subscription it served."""
        self._bridge.close()

    def _counter_key(self, topic: str, epoch: str) -> str:
        return f"{self._prefix}:seq:{self._digest(topic)}:{epoch}"

    def _handoff_key(self, topic: str, epoch: str) -> str:
        return f"{self._prefix}:next:{self._digest(topic)}:{epoch}"

    def _epoch_key(self, topic: str) -> str:
        return f"{self._prefix}:epoch:{self._digest(topic)}"

    def _group(self, topic: str) -> str:
        return f"{self._prefix}-{self._digest(topic)[:48]}"

    @staticmethod
    def _digest(topic: str) -> str:
        return hashlib.sha256(topic.encode()).hexdigest()

    @staticmethod
    def _topics(topics: Sequence[str]) -> tuple[str, ...]:
        selected = tuple(dict.fromkeys(validate_topic(topic) for topic in topics))
        if not selected:
            msg = "A broker subscription needs at least one topic."
            raise ValueError(msg)
        return selected


__all__ = ["ChannelsLayerBroker"]
