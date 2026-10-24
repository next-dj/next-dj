"""Redis broker using atomic sequence assignment and topic publication."""

import asyncio
import importlib
import json
import logging
import math
import os
import secrets
import threading
import time
import weakref
from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
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
from types import TracebackType
from typing import Any, Protocol, override
from weakref import WeakKeyDictionary

from django.core.exceptions import ImproperlyConfigured

from next.partial.envelope import Envelope

from .backends import BrokerBackend
from .errors import BrokerConnectionError
from .messages import Message, Position, validate_topic


logger = logging.getLogger(__name__)

_DEFAULT_URL = "redis://localhost:6379/0"
_DEFAULT_PREFIX = "next"
_DEFAULT_HEALTH_CHECK_INTERVAL = 10.0
_SUBSCRIBE_TIMEOUT = 5.0
_LIVENESS = "next-realtime-liveness"
_UNCONFIRMED = "Redis did not confirm the subscription in time."
_PUBLISH_SCRIPT = """
redis.call('HINCRBY', KEYS[1], 'seq', 1)
local state = redis.call('HMGET', KEYS[1], 'seq', 'epoch')
local epoch = state[2]
if state[1] == '1' or not epoch then
  epoch = ARGV[3]
  redis.call('HSET', KEYS[1], 'epoch', epoch)
end
local head = '{"seq":' .. state[1] .. ',"epoch":"' .. epoch .. '",'
redis.call('PUBLISH', ARGV[1], head .. string.sub(ARGV[2], 2))
return {state[1], epoch}
"""


class _PubSub(Protocol):
    def get_message(self, *, timeout: float) -> Mapping[str, Any] | None: ...

    def ping(self, message: str) -> object: ...


class _AsyncPubSub(Protocol):
    def get_message(self, *, timeout: float) -> Awaitable[Mapping[str, Any] | None]: ...

    def ping(self, message: str) -> Awaitable[object]: ...


class _TransportFailures:
    """Re-raise a Redis connection or timeout error as a broker connection error."""

    def __init__(self, errors: tuple[type[Exception], ...]) -> None:
        self.errors = errors

    def __enter__(self) -> None:
        """Enter a guarded Redis call."""

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Translate a transport failure so callers catch one broker error type."""
        if isinstance(error, self.errors):
            msg = f"The Redis broker connection failed: {error}"
            raise BrokerConnectionError(msg) from error


class _Watch:
    """Track the heartbeat deadline and the liveness probe of one subscription."""

    def __init__(self, timeout: float) -> None:
        self.timeout = timeout
        self.deadline = time.monotonic() + timeout
        self.probing = False

    def remaining(self) -> float:
        """Return the seconds left before the next heartbeat is due."""
        return self.deadline - time.monotonic()

    def expire(self) -> None:
        """Start a liveness probe, failing when the previous one went unanswered."""
        if self.probing:
            msg = "Redis stopped answering on a subscription connection."
            raise BrokerConnectionError(msg)
        self.probing = True
        self.deadline = time.monotonic() + self.timeout

    def answered(self, *, delivered: bool) -> None:
        """Record a frame from Redis and restart the heartbeat after a message."""
        self.probing = False
        if delivered:
            self.deadline = time.monotonic() + self.timeout


class RedisBroker(BrokerBackend):
    """Deliver messages across processes through Redis Pub/Sub."""

    def __init__(self, config: Mapping[str, object] | None = None) -> None:
        """Create the sync Redis client and prepare per-loop async clients."""
        super().__init__(config)
        try:
            redis = importlib.import_module("redis")
            redis_asyncio = importlib.import_module("redis.asyncio")
        except ImportError as exc:
            msg = (
                "RedisBroker requires the optional dependency. Install next.dj[broker]."
            )
            raise ImproperlyConfigured(msg) from exc
        url = self.options.get("URL", _DEFAULT_URL)
        prefix = self.options.get("PREFIX", _DEFAULT_PREFIX)
        interval = self.options.get(
            "HEALTH_CHECK_INTERVAL", _DEFAULT_HEALTH_CHECK_INTERVAL
        )
        if not isinstance(url, str) or not url:
            msg = "REALTIME.BROKER.OPTIONS.URL must be a non-empty Redis URL."
            raise ImproperlyConfigured(msg)
        if not isinstance(prefix, str) or not prefix:
            msg = "REALTIME.BROKER.OPTIONS.PREFIX must be a non-empty string."
            raise ImproperlyConfigured(msg)
        if (
            isinstance(interval, bool)
            or not isinstance(interval, int | float)
            or not math.isfinite(interval)
            or interval < 0
        ):
            msg = (
                "REALTIME.BROKER.OPTIONS.HEALTH_CHECK_INTERVAL must be a "
                "non-negative number of seconds."
            )
            raise ImproperlyConfigured(msg)
        self._prefix = prefix.rstrip(":")
        if not self._prefix:
            msg = "REALTIME.BROKER.OPTIONS.PREFIX must contain a non-colon character."
            raise ImproperlyConfigured(msg)
        self._url = url
        self._client_options: dict[str, object] = {
            "decode_responses": True,
            "health_check_interval": interval,
            "socket_keepalive": True,
        }
        self._redis: Any = redis.Redis.from_url(url, **self._client_options)
        self._publish_script: Any = self._redis.register_script(_PUBLISH_SCRIPT)
        self._async_redis_class: Any = redis_asyncio.Redis
        self._failures = _TransportFailures(
            (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError)
        )
        self._async_clients: WeakKeyDictionary[
            asyncio.AbstractEventLoop, tuple[Any, Any]
        ] = WeakKeyDictionary()
        self._async_lock = threading.Lock()
        _BROKERS.add(self)

    @override
    def publish(self, topic: str, envelope: Envelope) -> Message:
        """Atomically number and publish one envelope on a topic."""
        envelope = self.validate_publication(topic, envelope)
        published_at = time.time()
        with self._failures:
            seq, epoch = self._publish_script(
                keys=[self._sequence_key(topic)],
                args=self._script_args(topic, envelope, published_at),
            )
        return Message(topic, int(seq), envelope, published_at, epoch)

    @override
    async def apublish(self, topic: str, envelope: Envelope) -> Message:
        """Atomically number and publish one envelope through this loop's client."""
        envelope = self.validate_publication(topic, envelope)
        published_at = time.time()
        _, script = self._async_client()
        with self._failures:
            seq, epoch = await script(
                keys=[self._sequence_key(topic)],
                args=self._script_args(topic, envelope, published_at),
            )
        return Message(topic, int(seq), envelope, published_at, epoch)

    @override
    def subscribe(
        self, topics: Sequence[str], *, timeout: float
    ) -> AbstractContextManager[Iterator[Message | None]]:
        """Subscribe to Redis topic channels once Redis confirms every channel."""
        channels = self._channels(topics)

        @contextmanager
        def subscription() -> Iterator[Iterator[Message | None]]:
            pubsub = self._redis.pubsub()
            try:
                with self._failures:
                    pubsub.subscribe(*channels)
                    buffered = self._confirm(pubsub, channels)
                yield self._sync_messages(pubsub, buffered, timeout)
            finally:
                pubsub.close()

        return subscription()

    def _confirm(self, pubsub: _PubSub, channels: tuple[str, ...]) -> list[Message]:
        """Consume every subscribe acknowledgement, keeping messages seen meanwhile."""
        waiting = set(channels)
        buffered: list[Message] = []
        deadline = time.monotonic() + _SUBSCRIBE_TIMEOUT
        while waiting:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BrokerConnectionError(_UNCONFIRMED)
            self._collect(pubsub.get_message(timeout=remaining), waiting, buffered)
        return buffered

    def _sync_messages(
        self, pubsub: _PubSub, buffered: list[Message], wait_seconds: float
    ) -> Iterator[Message | None]:
        yield from buffered
        watch = _Watch(wait_seconds)
        while True:
            with self._failures:
                event = pubsub.get_message(timeout=max(watch.remaining(), 0.0))
            if event is not None:
                message = self._event_message(event)
                watch.answered(delivered=message is not None)
                if message is not None:
                    yield message
            elif watch.remaining() <= 0:
                watch.expire()
                with self._failures:
                    pubsub.ping(_LIVENESS)
                yield None

    @override
    def asubscribe(
        self, topics: Sequence[str], *, timeout: float
    ) -> AbstractAsyncContextManager[AsyncIterator[Message | None]]:
        """Subscribe asynchronously once Redis confirms every channel."""
        channels = self._channels(topics)

        @asynccontextmanager
        async def subscription() -> AsyncIterator[AsyncIterator[Message | None]]:
            client, _ = self._async_client()
            pubsub = client.pubsub()
            try:
                with self._failures:
                    await pubsub.subscribe(*channels)
                    buffered = await self._aconfirm(pubsub, channels)
                yield self._async_messages(pubsub, buffered, timeout)
            finally:
                await pubsub.aclose()

        return subscription()

    async def _aconfirm(
        self, pubsub: _AsyncPubSub, channels: tuple[str, ...]
    ) -> list[Message]:
        """Consume every subscribe acknowledgement without blocking the loop."""
        waiting = set(channels)
        buffered: list[Message] = []
        deadline = time.monotonic() + _SUBSCRIBE_TIMEOUT
        while waiting:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BrokerConnectionError(_UNCONFIRMED)
            event = await pubsub.get_message(timeout=remaining)
            self._collect(event, waiting, buffered)
        return buffered

    async def _async_messages(
        self, pubsub: _AsyncPubSub, buffered: list[Message], wait_seconds: float
    ) -> AsyncIterator[Message | None]:
        for early in buffered:
            yield early
        watch = _Watch(wait_seconds)
        while True:
            with self._failures:
                event = await pubsub.get_message(timeout=max(watch.remaining(), 0.0))
            if event is not None:
                message = self._event_message(event)
                watch.answered(delivered=message is not None)
                if message is not None:
                    yield message
            elif watch.remaining() <= 0:
                watch.expire()
                with self._failures:
                    await pubsub.ping(_LIVENESS)
                yield None

    @override
    def positions(self, topics: Sequence[str]) -> dict[str, Position]:
        """Read every topic position in one pipelined round trip."""
        selected = self._validated_topics(topics)
        pipe = self._redis.pipeline(transaction=False)
        for topic in selected:
            pipe.hmget(self._sequence_key(topic), "seq", "epoch")
        with self._failures:
            rows = pipe.execute()
        return self._positions(selected, rows)

    @override
    async def apositions(self, topics: Sequence[str]) -> dict[str, Position]:
        """Read every topic position in one pipelined round trip asynchronously."""
        selected = self._validated_topics(topics)
        client, _ = self._async_client()
        with self._failures:
            async with client.pipeline(transaction=False) as pipe:
                for topic in selected:
                    pipe.hmget(self._sequence_key(topic), "seq", "epoch")
                rows = await pipe.execute()
        return self._positions(selected, rows)

    @override
    def close(self) -> None:
        """Close the sync pool and schedule the close of clients on live loops."""
        with self._async_lock:
            clients = list(self._async_clients.items())
            self._async_clients.clear()
        for loop, (client, _) in clients:
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(client.aclose(), loop)
        self._redis.close()

    @override
    async def aclose(self) -> None:
        """Await the close of this loop's client, then release the rest."""
        with self._async_lock:
            entry = self._async_clients.pop(asyncio.get_running_loop(), None)
        if entry is not None:
            await entry[0].aclose()
        self.close()

    def forget_clients(self) -> None:
        """Drop the async clients a forked child inherits from its parent's loops."""
        self._async_lock = threading.Lock()
        self._async_clients = WeakKeyDictionary()

    def _async_client(self) -> tuple[Any, Any]:
        """Return the async client and script bound to the running event loop."""
        loop = asyncio.get_running_loop()
        with self._async_lock:
            for stale in [known for known in self._async_clients if known.is_closed()]:
                del self._async_clients[stale]
            entry = self._async_clients.get(loop)
            if entry is None:
                client = self._async_redis_class.from_url(
                    self._url, **self._client_options
                )
                entry = (client, client.register_script(_PUBLISH_SCRIPT))
                self._async_clients[loop] = entry
        return entry

    def _collect(
        self,
        event: Mapping[str, Any] | None,
        waiting: set[str],
        buffered: list[Message],
    ) -> None:
        """Tick off one acknowledgement or keep a message that arrived during setup."""
        if event is None:
            return
        if event.get("type") == "subscribe":
            waiting.discard(event.get("channel", ""))
            return
        message = self._event_message(event)
        if message is not None:
            buffered.append(message)

    def _event_message(self, event: Mapping[str, Any]) -> Message | None:
        """Decode a published frame and treat a fresh acknowledgement as reconnect."""
        kind = event.get("type")
        if kind == "subscribe":
            msg = "Redis reconnected a subscription, so messages may have been lost."
            raise BrokerConnectionError(msg)
        if kind != "message":
            return None
        return self._decode_message(event.get("data"))

    def _sequence_key(self, topic: str) -> str:
        return f"{self._prefix}:seq:{topic}"

    def _channel(self, topic: str) -> str:
        return f"{self._prefix}:topic:{topic}"

    def _channels(self, topics: Sequence[str]) -> tuple[str, ...]:
        selected = self._validated_topics(topics)
        if not selected:
            msg = "A broker subscription needs at least one topic."
            raise ValueError(msg)
        return tuple(self._channel(topic) for topic in selected)

    def _script_args(
        self, topic: str, envelope: Envelope, published_at: float
    ) -> list[str]:
        body = {
            "topic": topic,
            "envelope": envelope.as_dict(),
            "published_at": published_at,
        }
        return [
            self._channel(topic),
            json.dumps(body, separators=(",", ":")),
            secrets.token_hex(8),
        ]

    @staticmethod
    def _positions(
        topics: tuple[str, ...], rows: Sequence[Sequence[str | None]]
    ) -> dict[str, Position]:
        positions: dict[str, Position] = {}
        for topic, (seq, epoch) in zip(topics, rows, strict=True):
            positions[topic] = Position(epoch or "", 0 if seq is None else int(seq))
        return positions

    @staticmethod
    def _validated_topics(topics: Sequence[str]) -> tuple[str, ...]:
        return tuple(dict.fromkeys(validate_topic(topic) for topic in topics))

    @staticmethod
    def _decode_message(payload: object) -> Message | None:
        """Decode one published payload, logging and skipping a malformed one."""
        try:
            decoded = json.loads(payload) if isinstance(payload, str | bytes) else None
            message = Message.from_dict(decoded) if isinstance(decoded, dict) else None
        except (TypeError, ValueError):
            message = None
        if message is None:
            logger.warning("Skipped a malformed realtime message from Redis.")
        return message


_BROKERS: weakref.WeakSet[RedisBroker] = weakref.WeakSet()


def _forget_async_clients() -> None:
    """Reset per-loop client caches in a forked child."""
    for broker in tuple(_BROKERS):
        broker.forget_clients()


def _without_fork(**_hooks: Callable[[], object]) -> None:
    """Stand in for os.register_at_fork on platforms that cannot fork."""


getattr(os, "register_at_fork", _without_fork)(after_in_child=_forget_async_clients)


__all__ = ["RedisBroker"]
