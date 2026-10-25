import asyncio
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields
from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.test import override_settings

from next.realtime import Upstream, checks, handlers
from next.realtime.access import AccessCheck, SocketAccessCheck, TopicAuthorizer
from next.realtime.backends import MemoryBroker
from next.realtime.frames import Close
from next.realtime.handlers import HandlerRegistry, on
from next.realtime.socket import Handshake, TopicSocket
from next.realtime.tickets import TicketClaims
from next.testing.realtime import MemoryPort, SocketTicket
from tests.support.realtime import issue_session_ticket


_GRANTS = {"room": True}
_SEEN: list[tuple[str, object, str, bool]] = []


def _room_authorizer(_user: object, topic: str) -> bool:
    return _GRANTS["room"] and topic.startswith("room:")


_AUTHORIZER = "tests.realtime.test_upstream._room_authorizer"


@pytest.fixture(autouse=True)
def registry() -> Iterator[HandlerRegistry]:
    fresh = HandlerRegistry()
    _GRANTS["room"] = True
    _SEEN.clear()
    with (
        patch.object(handlers, "handler_registry", fresh),
        patch.object(checks, "handler_registry", fresh),
    ):
        yield fresh


def _register() -> None:
    @on("room:{room}", "typing")
    def typing(upstream: Upstream) -> None:
        main = threading.current_thread() is threading.main_thread()
        _SEEN.append((upstream.name, upstream.data, upstream.params["room"], main))

    @on("room:{room}", "cursor")
    async def cursor(upstream: Upstream) -> None:
        _SEEN.append((upstream.name, upstream.data, upstream.connection, False))

    @on("room:{room}", "boom")
    def boom(upstream: Upstream) -> None:
        msg = "handler failed"
        raise RuntimeError(msg)


def _ticket(*topics: str) -> SocketTicket:
    return issue_session_ticket(topics or ("room:42",), authorizer_ref=_AUTHORIZER)


class _Rechecking(TopicSocket):
    def access_check(
        self, claims: TicketClaims, authorizer: TopicAuthorizer | None
    ) -> AccessCheck:
        return SocketAccessCheck(
            claims.topics,
            authorizer=authorizer,
            session_key=self.handshake.credential,
            user=self.handshake.user,
            interval=0,
        )


def _socket(
    port: MemoryPort,
    ticket: SocketTicket,
    *,
    recheck: bool = False,
    clock: Callable[[], float] = time.time,
    socket_class: type[TopicSocket] = TopicSocket,
) -> TopicSocket:
    handshake = Handshake(ticket.credential, AnonymousUser(), None)
    chosen = _Rechecking if recheck else socket_class
    return chosen(port, handshake, broker=MemoryBroker(), heartbeat=60, clock=clock)


async def _frame(port: MemoryPort) -> dict[str, object]:
    async with asyncio.timeout(5):
        return await port.receive_json()


async def _open(
    port: MemoryPort,
    ticket: SocketTicket,
    *,
    recheck: bool = False,
    clock: Callable[[], float] = time.time,
    socket_class: type[TopicSocket] = TopicSocket,
) -> tuple[TopicSocket, asyncio.Task[None]]:
    socket = _socket(
        port, ticket, recheck=recheck, clock=clock, socket_class=socket_class
    )
    task = asyncio.create_task(socket.serve(port.inbound()))
    port.push_json(ticket.subscribe_frame())
    assert (await _frame(port))["type"] == "hello"
    return socket, task


async def _close(port: MemoryPort, task: asyncio.Task[None]) -> None:
    port.disconnect()
    async with asyncio.timeout(5):
        await task


def _up(name: str, data: object = None, *, topic: str = "room:42", **extra: object):
    return {"type": "up", "topic": topic, "name": name, "data": data, **extra}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("frame", "expected"),
    [
        (_up("typing", {"on": True}, id="1"), {"type": "ack", "id": "1", "ok": True}),
        (_up("cursor", [1, 2], id="2"), {"type": "ack", "id": "2", "ok": True}),
        (
            _up("typing", topic="room:7", id="3"),
            {"type": "ack", "id": "3", "ok": False, "error": "topic"},
        ),
        (
            _up("nobody", id="4"),
            {"type": "ack", "id": "4", "ok": False, "error": "unknown"},
        ),
        (
            _up("boom", id="5"),
            {"type": "ack", "id": "5", "ok": False, "error": "internal"},
        ),
        (
            _up("typing", "x" * 20000, id="6"),
            {"type": "ack", "id": "6", "ok": False, "error": "too_big"},
        ),
    ],
)
def test_an_event_with_an_id_is_acknowledged(
    frame: dict[str, object], expected: dict[str, object]
) -> None:
    _register()
    ticket = _ticket()

    async def scenario() -> None:
        port = MemoryPort()
        _, task = await _open(port, ticket)
        port.push_json(frame)
        assert await _frame(port) == expected
        await _close(port, task)

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_sync_and_async_handlers_get_the_event_and_its_connection() -> None:
    _register()
    ticket = _ticket()

    async def scenario() -> None:
        port = MemoryPort()
        socket, task = await _open(port, ticket)
        port.push_json(_up("typing", {"on": True}, id="1"))
        await _frame(port)
        port.push_json(_up("cursor", {"x": 3}, id="2"))
        await _frame(port)
        await _close(port, task)
        assert [
            ("typing", {"on": True}, "42", False),
            ("cursor", {"x": 3}, socket.connection_id, False),
        ] == _SEEN

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_an_event_without_an_id_gets_no_ack() -> None:
    _register()
    ticket = _ticket()

    async def scenario() -> None:
        port = MemoryPort()
        _, task = await _open(port, ticket)
        port.push_json(_up("typing", 1))
        port.push_json(_up("nobody", 2))
        port.push_json(_up("typing", 3, topic="room:9"))
        port.push_json(_up("typing", 4, id="last"))
        assert await _frame(port) == {"type": "ack", "id": "last", "ok": True}
        await _close(port, task)
        assert [entry[1] for entry in _SEEN] == [1, 4]
        assert len(port.sent) == 2

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_a_refused_authorizer_answers_denied_and_closes() -> None:
    _register()
    ticket = _ticket()

    async def scenario() -> None:
        port = MemoryPort()
        _, task = await _open(port, ticket, recheck=True)
        _GRANTS["room"] = False
        port.push_json(_up("typing", id="1"))
        assert await _frame(port) == {
            "type": "ack",
            "id": "1",
            "ok": False,
            "error": "denied",
        }
        assert await _frame(port) == {"type": "error", "code": "auth"}
        async with asyncio.timeout(5):
            await task
        assert port.close_code == Close.TOPIC_DENIED
        assert _SEEN == []

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_a_refused_event_without_an_id_still_closes() -> None:
    _register()
    ticket = _ticket()

    async def scenario() -> None:
        port = MemoryPort()
        _, task = await _open(port, ticket, recheck=True)
        _GRANTS["room"] = False
        port.push_json(_up("typing"))
        assert await _frame(port) == {"type": "error", "code": "auth"}
        async with asyncio.timeout(5):
            await task
        assert port.close_code == Close.TOPIC_DENIED

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_an_event_before_hello_waits_for_the_grant() -> None:
    _register()
    ticket = _ticket()

    async def scenario() -> None:
        port = MemoryPort()
        socket = _socket(port, ticket)
        task = asyncio.create_task(socket.serve(port.inbound()))
        port.push_json(ticket.subscribe_frame())
        port.push_json(_up("typing", id="1"))
        frames = [await _frame(port), await _frame(port)]
        assert {frame["type"] for frame in frames} == {"hello", "ack"}
        await _close(port, task)

    asyncio.run(scenario())


_TIGHT = {"REALTIME": {"UPSTREAM": {"RATE": 0.001, "BURST": 2}}}


@pytest.mark.django_db(transaction=True)
def test_an_empty_bucket_refuses_and_three_refusals_close_with_4429() -> None:
    _register()
    ticket = _ticket()

    async def scenario() -> None:
        port = MemoryPort()
        _, task = await _open(port, ticket)
        port.push_json(_up("typing", id="1"))
        port.push_json(_up("typing", id="2"))
        accepted = [await _frame(port), await _frame(port)]
        port.push_json(_up("typing", id="3"))
        port.push_json(_up("typing"))
        port.push_json(_up("typing", id="5"))
        refused = [await _frame(port) for _ in range(3)]
        async with asyncio.timeout(5):
            await task
        assert sorted(accepted, key=lambda frame: str(frame["id"])) == [
            {"type": "ack", "id": "1", "ok": True},
            {"type": "ack", "id": "2", "ok": True},
        ]
        assert refused == [
            {"type": "ack", "id": "3", "ok": False, "error": "limit"},
            {"type": "error", "code": "limit"},
            {"type": "error", "code": "limit"},
        ]
        assert port.close_code == Close.RATE_LIMITED

    with override_settings(NEXT_FRAMEWORK=_TIGHT):
        asyncio.run(scenario())


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


@pytest.mark.django_db(transaction=True)
def test_an_admitted_frame_resets_the_strikes() -> None:
    _register()
    ticket = _ticket()
    clock = _Clock()
    refill = [False, False, False, True, False, False, True]

    async def scenario() -> list[object]:
        port = MemoryPort()
        _, task = await _open(port, ticket, clock=clock)
        received = []
        for index, wait in enumerate(refill):
            if wait:
                clock.now += 1
            port.push_json(_up("typing", id=str(index)))
            received.append((await _frame(port))["ok"])
        await _close(port, task)
        assert port.close_code is None
        return received

    limits = {"REALTIME": {"UPSTREAM": {"RATE": 1, "BURST": 1}}}
    with override_settings(NEXT_FRAMEWORK=limits):
        assert asyncio.run(scenario()) == [True, False, False, True, False, False, True]


@pytest.mark.django_db(transaction=True)
def test_pong_and_unsupported_frames_draw_tokens() -> None:
    ticket = _ticket()

    async def scenario() -> None:
        port = MemoryPort()
        _, task = await _open(port, ticket)
        port.push_json({"type": "nope"})
        assert await _frame(port) == {"type": "error", "code": "unsupported"}
        port.push_json({"type": "pong"})
        port.push_json({"type": "nope"})
        port.push_json({"type": "pong"})
        port.push_json({"type": "nope"})
        assert await _frame(port) == {"type": "error", "code": "limit"}
        async with asyncio.timeout(5):
            await task
        assert port.close_code == Close.RATE_LIMITED

    limits = {"REALTIME": {"UPSTREAM": {"RATE": 0.001, "BURST": 1}}}
    with override_settings(NEXT_FRAMEWORK=limits):
        asyncio.run(scenario())


class _Routing(TopicSocket):
    async def handle(self, upstream: Upstream) -> bool:
        _SEEN.append((upstream.name, upstream.data, upstream.topic, False))
        return upstream.name == "mine"


@pytest.mark.django_db(transaction=True)
def test_a_subclass_routes_events_through_handle() -> None:
    _register()
    ticket = _ticket()

    async def scenario() -> list[dict[str, object]]:
        port = MemoryPort()
        _, task = await _open(port, ticket, socket_class=_Routing)
        port.push_json(_up("mine", 1, id="1"))
        first = await _frame(port)
        port.push_json(_up("typing", 2, id="2"))
        second = await _frame(port)
        await _close(port, task)
        return [first, second]

    assert asyncio.run(scenario()) == [
        {"type": "ack", "id": "1", "ok": True},
        {"type": "ack", "id": "2", "ok": False, "error": "unknown"},
    ]
    assert _SEEN == [("mine", 1, "room:42", False), ("typing", 2, "room:42", False)]


@pytest.mark.django_db(transaction=True)
def test_a_slow_handler_times_out_on_its_own_pool() -> None:
    release = threading.Event()
    threads: list[str] = []

    @on("room:{room}", "slow")
    def slow(upstream: Upstream) -> None:
        threads.append(threading.current_thread().name)
        release.wait(5)

    ticket = _ticket()

    async def scenario() -> list[dict[str, object]]:
        port = MemoryPort()
        _, task = await _open(port, ticket)
        for index in range(3):
            port.push_json(_up("slow", id=str(index)))
        acks = [await _frame(port) for _ in range(3)]
        release.set()
        await _close(port, task)
        return acks

    limits = {"REALTIME": {"UPSTREAM": {"TIMEOUT": 0.1}}}
    with (
        override_settings(NEXT_FRAMEWORK=limits),
        ThreadPoolExecutor(1, thread_name_prefix="own") as pool,
        patch.object(handlers, "handler_pool", lambda: pool),
    ):
        acks = asyncio.run(scenario())
    assert sorted(ack["id"] for ack in acks) == ["0", "1", "2"]
    assert {ack["error"] for ack in acks} == {"timeout"}
    assert threads
    assert all(name.startswith("own") for name in threads)


_GATE: dict[str, asyncio.Event] = {}


@pytest.mark.django_db(transaction=True)
def test_events_past_the_concurrency_are_refused_without_a_strike() -> None:
    @on("room:{room}", "hold")
    async def hold(upstream: Upstream) -> None:
        await _GATE["open"].wait()

    ticket = _ticket()

    async def scenario() -> None:
        _GATE["open"] = asyncio.Event()
        port = MemoryPort()
        _, task = await _open(port, ticket)
        port.push_json(_up("hold", id="1"))
        port.push_json(_up("hold", id="2"))
        for index in range(3, 7):
            port.push_json(_up("hold", id=str(index)))
        port.push_json(_up("hold"))
        refused = [await _frame(port) for _ in range(5)]
        _GATE["open"].set()
        held = [await _frame(port), await _frame(port)]
        port.push_json(_up("hold", id="8"))
        last = await _frame(port)
        await _close(port, task)
        assert refused == [
            *(
                {"type": "ack", "id": str(index), "ok": False, "error": "limit"}
                for index in range(3, 7)
            ),
            {"type": "error", "code": "limit"},
        ]
        assert sorted(frame["id"] for frame in held) == ["1", "2"]
        assert all(frame["ok"] for frame in held)
        assert last == {"type": "ack", "id": "8", "ok": True}
        assert port.close_code is None

    limits = {"REALTIME": {"UPSTREAM": {"CONCURRENCY": 2}}}
    with override_settings(NEXT_FRAMEWORK=limits):
        asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_a_handler_never_sees_the_session_key_behind_the_subject() -> None:
    received: list[Upstream] = []

    @on("room:{room}", "typing")
    def typing(upstream: Upstream) -> None:
        received.append(upstream)

    ticket = _ticket()
    key = ticket.session.session_key
    assert key is not None

    async def scenario() -> TopicSocket:
        port = MemoryPort()
        socket, task = await _open(port, ticket)
        port.push_json(_up("typing", id="1"))
        assert (await _frame(port))["ok"] is True
        await _close(port, task)
        return socket

    socket = asyncio.run(scenario())
    (upstream,) = received
    values = [getattr(upstream, field.name) for field in fields(upstream)]
    assert all(key not in repr(value) for value in values)
    assert key not in repr(upstream)
    assert key not in repr(socket.handshake)
    assert upstream.subject == socket.handshake.subject
    assert len(upstream.subject) == 64
