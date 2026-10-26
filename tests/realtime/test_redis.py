import asyncio
import json
import sys
import threading
import time
from contextlib import ExitStack
from dataclasses import replace
from unittest.mock import AsyncMock, Mock, create_autospec

import fakeredis
import fakeredis.aioredis
import pytest
import redis
import redis.asyncio
from django.core.exceptions import ImproperlyConfigured
from redis.crc import key_slot

from next.partial import Patches
from next.realtime import BrokerConnectionError, Position, Presence, RedisBroker
from next.realtime.redis import broker as broker_module, pubsub as pubsub_module
from tests.support.realtime import message_payload, refresh_envelope, silent_schedule


class _Clients:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.server = fakeredis.FakeServer()
        self.sync = fakeredis.FakeRedis(server=self.server, decode_responses=True)
        self.created: list[fakeredis.aioredis.FakeRedis] = []
        self.options: list[dict[str, object]] = []
        self.async_options: list[dict[str, object]] = []
        self.async_pubsub: object | None = None
        monkeypatch.setattr(redis.Redis, "from_url", self._sync_factory)
        monkeypatch.setattr(redis.asyncio.Redis, "from_url", self._async_factory)

    def _sync_factory(self, url: str, **kwargs: object) -> fakeredis.FakeRedis:
        self.options.append(kwargs)
        return self.sync

    def _async_factory(self, url: str, **kwargs: object):
        self.async_options.append(kwargs)
        client = fakeredis.aioredis.FakeRedis(server=self.server, decode_responses=True)
        if self.async_pubsub is not None:
            client.pubsub = lambda **_kwargs: self.async_pubsub
        self.created.append(client)
        return client


@pytest.fixture()
def clients(monkeypatch: pytest.MonkeyPatch) -> _Clients:
    return _Clients(monkeypatch)


def _capture_pubsub(monkeypatch: pytest.MonkeyPatch, client) -> list:
    created = []
    original = client.pubsub

    def factory(**kwargs):
        pubsub = original(**kwargs)
        created.append(pubsub)
        return pubsub

    monkeypatch.setattr(client, "pubsub", factory)
    return created


def test_redis_publish_twice_shares_epoch_under_prefixed_keys(clients) -> None:
    backend = RedisBroker(
        {"OPTIONS": {"URL": "redis://fake/1", "PREFIX": "app:realtime:"}}
    )
    envelope = Patches(None, echo_of="r1").refresh(zone="results").envelope()

    first = backend.publish("poll:1", envelope)
    second = backend.publish("poll:1", envelope)

    assert (first.seq, second.seq) == (1, 2)
    assert first.epoch == second.epoch
    assert len(first.epoch) == 16
    assert backend.positions(["poll:1", "poll:empty"]) == {
        "poll:1": Position(first.epoch, 2),
        "poll:empty": Position("", 0),
    }
    assert clients.sync.hgetall("app:realtime:seq:{poll:1}") == {
        "seq": "2",
        "epoch": first.epoch,
    }
    assert backend._channel("poll:1") == "app:realtime:topic:poll:1"


def test_redis_lost_counter_or_epoch_starts_new_epoch(clients) -> None:
    backend = RedisBroker()
    first = backend.publish("poll:1", refresh_envelope())
    clients.sync.delete("next:seq:{poll:1}")
    restarted = backend.publish("poll:1", refresh_envelope())
    clients.sync.hdel("next:seq:{poll:1}", "epoch")
    regenerated = backend.publish("poll:1", refresh_envelope())
    following = backend.publish("poll:1", refresh_envelope())

    assert restarted.seq == 1
    assert restarted.epoch != first.epoch
    assert regenerated.seq == 2
    assert regenerated.epoch not in {first.epoch, restarted.epoch}
    assert following.epoch == regenerated.epoch


def test_redis_publish_uses_registered_script_with_counter_and_log_keys(
    clients, monkeypatch
) -> None:
    backend = RedisBroker()
    calls = []
    original = clients.sync.evalsha

    def spy(sha, numkeys, *keys_and_args):
        calls.append((numkeys, keys_and_args))
        return original(sha, numkeys, *keys_and_args)

    monkeypatch.setattr(clients.sync, "evalsha", spy)
    monkeypatch.setattr(
        clients.sync,
        "eval",
        create_autospec(clients.sync.eval, side_effect=AssertionError),
    )
    backend.publish("poll:1", refresh_envelope())

    (numkeys, keys_and_args), *_ = calls
    assert numkeys == 2
    assert keys_and_args[:3] == (
        "next:seq:{poll:1}",
        "next:log:{poll:1}",
        "next:topic:poll:1",
    )
    assert keys_and_args[-2:] == ("100", "300000")


def test_redis_client_options_enable_health_checks_and_keepalive(clients) -> None:
    RedisBroker({"OPTIONS": {"HEALTH_CHECK_INTERVAL": 3}})
    RedisBroker()
    assert clients.options == [
        {
            "decode_responses": True,
            "health_check_interval": 3,
            "socket_keepalive": True,
        },
        {
            "decode_responses": True,
            "health_check_interval": 10.0,
            "socket_keepalive": True,
        },
    ]


@pytest.mark.parametrize("interval", [-1, True, "5", None, float("inf"), float("nan")])
def test_redis_invalid_health_check_interval_raises(clients, interval) -> None:
    with pytest.raises(ImproperlyConfigured, match="HEALTH_CHECK_INTERVAL"):
        RedisBroker({"OPTIONS": {"HEALTH_CHECK_INTERVAL": interval}})


def test_redis_client_option_reaches_sync_and_async_clients(clients) -> None:
    client = {"max_connections": 500, "socket_keepalive": False, "ssl": True}
    backend = RedisBroker({"OPTIONS": {"CLIENT": client}})
    expected = {
        "decode_responses": True,
        "health_check_interval": 10.0,
        "max_connections": 500,
        "socket_keepalive": False,
        "ssl": True,
    }

    async def scenario() -> None:
        await backend.apositions(["poll:1"])

    asyncio.run(scenario())
    assert clients.options == [expected]
    assert clients.async_options == [expected]


def test_redis_client_option_overrides_the_health_check_default(clients) -> None:
    RedisBroker(
        {
            "OPTIONS": {
                "HEALTH_CHECK_INTERVAL": 3,
                "CLIENT": {"health_check_interval": 7, "decode_responses": True},
            }
        }
    )
    assert clients.options[0]["health_check_interval"] == 7
    assert clients.options[0]["decode_responses"] is True


@pytest.mark.parametrize(
    ("client", "match"),
    [
        (["max_connections"], "mapping of keyword names"),
        ({1: 2}, "mapping of keyword names"),
        ({"decode_responses": False}, "decode_responses"),
    ],
)
def test_redis_invalid_client_option_raises(clients, client, match) -> None:
    with pytest.raises(ImproperlyConfigured, match=match):
        RedisBroker({"OPTIONS": {"CLIENT": client}})
    assert clients.options == []


class _FakeServerBroker(RedisBroker):
    def __init__(self, server: fakeredis.FakeServer, config: dict) -> None:
        self.server = server
        self.calls: list[tuple[str, dict[str, object], bool]] = []
        super().__init__(config)

    def create_client(self, url, options, *, asynchronous):
        self.calls.append((url, dict(options), asynchronous))
        factory = fakeredis.aioredis.FakeRedis if asynchronous else fakeredis.FakeRedis
        return factory(server=self.server, **options)


def test_redis_subclass_create_client_builds_both_clients(clients) -> None:
    server = fakeredis.FakeServer()
    backend = _FakeServerBroker(
        server,
        {
            "OPTIONS": {
                "URL": "redis://sentinel/0",
                "CLIENT": {"db": 0, "health_check_interval": 0},
            }
        },
    )
    envelope = refresh_envelope()

    async def scenario() -> int:
        message = await backend.apublish("poll:1", envelope)
        return message.seq

    assert asyncio.run(scenario()) == 1
    assert backend.publish("poll:1", envelope).seq == 2
    options = {
        "db": 0,
        "decode_responses": True,
        "health_check_interval": 0,
        "socket_keepalive": True,
    }
    assert backend.calls == [
        ("redis://sentinel/0", options, False),
        ("redis://sentinel/0", options, True),
    ]
    assert clients.options == []
    assert clients.async_options == []


def test_redis_sync_pool_exhaustion_logs_a_warning(clients, caplog) -> None:
    backend = _FakeServerBroker(
        fakeredis.FakeServer(),
        {"OPTIONS": {"CLIENT": {"max_connections": 1, "health_check_interval": 0}}},
    )
    with (
        backend.subscribe(["poll:1"], timeout=0.05),
        pytest.raises(BrokerConnectionError) as caught,
        backend.subscribe(["poll:2"], timeout=0.05),
    ):
        pass
    assert isinstance(caught.value.__cause__, redis.exceptions.MaxConnectionsError)
    assert "connection pool is exhausted" in caplog.text
    assert caplog.records[-1].levelname == "WARNING"


def test_redis_async_pool_exhaustion_logs_a_warning(clients, caplog) -> None:
    backend = _FakeServerBroker(
        fakeredis.FakeServer(),
        {"OPTIONS": {"CLIENT": {"max_connections": 1, "health_check_interval": 0}}},
    )

    async def scenario() -> None:
        async with backend.asubscribe(["poll:1"], timeout=0.05):
            with pytest.raises(BrokerConnectionError):
                async with backend.asubscribe(["poll:2"], timeout=0.05):
                    pass
        await backend.aclose()

    asyncio.run(scenario())
    assert "connection pool is exhausted" in caplog.text


@pytest.mark.parametrize(
    ("failure", "warned"),
    [
        (redis.exceptions.MaxConnectionsError("Too many connections"), True),
        (redis.ConnectionError("Too many connections"), False),
        (redis.ConnectionError("No connection available."), False),
        (redis.ConnectionError("Connection closed by server."), False),
        (redis.TimeoutError("Timeout reading from socket"), False),
    ],
)
def test_redis_pool_exhaustion_is_told_apart_from_other_failures(
    clients, caplog, failure, warned
) -> None:
    backend = RedisBroker()
    pubsub = Mock(spec=redis.client.PubSub)
    pubsub.get_message.side_effect = failure
    with pytest.raises(BrokerConnectionError):
        next(pubsub_module.messages(pubsub, [], 0.01, backend._failures))
    assert ("connection pool is exhausted" in caplog.text) is warned


def test_redis_publish_fans_out_to_thirty_subscribers(clients) -> None:
    backend = RedisBroker()
    with ExitStack() as stack:
        subscribers = [
            stack.enter_context(backend.subscribe(["poll:1"], timeout=0.01))
            for _ in range(30)
        ]
        assert [next(messages) for messages in subscribers] == [None] * 30
        published = backend.publish("poll:1", refresh_envelope())
        assert [next(messages) for messages in subscribers] == [published] * 30


def test_redis_lua_publish_preserves_json_types_and_precision(clients) -> None:
    backend = RedisBroker()
    detail = {
        "empty_array": [],
        "empty_object": {},
        "nested": [{"values": [], "label": '"seq":0'}],
        "integer": 9007199254740991,
        "fraction": 0.12345678901234567,
        "label": "Тест",
    }
    envelope = Patches(None).event("room:update", detail).envelope()
    with backend.subscribe(["room:json"], timeout=0.01) as messages:
        assert next(messages) is None
        published = backend.publish("room:json", envelope)
        received = next(messages)
    assert published.envelope == envelope
    assert received == published


def test_redis_sync_subscribe_failure_closes_pubsub(clients, monkeypatch) -> None:
    pubsub = Mock(spec=redis.client.PubSub)
    pubsub.subscribe.side_effect = redis.ConnectionError("unavailable")
    monkeypatch.setattr(clients.sync, "pubsub", lambda **kwargs: pubsub)
    backend = RedisBroker()
    with (
        pytest.raises(BrokerConnectionError, match="unavailable"),
        backend.subscribe(["room"], timeout=0.01),
    ):
        pytest.fail("A failed subscription cannot enter the context")
    pubsub.close.assert_called_once_with()


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (redis.ConnectionError, BrokerConnectionError),
        (asyncio.CancelledError, asyncio.CancelledError),
    ],
)
def test_redis_async_subscribe_failure_closes_pubsub(clients, error, expected) -> None:
    pubsub = AsyncMock(spec=redis.asyncio.client.PubSub)
    pubsub.subscribe.side_effect = error()
    clients.async_pubsub = pubsub
    backend = RedisBroker()

    async def scenario() -> None:
        with pytest.raises(expected):
            async with backend.asubscribe(["room"], timeout=0.01):
                pytest.fail("A failed subscription cannot enter the context")
        pubsub.aclose.assert_awaited_once_with()

    asyncio.run(scenario())


def test_redis_sync_subscribe_consumes_every_ack_before_yielding(
    clients, monkeypatch
) -> None:
    created = _capture_pubsub(monkeypatch, clients.sync)
    backend = RedisBroker()
    with backend.subscribe(["poll:1", "poll:2", "poll:1"], timeout=0.2) as messages:
        (pubsub,) = created
        assert pubsub.parse_response(block=False, timeout=0.01) is None
        started = time.monotonic()
        assert next(messages) is None
        assert time.monotonic() - started >= 0.15


def test_redis_async_subscribe_consumes_every_ack_before_yielding(clients) -> None:
    backend = RedisBroker()

    async def scenario() -> None:
        async with backend.asubscribe(["poll:1", "poll:2"], timeout=0.2) as messages:
            assert len(clients.created) == 1
            started = time.monotonic()
            assert await anext(messages) is None
            assert time.monotonic() - started >= 0.15
            published = await backend.apublish("poll:2", refresh_envelope())
            assert await anext(messages) == published

    asyncio.run(scenario())


def _scripted_pubsub(frames: list) -> redis.client.PubSub:
    pubsub = create_autospec(redis.client.PubSub, instance=True)
    pubsub.get_message.side_effect = lambda **_: frames.pop(0) if frames else None
    return pubsub


def _frame(topic: str, seq: int) -> dict[str, object]:
    message = message_payload(topic=topic, seq=seq, published_at=1.0)
    return {"type": "message", "channel": topic, "data": json.dumps(message)}


def test_redis_confirm_message_between_acks_is_buffered(clients) -> None:
    pubsub = _scripted_pubsub(
        [
            {"type": "subscribe", "channel": "next:topic:a"},
            _frame("a", 4),
            None,
            {"type": "pong", "data": "x"},
            {"type": "subscribe", "channel": "next:topic:b"},
        ]
    )
    buffered = pubsub_module.confirm(pubsub, ("next:topic:a", "next:topic:b"))
    assert [message.seq for message in buffered] == [4]


def test_redis_sync_buffered_setup_messages_are_yielded_first(clients) -> None:
    backend = RedisBroker()
    pubsub = Mock(spec=redis.client.PubSub)
    pubsub.get_message.return_value = None
    early = pubsub_module.decode_message(_frame("a", 4)["data"])
    messages = pubsub_module.messages(pubsub, [early], 0.01, backend._failures)
    assert next(messages) == early
    assert next(messages) is None


def test_redis_confirm_without_ack_raises_connection_error(
    clients, monkeypatch
) -> None:
    monkeypatch.setattr(pubsub_module, "_SUBSCRIBE_TIMEOUT", 0.01)
    with pytest.raises(BrokerConnectionError, match="confirm"):
        pubsub_module.confirm(_scripted_pubsub([]), ("next:topic:a",))

    async def scenario() -> None:
        pubsub = AsyncMock(spec=redis.asyncio.client.PubSub)
        pubsub.get_message.return_value = None
        with pytest.raises(BrokerConnectionError, match="confirm"):
            await pubsub_module.aconfirm(pubsub, ("next:topic:a",))

    asyncio.run(scenario())


def test_redis_async_confirm_message_between_acks_is_yielded_first(clients) -> None:
    backend = RedisBroker()
    pubsub = AsyncMock(spec=redis.asyncio.client.PubSub)
    frames = [
        {"type": "subscribe", "channel": "next:topic:a"},
        _frame("a", 7),
        {"type": "subscribe", "channel": "next:topic:b"},
    ]
    pubsub.get_message.side_effect = lambda **_: frames.pop(0) if frames else None
    pubsub.ping = AsyncMock(spec=redis.asyncio.client.PubSub.ping)

    async def scenario() -> None:
        buffered = await pubsub_module.aconfirm(
            pubsub, ("next:topic:a", "next:topic:b")
        )
        assert [message.seq for message in buffered] == [7]
        messages = pubsub_module.amessages(pubsub, buffered, 0.01, backend._failures)
        assert (await anext(messages)).seq == 7
        assert await anext(messages) is None

    asyncio.run(scenario())


def test_redis_sync_subscription_reconnect_raises_connection_error(
    clients, monkeypatch
) -> None:
    created = _capture_pubsub(monkeypatch, clients.sync)
    backend = RedisBroker()
    with backend.subscribe(["poll:1"], timeout=0.05) as messages:
        assert next(messages) is None
        created[0].connection.disconnect()
        with pytest.raises(BrokerConnectionError, match="reconnected"):
            next(messages)


def test_redis_async_subscription_reconnect_raises_connection_error(clients) -> None:
    backend = RedisBroker()

    async def scenario() -> None:
        async with backend.asubscribe(["poll:1"], timeout=0.05) as messages:
            assert await anext(messages) is None
            (client,) = clients.created
            await client.connection_pool.disconnect()
            with pytest.raises(BrokerConnectionError, match="reconnected"):
                await anext(messages)

    asyncio.run(scenario())


def test_redis_sync_unanswered_liveness_probe_raises_connection_error(
    clients, monkeypatch
) -> None:
    created = _capture_pubsub(monkeypatch, clients.sync)
    backend = RedisBroker()
    with backend.subscribe(["poll:1"], timeout=0.01) as messages:
        assert next(messages) is None
        assert next(messages) is None
        monkeypatch.setattr(
            created[0], "ping", create_autospec(created[0].ping, return_value=None)
        )
        assert next(messages) is None
        with pytest.raises(BrokerConnectionError, match="stopped answering"):
            next(messages)


def test_redis_async_unanswered_liveness_probe_raises_connection_error(clients) -> None:
    backend = RedisBroker()
    pubsub = AsyncMock(spec=redis.asyncio.client.PubSub)
    pubsub.get_message.return_value = None
    pubsub.ping = AsyncMock(spec=redis.asyncio.client.PubSub.ping)

    async def scenario() -> None:
        messages = pubsub_module.amessages(pubsub, [], 0.01, backend._failures)
        assert await anext(messages) is None
        pubsub.ping.assert_awaited_once_with(pubsub_module._LIVENESS)
        with pytest.raises(BrokerConnectionError, match="stopped answering"):
            await anext(messages)

    asyncio.run(scenario())


def test_redis_malformed_payload_is_skipped_and_logged(clients, caplog) -> None:
    backend = RedisBroker()
    with backend.subscribe(["poll:1"], timeout=0.5) as messages:
        clients.sync.publish(backend._channel("poll:1"), "not-json")
        clients.sync.publish(backend._channel("poll:1"), json.dumps({"seq": "x"}))
        published = backend.publish("poll:1", refresh_envelope())
        assert next(messages) == published
    assert caplog.text.count("Skipped a malformed realtime message") == 2


def test_redis_async_publish_and_subscribe_share_sequence(clients) -> None:
    backend = RedisBroker()

    async def scenario() -> None:
        async with backend.asubscribe(["poll:1"], timeout=0.1) as messages:
            assert await anext(messages) is None
            message = await backend.apublish("poll:1", refresh_envelope())
            received = await anext(messages)
        assert message.seq == 1
        assert received == message
        assert await backend.apositions(["poll:1", "poll:2"]) == {
            "poll:1": Position(message.epoch, 1),
            "poll:2": Position("", 0),
        }

    asyncio.run(scenario())


def test_redis_async_client_per_event_loop_is_not_reused(clients) -> None:
    backend = RedisBroker()

    async def publish_twice() -> None:
        await backend.apublish("poll:1", refresh_envelope())
        await backend.apublish("poll:1", refresh_envelope())

    asyncio.run(publish_twice())
    asyncio.run(publish_twice())

    assert len(clients.created) == 2
    assert clients.created[0] is not clients.created[1]
    assert clients.created[0] not in [
        client for client, _ in backend._async_clients.values()
    ]
    assert backend.positions(["poll:1"])["poll:1"].seq == 4


def test_redis_async_client_drops_the_entry_of_a_closed_loop(clients) -> None:
    backend = RedisBroker()
    closed = asyncio.new_event_loop()
    closed.run_until_complete(backend.apublish("poll:1", refresh_envelope()))
    closed.close()
    assert closed in backend._async_clients
    running = asyncio.new_event_loop()
    try:
        running.run_until_complete(backend.apublish("poll:1", refresh_envelope()))
    finally:
        running.close()
    assert closed not in backend._async_clients
    assert running in backend._async_clients
    assert len(clients.created) == 2


def test_redis_aclose_releases_sync_and_async_clients(clients, monkeypatch) -> None:
    backend = RedisBroker()
    closed = create_autospec(clients.sync.close)
    monkeypatch.setattr(clients.sync, "close", closed)

    async def use_and_close() -> None:
        await backend.apositions(["poll:1"])
        (client,) = clients.created
        aclose = create_autospec(client.aclose)
        monkeypatch.setattr(client, "aclose", aclose)
        await backend.aclose()
        aclose.assert_awaited_once_with()

    asyncio.run(use_and_close())
    assert backend._async_clients == {}
    closed.assert_called_once_with()


def test_redis_close_with_running_loop_schedules_async_close(
    clients, monkeypatch
) -> None:
    backend = RedisBroker()
    ready = threading.Event()
    done = threading.Event()
    loop = asyncio.new_event_loop()
    worker = threading.Thread(target=loop.run_forever, daemon=True)
    worker.start()

    async def register() -> None:
        backend._loop_client()
        ready.set()

    asyncio.run_coroutine_threadsafe(register(), loop).result(5)
    assert ready.is_set()
    (client,) = clients.created

    async def aclose() -> None:
        done.set()

    monkeypatch.setattr(client, "aclose", aclose)
    backend.close()
    assert done.wait(5)
    loop.call_soon_threadsafe(loop.stop)
    worker.join(5)
    loop.close()
    assert backend._async_clients == {}


def test_redis_close_with_stopped_loop_drops_its_client(clients) -> None:
    backend = RedisBroker()
    loop = asyncio.new_event_loop()

    async def register() -> None:
        backend._loop_client()

    loop.run_until_complete(register())
    backend.close()
    loop.close()
    assert backend._async_clients == {}


def test_redis_subscribe_empty_topics_raises(clients) -> None:
    backend = RedisBroker()
    with pytest.raises(ValueError, match="at least one topic"):
        backend.subscribe([], timeout=0.1)

    async def scenario() -> None:
        with pytest.raises(ValueError, match="at least one topic"):
            backend.asubscribe([], timeout=0.1)

    asyncio.run(scenario())


def test_redis_invalid_url_or_prefix_raises(clients) -> None:
    for options in (
        {"URL": ""},
        {"PREFIX": ""},
        {"PREFIX": ":::"},
        {"PREFIX": "{tenant}"},
        {"PREFIX": "app}"},
    ):
        with pytest.raises(ImproperlyConfigured):
            RedisBroker({"OPTIONS": options})


def test_redis_broker_with_redis_blocked_in_sys_modules_names_the_optional_extra(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "redis", None)
    monkeypatch.setitem(sys.modules, "redis.asyncio", None)
    with pytest.raises(ImproperlyConfigured, match=r"next\.dj\[broker\]"):
        RedisBroker()


@pytest.mark.parametrize("payload", [json.dumps([]), "not-json", None, b"\xff"])
def test_redis_decode_invalid_payload_is_skipped_and_logged(payload, caplog) -> None:
    assert pubsub_module.decode_message(payload) is None
    assert "Skipped a malformed realtime message" in caplog.text


def test_redis_decode_bytes_payload_returns_message() -> None:
    payload = json.dumps(message_payload(seq=1, published_at=1.0)).encode()
    message = pubsub_module.decode_message(payload)
    assert message is not None
    assert message.epoch == "e1"


def test_redis_publish_non_envelope_raises(clients) -> None:
    backend = RedisBroker()
    with pytest.raises(TypeError, match="Envelope"):
        backend.publish("poll:1", object())

    async def scenario() -> None:
        with pytest.raises(TypeError, match="Envelope"):
            await backend.apublish("poll:1", object())

    asyncio.run(scenario())


def test_redis_aclose_without_async_client_closes_sync_pool(
    clients, monkeypatch
) -> None:
    backend = RedisBroker()
    closed = create_autospec(clients.sync.close)
    monkeypatch.setattr(clients.sync, "close", closed)
    asyncio.run(backend.aclose())
    closed.assert_called_once_with()
    assert clients.created == []


_TRANSPORT_ERRORS = [
    redis.ConnectionError("Connection closed by server."),
    redis.TimeoutError("Timeout reading from socket"),
]


@pytest.mark.parametrize("failure", _TRANSPORT_ERRORS)
def test_sync_message_loop_wraps_redis_transport_errors(clients, failure) -> None:
    backend = RedisBroker()
    pubsub = Mock(spec=redis.client.PubSub)
    pubsub.get_message.side_effect = failure
    with pytest.raises(BrokerConnectionError) as caught:
        next(pubsub_module.messages(pubsub, [], 0.01, backend._failures))
    assert caught.value.__cause__ is failure
    assert isinstance(caught.value, ConnectionError)


@pytest.mark.parametrize("failure", _TRANSPORT_ERRORS)
def test_sync_liveness_probe_wraps_redis_transport_errors(clients, failure) -> None:
    backend = RedisBroker()
    pubsub = Mock(spec=redis.client.PubSub)
    pubsub.get_message.return_value = None
    pubsub.ping.side_effect = failure
    with pytest.raises(BrokerConnectionError) as caught:
        next(pubsub_module.messages(pubsub, [], 0.0, backend._failures))
    assert caught.value.__cause__ is failure


def test_sync_subscription_killed_mid_stream_raises_broker_error(
    clients, monkeypatch
) -> None:
    created = _capture_pubsub(monkeypatch, clients.sync)
    backend = RedisBroker()
    with backend.subscribe(["poll:1"], timeout=0.05) as messages:
        failure = redis.ConnectionError("Connection closed by server.")
        monkeypatch.setattr(
            created[0],
            "get_message",
            create_autospec(created[0].get_message, side_effect=failure),
        )
        with pytest.raises(BrokerConnectionError, match="closed by server"):
            next(messages)


def test_sync_ack_wait_wraps_redis_transport_errors(clients, monkeypatch) -> None:
    created = _capture_pubsub(monkeypatch, clients.sync)
    original = clients.sync.pubsub

    def failing_pubsub(**kwargs):
        pubsub = original(**kwargs)
        pubsub.get_message = create_autospec(
            pubsub.get_message, side_effect=redis.ConnectionError("gone")
        )
        return pubsub

    monkeypatch.setattr(clients.sync, "pubsub", failing_pubsub)
    backend = RedisBroker()
    with (
        pytest.raises(BrokerConnectionError, match="gone"),
        backend.subscribe(["poll:1"], timeout=0.05),
    ):
        pytest.fail("A failed subscription cannot enter the context")
    assert len(created) == 1


@pytest.mark.parametrize("failure", _TRANSPORT_ERRORS)
def test_async_loop_and_setup_wrap_redis_transport_errors(clients, failure) -> None:
    backend = RedisBroker()

    async def scenario() -> None:
        pubsub = AsyncMock(spec=redis.asyncio.client.PubSub)
        pubsub.get_message.side_effect = failure
        with pytest.raises(BrokerConnectionError) as caught:
            await anext(pubsub_module.amessages(pubsub, [], 0.01, backend._failures))
        assert caught.value.__cause__ is failure

        probing = AsyncMock(spec=redis.asyncio.client.PubSub)
        probing.get_message.return_value = None
        probing.ping = AsyncMock(
            spec=redis.asyncio.client.PubSub.ping, side_effect=failure
        )
        with pytest.raises(BrokerConnectionError):
            await anext(pubsub_module.amessages(probing, [], 0.0, backend._failures))

        clients.async_pubsub = pubsub
        with pytest.raises(BrokerConnectionError):
            async with backend.asubscribe(["poll:1"], timeout=0.01):
                pytest.fail("A failed subscription cannot enter the context")
        pubsub.aclose.assert_awaited_once_with()

    asyncio.run(scenario())


def test_publish_and_positions_wrap_redis_transport_errors(
    clients, monkeypatch
) -> None:
    backend = RedisBroker()
    failure = redis.ConnectionError("Connection refused")
    monkeypatch.setattr(
        backend,
        "_scripts",
        replace(
            backend._scripts,
            publish=create_autospec(backend._scripts.publish, side_effect=failure),
        ),
    )
    pipe = Mock(spec=redis.client.Pipeline)
    pipe.execute.side_effect = failure
    monkeypatch.setattr(
        clients.sync,
        "pipeline",
        create_autospec(clients.sync.pipeline, return_value=pipe),
    )
    with pytest.raises(BrokerConnectionError, match="refused"):
        backend.publish("poll:1", refresh_envelope())
    with pytest.raises(BrokerConnectionError, match="refused"):
        backend.positions(["poll:1"])

    async def scenario() -> None:
        entry = backend._loop_client()
        client, scripts = entry
        failing = AsyncMock(spec=scripts.publish, side_effect=failure)
        backend._async_clients[asyncio.get_running_loop()] = entry._replace(
            scripts=replace(scripts, publish=failing)
        )
        monkeypatch.setattr(
            client, "pipeline", create_autospec(client.pipeline, side_effect=failure)
        )
        with pytest.raises(BrokerConnectionError, match="refused"):
            await backend.apublish("poll:1", refresh_envelope())
        with pytest.raises(BrokerConnectionError, match="refused"):
            await backend.apositions(["poll:1"])

    asyncio.run(scenario())


def test_unrelated_errors_are_not_translated(clients, monkeypatch) -> None:
    backend = RedisBroker()
    refused = create_autospec(
        backend._scripts.publish, side_effect=redis.ResponseError("NOSCRIPT")
    )
    monkeypatch.setattr(backend, "_scripts", replace(backend._scripts, publish=refused))
    with pytest.raises(redis.ResponseError):
        backend.publish("poll:1", refresh_envelope())


def test_redis_forked_child_forgets_parent_async_clients(clients) -> None:
    backend = RedisBroker()

    async def use() -> None:
        await backend.apositions(["poll:1"])

    loop = asyncio.new_event_loop()
    loop.run_until_complete(use())
    parent_lock = backend._async_lock
    assert len(backend._async_clients) == 1
    broker_module._forget_async_clients()
    assert backend._async_lock is not parent_lock
    assert len(backend._async_clients) == 0
    asyncio.run(use())
    assert len(clients.created) == 2
    loop.close()


def test_redis_positions_keep_an_epoch_whose_counter_is_gone(clients) -> None:
    backend = RedisBroker()
    published = backend.publish("poll:1", refresh_envelope())
    clients.sync.hdel("next:seq:{poll:1}", "seq")
    assert backend.positions(["poll:1"]) == {"poll:1": Position(published.epoch, 0)}


def test_redis_topic_keys_share_one_cluster_slot(clients) -> None:
    backend = RedisBroker({"OPTIONS": {"PREFIX": "app"}})
    seq, log = backend.topic_key("seq", "a:b"), backend.topic_key("log", "a:b")
    assert (seq, log) == ("app:seq:{a:b}", "app:log:{a:b}")
    assert key_slot(seq.encode()) == key_slot(log.encode())
    assert backend._channel("a:b") == "app:topic:a:b"


class _FlatKeys(RedisBroker):
    def topic_key(self, kind: str, topic: str) -> str:
        return f"flat-{kind}-{topic}"


def test_redis_subclass_topic_key_lays_out_every_stored_key(clients) -> None:
    backend = _FlatKeys()
    first = backend.publish("poll:1", refresh_envelope())
    assert clients.sync.hget("flat-seq-poll:1", "epoch") == first.epoch
    assert clients.sync.llen("flat-log-poll:1") == 1
    assert backend.positions(["poll:1"]) == {"poll:1": Position(first.epoch, 1)}
    assert [
        m.seq for m in backend.replay("poll:1", Position(first.epoch, 0)).messages
    ] == [1]


@pytest.mark.parametrize("operation", ["join_again", "join_another", "touch", "leave"])
def test_redis_presence_heals_an_evicted_identified_set(clients, operation) -> None:
    backend = RedisBroker(schedule=silent_schedule)
    backend.join("room", "a", {"id": 1}, 30)
    backend.join("room", "b", {"id": 1}, 30)
    clients.sync.delete(backend.topic_key("identified", "room"))
    if operation == "join_again":
        backend.join("room", "a", {"id": 2}, 30)
    elif operation == "join_another":
        backend.join("room", "c", {"id": 1}, 30)
    elif operation == "touch":
        assert backend.touch("room", "a", 30) is True
    else:
        backend.leave("room", "a")
    backend.leave("room", "a")
    backend.leave("room", "b")
    backend.leave("room", "c")
    assert backend.presence("room") == Presence(0)
    backend.join("room", "d", {"id": 3}, 30)
    assert backend.presence("room") == Presence(1, ({"id": 3},))
