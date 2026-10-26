import json
import re
from collections.abc import Generator
from io import StringIO

import fakeredis
import pytest
import redis
from django.core.management import CommandError, call_command
from django.core.management.base import SystemCheckError
from django.test import override_settings

from next.management.commands.realtime import Command
from next.partial.patches import Patches
from next.realtime.backends import BrokerBackend, MemoryBroker
from next.realtime.errors import BrokerConnectionError
from next.realtime.manager import broker_manager


TOPIC = "poll:1"
CURSOR = re.compile(r"[0-9a-f]{16}:\d+")
THIS = "tests.management.commands.test_realtime"


class PlainBroker(BrokerBackend):
    """Number messages in memory, with neither a replay log nor presence."""

    def __init__(self, config: object = None) -> None:
        """Wrap a fresh memory broker."""
        super().__init__()
        self.inner = MemoryBroker()

    def publish(self, topic, envelope):
        return self.inner.publish(topic, envelope)

    async def apublish(self, topic, envelope):
        return self.inner.publish(topic, envelope)

    def subscribe(self, topics, *, timeout):
        return self.inner.subscribe(topics, timeout=timeout)

    def asubscribe(self, topics, *, timeout):
        return self.inner.asubscribe(topics, timeout=timeout)

    def positions(self, topics):
        return self.inner.positions(topics)

    async def apositions(self, topics):
        return self.inner.positions(topics)


class DownBroker(PlainBroker):
    """Fail every read the way a broker that lost its connection does."""

    def positions(self, topics):
        msg = "Connection refused."
        raise BrokerConnectionError(msg)


def _broker(backend: str) -> override_settings:
    return override_settings(
        NEXT_FRAMEWORK={"REALTIME": {"BROKER": {"BACKEND": backend}}}
    )


@pytest.fixture(autouse=True)
def _reset_broker_manager() -> Generator[None, None, None]:
    broker_manager.reset()
    yield
    broker_manager.reset()


@pytest.fixture(params=["memory", "redis"])
def kind(request, monkeypatch) -> Generator[str, None, None]:
    """Configure a memory broker or a Redis broker on one fakeredis server."""
    server = fakeredis.FakeServer()

    def client(url: str, **kwargs: object) -> fakeredis.FakeRedis:
        return fakeredis.FakeRedis(server=server, decode_responses=True)

    monkeypatch.setattr(redis.Redis, "from_url", client)
    backend = {"memory": "MemoryBroker", "redis": "RedisBroker"}[request.param]
    with _broker(f"next.realtime.{backend}"):
        yield request.param


@pytest.fixture()
def shared(monkeypatch) -> Generator[None, None, None]:
    """Configure a Redis broker on one fakeredis server, a broker a server shares."""
    server = fakeredis.FakeServer()

    def client(url: str, **kwargs: object) -> fakeredis.FakeRedis:
        return fakeredis.FakeRedis(server=server, decode_responses=True)

    monkeypatch.setattr(redis.Redis, "from_url", client)
    with _broker("next.realtime.RedisBroker"):
        yield


def _run(*args: str) -> tuple[str, str]:
    out, err = StringIO(), StringIO()
    call_command("realtime", *args, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


def _publish_refresh(zone: str = "results") -> str:
    message = broker_manager.get().publish(
        TOPIC, Patches(None).refresh(zone=zone).envelope()
    )
    return f"{message.epoch}:{message.seq}"


class TestPublish:
    """`publish` sends zone refreshes or one event through `publish()`."""

    def test_publishes_refreshes(self, shared) -> None:
        out, _ = _run("publish", TOPIC, "--refresh", "results", "chart")
        match = re.fullmatch(
            rf"Published refresh results, refresh chart to {TOPIC} at ({CURSOR.pattern})\.\n",
            out,
        )
        assert match is not None
        position = broker_manager.get().positions([TOPIC])[TOPIC]
        assert match[1] == f"{position.epoch}:{position.seq}"

    def test_publishes_an_event_as_json(self, shared) -> None:
        out, _ = _run(
            "publish",
            TOPIC,
            "--event",
            "poll:votes",
            "--data",
            '{"total": 3}',
            "--json",
        )
        published = json.loads(out)
        assert published["topic"] == TOPIC
        assert published["seq"] == 1
        assert published["envelope"]["ops"] == [
            {"op": "event", "name": "poll:votes", "detail": {"total": 3}}
        ]

    def test_publishes_an_event_without_data(self, shared) -> None:
        out, err = _run("publish", TOPIC, "--event", "poll:votes")
        assert out.startswith(f"Published event poll:votes to {TOPIC} at ")
        assert err == ""

    def test_refuses_a_memory_broker(self) -> None:
        with pytest.raises(
            CommandError, match="MemoryBroker keeps its topics inside this process"
        ):
            _run("publish", TOPIC, "--refresh", "results")
        assert broker_manager.get().positions([TOPIC])[TOPIC].seq == 0

    @pytest.mark.parametrize(
        ("args", "error"),
        [
            (("bad topic!", "--refresh", "a"), "Invalid realtime topic 'bad topic!'"),
            (("next:x", "--refresh", "a"), "Invalid realtime topic 'next:x'"),
            ((TOPIC, "--refresh", ""), "A zone to refresh needs a name."),
            ((TOPIC, "--event", "next:ready"), 'Event name "next:ready" is reserved'),
            ((TOPIC, "--event", "e", "--data", "{"), "--data is not JSON."),
            ((TOPIC, "--event", "e", "--data", "[1]"), "--data must be a JSON object."),
            ((TOPIC, "--event", "e", "--data", '{"n": NaN}'), "not JSON serializable"),
            ((TOPIC, "--refresh", "a", "--data", "{}"), "--data needs --event."),
            ((TOPIC,), "one of the arguments --refresh --event is required"),
            ((TOPIC, "--refresh", "a", "--event", "e"), "not allowed with argument"),
        ],
    )
    def test_refuses_with_the_reason(self, shared, args, error) -> None:
        with pytest.raises(CommandError, match=re.escape(error)):
            _run("publish", *args)
        assert broker_manager.get().positions([TOPIC])[TOPIC].seq == 0


class TestPositions:
    """`positions` prints each topic's cursor, `-` for one never published."""

    def test_memory_broker_warns_it_reads_no_server(self, kind) -> None:
        _, err = _run("positions", TOPIC)
        assert ("MemoryBroker keeps its topics inside this process" in err) is (
            kind == "memory"
        )

    def test_prints_a_table(self, kind) -> None:
        cursor = _publish_refresh()
        out, _ = _run("positions", TOPIC, "poll:2")
        assert out.splitlines() == [
            "Topic   Position",
            f"{TOPIC}  {cursor}",
            "poll:2  -",
        ]

    def test_prints_json(self, kind) -> None:
        cursor = _publish_refresh()
        out, _ = _run("positions", TOPIC, "poll:2", "--json")
        epoch, seq = cursor.split(":")
        assert json.loads(out) == {
            TOPIC: {"epoch": epoch, "seq": int(seq)},
            "poll:2": None,
        }

    def test_refuses_an_invalid_topic(self, kind) -> None:
        with pytest.raises(CommandError, match="Invalid realtime topic"):
            _run("positions", TOPIC, "bad topic!")

    def test_reports_an_unreachable_broker(self) -> None:
        with (
            _broker(f"{THIS}.DownBroker"),
            pytest.raises(
                CommandError,
                match=r"The realtime broker is unreachable\. Connection refused\.",
            ),
        ):
            _run("positions", TOPIC)

    def test_reports_a_misconfigured_broker(self) -> None:
        with (
            _broker("missing.realtime.Backend"),
            pytest.raises(CommandError, match="Could not import the configured"),
        ):
            _run("positions", TOPIC)

    def test_exits_non_zero_from_the_command_line(self, kind, capsys) -> None:
        with pytest.raises(SystemExit) as exited:
            Command().run_from_argv(["manage.py", "realtime", "positions", "bad!"])
        assert exited.value.code == 1
        assert "Invalid realtime topic 'bad!'" in capsys.readouterr().err


class TestSystemChecks:
    """The command runs the system checks first, so a bad broker never connects."""

    def test_a_bad_broker_fails_the_checks(self) -> None:
        config = {"REALTIME": {"BROKER": {"BACKEND": "missing.module.Broker"}}}
        with (
            override_settings(NEXT_FRAMEWORK=config),
            pytest.raises(SystemCheckError, match=r"next\.E184"),
        ):
            call_command("realtime", "positions", TOPIC, skip_checks=False)


class TestPresence:
    """`presence` prints the attendee count and the members listed."""

    def test_prints_the_count_and_members(self, kind) -> None:
        backend = broker_manager.get()
        backend.join(TOPIC, "a", {"name": "Ada"}, 30)
        backend.join(TOPIC, "b", None, 30)
        out, _ = _run("presence", TOPIC)
        assert out.splitlines() == [f"2 present on {TOPIC}", '  {"name": "Ada"}']

    def test_prints_json(self, kind) -> None:
        broker_manager.get().join(TOPIC, "a", {"name": "Ada"}, 30)
        out, _ = _run("presence", TOPIC, "--json")
        assert json.loads(out) == {
            "topic": TOPIC,
            "count": 1,
            "members": [{"name": "Ada"}],
        }

    def test_prints_an_empty_topic(self, kind) -> None:
        out, _ = _run("presence", TOPIC)
        assert out == f"0 present on {TOPIC}\n"

    def test_refuses_an_invalid_topic(self, kind) -> None:
        with pytest.raises(CommandError, match="Invalid realtime topic"):
            _run("presence", "bad topic!")

    def test_refuses_a_broker_without_presence(self) -> None:
        with (
            _broker(f"{THIS}.PlainBroker"),
            pytest.raises(CommandError, match=r"PlainBroker keeps no presence\."),
        ):
            _run("presence", TOPIC)


class TestReplay:
    """`replay` prints what the log holds after a cursor."""

    def test_prints_the_messages_after_the_cursor(self, kind) -> None:
        after = _publish_refresh("results")
        later = _publish_refresh("chart")
        out, _ = _run("replay", TOPIC, "--after", after)
        header, row = out.splitlines()
        assert header.split() == ["Position", "Published", "Patches"]
        cursor, published, *patches = row.split()
        assert cursor == later
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\+00:00", published)
        assert patches == ["refresh", "chart"]

    def test_prints_json(self, kind) -> None:
        after = _publish_refresh()
        _publish_refresh("chart")
        out, _ = _run("replay", TOPIC, "--after", after, "--json")
        replay = json.loads(out)
        assert replay["topic"] == TOPIC
        assert replay["after"] == after
        assert replay["complete"] is True
        assert [m["seq"] for m in replay["messages"]] == [2]

    def test_says_when_nothing_follows(self, kind) -> None:
        after = _publish_refresh()
        out, _ = _run("replay", TOPIC, "--after", after)
        assert out == f"Nothing on {TOPIC} after {after}.\n"

    def test_says_when_the_log_cannot_close_the_gap(self, kind) -> None:
        _publish_refresh()
        out, _ = _run("replay", TOPIC, "--after", "0123456789abcdef:0")
        assert out == (
            f"The log of {TOPIC} cannot close the gap after 0123456789abcdef:0, so "
            "a subscriber there resyncs.\n"
        )

    def test_names_a_patch_without_a_subject_by_its_operation(self, kind) -> None:
        after = _publish_refresh()
        broker_manager.get().publish(
            TOPIC, Patches(None).remove({"id": "card"}).envelope()
        )
        out, _ = _run("replay", TOPIC, "--after", after)
        assert out.splitlines()[1].endswith("  remove")

    @pytest.mark.parametrize("after", ["1", "abc:1", "0123456789abcdef:-1"])
    def test_refuses_a_malformed_cursor(self, kind, after) -> None:
        with pytest.raises(CommandError, match="--after takes an <epoch>:<seq>"):
            _run("replay", TOPIC, "--after", after)

    def test_refuses_an_invalid_topic(self, kind) -> None:
        with pytest.raises(CommandError, match="Invalid realtime topic"):
            _run("replay", "bad topic!", "--after", "0123456789abcdef:0")

    def test_refuses_a_broker_without_a_log(self) -> None:
        with (
            _broker(f"{THIS}.PlainBroker"),
            pytest.raises(CommandError, match=r"PlainBroker keeps no replay log\."),
        ):
            _run("replay", TOPIC, "--after", "0123456789abcdef:0")
