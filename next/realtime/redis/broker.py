"""Redis broker using atomic sequence assignment and topic publication."""

import asyncio
import importlib
import importlib.util
import json
import logging
import math
import secrets
import threading
import time
import weakref
from collections.abc import (
    AsyncIterator,
    Awaitable,
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
from types import ModuleType, TracebackType
from typing import Any, Final, NamedTuple, Protocol, TypeGuard, cast, override
from weakref import WeakKeyDictionary

from django.conf import settings
from django.core.checks import CheckMessage, Error
from django.core.exceptions import ImproperlyConfigured

from next.conf.checks import errors_for_unknown_keys
from next.partial.envelope import Envelope
from next.realtime.backends import BROKER_OPTIONS, BrokerBackend
from next.realtime.errors import BrokerConnectionError
from next.realtime.lifecycle import apublished, published
from next.realtime.messages import Message, Position, validate_topic, validate_topics
from next.realtime.replay import INCOMPLETE, Replay, replay_after, replay_window
from next.realtime.roster import (
    Member,
    Presence,
    PresenceWindow,
    Scheduler,
    decode_member,
    encode_member,
    presence_event,
    snapshot_message,
)
from next.utils import after_fork, is_int, non_negative_number

from .pubsub import (
    AsyncPubSub,
    PubSub,
    aconfirm,
    amessages,
    confirm,
    decode_message,
    messages,
)
from .scripts import AsyncScripts, ScriptClient, Scripts, SyncScripts


logger = logging.getLogger(__name__)

_DEFAULT_URL = "redis://localhost:6379/0"
_DEFAULT_PREFIX = "next"
_DEFAULT_HEALTH_CHECK_INTERVAL = 10.0
_OPTIONS: Final = frozenset(
    {"URL", "PREFIX", "HEALTH_CHECK_INTERVAL", "CLIENT", "SUBSCRIBE_MAX_CONNECTIONS"}
)
_BRACES = frozenset("{}")
_POOL_EXHAUSTED = (
    "The Redis connection pool is exhausted, raise max_connections in "
    "REALTIME.BROKER.OPTIONS.CLIENT."
)
_SUBSCRIPTIONS_EXHAUSTED = (
    "The Redis subscription pool is exhausted, so a new stream is refused. Raise "
    "REALTIME.BROKER.OPTIONS.SUBSCRIBE_MAX_CONNECTIONS to serve more open streams."
)
_HOLE = "\x00detail\x00"


class _Pipeline(Protocol):
    def hmget(self, name: str, *keys: str) -> object: ...

    def lrange(self, name: str, start: int, end: int) -> object: ...


class _SyncPipeline(_Pipeline, Protocol):
    def execute(self) -> list[Any]: ...


class _AsyncPipeline(_Pipeline, Protocol):
    def execute(self) -> Awaitable[list[Any]]: ...


class _SyncClient(ScriptClient[object], Protocol):
    def publish(self, channel: str, message: str) -> object: ...

    def pubsub(self) -> PubSub: ...

    def pipeline(self, *, transaction: bool) -> _SyncPipeline: ...

    def close(self) -> None: ...


class _AsyncClient(ScriptClient[Awaitable[object]], Protocol):
    def pubsub(self) -> AsyncPubSub: ...

    def pipeline(
        self, *, transaction: bool
    ) -> AbstractAsyncContextManager[_AsyncPipeline]: ...

    def aclose(self) -> Coroutine[Any, Any, None]: ...


class _LoopClient(NamedTuple):
    """The async command client of one event loop, its scripts and its stream client."""

    client: _AsyncClient
    scripts: AsyncScripts
    subscriber: _AsyncClient

    async def aclose(self) -> None:
        """Close the command pool and the subscription pool of the loop."""
        try:
            await self.client.aclose()
        finally:
            await self.subscriber.aclose()


def valid_url(value: object) -> TypeGuard[str]:
    """Whether a `URL` is the non-empty string `Redis.from_url` parses."""
    return isinstance(value, str) and bool(value)


def valid_prefix(value: object) -> TypeGuard[str]:
    """Whether a `PREFIX` has a non-colon character and no brace that moves the tag."""
    return (
        isinstance(value, str) and bool(value.rstrip(":")) and _BRACES.isdisjoint(value)
    )


def valid_interval(value: object) -> TypeGuard[float]:
    """Whether a `HEALTH_CHECK_INTERVAL` is a non-negative finite number of seconds."""
    return non_negative_number(value, math.inf)


def valid_client(value: object) -> TypeGuard[Mapping[str, object]]:
    """Whether `CLIENT` is a mapping of keyword names that keeps decoded replies."""
    return (
        isinstance(value, Mapping)
        and all(isinstance(key, str) for key in value)
        and value.get("decode_responses", True) is True
    )


def valid_connection_limit(value: object) -> TypeGuard[int]:
    """Whether `SUBSCRIBE_MAX_CONNECTIONS` is a positive int that is not a bool."""
    return is_int(value) and value >= 1


def _transport_errors(exceptions: ModuleType) -> tuple[type[Exception], ...]:
    """Return the Redis errors that mean the server cannot serve now, not a bad call.

    A replica, a full memory or a cluster mid-failover replies with one of these.
    """
    return (
        exceptions.ConnectionError,
        exceptions.TimeoutError,
        exceptions.ReadOnlyError,
        exceptions.OutOfMemoryError,
        exceptions.ClusterDownError,
        exceptions.TryAgainError,
    )


class _TransportFailures:
    """Re-raise a Redis transport or server availability error as a broker error."""

    def __init__(
        self,
        errors: tuple[type[Exception], ...],
        exhausted: type[Exception],
        warning: str,
    ) -> None:
        self.errors = errors
        self.exhausted = exhausted
        self.warning = warning

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
            if isinstance(error, self.exhausted):
                logger.warning(self.warning)
                raise BrokerConnectionError(self.warning) from error
            msg = f"The Redis broker connection failed: {error}"
            raise BrokerConnectionError(msg) from error


class RedisBroker(BrokerBackend, PresenceWindow):
    """Deliver messages across processes through Redis Pub/Sub.

    Streams hold connections of their own pool, so open streams never starve publish.
    """

    def __init__(
        self,
        config: Mapping[str, object] | None = None,
        *,
        replay: Mapping[str, object] | None = None,
        presence: Mapping[str, object] | None = None,
        clock: Callable[[], float] = time.monotonic,
        schedule: Scheduler | None = None,
    ) -> None:
        """Create the sync Redis clients and prepare per-loop async clients.

        `clock` and `schedule` time and build the presence schedule, not the deadlines.
        """
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
        if not valid_url(url):
            msg = "REALTIME.BROKER.OPTIONS.URL must be a non-empty Redis URL."
            raise ImproperlyConfigured(msg)
        if not valid_prefix(prefix):
            msg = (
                "REALTIME.BROKER.OPTIONS.PREFIX must be a string with a non-colon "
                "character and no { or }."
            )
            raise ImproperlyConfigured(msg)
        if not valid_interval(interval):
            msg = (
                "REALTIME.BROKER.OPTIONS.HEALTH_CHECK_INTERVAL must be a "
                "non-negative number of seconds."
            )
            raise ImproperlyConfigured(msg)
        self._prefix = prefix.rstrip(":")
        self._url = url
        self.replay_size, self.replay_ttl = replay_window(replay)
        self._window_args = (
            str(self.replay_size),
            str(math.ceil(self.replay_ttl * 1000)),
        )
        self._client_options = self._merged_client_options(interval)
        self._subscriber_options = self._merged_subscriber_options()
        self._modules: tuple[ModuleType, ModuleType] = (redis, redis_asyncio)
        errors = _transport_errors(redis.exceptions)
        exhausted = redis.exceptions.MaxConnectionsError
        self._failures = _TransportFailures(errors, exhausted, _POOL_EXHAUSTED)
        self._subscribe_failures = _TransportFailures(
            errors, exhausted, _SUBSCRIPTIONS_EXHAUSTED
        )
        client = cast(
            "_SyncClient",
            self.create_client(url, dict(self._client_options), asynchronous=False),
        )
        self._redis = client
        self._subscriber = cast(
            "_SyncClient",
            self.create_client(url, dict(self._subscriber_options), asynchronous=False),
        )
        self._scripts: SyncScripts = Scripts.bind(client)
        self.keep_presence(presence, schedule, clock)
        self._window_ms = _milliseconds(self.presence_limits.window)
        self._async_clients: WeakKeyDictionary[
            asyncio.AbstractEventLoop, _LoopClient
        ] = WeakKeyDictionary()
        self._async_lock = threading.Lock()
        _BROKERS.add(self)

    @classmethod
    @override
    def check_options(cls, options: Mapping[str, object]) -> list[CheckMessage]:
        """Check the dependency, URL, prefix, interval, client and stream pool limit.

        A subclass may read keys of its own, so only this class reports unknown keys.
        """
        errors = (
            errors_for_unknown_keys(
                dict(options), allowed=_OPTIONS, prefix=BROKER_OPTIONS
            )
            if cls is RedisBroker
            else []
        )
        if importlib.util.find_spec("redis") is None:
            errors.append(
                Error(
                    "NEXT_FRAMEWORK['REALTIME'] selects RedisBroker but the redis "
                    "package is not installed.",
                    hint="Install next.dj[broker].",
                    obj=settings,
                    id="next.E179",
                )
            )
        if not valid_url(options.get("URL", _DEFAULT_URL)):
            errors.append(
                Error(
                    f"{BROKER_OPTIONS}['URL'] must be a non-empty Redis URL.",
                    obj=settings,
                    id="next.E213",
                )
            )
        if "PREFIX" in options and not valid_prefix(options["PREFIX"]):
            errors.append(
                Error(
                    f"{BROKER_OPTIONS}['PREFIX'] must be a string with a non-colon "
                    "character and no { or }.",
                    hint="A brace moves the Redis Cluster hash tag off the topic name.",
                    obj=settings,
                    id="next.E192",
                )
            )
        if not valid_interval(
            options.get("HEALTH_CHECK_INTERVAL", _DEFAULT_HEALTH_CHECK_INTERVAL)
        ):
            errors.append(
                Error(
                    f"{BROKER_OPTIONS}['HEALTH_CHECK_INTERVAL'] must be a non-negative "
                    "number of seconds.",
                    obj=settings,
                    id="next.E186",
                )
            )
        if not valid_client(options.get("CLIENT", {})):
            errors.append(
                Error(
                    f"{BROKER_OPTIONS}['CLIENT'] must be a mapping of keyword names "
                    "that keeps decode_responses on.",
                    obj=settings,
                    id="next.E187",
                )
            )
        if "SUBSCRIBE_MAX_CONNECTIONS" in options and not valid_connection_limit(
            options["SUBSCRIBE_MAX_CONNECTIONS"]
        ):
            errors.append(
                Error(
                    f"{BROKER_OPTIONS}['SUBSCRIBE_MAX_CONNECTIONS'] must be a positive "
                    "integer.",
                    hint="It caps the Redis connections open streams hold at once.",
                    obj=settings,
                    id="next.E216",
                )
            )
        return errors

    def create_client(
        self, url: str, options: Mapping[str, object], *, asynchronous: bool
    ) -> object:
        """Build a sync or a per-loop async client from the URL and client options.

        A subclass overrides it to reach Redis through Sentinel or Cluster instead.
        """
        module = self._modules[1] if asynchronous else self._modules[0]
        return module.Redis.from_url(url, **options)

    def _merged_client_options(self, interval: float) -> dict[str, object]:
        """Lay the CLIENT option over the defaults, keeping decoded replies."""
        client = self.options.get("CLIENT", {})
        if not valid_client(client):
            msg = (
                "REALTIME.BROKER.OPTIONS.CLIENT must be a mapping of keyword names "
                "that keeps decode_responses on."
            )
            raise ImproperlyConfigured(msg)
        return {
            "health_check_interval": interval,
            "socket_keepalive": True,
            **client,
            "decode_responses": True,
        }

    def _merged_subscriber_options(self) -> dict[str, object]:
        """Return the client options capped by `SUBSCRIBE_MAX_CONNECTIONS` when set."""
        if "SUBSCRIBE_MAX_CONNECTIONS" not in self.options:
            return dict(self._client_options)
        limit = self.options["SUBSCRIBE_MAX_CONNECTIONS"]
        if not valid_connection_limit(limit):
            msg = (
                "REALTIME.BROKER.OPTIONS.SUBSCRIBE_MAX_CONNECTIONS must be a positive "
                "integer."
            )
            raise ImproperlyConfigured(msg)
        return {**self._client_options, "max_connections": limit}

    @override
    def publish(self, topic: str, envelope: Envelope) -> Message:
        """Atomically number and publish one envelope on a topic."""
        envelope = self.validate_publication(topic, envelope)
        published_at = time.time()
        with self._failures:
            row = self._scripts.publish(
                keys=[self._sequence_key(topic), self._log_key(topic)],
                args=self._script_args(topic, envelope, published_at),
            )
        seq, epoch = cast("list[str]", row)
        return Message(topic, int(seq), envelope, published_at, epoch)

    @override
    async def apublish(self, topic: str, envelope: Envelope) -> Message:
        """Atomically number and publish one envelope through this loop's client."""
        envelope = self.validate_publication(topic, envelope)
        published_at = time.time()
        scripts = self._loop_client().scripts
        with self._failures:
            row = await scripts.publish(
                keys=[self._sequence_key(topic), self._log_key(topic)],
                args=self._script_args(topic, envelope, published_at),
            )
        seq, epoch = cast("list[str]", row)
        return Message(topic, int(seq), envelope, published_at, epoch)

    @override
    def broadcast(self, topic: str, envelope: Envelope) -> Message:
        """Publish an envelope on the topic channel alone, unnumbered and unlogged."""
        envelope = self.validate_publication(topic, envelope)
        message = Message(topic, 0, envelope, time.time())
        payload = json.dumps(message.as_dict(), separators=(",", ":"))
        with self._failures:
            self._redis.publish(self._channel(topic), payload)
        return message

    @override
    def subscribe(
        self, topics: Sequence[str], *, timeout: float
    ) -> AbstractContextManager[Iterator[Message | None]]:
        """Subscribe to Redis topic channels once Redis confirms every channel."""
        channels = self._channels(topics)

        @contextmanager
        def subscription() -> Iterator[Iterator[Message | None]]:
            pubsub = self._subscriber.pubsub()
            try:
                with self._subscribe_failures:
                    pubsub.subscribe(*channels)
                    buffered = confirm(pubsub, channels)
                yield messages(pubsub, buffered, timeout, self._subscribe_failures)
            finally:
                pubsub.close()

        return subscription()

    @override
    def asubscribe(
        self, topics: Sequence[str], *, timeout: float
    ) -> AbstractAsyncContextManager[AsyncIterator[Message | None]]:
        """Subscribe asynchronously once Redis confirms every channel."""
        channels = self._channels(topics)

        @asynccontextmanager
        async def subscription() -> AsyncIterator[AsyncIterator[Message | None]]:
            pubsub = self._loop_client().subscriber.pubsub()
            try:
                with self._subscribe_failures:
                    await pubsub.subscribe(*channels)
                    buffered = await aconfirm(pubsub, channels)
                yield amessages(pubsub, buffered, timeout, self._subscribe_failures)
            finally:
                await pubsub.aclose()

        return subscription()

    @override
    def positions(self, topics: Sequence[str]) -> dict[str, Position]:
        """Read every topic position in one pipelined round trip."""
        selected = validate_topics(topics)
        pipe = self._redis.pipeline(transaction=False)
        for topic in selected:
            pipe.hmget(self._sequence_key(topic), "seq", "epoch")
        with self._failures:
            rows = pipe.execute()
        return self._positions(selected, rows)

    @override
    async def apositions(self, topics: Sequence[str]) -> dict[str, Position]:
        """Read every topic position in one pipelined round trip asynchronously."""
        selected = validate_topics(topics)
        client = self._loop_client().client
        with self._failures:
            async with client.pipeline(transaction=False) as pipe:
                for topic in selected:
                    pipe.hmget(self._sequence_key(topic), "seq", "epoch")
                rows = await pipe.execute()
        return self._positions(selected, rows)

    def replay(self, topic: str, after: Position) -> Replay:
        """Read the topic position and its log in one transaction, then cut the gap."""
        validate_topic(topic)
        if not self.replay_size:
            return INCOMPLETE
        pipe = self._redis.pipeline(transaction=True)
        pipe.hmget(self._sequence_key(topic), "seq", "epoch")
        pipe.lrange(self._log_key(topic), 0, -1)
        with self._failures:
            row, logged = pipe.execute()
        return self._replay(topic, after, row, logged)

    async def areplay(self, topic: str, after: Position) -> Replay:
        """Read the topic position and its log in one asynchronous transaction."""
        validate_topic(topic)
        if not self.replay_size:
            return INCOMPLETE
        client = self._loop_client().client
        with self._failures:
            async with client.pipeline(transaction=True) as pipe:
                pipe.hmget(self._sequence_key(topic), "seq", "epoch")
                pipe.lrange(self._log_key(topic), 0, -1)
                row, logged = await pipe.execute()
        return self._replay(topic, after, row, logged)

    def _replay(
        self,
        topic: str,
        after: Position,
        row: Sequence[str | None],
        logged: Sequence[str],
    ) -> Replay:
        """Decode the newest-first log down to the cursor and compare the two."""
        current = self._positions((topic,), (row,))[topic]
        if after.epoch != current.epoch:
            return INCOMPLETE
        found: list[Message] = []
        for payload in logged:
            message = decode_message(payload)
            if message is None:
                continue
            if message.seq <= after.seq:
                break
            found.append(message)
        found.reverse()
        return replay_after(after, current, found)

    def join(
        self, topic: str, connection: str, member: Member | None, ttl: float
    ) -> None:
        """Record a connection in one script that also prunes and claims the window."""
        keys, args = self._join_call(topic, connection, member, ttl)
        with self._failures:
            claimed = self._scripts.join(keys=keys, args=args)
        if claimed:
            self.schedule_presence(topic)

    async def ajoin(
        self, topic: str, connection: str, member: Member | None, ttl: float
    ) -> None:
        """Record a connection through this loop's client."""
        keys, args = self._join_call(topic, connection, member, ttl)
        scripts = self._loop_client().scripts
        with self._failures:
            claimed = await scripts.join(keys=keys, args=args)
        if claimed:
            self.schedule_presence(topic)

    def touch(self, topic: str, connection: str, ttl: float) -> bool:
        """Push a deadline out in one script, False once the record was pruned."""
        keys, args = self._touch_call(topic, connection, ttl)
        with self._failures:
            row = self._scripts.touch(keys=keys, args=args)
        present, claimed = cast("list[int]", row)
        if claimed:
            self.schedule_presence(topic)
        return bool(present)

    async def atouch(self, topic: str, connection: str, ttl: float) -> bool:
        """Push a deadline out through this loop's client."""
        keys, args = self._touch_call(topic, connection, ttl)
        scripts = self._loop_client().scripts
        with self._failures:
            row = await scripts.touch(keys=keys, args=args)
        present, claimed = cast("list[int]", row)
        if claimed:
            self.schedule_presence(topic)
        return bool(present)

    def leave(self, topic: str, connection: str) -> None:
        """Forget a connection in one script that also prunes and claims the window."""
        keys, args = self._leave_call(topic, connection)
        with self._failures:
            row = self._scripts.leave(keys=keys, args=args)
        self._after_leave(topic, *cast("list[int]", row))

    async def aleave(self, topic: str, connection: str) -> None:
        """Forget a connection through this loop's client."""
        keys, args = self._leave_call(topic, connection)
        scripts = self._loop_client().scripts
        with self._failures:
            row = await scripts.leave(keys=keys, args=args)
        self._after_leave(topic, *cast("list[int]", row))

    def _after_leave(self, topic: str, claimed: int, wait: int) -> None:
        """Schedule the claimed window, or a snapshot once another process's closes.

        A topic left empty gets no touch, so it cannot wait for one to heal its count.
        """
        if claimed:
            self.schedule_presence(topic)
        elif wait > 0:
            self.presence_schedule.schedule(topic, wait / 1000)

    def presence(self, topic: str) -> Presence:
        """Count the unexpired attendees and read the first identified members."""
        keys = self._snapshot_keys(topic)
        with self._failures:
            row = self._scripts.snapshot(
                keys=keys, args=[str(self.presence_limits.size)]
            )
        return _snapshot(cast("list[Any]", row))

    async def apresence(self, topic: str) -> Presence:
        """Read the presence of a topic through this loop's client."""
        keys = self._snapshot_keys(topic)
        scripts = self._loop_client().scripts
        with self._failures:
            row = await scripts.snapshot(
                keys=keys, args=[str(self.presence_limits.size)]
            )
        return _snapshot(cast("list[Any]", row))

    @override
    def announce_presence(self, topic: str) -> None:
        """Read the snapshot and publish it in one script, so windows keep change order.

        The script skips a topic whose last change some process already announced.
        """
        published_at = time.time()
        keys, args = self._announce_call(topic, published_at)
        with self._failures:
            row = self._scripts.announce(keys=keys, args=args)
        if row is not None:
            snapshot = _snapshot(cast("list[Any]", row))
            published(self, snapshot_message(topic, snapshot, published_at))

    @override
    async def aannounce_presence(self, topic: str) -> None:
        """Announce through this loop's client, the sync receivers run off the loop."""
        published_at = time.time()
        keys, args = self._announce_call(topic, published_at)
        scripts = self._loop_client().scripts
        with self._failures:
            row = await scripts.announce(keys=keys, args=args)
        if row is not None:
            snapshot = _snapshot(cast("list[Any]", row))
            await apublished(self, snapshot_message(topic, snapshot, published_at))

    def _announce_call(
        self, topic: str, published_at: float
    ) -> tuple[list[str], list[str]]:
        """Split the snapshot frame around the detail the script fills in."""
        frame = snapshot_message(topic, Presence(0), published_at).as_dict()
        frame["envelope"] = presence_event(_HOLE).as_dict()
        head, _, tail = json.dumps(frame, separators=(",", ":")).partition(
            json.dumps(_HOLE)
        )
        args = [str(self.presence_limits.size), self._channel(topic), head, tail]
        return [*self._snapshot_keys(topic), self.topic_key("state", topic)], args

    def _join_call(
        self, topic: str, connection: str, member: Member | None, ttl: float
    ) -> tuple[list[str], list[str]]:
        encoded = encode_member(member, self.presence_limits.member_bytes)
        args = [connection, _milliseconds(ttl), encoded or "", self._window_ms]
        return self._presence_keys(topic), args

    def _touch_call(
        self, topic: str, connection: str, ttl: float
    ) -> tuple[list[str], list[str]]:
        args = [connection, _milliseconds(ttl), self._window_ms]
        return self._presence_keys(topic), args

    def _leave_call(self, topic: str, connection: str) -> tuple[list[str], list[str]]:
        return self._presence_keys(topic), [connection, self._window_ms]

    def _presence_keys(self, topic: str) -> list[str]:
        """Return the presence keys of a topic, all under the topic's hash tag.

        The state hash counts the changes and the order set ranks attendees by arrival.
        """
        validate_topic(topic)
        return [
            self.topic_key(kind, topic)
            for kind in (
                "presence",
                "members",
                "window",
                "identified",
                "attendees",
                "holders",
                "state",
                "order",
            )
        ]

    def _snapshot_keys(self, topic: str) -> list[str]:
        """Return the unnamed connections, the attendees and their arrival order."""
        validate_topic(topic)
        return [
            self.topic_key("presence", topic),
            self.topic_key("attendees", topic),
            self.topic_key("order", topic),
        ]

    @override
    def close(self) -> None:
        """Close the sync pools and schedule the close of clients on live loops."""
        self.presence_schedule.close()
        self._release()

    @override
    async def aclose(self) -> None:
        """Announce the pending snapshots and close this loop's clients on the loop.

        The clients of other loops close on theirs, the sync pools in a worker thread.
        """
        await self.presence_schedule.aclose(self.aannounce_presence)
        with self._async_lock:
            entry = self._async_clients.pop(asyncio.get_running_loop(), None)
        if entry is not None:
            await entry.aclose()
        self._release_loops()
        await asyncio.to_thread(self._close_sync)

    def _release(self) -> None:
        """Schedule the close of the clients on live loops and close the sync pools."""
        self._release_loops()
        self._close_sync()

    def _close_sync(self) -> None:
        """Close the sync command pool and the sync subscription pool."""
        try:
            self._redis.close()
        finally:
            self._subscriber.close()

    def _release_loops(self) -> None:
        """Schedule the close of every async client on its loop, if it still runs."""
        with self._async_lock:
            clients = list(self._async_clients.items())
            self._async_clients.clear()
        for loop, entry in clients:
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(entry.aclose(), loop)

    def forget_clients(self) -> None:
        """Drop the async clients a forked child inherits from its parent's loops."""
        self._async_lock = threading.Lock()
        self._async_clients = WeakKeyDictionary()

    def _loop_client(self) -> _LoopClient:
        """Return the async clients and the scripts bound to the running event loop."""
        loop = asyncio.get_running_loop()
        with self._async_lock:
            for stale in [known for known in self._async_clients if known.is_closed()]:
                del self._async_clients[stale]
            entry = self._async_clients.get(loop)
            if entry is None:
                client = cast(
                    "_AsyncClient",
                    self.create_client(
                        self._url, dict(self._client_options), asynchronous=True
                    ),
                )
                subscriber = cast(
                    "_AsyncClient",
                    self.create_client(
                        self._url, dict(self._subscriber_options), asynchronous=True
                    ),
                )
                entry = _LoopClient(client, Scripts.bind(client), subscriber)
                self._async_clients[loop] = entry
        return entry

    def topic_key(self, kind: str, topic: str) -> str:
        """Return a key of a topic, hash-tagged into one Cluster slot.

        Every script on a topic touches several of its keys, so they must share it.
        """
        return f"{self._prefix}:{kind}:{{{topic}}}"

    def _sequence_key(self, topic: str) -> str:
        return self.topic_key("seq", topic)

    def _log_key(self, topic: str) -> str:
        return self.topic_key("log", topic)

    def _channel(self, topic: str) -> str:
        return f"{self._prefix}:topic:{topic}"

    def _channels(self, topics: Sequence[str]) -> tuple[str, ...]:
        selected = validate_topics(topics, at_least_one=True)
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
            *self._window_args,
        ]

    @staticmethod
    def _positions(
        topics: tuple[str, ...], rows: Sequence[Sequence[str | None]]
    ) -> dict[str, Position]:
        positions: dict[str, Position] = {}
        for topic, (seq, epoch) in zip(topics, rows, strict=True):
            positions[topic] = Position(epoch or "", 0 if seq is None else int(seq))
        return positions


def _milliseconds(seconds: float) -> str:
    """Return a TTL as the whole milliseconds a script adds to the Redis clock."""
    return str(max(1, math.ceil(seconds * 1000)))


def _snapshot(row: Sequence[Any]) -> Presence:
    """Decode the count and the stored members a snapshot script returned."""
    return Presence(int(row[0]), _members(row[1:]))


def _members(payloads: Sequence[Any]) -> tuple[Member, ...]:
    """Decode the stored members in one call, one by one when any row is malformed."""
    try:
        decoded = json.loads(f"[{','.join(payloads)}]")
    except (TypeError, ValueError):
        decoded = None
    if (
        isinstance(decoded, list)
        and len(decoded) == len(payloads)
        and all(isinstance(member, dict) for member in decoded)
    ):
        return tuple(decoded)
    return tuple(
        member
        for member in (decode_member(payload) for payload in payloads)
        if member is not None
    )


_BROKERS: weakref.WeakSet[RedisBroker] = weakref.WeakSet()


def _forget_async_clients() -> None:
    """Reset per-loop client caches in a forked child."""
    for broker in tuple(_BROKERS):
        broker.forget_clients()


after_fork(_forget_async_clients)


__all__ = [
    "RedisBroker",
    "valid_client",
    "valid_connection_limit",
    "valid_interval",
    "valid_prefix",
    "valid_url",
]
