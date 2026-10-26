import asyncio

import fakeredis
import fakeredis.aioredis
import pytest
import redis
import redis.asyncio
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from next.realtime import (
    BrokerBackend,
    BrokerConnectionError,
    InvalidTopicError,
    MemoryBroker,
    Message,
    Position,
    RedisBroker,
    Replay,
    ReplayingBroker,
)
from next.realtime.backends import _since_idle
from next.realtime.replay import (
    INCOMPLETE,
    MAX_SIZE,
    MAX_TTL,
    acatch_up,
    catch_up,
    format_event_id,
    parse_event_id,
    replay_after,
)
from tests.support.realtime import ServerClock, freeze_fakeredis, refresh_envelope


EPOCH = "0123456789abcdef"


class _Harness:
    """Build one broker kind over a controllable clock and replay it sync or async."""

    def __init__(
        self, kind: str, monkeypatch: pytest.MonkeyPatch, *, asynchronous: bool
    ) -> None:
        self.kind = kind
        self.asynchronous = asynchronous
        self.clock = ServerClock()
        self.server = fakeredis.FakeServer()
        self.redis = fakeredis.FakeRedis(server=self.server, decode_responses=True)
        if kind == "redis":
            monkeypatch.setattr(redis.Redis, "from_url", self._sync_client)
            monkeypatch.setattr(redis.asyncio.Redis, "from_url", self._async_client)
            freeze_fakeredis(monkeypatch, self.clock)

    def _sync_client(self, url: str, **kwargs: object) -> fakeredis.FakeRedis:
        return self.redis

    def _async_client(self, url: str, **kwargs: object) -> fakeredis.aioredis.FakeRedis:
        return fakeredis.aioredis.FakeRedis(server=self.server, decode_responses=True)

    def broker(self, *, size: int = 3, ttl: float = 5) -> MemoryBroker | RedisBroker:
        replay = {"SIZE": size, "TTL": ttl}
        with override_settings(NEXT_FRAMEWORK={"REALTIME": {"REPLAY": replay}}):
            if self.kind == "memory":
                return MemoryBroker(clock=self.clock)
            return RedisBroker()

    def restarted(
        self, broker: MemoryBroker | RedisBroker
    ) -> MemoryBroker | RedisBroker:
        """Lose the topic counter the way a process or Redis restart does."""
        if self.kind == "memory":
            return MemoryBroker(clock=self.clock)
        self.redis.delete("next:seq:{poll:1}")
        return broker

    def replay(self, broker: ReplayingBroker, topic: str, after: Position) -> Replay:
        if not self.asynchronous:
            return broker.replay(topic, after)
        return asyncio.run(broker.areplay(topic, after))


@pytest.fixture(
    params=[("memory", False), ("memory", True), ("redis", False), ("redis", True)],
    ids=["memory-sync", "memory-async", "redis-sync", "redis-async"],
)
def harness(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    kind, asynchronous = request.param
    return _Harness(kind, monkeypatch, asynchronous=asynchronous)


def _publish(broker: BrokerBackend, count: int, topic: str = "poll:1") -> list[Message]:
    return [broker.publish(topic, refresh_envelope()) for _ in range(count)]


def _seqs(replay: Replay) -> list[int]:
    return [message.seq for message in replay.messages]


def test_builtin_brokers_are_replaying_brokers(harness: _Harness) -> None:
    assert isinstance(harness.broker(), ReplayingBroker)


def test_replay_covers_a_gap_inside_the_log_in_ascending_order(
    harness: _Harness,
) -> None:
    broker = harness.broker()
    first, *_ = _publish(broker, 3)
    replay = harness.replay(broker, "poll:1", Position(first.epoch, 1))
    assert replay.complete
    assert _seqs(replay) == [2, 3]
    assert {message.epoch for message in replay.messages} == {first.epoch}
    assert {message.topic for message in replay.messages} == {"poll:1"}
    assert replay.messages[0].envelope == refresh_envelope()
    whole = harness.replay(broker, "poll:1", Position(first.epoch, 0))
    assert whole == Replay(whole.messages, complete=True)
    assert _seqs(whole) == [1, 2, 3]


def test_replay_holds_exactly_the_messages_strictly_after_each_cursor(
    harness: _Harness,
) -> None:
    broker = harness.broker(size=4)
    first, *_ = _publish(broker, 4)
    for cursor in range(5):
        replay = harness.replay(broker, "poll:1", Position(first.epoch, cursor))
        assert replay.complete
        assert _seqs(replay) == list(range(cursor + 1, 5))


def test_replay_of_a_gap_longer_than_the_log_is_incomplete(harness: _Harness) -> None:
    broker = harness.broker(size=3)
    first, *_ = _publish(broker, 5)
    assert harness.replay(broker, "poll:1", Position(first.epoch, 1)) == INCOMPLETE
    edge = harness.replay(broker, "poll:1", Position(first.epoch, 2))
    assert edge.complete
    assert _seqs(edge) == [3, 4, 5]


def test_replay_at_the_current_seq_is_complete_and_empty(harness: _Harness) -> None:
    broker = harness.broker()
    first, *_ = _publish(broker, 2)
    assert harness.replay(broker, "poll:1", Position(first.epoch, 2)) == Replay(
        (), complete=True
    )
    never = harness.replay(broker, "poll:empty", Position("", 0))
    assert never == Replay((), complete=True)


def test_replay_ahead_of_the_current_seq_is_incomplete(harness: _Harness) -> None:
    broker = harness.broker()
    first, *_ = _publish(broker, 2)
    assert harness.replay(broker, "poll:1", Position(first.epoch, 3)) == INCOMPLETE


def test_replay_from_another_epoch_is_incomplete(harness: _Harness) -> None:
    broker = harness.broker()
    _publish(broker, 2)
    assert harness.replay(broker, "poll:1", Position(EPOCH, 1)) == INCOMPLETE
    assert harness.replay(broker, "poll:empty", Position(EPOCH, 0)) == INCOMPLETE


def test_replay_after_an_epoch_change_serves_only_the_new_epoch(
    harness: _Harness,
) -> None:
    broker = harness.broker()
    old, *_ = _publish(broker, 3)
    broker = harness.restarted(broker)
    fresh = broker.publish("poll:1", refresh_envelope())
    assert fresh.epoch != old.epoch
    for cursor in (Position(old.epoch, 1), Position(old.epoch, 3)):
        assert harness.replay(broker, "poll:1", cursor) == INCOMPLETE
    replay = harness.replay(broker, "poll:1", Position(fresh.epoch, fresh.seq - 1))
    assert [(message.epoch, message.seq) for message in replay.messages] == [
        (fresh.epoch, fresh.seq)
    ]


def test_replay_log_expires_after_the_ttl_since_the_last_publish(
    harness: _Harness,
) -> None:
    broker = harness.broker(ttl=5)
    first, *_ = _publish(broker, 2)
    harness.clock.advance(4)
    _publish(broker, 1)
    harness.clock.advance(4)
    alive = harness.replay(broker, "poll:1", Position(first.epoch, 1))
    assert _seqs(alive) == [2, 3]
    harness.clock.advance(1.5)
    assert harness.replay(broker, "poll:1", Position(first.epoch, 1)) == INCOMPLETE
    assert harness.replay(broker, "poll:1", Position(first.epoch, 3)) == INCOMPLETE


def test_publish_after_the_ttl_starts_a_fresh_log_in_a_new_epoch(
    harness: _Harness,
) -> None:
    broker = harness.broker(size=10, ttl=5)
    first, *_ = _publish(broker, 3)
    harness.clock.advance(6)
    (fresh,) = _publish(broker, 1)
    assert fresh.seq == 1
    assert fresh.epoch != first.epoch
    assert harness.replay(broker, "poll:1", Position(first.epoch, 3)) == INCOMPLETE
    assert _seqs(harness.replay(broker, "poll:1", Position(fresh.epoch, 0))) == [1]


def test_position_expires_a_ttl_after_its_last_publish(harness: _Harness) -> None:
    broker = harness.broker(size=10, ttl=5)
    (first,) = _publish(broker, 1)
    harness.clock.advance(4.9)
    assert broker.positions(["poll:1"]) == {"poll:1": Position(first.epoch, 1)}
    harness.clock.advance(0.2)
    assert broker.positions(["poll:1"]) == {"poll:1": Position("", 0)}
    assert asyncio.run(broker.apositions(["poll:1"])) == {"poll:1": Position("", 0)}


def test_position_without_a_log_still_expires(harness: _Harness) -> None:
    broker = harness.broker(size=0, ttl=5)
    _publish(broker, 2)
    harness.clock.advance(5.1)
    (fresh,) = _publish(broker, 1)
    assert fresh.seq == 1


def test_memory_position_idle_for_exactly_the_ttl_is_never_published(
    clock: ServerClock,
) -> None:
    broker = MemoryBroker(clock=clock, replay={"SIZE": 0, "TTL": 5})
    published = broker.publish("poll:1", refresh_envelope())
    clock.advance(4.999)
    assert broker.positions(["poll:1"]) == {
        "poll:1": Position(published.epoch, published.seq)
    }
    clock.advance(0.001)
    assert broker.positions(["poll:1"]) == {"poll:1": Position("", 0)}


def test_memory_publish_sweeps_the_idle_positions(clock: ServerClock) -> None:
    broker = MemoryBroker(clock=clock, replay={"SIZE": 0, "TTL": 5})
    for index in range(100):
        broker.publish(f"card:{index}", refresh_envelope())
    clock.advance(5)
    broker.publish("other", refresh_envelope())
    assert list(broker._positions) == ["other"]


def test_redis_sequence_key_carries_the_replay_ttl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _Harness("redis", monkeypatch, asynchronous=False)
    broker = harness.broker(size=0, ttl=1.5)
    _publish(broker, 1)
    assert harness.redis.pttl("next:seq:{poll:1}") == 1500


def test_memory_publish_sweeps_the_expired_logs_once_per_ttl(
    clock: ServerClock,
) -> None:
    broker = MemoryBroker(clock=clock, replay={"SIZE": 3, "TTL": 300})
    broker.publish("poll:a", refresh_envelope())
    clock.advance(100)
    broker.publish("poll:b", refresh_envelope())
    clock.advance(100)
    broker.publish("poll:a", refresh_envelope())
    assert list(broker._logs) == ["poll:a", "poll:b"]
    clock.advance(250)
    broker.publish("poll:c", refresh_envelope())
    assert list(broker._logs) == ["poll:a", "poll:c"]
    clock.advance(200)
    broker.publish("poll:c", refresh_envelope())
    assert list(broker._logs) == ["poll:a", "poll:c"]
    clock.advance(100)
    broker.publish("poll:c", refresh_envelope())
    assert list(broker._logs) == ["poll:c"]


def test_memory_publish_between_sweeps_still_restarts_an_expired_log(
    clock: ServerClock,
) -> None:
    broker = MemoryBroker(clock=clock, replay={"SIZE": 10, "TTL": 5})
    first = broker.publish("poll:a", refresh_envelope())
    clock.advance(4)
    broker.publish("poll:a", refresh_envelope())
    clock.advance(1)
    broker.publish("poll:b", refresh_envelope())
    clock.advance(4.5)
    fresh = broker.publish("poll:a", refresh_envelope())
    assert len(broker._logs["poll:a"]) == 3
    assert (fresh.epoch, fresh.seq) != (first.epoch, 3)
    assert broker.replay("poll:a", Position(first.epoch, 2)) == INCOMPLETE
    assert _seqs(broker.replay("poll:a", Position(fresh.epoch, 0))) == [1]


def test_log_restarts_after_a_pause_of_exactly_the_ttl() -> None:
    def message(seq: int, published_at: float) -> Message:
        return Message("poll:1", seq, refresh_envelope(), published_at, EPOCH)

    first, second, third = message(1, 0.0), message(2, 5.0), message(3, 9.99)
    assert _since_idle((first,), 4.99, 5) == (first,)
    assert _since_idle((first,), 5.0, 5) == ()
    assert _since_idle((first, third), 9.99, 5) == (third,)
    assert _since_idle((first, second), 5.0, 5.01) == (first, second)
    assert _since_idle((first, second), 9.99, 5) == (second,)


def test_replay_size_zero_stores_nothing_and_never_replays(harness: _Harness) -> None:
    broker = harness.broker(size=0)
    first, *_ = _publish(broker, 2)
    assert harness.replay(broker, "poll:1", Position(first.epoch, 1)) == INCOMPLETE
    assert harness.replay(broker, "poll:1", Position(first.epoch, 2)) == INCOMPLETE
    assert harness.redis.exists("next:log:{poll:1}") == 0
    assert getattr(broker, "_logs", {}) == {}


def test_redis_size_zero_sends_no_log_command(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _Harness("redis", monkeypatch, asynchronous=False)
    broker = harness.broker(size=0)
    harness.redis.rpush("next:log:{poll:1}", "kept")
    broker.publish("poll:1", refresh_envelope())
    broker.publish("poll:1", refresh_envelope())
    assert harness.redis.lrange("next:log:{poll:1}", 0, -1) == ["kept"]
    assert harness.redis.pttl("next:log:{poll:1}") == -1
    harness.server.connected = False
    assert broker.replay("poll:1", Position(EPOCH, 0)) == INCOMPLETE
    assert asyncio.run(broker.areplay("poll:1", Position(EPOCH, 0))) == INCOMPLETE


def test_replay_rejects_an_invalid_topic(harness: _Harness) -> None:
    broker = harness.broker()
    with pytest.raises(InvalidTopicError):
        harness.replay(broker, "bad topic", Position(EPOCH, 0))


@pytest.mark.parametrize(
    "replay",
    [
        "SIZE=3",
        {"SIZE": 3, "LIMIT": 1},
        {"SIZE": -1},
        {"SIZE": True},
        {"SIZE": 1.5},
        {"SIZE": MAX_SIZE + 1},
        {"TTL": 0},
        {"TTL": float("inf")},
        {"TTL": 1e300},
        {"TTL": MAX_TTL + 1},
        {"TTL": False},
    ],
)
@pytest.mark.parametrize("kind", ["memory", "redis"])
def test_broker_refuses_a_bad_replay_setting(
    monkeypatch: pytest.MonkeyPatch, kind: str, replay: object
) -> None:
    _Harness(kind, monkeypatch, asynchronous=False)
    broker_class = MemoryBroker if kind == "memory" else RedisBroker
    with (
        override_settings(NEXT_FRAMEWORK={"REALTIME": {"REPLAY": replay}}),
        pytest.raises(ImproperlyConfigured, match=r"REALTIME\.REPLAY"),
    ):
        broker_class()


def test_brokers_read_the_replay_defaults_without_a_replay_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _Harness("redis", monkeypatch, asynchronous=False)
    with override_settings(NEXT_FRAMEWORK={"REALTIME": {}}):
        for broker in (MemoryBroker(), RedisBroker()):
            assert (broker.replay_size, broker.replay_ttl) == (100, 300.0)
    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"REPLAY": {"SIZE": 7}}}):
        assert (MemoryBroker().replay_size, MemoryBroker().replay_ttl) == (7, 300.0)


@pytest.mark.parametrize("kind", ["memory", "redis"])
def test_broker_replay_keyword_wins_over_the_replay_setting(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    _Harness("redis", monkeypatch, asynchronous=False)
    broker_class = MemoryBroker if kind == "memory" else RedisBroker
    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"REPLAY": "invalid"}}):
        broker = broker_class(replay={"SIZE": 4, "TTL": 9})
        assert (broker.replay_size, broker.replay_ttl) == (4, 9.0)
        default = broker_class(replay={})
        assert (default.replay_size, default.replay_ttl) == (100, 300.0)


@pytest.mark.parametrize("kind", ["memory", "redis"])
def test_broker_refuses_a_bad_replay_keyword(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    _Harness("redis", monkeypatch, asynchronous=False)
    broker_class = MemoryBroker if kind == "memory" else RedisBroker
    with pytest.raises(ImproperlyConfigured, match=r"REALTIME\.REPLAY"):
        broker_class(replay={"SIZE": -1})


def test_redis_log_lives_under_the_prefix_with_the_ttl_and_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _Harness("redis", monkeypatch, asynchronous=False)
    with override_settings(
        NEXT_FRAMEWORK={
            "REALTIME": {
                "BROKER": {
                    "BACKEND": "next.realtime.RedisBroker",
                    "OPTIONS": {"PREFIX": "app:rt:"},
                },
                "REPLAY": {"SIZE": 2, "TTL": 1.5},
            }
        }
    ):
        broker = RedisBroker({"OPTIONS": {"PREFIX": "app:rt:"}})
    _publish(broker, 3)
    assert harness.redis.llen("app:rt:log:{poll:1}") == 2
    assert harness.redis.pttl("app:rt:log:{poll:1}") == 1500
    assert harness.redis.exists("next:log:{poll:1}") == 0


def test_redis_accepts_the_longest_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    harness = _Harness("redis", monkeypatch, asynchronous=False)
    broker = harness.broker(ttl=MAX_TTL)
    _publish(broker, 1)
    assert harness.redis.pttl("next:log:{poll:1}") == MAX_TTL * 1000


@pytest.mark.parametrize("lose", ["counter", "epoch"])
def test_redis_epoch_change_clears_the_log_in_the_publish_script(
    monkeypatch: pytest.MonkeyPatch, lose: str
) -> None:
    harness = _Harness("redis", monkeypatch, asynchronous=False)
    broker = harness.broker()
    first, *_ = _publish(broker, 2)
    if lose == "counter":
        harness.redis.delete("next:seq:{poll:1}")
    else:
        harness.redis.hdel("next:seq:{poll:1}", "epoch")
    restarted = broker.publish("poll:1", refresh_envelope())
    assert restarted.epoch != first.epoch
    assert harness.redis.llen("next:log:{poll:1}") == 1
    assert broker.replay("poll:1", Position(first.epoch, 1)) == INCOMPLETE
    fresh = broker.replay("poll:1", Position(restarted.epoch, restarted.seq - 1))
    assert [message.seq for message in fresh.messages] == [restarted.seq]


def test_redis_replay_skips_a_malformed_log_entry(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    harness = _Harness("redis", monkeypatch, asynchronous=False)
    broker = harness.broker()
    first, *_ = _publish(broker, 2)
    harness.redis.lpush("next:log:{poll:1}", "not json")
    replay = broker.replay("poll:1", Position(first.epoch, 1))
    assert _seqs(replay) == [2]
    assert "malformed realtime message" in caplog.text


def test_redis_async_publish_writes_the_same_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _Harness("redis", monkeypatch, asynchronous=True)
    broker = harness.broker()

    async def scenario() -> Replay:
        first = await broker.apublish("poll:1", refresh_envelope())
        await broker.apublish("poll:1", refresh_envelope())
        return await broker.areplay("poll:1", Position(first.epoch, 0))

    assert _seqs(asyncio.run(scenario())) == [1, 2]


def test_redis_replay_wraps_transport_errors(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    harness = _Harness("redis", monkeypatch, asynchronous=mode == "async")
    broker = harness.broker()
    harness.server.connected = False
    with pytest.raises(BrokerConnectionError):
        harness.replay(broker, "poll:1", Position(EPOCH, 0))


def _message(seq: int, epoch: str = EPOCH) -> Message:
    return Message("poll:1", seq, refresh_envelope(), 1.0, epoch)


@pytest.mark.parametrize(
    ("after", "current", "logged", "expected"),
    [
        (Position(EPOCH, 1), Position(EPOCH, 3), [2, 3], [2, 3]),
        (Position(EPOCH, 1), Position(EPOCH, 3), [3], None),
        (Position(EPOCH, 1), Position(EPOCH, 3), [], None),
        (Position(EPOCH, 1), Position(EPOCH, 3), [2, 2], None),
        (Position(EPOCH, 1), Position(EPOCH, 4), [2, 2, 4], None),
        (Position(EPOCH, 1), Position(EPOCH, 4), [2, 4, 3], None),
        (Position(EPOCH, 3), Position(EPOCH, 3), [], []),
        (Position(EPOCH, 4), Position(EPOCH, 3), [], None),
        (Position("f" * 16, 1), Position(EPOCH, 3), [2, 3], None),
    ],
    ids=[
        "covered",
        "gap",
        "empty",
        "duplicate",
        "duplicate-over-a-hole",
        "out-of-order",
        "current",
        "ahead",
        "epoch",
    ],
)
def test_replay_after_cuts_the_log_or_reports_the_gap(
    after: Position, current: Position, logged: list[int], expected: list[int] | None
) -> None:
    replay = replay_after(after, current, [_message(seq) for seq in logged])
    if expected is None:
        assert replay == INCOMPLETE
    else:
        assert replay.complete
        assert _seqs(replay) == expected


def test_replay_after_ignores_entries_of_another_epoch() -> None:
    logged = [_message(2, "f" * 16), _message(2), _message(3)]
    replay = replay_after(Position(EPOCH, 1), Position(EPOCH, 3), logged)
    assert _seqs(replay) == [2, 3]
    assert {message.epoch for message in replay.messages} == {EPOCH}


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (f"{EPOCH}:0", Position(EPOCH, 0)),
        (f"{EPOCH}:42", Position(EPOCH, 42)),
        (f"{EPOCH}:{'9' * 19}", Position(EPOCH, int("9" * 19))),
        (f"{EPOCH}:{'9' * 20}", None),
        (f"{EPOCH}:01", None),
        (f"{EPOCH}:-1", None),
        (f"{EPOCH}:", None),
        (f"{EPOCH}:4\n", None),
        (f"{EPOCH}:4 ", None),
        (f"{EPOCH.upper()}:4", None),
        (f"{EPOCH[:-1]}:4", None),
        ("0123456789abcdeg:4", None),
        (f"{EPOCH}0:4", None),
        (f"{EPOCH}:4:5", None),
        ("room:4", None),
        ("", None),
        ("x" * 4096, None),
        (None, None),
        (42, None),
    ],
)
def test_parse_event_id_accepts_only_the_exact_cursor_grammar(
    value: object, expected: Position | None
) -> None:
    assert parse_event_id(value) == expected


def test_format_event_id_round_trips_and_refuses_an_unparsable_epoch() -> None:
    assert format_event_id(EPOCH, 7) == f"{EPOCH}:7"
    assert parse_event_id(format_event_id(EPOCH, 7)) == Position(EPOCH, 7)
    assert format_event_id("", 7) is None
    assert format_event_id("e1\nid: 9", 7) is None


class _ScriptedBroker:
    def __init__(self, replays: dict[str, Replay | Exception]) -> None:
        self.replays = replays
        self.calls: list[tuple[str, Position]] = []

    def _answer(self, topic: str, after: Position) -> Replay:
        self.calls.append((topic, after))
        answer = self.replays[topic]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def replay(self, topic: str, after: Position) -> Replay:
        return self._answer(topic, after)

    async def areplay(self, topic: str, after: Position) -> Replay:
        return self._answer(topic, after)


def _catch_up(
    mode: str, broker: object, cursors: dict, positions: dict
) -> tuple[Message, ...]:
    if mode == "async":
        return asyncio.run(acatch_up(broker, cursors, positions))
    return catch_up(broker, cursors, positions)


@pytest.fixture(params=["sync", "async"])
def mode(request: pytest.FixtureRequest) -> str:
    return request.param


def test_catch_up_stops_at_the_position_snapshot(mode: str) -> None:
    broker = _ScriptedBroker(
        {
            "a": Replay((_message(2), _message(3), _message(4)), complete=True),
            "b": INCOMPLETE,
        }
    )
    missed = _catch_up(
        mode,
        broker,
        {"a": Position(EPOCH, 1), "b": Position(EPOCH, 1)},
        {"a": Position(EPOCH, 3), "b": Position(EPOCH, 5)},
    )
    assert [message.seq for message in missed] == [2, 3]
    assert broker.calls == [("a", Position(EPOCH, 1)), ("b", Position(EPOCH, 1))]


@pytest.mark.parametrize(
    ("cursor", "position"),
    [
        (Position(EPOCH, 3), Position(EPOCH, 3)),
        (Position(EPOCH, 4), Position(EPOCH, 3)),
        (Position("f" * 16, 1), Position(EPOCH, 3)),
    ],
    ids=["current", "ahead", "epoch"],
)
def test_catch_up_skips_a_cursor_that_trails_nothing(
    mode: str, cursor: Position, position: Position
) -> None:
    broker = _ScriptedBroker({})
    assert _catch_up(mode, broker, {"a": cursor}, {"a": position}) == ()
    assert broker.calls == []


@pytest.mark.parametrize(
    "answer",
    [
        Replay((_message(2),), complete=True),
        Replay((_message(2, "f" * 16), _message(3, "f" * 16)), complete=True),
        Replay((), complete=True),
        BrokerConnectionError("down"),
    ],
    ids=["short", "foreign-epoch", "empty", "connection"],
)
def test_catch_up_replays_nothing_short_of_the_snapshot(
    mode: str, answer: Replay | Exception
) -> None:
    broker = _ScriptedBroker({"a": answer})
    cursors = {"a": Position(EPOCH, 1)}
    assert _catch_up(mode, broker, cursors, {"a": Position(EPOCH, 3)}) == ()


def test_catch_up_needs_a_cursor_on_a_snapshot_topic_and_a_replaying_broker(
    mode: str,
) -> None:
    broker = _ScriptedBroker({})
    cursors = {"other": Position(EPOCH, 1)}
    assert _catch_up(mode, broker, cursors, {"a": Position(EPOCH, 3)}) == ()
    assert _catch_up(mode, broker, {}, {"a": Position(EPOCH, 3)}) == ()
    assert _catch_up(mode, object(), cursors, {"other": Position(EPOCH, 3)}) == ()
    assert broker.calls == []
