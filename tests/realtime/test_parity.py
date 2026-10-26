import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from functools import partial

import pytest
from django.test import AsyncRequestFactory

from next.realtime import (
    TopicStream,
    WebSocketApp,
    apublish,
    socket as socket_module,
    topics as topics_module,
)
from next.realtime.access import AccessCheck, SocketAccessCheck
from next.realtime.messages import Message
from next.testing import SseReader, memory_broker
from next.testing.realtime import (
    SocketTicket,
    WebSocketCommunicator,
    override_websocket_mount,
)
from tests.support.realtime import ServerClock, issue_session_ticket, refresh_envelope


_HERE = "tests.realtime.test_parity"
_TOPIC = "room:42"
_GRANTS = {"room": True}
_Observation = tuple[object, ...]


def toggled_authorizer(_user: object, topic: str) -> bool:
    return _GRANTS["room"] and topic.startswith("room:")


def skip_everything(_message: Message) -> None:
    return None


@dataclass
class _Wire:
    hello: dict[str, object]
    receive: Callable[[], Awaitable[_Observation]]


@pytest.fixture(autouse=True)
def _grant_the_room() -> None:
    _GRANTS["room"] = True


@pytest.fixture()
def clock(monkeypatch: pytest.MonkeyPatch) -> ServerClock:
    clock = ServerClock(0.0)
    monkeypatch.setattr(
        socket_module, "SocketAccessCheck", partial(SocketAccessCheck, clock=clock)
    )
    monkeypatch.setattr(topics_module, "AccessCheck", partial(AccessCheck, clock=clock))
    return clock


@asynccontextmanager
async def _over_sse() -> AsyncIterator[_Wire]:
    stream = TopicStream(
        AsyncRequestFactory().get("/rooms/42/stream/"),
        _TOPIC,
        transform=skip_everything,
        authorize=toggled_authorizer,
        heartbeat_seconds=15,
    )
    async with SseReader(stream) as events:
        hello = await anext(events)
        assert hello.event == "next-hello"

        async def receive() -> _Observation:
            event = await anext(events)
            body = event.json()
            if event.event == "next-skip":
                return ("skip", body["topic"], body["seq"])
            assert event.event == "next-end"
            return ("end", body["code"])

        yield _Wire(hello.json(), receive)


@asynccontextmanager
async def _over_websocket(ticket: SocketTicket) -> AsyncIterator[_Wire]:
    communicator = WebSocketCommunicator(
        WebSocketApp(),
        "/_next/ws/",
        headers=[
            (b"host", b"testserver"),
            (b"origin", b"http://testserver"),
            ticket.cookie,
        ],
    )
    connected, _ = await communicator.connect()
    assert connected
    hello = await communicator.subscribe(ticket)

    async def receive() -> _Observation:
        frame = await communicator.receive_json_from()
        if frame["type"] == "skip":
            return ("skip", frame["topic"], frame["seq"])
        assert frame["type"] == "error"
        return ("end", frame["code"])

    try:
        yield _Wire(hello, receive)
    finally:
        await communicator.disconnect()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("transport", ["sse", "ws"])
def test_both_transports_say_hello_skip_and_end_on_a_revoked_grant(
    transport: str, clock: ServerClock
) -> None:
    ticket = issue_session_ticket(
        authorizer_ref=f"{_HERE}.toggled_authorizer",
        transform_ref=f"{_HERE}.skip_everything",
    )

    async def scenario() -> tuple[object, list[_Observation]]:
        wire = _over_sse() if transport == "sse" else _over_websocket(ticket)
        async with wire as opened:
            await apublish(_TOPIC, refresh_envelope())
            skipped = await opened.receive()
            _GRANTS["room"] = False
            clock.advance(10_000)
            await apublish(_TOPIC, refresh_envelope())
            ended = await opened.receive()
        return opened.hello["topics"], [skipped, ended]

    with memory_broker(), override_websocket_mount():
        topics, observed = asyncio.run(scenario())

    assert topics == {_TOPIC: {"epoch": "", "seq": 0}}
    assert observed == [("skip", _TOPIC, 1), ("end", "auth")]
