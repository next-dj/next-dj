import asyncio
from collections.abc import Callable

import pytest

from next.partial import Patches
from next.realtime import WebSocketApp
from next.realtime.access import Access, AccessCheck
from next.realtime.backends import BrokerBackend, MemoryBroker
from next.realtime.errors import BrokerConnectionError
from next.realtime.frames import Close
from next.realtime.manager import broker_manager
from next.realtime.messages import Message, Position
from next.realtime.replay import Replay
from next.realtime.socket import Handshake, TopicSocket
from next.testing.realtime import (
    MemoryPort,
    SocketTicket,
    WebsocketCommunicator,
    override_websocket_mount,
)
from tests.support.realtime import (
    PlainBroker,
    ResumingClient,
    issue_session_ticket,
    refresh_envelope,
)


_HERE = "tests.realtime.test_socket_replay"
_TOPIC = "room:42"
_STALE = "f" * 16


def skip_second(message: Message) -> Patches | None:
    if message.seq == 2:
        return None
    return Patches(None).event("room:update", {"seq": message.seq})


def _published(broker: BrokerBackend, count: int) -> str:
    first = broker.publish(_TOPIC, refresh_envelope())
    for _ in range(count - 1):
        broker.publish(_TOPIC, refresh_envelope())
    return first.epoch


def _cursor(epoch: str, seq: int) -> dict[str, object]:
    return {_TOPIC: {"epoch": epoch, "seq": seq}}


def _trace(frame: dict[str, object]) -> object:
    if frame["type"] == "patches":
        return frame["data"]["seq"]
    if frame["type"] == "hello":
        return ("hello", frame["topics"][_TOPIC]["seq"])
    if frame["type"] == "skip":
        return ("skip", frame["seq"])
    return frame["type"]


async def _frames(port: MemoryPort, count: int) -> list[dict[str, object]]:
    async with asyncio.timeout(5):
        return [await port.receive_json() for _ in range(count)]


async def _served_frames(
    broker: BrokerBackend,
    ticket: SocketTicket,
    last_seq: dict[str, object],
    count: int,
    *,
    live: int = 0,
) -> tuple[list[dict[str, object]], MemoryPort]:
    port = MemoryPort()
    handshake = Handshake(ticket.credential, None, None)
    socket = TopicSocket(port, handshake, broker=broker, heartbeat=60)
    task = asyncio.create_task(socket.serve(port.inbound()))
    port.push_json(ticket.subscribe_frame(last_seq=last_seq))
    frames = await _frames(port, count)
    for _ in range(live):
        await broker.apublish(_TOPIC, refresh_envelope())
    frames.extend(await _frames(port, live))
    port.disconnect()
    async with asyncio.timeout(5):
        await task
    return frames, port


async def _served(
    broker: BrokerBackend,
    ticket: SocketTicket,
    last_seq: dict[str, object],
    count: int,
    *,
    live: int = 0,
) -> tuple[list[object], MemoryPort]:
    frames, port = await _served_frames(broker, ticket, last_seq, count, live=live)
    return [_trace(frame) for frame in frames], port


@pytest.mark.django_db(transaction=True)
def test_cursor_in_the_current_epoch_replays_the_gap_before_hello(
    make_broker: Callable[..., BrokerBackend], session_ticket: SocketTicket
) -> None:
    broker = make_broker()
    epoch = _published(broker, 3)
    traced, _ = asyncio.run(
        _served(broker, session_ticket, _cursor(epoch, 1), 3, live=1)
    )
    assert traced == [2, 3, ("hello", 3), 4]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "last_seq",
    [_cursor(_STALE, 1), {_TOPIC: 1}, {"room:99": {"epoch": _STALE, "seq": 1}}],
    ids=["stale-epoch", "bare-seq", "foreign-topic"],
)
def test_cursor_without_a_matching_epoch_goes_straight_to_hello(
    make_broker: Callable[..., BrokerBackend],
    session_ticket: SocketTicket,
    last_seq: dict[str, object],
) -> None:
    broker = make_broker()
    _published(broker, 3)
    traced, _ = asyncio.run(_served(broker, session_ticket, last_seq, 1, live=1))
    assert traced == [("hello", 3), 4]


@pytest.mark.django_db(transaction=True)
def test_gap_longer_than_the_log_goes_straight_to_hello(
    make_broker: Callable[..., BrokerBackend], session_ticket: SocketTicket
) -> None:
    broker = make_broker(size=2)
    epoch = _published(broker, 5)
    traced, _ = asyncio.run(
        _served(broker, session_ticket, _cursor(epoch, 1), 1, live=1)
    )
    assert traced == [("hello", 5), 6]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("step", "expected"),
    [("apositions", [2, 3, 4, ("hello", 4), 4]), ("areplay", [2, 3, ("hello", 3), 4])],
    ids=["before-snapshot", "during-replay"],
)
def test_live_message_around_replay_is_neither_lost_nor_applied_twice(
    make_broker: Callable[..., BrokerBackend],
    session_ticket: SocketTicket,
    monkeypatch: pytest.MonkeyPatch,
    step: str,
    expected: list[object],
) -> None:
    broker = make_broker()
    epoch = _published(broker, 3)
    original = getattr(broker, step)

    async def publishing(*args: object) -> object:
        monkeypatch.setattr(broker, step, original)
        await broker.apublish(_TOPIC, refresh_envelope())
        return await original(*args)

    monkeypatch.setattr(broker, step, publishing)
    frames, _ = asyncio.run(
        _served_frames(broker, session_ticket, _cursor(epoch, 1), len(expected))
    )
    assert [_trace(frame) for frame in frames] == expected
    client = ResumingClient({_TOPIC: (epoch, 1)})
    for frame in frames:
        if frame["type"] == "hello":
            client.hello(frame)
        else:
            client.patches(frame["data"])
    assert client.applied == [(_TOPIC, epoch, seq) for seq in (2, 3, 4)]
    assert client.dropped == [(_TOPIC, epoch, 4)] * (len(expected) - 4)
    assert client.gaps == []
    assert client.cursors == {_TOPIC: (epoch, 4)}


@pytest.mark.django_db(transaction=True)
def test_replayed_messages_pass_through_the_ticket_transform(
    make_broker: Callable[..., BrokerBackend],
) -> None:
    ticket = issue_session_ticket(transform_ref=f"{_HERE}.skip_second")
    broker = make_broker()
    epoch = _published(broker, 3)

    async def scenario() -> list[dict[str, object]]:
        port = MemoryPort()
        socket = TopicSocket(
            port, Handshake(ticket.credential, None, None), broker=broker
        )
        task = asyncio.create_task(socket.serve(port.inbound()))
        port.push_json(ticket.subscribe_frame(last_seq=_cursor(epoch, 1)))
        async with asyncio.timeout(5):
            sent = [await port.receive_json() for _ in range(3)]
        port.disconnect()
        async with asyncio.timeout(5):
            await task
        return sent

    skipped, replayed, hello = asyncio.run(scenario())
    assert skipped == {"type": "skip", "topic": _TOPIC, "epoch": epoch, "seq": 2}
    assert replayed["data"]["seq"] == 3
    assert replayed["data"]["ops"][0]["name"] == "room:update"
    assert hello["type"] == "hello"


@pytest.mark.django_db(transaction=True)
def test_access_revoked_during_replay_closes_before_hello(
    make_broker: Callable[..., BrokerBackend],
    session_ticket: SocketTicket,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker = make_broker()
    epoch = _published(broker, 3)
    verdicts = iter([Access.GRANTED, Access.GRANTED, Access.TOPIC_DENIED])

    async def acheck(self: AccessCheck) -> Access:
        return next(verdicts)

    monkeypatch.setattr(AccessCheck, "acheck", acheck)
    traced, port = asyncio.run(_served(broker, session_ticket, _cursor(epoch, 1), 2))
    assert traced == [2, "error"]
    assert port.close_code == Close.TOPIC_DENIED


@pytest.mark.django_db(transaction=True)
def test_broker_failure_during_replay_falls_back_to_hello(
    make_broker: Callable[..., BrokerBackend],
    session_ticket: SocketTicket,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker = make_broker()
    epoch = _published(broker, 3)
    attempts: list[Position] = []

    async def areplay(topic: str, after: Position) -> Replay:
        attempts.append(after)
        raise BrokerConnectionError(topic)

    monkeypatch.setattr(broker, "areplay", areplay)
    traced, _ = asyncio.run(
        _served(broker, session_ticket, _cursor(epoch, 1), 1, live=1)
    )
    assert attempts == [Position(epoch, 1)]
    assert traced == [("hello", 3), 4]


@pytest.mark.django_db(transaction=True)
def test_broker_without_replay_goes_straight_to_hello(
    session_ticket: SocketTicket,
) -> None:
    broker = PlainBroker()
    epoch = _published(broker, 3)
    traced, _ = asyncio.run(
        _served(broker, session_ticket, _cursor(epoch, 1), 1, live=1)
    )
    assert traced == [("hello", 3), 4]


@pytest.mark.django_db(transaction=True)
def test_app_replays_from_the_default_broker_over_the_websocket(
    monkeypatch: pytest.MonkeyPatch, session_ticket: SocketTicket
) -> None:
    broker = MemoryBroker()
    monkeypatch.setattr(broker_manager, "_backend", broker)
    epoch = _published(broker, 2)

    async def scenario() -> list[object]:
        communicator = WebsocketCommunicator(
            WebSocketApp(),
            "/_next/ws/",
            headers=[
                (b"host", b"testserver"),
                (b"origin", b"http://testserver"),
                session_ticket.cookie,
            ],
        )
        connected, _ = await communicator.connect()
        assert connected
        await communicator.send_json_to(
            session_ticket.subscribe_frame(last_seq=_cursor(epoch, 1))
        )
        traced = [_trace(await communicator.receive_json_from()) for _ in range(2)]
        await communicator.disconnect()
        return traced

    with override_websocket_mount():
        assert asyncio.run(scenario()) == [2, ("hello", 2)]
