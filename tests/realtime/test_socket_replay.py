import asyncio
from collections.abc import Callable

import pytest

from next.realtime.backends import BrokerBackend
from next.realtime.errors import BrokerConnectionError
from next.realtime.messages import Position
from next.realtime.replay import Replay
from next.realtime.socket import Handshake, TopicSocket
from next.testing.realtime import MemoryPort, SocketTicket
from tests.support.realtime import ROOM_TOPIC, published, refresh_envelope, socket_frame


_STALE = "f" * 16


def _cursor(epoch: str, seq: int) -> dict[str, object]:
    return {ROOM_TOPIC: {"epoch": epoch, "seq": seq}}


async def _frames(port: MemoryPort, count: int) -> list[object]:
    async with asyncio.timeout(5):
        return [socket_frame(await port.receive_json()).trace for _ in range(count)]


async def _served(
    broker: BrokerBackend,
    ticket: SocketTicket,
    last_seq: dict[str, object],
    count: int,
    *,
    live: int = 0,
) -> list[object]:
    port = MemoryPort()
    handshake = Handshake(ticket.credential, None, None)
    socket = TopicSocket(port, handshake, broker=broker, heartbeat=60)
    task = asyncio.create_task(socket.serve(port.inbound()))
    port.push_json(ticket.subscribe_frame(last_seq=last_seq))
    traced = await _frames(port, count)
    for _ in range(live):
        await broker.apublish(ROOM_TOPIC, refresh_envelope())
    traced.extend(await _frames(port, live))
    port.disconnect()
    async with asyncio.timeout(5):
        await task
    return traced


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "last_seq",
    [_cursor(_STALE, 1), {ROOM_TOPIC: 1}, {"room:99": {"epoch": _STALE, "seq": 1}}],
    ids=["stale-epoch", "bare-seq", "foreign-topic"],
)
def test_cursor_without_a_matching_epoch_goes_straight_to_hello(
    make_broker: Callable[..., BrokerBackend],
    session_ticket: SocketTicket,
    last_seq: dict[str, object],
) -> None:
    broker = make_broker()
    published(broker, 3)
    traced = asyncio.run(_served(broker, session_ticket, last_seq, 1, live=1))
    assert traced == [("hello", 3), 4]


@pytest.mark.django_db(transaction=True)
def test_gap_longer_than_the_log_goes_straight_to_hello(
    make_broker: Callable[..., BrokerBackend], session_ticket: SocketTicket
) -> None:
    broker = make_broker(size=2)
    epoch = published(broker, 5)
    traced = asyncio.run(_served(broker, session_ticket, _cursor(epoch, 1), 1, live=1))
    assert traced == [("hello", 5), 6]


@pytest.mark.django_db(transaction=True)
def test_broker_failure_during_replay_falls_back_to_hello(
    make_broker: Callable[..., BrokerBackend],
    session_ticket: SocketTicket,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker = make_broker()
    epoch = published(broker, 3)
    attempts: list[Position] = []

    async def areplay(topic: str, after: Position) -> Replay:
        attempts.append(after)
        raise BrokerConnectionError(topic)

    monkeypatch.setattr(broker, "areplay", areplay)
    traced = asyncio.run(_served(broker, session_ticket, _cursor(epoch, 1), 1, live=1))
    assert attempts == [Position(epoch, 1)]
    assert traced == [("hello", 3), 4]
