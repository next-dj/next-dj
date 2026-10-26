"""Redis broker using atomic sequence assignment and topic publication."""

import asyncio
import importlib
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
from typing import Any, Protocol, TypeGuard, override
from weakref import WeakKeyDictionary

from django.core.exceptions import ImproperlyConfigured

from next.partial.envelope import Envelope
from next.utils import after_fork

from .backends import BrokerBackend
from .errors import BrokerConnectionError
from .messages import Message, Position, validate_topic
from .replay import INCOMPLETE, Replay, replay_after, replay_window
from .roster import (
    Member,
    Presence,
    PresenceWindow,
    Scheduler,
    decode_member,
    encode_member,
    presence_event,
    snapshot_message,
)
from .signals import published


logger = logging.getLogger(__name__)

_DEFAULT_URL = "redis://localhost:6379/0"
_DEFAULT_PREFIX = "next"
_DEFAULT_HEALTH_CHECK_INTERVAL = 10.0
_SUBSCRIBE_TIMEOUT = 5.0
_LIVENESS = "next-realtime-liveness"
_BRACES = frozenset("{}")
_UNCONFIRMED = "Redis did not confirm the subscription in time."
_POOL_EXHAUSTED = (
    "The Redis connection pool is exhausted, raise max_connections in "
    "REALTIME.BROKER.OPTIONS.CLIENT."
)
_PUBLISH_SCRIPT = """
redis.call('HINCRBY', KEYS[1], 'seq', 1)
local state = redis.call('HMGET', KEYS[1], 'seq', 'epoch')
local epoch = state[2]
local size = tonumber(ARGV[4])
if state[1] == '1' or not epoch then
  epoch = ARGV[3]
  redis.call('HSET', KEYS[1], 'epoch', epoch)
  if size > 0 then
    redis.call('DEL', KEYS[2])
  end
end
local payload = '{"seq":' .. state[1] .. ',"epoch":"' .. epoch .. '",'
  .. string.sub(ARGV[2], 2)
if size > 0 then
  redis.call('LPUSH', KEYS[2], payload)
  redis.call('LTRIM', KEYS[2], 0, size - 1)
  redis.call('PEXPIRE', KEYS[2], ARGV[5])
end
redis.call('PEXPIRE', KEYS[1], ARGV[5])
redis.call('PUBLISH', ARGV[1], payload)
return {state[1], epoch}
"""

_CLOCK = """
local clock = redis.call('TIME')
local now = tonumber(clock[1]) * 1000 + math.floor(tonumber(clock[2]) / 1000)
"""
_PRUNE = (
    _CLOCK
    + """
local SEP = string.char(0)
local BATCH = 1000
local changed = redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now) > 0
local lapsed = redis.call('ZRANGEBYSCORE', KEYS[4], '-inf', now)
if #lapsed > 0 then
  redis.call('ZREMRANGEBYSCORE', KEYS[4], '-inf', now)
  for first = 1, #lapsed, BATCH do
    local last = math.min(first + BATCH - 1, #lapsed)
    local payloads = redis.call('HMGET', KEYS[2], unpack(lapsed, first, last))
    local held = {}
    for index = 1, #payloads do
      if payloads[index] then
        held[#held + 1] = payloads[index] .. SEP .. lapsed[first + index - 1]
      end
    end
    redis.call('HDEL', KEYS[2], unpack(lapsed, first, last))
    if #held > 0 then
      redis.call('ZREM', KEYS[6], unpack(held))
    end
  end
  local gone = redis.call('ZRANGEBYSCORE', KEYS[5], '-inf', now)
  if #gone > 0 then
    redis.call('ZREMRANGEBYSCORE', KEYS[5], '-inf', now)
    for first = 1, #gone, BATCH do
      local last = math.min(first + BATCH - 1, #gone)
      redis.call('ZREM', KEYS[8], unpack(gone, first, last))
    end
    changed = true
  end
end
"""
)
_SETTLE = """
local function settle(window)
  if changed then
    redis.call('HINCRBY', KEYS[7], 'version', 1)
  end
  local state = redis.call('HMGET', KEYS[7], 'version', 'published')
  if state[1] and state[1] ~= state[2]
      and redis.call('SET', KEYS[3], '1', 'NX', 'PX', window) then
    return 1
  end
  return 0
end
"""
_KEEP = """
local function keep(ttl)
  for _, group in ipairs({{1}, {4, 2, 5, 6, 8}, {7}}) do
    local left = redis.call('PTTL', KEYS[group[1]])
    if left ~= -2 and left < ttl then
      for _, index in ipairs(group) do
        redis.call('PEXPIRE', KEYS[index], ttl * 2)
      end
    end
  end
end
"""
_LATEST = """
local function latest(payload)
  local holders = redis.call(
    'ZRANGEBYLEX', KEYS[6], '[' .. payload .. SEP, '(' .. payload .. string.char(1))
  local count, best = 0, 0
  for _, holder in ipairs(holders) do
    local connection = string.sub(holder, #payload + 2)
    local deadline = redis.call('ZSCORE', KEYS[4], connection)
    if deadline then
      count = count + 1
      best = math.max(best, tonumber(deadline))
    else
      redis.call('ZREM', KEYS[6], holder)
      redis.call('HDEL', KEYS[2], connection)
    end
  end
  return count, best
end
"""
_RESCORE = """
local function rescore(connection, payload, deadline)
  local held = redis.call('ZSCORE', KEYS[5], payload)
  if not held or tonumber(held) <= deadline then
    redis.call('ZADD', KEYS[4], deadline, connection)
    redis.call('ZADD', KEYS[5], deadline, payload)
    return
  end
  local before = redis.call('ZSCORE', KEYS[4], connection)
  redis.call('ZADD', KEYS[4], deadline, connection)
  if before and tonumber(before) >= tonumber(held) then
    local _, best = latest(payload)
    redis.call('ZADD', KEYS[5], best, payload)
  end
end
"""
_DETACH = """
local function detach(connection, payload)
  redis.call('ZREM', KEYS[4], connection)
  redis.call('HDEL', KEYS[2], connection)
  redis.call('ZREM', KEYS[6], payload .. SEP .. connection)
  local count, best = latest(payload)
  if count == 0 then
    redis.call('ZREM', KEYS[5], payload)
    redis.call('ZREM', KEYS[8], payload)
    return true
  end
  redis.call('ZADD', KEYS[5], best, payload)
  return false
end
"""
_JOIN_SCRIPT = (
    _PRUNE
    + _SETTLE
    + _KEEP
    + _LATEST
    + _RESCORE
    + _DETACH
    + """
local ttl = tonumber(ARGV[2])
local deadline = now + ttl
local previous = redis.call('HGET', KEYS[2], ARGV[1])
if ARGV[3] == '' then
  if previous then
    detach(ARGV[1], previous)
  end
  if redis.call('ZADD', KEYS[1], deadline, ARGV[1]) == 1 then
    changed = true
  end
else
  redis.call('ZREM', KEYS[1], ARGV[1])
  if previous ~= ARGV[3] then
    if previous then
      detach(ARGV[1], previous)
    end
    redis.call('HSET', KEYS[2], ARGV[1], ARGV[3])
    redis.call('ZADD', KEYS[6], 0, ARGV[3] .. SEP .. ARGV[1])
    redis.call('ZADD', KEYS[8], 'NX', now, ARGV[3])
    changed = true
  end
  rescore(ARGV[1], ARGV[3], deadline)
end
local claimed = settle(ARGV[4])
keep(ttl)
return claimed
"""
)
_TOUCH_SCRIPT = (
    _PRUNE
    + _SETTLE
    + _KEEP
    + _LATEST
    + _RESCORE
    + """
local ttl = tonumber(ARGV[2])
local deadline = now + ttl
local payload = redis.call('HGET', KEYS[2], ARGV[1])
local present = 1
if payload then
  rescore(ARGV[1], payload, deadline)
elseif redis.call('ZSCORE', KEYS[1], ARGV[1]) then
  redis.call('ZADD', KEYS[1], 'XX', deadline, ARGV[1])
else
  present = 0
end
local claimed = settle(ARGV[3])
keep(ttl)
return {present, claimed}
"""
)
_LEAVE_SCRIPT = (
    _PRUNE
    + _SETTLE
    + _LATEST
    + _DETACH
    + """
local payload = redis.call('HGET', KEYS[2], ARGV[1])
if payload then
  if detach(ARGV[1], payload) then
    changed = true
  end
elseif redis.call('ZREM', KEYS[1], ARGV[1]) == 1 then
  changed = true
end
local claimed = settle(ARGV[2])
if changed and redis.call('PTTL', KEYS[7]) == -1 then
  redis.call('PEXPIRE', KEYS[7], ARGV[2] * 2)
end
if claimed == 0 and redis.call('EXISTS', KEYS[1], KEYS[5]) == 0 then
  local state = redis.call('HMGET', KEYS[7], 'version', 'published')
  if state[1] and state[1] ~= state[2] then
    return {0, redis.call('PTTL', KEYS[3])}
  end
end
return {claimed, 0}
"""
)
_LIST = (
    _CLOCK
    + """
local live = '(' .. now
local attendees = redis.call('ZCOUNT', KEYS[2], live, '+inf')
local result = {redis.call('ZCOUNT', KEYS[1], live, '+inf') + attendees}
local size = tonumber(ARGV[1])
local lapsed = redis.call('ZCARD', KEYS[2]) > attendees
if size > 0 and not lapsed then
  for _, payload in ipairs(redis.call('ZRANGE', KEYS[3], 0, size - 1)) do
    result[#result + 1] = payload
  end
end
local start = 0
while lapsed and #result <= size do
  local chunk = redis.call('ZRANGE', KEYS[3], start, start + 255)
  if #chunk == 0 then
    break
  end
  for _, payload in ipairs(chunk) do
    local deadline = redis.call('ZSCORE', KEYS[2], payload)
    if deadline and tonumber(deadline) > now then
      result[#result + 1] = payload
      if #result > size then
        break
      end
    end
  end
  start = start + 256
end
"""
)
_SNAPSHOT_SCRIPT = _LIST + "return result\n"
_ANNOUNCE_SCRIPT = (
    """
local state = redis.call('HMGET', KEYS[4], 'version', 'published')
if state[1] and state[1] == state[2] then
  return false
end
"""
    + _LIST
    + """
if state[1] then
  redis.call('HSET', KEYS[4], 'published', state[1])
end
local detail = '{"count":' .. result[1]
if #result > 1 then
  detail = detail .. ',"members":[' .. table.concat(result, ',', 2) .. ']'
end
redis.call('PUBLISH', ARGV[2], ARGV[3] .. detail .. '}' .. ARGV[4])
return result
"""
)
_SCRIPTS = (
    _PUBLISH_SCRIPT,
    _JOIN_SCRIPT,
    _TOUCH_SCRIPT,
    _LEAVE_SCRIPT,
    _SNAPSHOT_SCRIPT,
)
_HOLE = "\x00detail\x00"


def valid_prefix(value: object) -> TypeGuard[str]:
    """Whether a `PREFIX` has a non-colon character and no brace that moves the tag."""
    return (
        isinstance(value, str) and bool(value.rstrip(":")) and _BRACES.isdisjoint(value)
    )


class _PubSub(Protocol):
    def get_message(self, *, timeout: float) -> Mapping[str, Any] | None: ...

    def ping(self, message: str) -> object: ...


class _AsyncPubSub(Protocol):
    def get_message(self, *, timeout: float) -> Awaitable[Mapping[str, Any] | None]: ...

    def ping(self, message: str) -> Awaitable[object]: ...


class _AsyncScript(Protocol):
    def __call__(self, *, keys: list[str], args: list[str]) -> Awaitable[Any]: ...


class _TransportFailures:
    """Re-raise a Redis connection or timeout error as a broker connection error."""

    def __init__(
        self, errors: tuple[type[Exception], ...], exhausted: type[Exception]
    ) -> None:
        self.errors = errors
        self.exhausted = exhausted

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
                logger.warning(_POOL_EXHAUSTED)
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


class RedisBroker(BrokerBackend, PresenceWindow):
    """Deliver messages across processes through Redis Pub/Sub.

    A topic's sequence and log expire `REPLAY['TTL']` seconds after its last publish.
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
        """Create the sync Redis client and prepare per-loop async clients.

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
        if not isinstance(url, str) or not url:
            msg = "REALTIME.BROKER.OPTIONS.URL must be a non-empty Redis URL."
            raise ImproperlyConfigured(msg)
        if not valid_prefix(prefix):
            msg = (
                "REALTIME.BROKER.OPTIONS.PREFIX must be a string with a non-colon "
                "character and no { or }."
            )
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
        self._url = url
        self.replay_size, self.replay_ttl = replay_window(replay)
        self._window_args = (
            str(self.replay_size),
            str(math.ceil(self.replay_ttl * 1000)),
        )
        self._client_options = self._merged_client_options(interval)
        self._modules: tuple[ModuleType, ModuleType] = (redis, redis_asyncio)
        transport = (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError)
        exhausted = redis.exceptions.MaxConnectionsError
        self._failures = _TransportFailures(transport, exhausted)
        self._leave_failures = _TransportFailures(
            (*transport, redis.exceptions.ResponseError), exhausted
        )
        client: Any = self.create_client(
            url, dict(self._client_options), asynchronous=False
        )
        self._redis: Any = client
        self._publish_script: Any = client.register_script(_PUBLISH_SCRIPT)
        self.keep_presence(presence, schedule, clock)
        self._window_ms = _milliseconds(self.presence_limits.window)
        self._join_script: Any = client.register_script(_JOIN_SCRIPT)
        self._touch_script: Any = client.register_script(_TOUCH_SCRIPT)
        self._leave_script: Any = client.register_script(_LEAVE_SCRIPT)
        self._snapshot_script: Any = client.register_script(_SNAPSHOT_SCRIPT)
        self._announce_script: Any = client.register_script(_ANNOUNCE_SCRIPT)
        self._async_clients: WeakKeyDictionary[
            asyncio.AbstractEventLoop, tuple[Any, Mapping[str, Any]]
        ] = WeakKeyDictionary()
        self._async_lock = threading.Lock()
        _BROKERS.add(self)

    def create_client(
        self, url: str, options: Mapping[str, object], *, asynchronous: bool
    ) -> object:
        """Build the sync or the per-loop async client from the URL and client options.

        A subclass overrides it to reach Redis through Sentinel or Cluster instead.
        """
        module = self._modules[1] if asynchronous else self._modules[0]
        return module.Redis.from_url(url, **options)

    def _merged_client_options(self, interval: float) -> dict[str, object]:
        """Lay the CLIENT option over the defaults, keeping decoded replies."""
        client = self.options.get("CLIENT", {})
        if not isinstance(client, Mapping) or not all(
            isinstance(key, str) for key in client
        ):
            msg = "REALTIME.BROKER.OPTIONS.CLIENT must be a mapping of keyword names."
            raise ImproperlyConfigured(msg)
        if client.get("decode_responses", True) is not True:
            msg = "REALTIME.BROKER.OPTIONS.CLIENT cannot turn off decode_responses."
            raise ImproperlyConfigured(msg)
        return {
            "health_check_interval": interval,
            "socket_keepalive": True,
            **client,
            "decode_responses": True,
        }

    @override
    def publish(self, topic: str, envelope: Envelope) -> Message:
        """Atomically number and publish one envelope on a topic."""
        envelope = self.validate_publication(topic, envelope)
        published_at = time.time()
        with self._failures:
            seq, epoch = self._publish_script(
                keys=[self._sequence_key(topic), self._log_key(topic)],
                args=self._script_args(topic, envelope, published_at),
            )
        return Message(topic, int(seq), envelope, published_at, epoch)

    @override
    async def apublish(self, topic: str, envelope: Envelope) -> Message:
        """Atomically number and publish one envelope through this loop's client."""
        envelope = self.validate_publication(topic, envelope)
        published_at = time.time()
        script = self._async_script(_PUBLISH_SCRIPT)
        with self._failures:
            seq, epoch = await script(
                keys=[self._sequence_key(topic), self._log_key(topic)],
                args=self._script_args(topic, envelope, published_at),
            )
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
        client, _ = self._async_client()
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
        messages: list[Message] = []
        for payload in logged:
            message = self._decode_message(payload)
            if message is None:
                continue
            if message.seq <= after.seq:
                break
            messages.append(message)
        messages.reverse()
        return replay_after(after, current, messages)

    def join(
        self, topic: str, connection: str, member: Member | None, ttl: float
    ) -> None:
        """Record a connection in one script that also prunes and claims the window."""
        keys, args = self._join_call(topic, connection, member, ttl)
        with self._failures:
            claimed = self._join_script(keys=keys, args=args)
        if claimed:
            self.schedule_presence(topic)

    async def ajoin(
        self, topic: str, connection: str, member: Member | None, ttl: float
    ) -> None:
        """Record a connection through this loop's client."""
        keys, args = self._join_call(topic, connection, member, ttl)
        script = self._async_script(_JOIN_SCRIPT)
        with self._failures:
            claimed = await script(keys=keys, args=args)
        if claimed:
            self.schedule_presence(topic)

    def touch(self, topic: str, connection: str, ttl: float) -> bool:
        """Push a deadline out in one script, False once the record was pruned."""
        keys, args = self._touch_call(topic, connection, ttl)
        with self._failures:
            present, claimed = self._touch_script(keys=keys, args=args)
        if claimed:
            self.schedule_presence(topic)
        return bool(present)

    async def atouch(self, topic: str, connection: str, ttl: float) -> bool:
        """Push a deadline out through this loop's client."""
        keys, args = self._touch_call(topic, connection, ttl)
        script = self._async_script(_TOUCH_SCRIPT)
        with self._failures:
            present, claimed = await script(keys=keys, args=args)
        if claimed:
            self.schedule_presence(topic)
        return bool(present)

    def leave(self, topic: str, connection: str) -> None:
        """Forget a connection in one script that also prunes and claims the window."""
        keys, args = self._leave_call(topic, connection)
        with self._leave_failures:
            claimed, wait = self._leave_script(keys=keys, args=args)
        self._after_leave(topic, claimed, wait)

    async def aleave(self, topic: str, connection: str) -> None:
        """Forget a connection through this loop's client."""
        keys, args = self._leave_call(topic, connection)
        script = self._async_script(_LEAVE_SCRIPT)
        with self._leave_failures:
            claimed, wait = await script(keys=keys, args=args)
        self._after_leave(topic, claimed, wait)

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
            row = self._snapshot_script(
                keys=keys, args=[str(self.presence_limits.size)]
            )
        return _snapshot(row)

    async def apresence(self, topic: str) -> Presence:
        """Read the presence of a topic through this loop's client."""
        keys = self._snapshot_keys(topic)
        script = self._async_script(_SNAPSHOT_SCRIPT)
        with self._failures:
            row = await script(keys=keys, args=[str(self.presence_limits.size)])
        return _snapshot(row)

    @override
    def announce_presence(self, topic: str) -> None:
        """Read the snapshot and publish it in one script, so windows keep change order.

        The script skips a topic whose last change some process already announced.
        """
        published_at = time.time()
        frame = snapshot_message(topic, Presence(0), published_at).as_dict()
        frame["envelope"] = presence_event(_HOLE).as_dict()
        head, _, tail = json.dumps(frame, separators=(",", ":")).partition(
            json.dumps(_HOLE)
        )
        args = [str(self.presence_limits.size), self._channel(topic), head, tail]
        keys = [*self._snapshot_keys(topic), self.topic_key("state", topic)]
        with self._failures:
            row = self._announce_script(keys=keys, args=args)
        if row is None:
            return
        published(self, snapshot_message(topic, _snapshot(row), published_at))

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

    def _async_script(self, source: str) -> _AsyncScript:
        """Return a script bound once to this loop's client."""
        _, scripts = self._async_client()
        script: _AsyncScript = scripts[source]
        return script

    @override
    def close(self) -> None:
        """Close the sync pool and schedule the close of clients on live loops."""
        self.presence_schedule.close()
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

    def _async_client(self) -> tuple[Any, Mapping[str, Any]]:
        """Return the async client and the scripts bound to the running event loop."""
        loop = asyncio.get_running_loop()
        with self._async_lock:
            for stale in [known for known in self._async_clients if known.is_closed()]:
                del self._async_clients[stale]
            entry = self._async_clients.get(loop)
            if entry is None:
                client: Any = self.create_client(
                    self._url, dict(self._client_options), asynchronous=True
                )
                scripts = {
                    source: client.register_script(source) for source in _SCRIPTS
                }
                entry = (client, scripts)
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


__all__ = ["RedisBroker"]
