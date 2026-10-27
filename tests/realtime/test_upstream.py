import asyncio
import json
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import fields
from functools import partial
from unittest.mock import patch

import pytest
from django.contrib.auth.models import AnonymousUser
from django.dispatch import Signal
from django.test import override_settings

from next.realtime import Upstream, checks, handlers, socket as socket_module
from next.realtime.access import AccessCheck, SocketAccessCheck, TopicAuthorizer
from next.realtime.backends import MemoryBroker
from next.realtime.frames import Close
from next.realtime.handlers import HandlerRegistry, on
from next.realtime.signals import upstream_received, upstream_rejected
from next.realtime.socket import Handshake, SocketClock, TopicSocket
from next.realtime.tickets import TicketClaims
from next.testing import SignalRecorder, capture_signals
from next.testing.realtime import MemoryPort, SocketTicket
from tests.support.realtime import ServerClock, issue_session_ticket, signed_in_session


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
    clock: Callable[[], float] = time.monotonic,
    socket_class: type[TopicSocket] = TopicSocket,
) -> TopicSocket:
    handshake = Handshake(ticket.credential, AnonymousUser(), None)
    chosen = _Rechecking if recheck else socket_class
    return chosen(
        port,
        handshake,
        broker=MemoryBroker(),
        heartbeat=60,
        clock=SocketClock(monotonic=clock),
    )


async def _frame(port: MemoryPort) -> dict[str, object]:
    async with asyncio.timeout(5):
        return await port.receive_json()


async def _open(
    port: MemoryPort,
    ticket: SocketTicket,
    *,
    recheck: bool = False,
    clock: Callable[[], float] = time.monotonic,
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
def test_event_with_an_id_is_acknowledged(
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
@pytest.mark.parametrize(("slack", "error"), [(0, None), (-1, "too_big")])
def test_frame_of_exactly_max_bytes_is_delivered(slack: int, error: str | None) -> None:
    _register()
    ticket = _ticket()
    frame = _up("typing", {"on": True}, id="1")
    max_bytes = len(json.dumps(frame).encode()) + slack

    async def scenario() -> dict[str, object]:
        port = MemoryPort()
        _, task = await _open(port, ticket)
        port.push_json(frame)
        ack = await _frame(port)
        await _close(port, task)
        return ack

    limits = {"REALTIME": {"UPSTREAM": {"MAX_BYTES": max_bytes}}}
    with override_settings(NEXT_FRAMEWORK=limits):
        ack = asyncio.run(scenario())
    assert ack.get("error") == error
    assert ack["ok"] is (error is None)
    assert bool(_SEEN) is (error is None)


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
def test_hello_names_the_connection_a_handler_sees() -> None:
    _register()
    ticket = _ticket()

    async def scenario() -> None:
        port = MemoryPort()
        socket = _socket(port, ticket)
        task = asyncio.create_task(socket.serve(port.inbound()))
        port.push_json(ticket.subscribe_frame())
        hello = await _frame(port)
        port.push_json(_up("cursor", {"x": 1}, id="1"))
        await _frame(port)
        await _close(port, task)
        assert hello["connection"] == socket.connection_id
        assert [("cursor", {"x": 1}, hello["connection"], False)] == _SEEN

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_two_sockets_of_one_session_get_distinct_connections() -> None:
    ticket = _ticket()

    async def scenario() -> list[object]:
        seen: list[object] = []
        for _ in range(2):
            port = MemoryPort()
            socket = _socket(port, ticket)
            task = asyncio.create_task(socket.serve(port.inbound()))
            port.push_json(ticket.subscribe_frame())
            seen.append((await _frame(port))["connection"])
            await _close(port, task)
        return seen

    first, second = asyncio.run(scenario())
    assert isinstance(first, str)
    assert first
    assert first != second


class _Unacked(TopicSocket):
    """Flag the handled event without an id, since no ack tells a test it ran."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.unacked = asyncio.Event()

    async def handle(self, upstream: Upstream) -> bool:
        served = await super().handle(upstream)
        if upstream.data == 1:
            self.unacked.set()
        return served


@pytest.mark.django_db(transaction=True)
def test_event_without_an_id_gets_no_ack() -> None:
    _register()
    ticket = _ticket()

    async def scenario() -> None:
        port = MemoryPort()
        socket, task = await _open(port, ticket, socket_class=_Unacked)
        port.push_json(_up("typing", 1))
        port.push_json(_up("nobody", 2))
        port.push_json(_up("typing", 3, topic="room:9"))
        port.push_json(_up("typing", 4, id="last"))
        assert await _frame(port) == {"type": "ack", "id": "last", "ok": True}
        async with asyncio.timeout(5):
            assert isinstance(socket, _Unacked)
            await socket.unacked.wait()
        await _close(port, task)
        assert sorted(entry[1] for entry in _SEEN) == [1, 4]
        assert len(port.sent) == 2

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_refused_authorizer_answers_denied_and_closes() -> None:
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
def test_refused_event_without_an_id_still_closes() -> None:
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
def test_event_before_hello_waits_for_the_grant() -> None:
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
def test_empty_bucket_refuses_and_three_refusals_close_with_4429() -> None:
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


@pytest.mark.django_db(transaction=True)
def test_admitted_frame_resets_the_strikes(clock: ServerClock) -> None:
    _register()
    ticket = _ticket()
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
def test_subclass_routes_events_through_handle() -> None:
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
def test_slow_handler_times_out_on_its_own_pool() -> None:
    entered = threading.Event()
    release = threading.Event()
    threads: list[str] = []

    @on("room:{room}", "slow")
    def slow(upstream: Upstream) -> None:
        threads.append(threading.current_thread().name)
        entered.set()
        release.wait(5)

    ticket = _ticket()

    async def scenario() -> list[dict[str, object]]:
        loop = asyncio.get_running_loop()
        clock = _LoopClock(loop)
        port = MemoryPort()
        with patch.object(loop, "time", clock):
            _, task = await _open(port, ticket)
            for index in range(3):
                port.push_json(_up("slow", id=str(index)))
            assert await asyncio.to_thread(entered.wait, 5)
            clock.offset += 31
            acks = [await _frame(port) for _ in range(3)]
            release.set()
            await _close(port, task)
        return acks

    limits = {"REALTIME": {"UPSTREAM": {"TIMEOUT": 30}}}
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
def test_handler_never_sees_the_session_key_behind_the_subject() -> None:
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


def _rejections(recorder: SignalRecorder) -> list[str]:
    return [event.kwargs["reason"] for event in recorder.events_for(upstream_rejected)]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("frame", "reason"),
    [
        (_up("typing", topic="room:7", id="1"), "topic"),
        (_up("nobody", id="1"), "unknown"),
        (_up("boom", id="1"), "internal"),
        (_up("typing", "x" * 20000, id="1"), "too_big"),
    ],
)
def test_rejected_event_is_reported_before_its_ack(
    frame: dict[str, object], reason: str
) -> None:
    _register()
    ticket = _ticket()

    async def scenario() -> TopicSocket:
        port = MemoryPort()
        socket, task = await _open(port, ticket)
        port.push_json(frame)
        ack = await _frame(port)
        assert ack["error"] == reason
        assert _rejections(recorder) == [reason]
        await _close(port, task)
        return socket

    with capture_signals(upstream_rejected) as recorder:
        socket = asyncio.run(scenario())
    (event,) = recorder.events_for(upstream_rejected)
    assert event.sender is TopicSocket
    assert event.kwargs == {
        "topic": frame["topic"],
        "name": frame["name"],
        "reason": reason,
        "connection": socket.connection_id,
        "subject": socket.handshake.subject,
    }


@pytest.mark.django_db(transaction=True)
def test_delivered_event_is_never_reported_as_rejected() -> None:
    _register()
    ticket = _ticket()

    async def scenario() -> None:
        port = MemoryPort()
        _, task = await _open(port, ticket)
        port.push_json(_up("typing", id="1"))
        assert (await _frame(port))["ok"] is True
        await _close(port, task)

    with capture_signals(upstream_rejected) as recorder:
        asyncio.run(scenario())
    assert len(recorder) == 0


@pytest.mark.django_db(transaction=True)
def test_denied_event_is_reported_before_the_close() -> None:
    _register()
    ticket = _ticket()

    async def scenario() -> None:
        port = MemoryPort()
        _, task = await _open(port, ticket, recheck=True)
        _GRANTS["room"] = False
        port.push_json(_up("typing"))
        async with asyncio.timeout(5):
            await task

    with capture_signals(upstream_rejected) as recorder:
        asyncio.run(scenario())
    assert _rejections(recorder) == ["denied"]


@pytest.mark.django_db(transaction=True)
def test_refused_events_are_reported_as_limit_with_or_without_an_id() -> None:
    _register()
    ticket = _ticket()

    async def scenario() -> None:
        port = MemoryPort()
        _, task = await _open(port, ticket)
        for frame in (
            _up("typing", id="1"),
            _up("typing", id="2"),
            _up("typing", id="3"),
            _up("typing"),
            _up("typing", id="5"),
        ):
            port.push_json(frame)
        async with asyncio.timeout(5):
            await task

    with (
        override_settings(NEXT_FRAMEWORK=_TIGHT),
        capture_signals(upstream_rejected) as recorder,
    ):
        asyncio.run(scenario())
    assert _rejections(recorder) == ["limit", "limit", "limit"]


@pytest.mark.django_db(transaction=True)
def test_event_past_the_concurrency_is_reported_as_limit() -> None:
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
        assert (await _frame(port))["error"] == "limit"
        _GATE["open"].set()
        assert (await _frame(port))["ok"] is True
        await _close(port, task)

    limits = {"REALTIME": {"UPSTREAM": {"CONCURRENCY": 1}}}
    with (
        override_settings(NEXT_FRAMEWORK=limits),
        capture_signals(upstream_rejected) as recorder,
    ):
        asyncio.run(scenario())
    assert _rejections(recorder) == ["limit"]


class _LoopClock:
    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._real = loop.time
        self.offset = 0.0

    def __call__(self) -> float:
        return self._real() + self.offset


@pytest.mark.django_db(transaction=True)
def test_timed_out_event_is_reported_as_timeout() -> None:
    @on("room:{room}", "stall")
    async def stall(upstream: Upstream) -> None:
        _GATE["started"].set()
        await _GATE["open"].wait()

    ticket = _ticket()

    async def scenario() -> None:
        _GATE["started"], _GATE["open"] = asyncio.Event(), asyncio.Event()
        loop = asyncio.get_running_loop()
        clock = _LoopClock(loop)
        port = MemoryPort()
        with patch.object(loop, "time", clock):
            _, task = await _open(port, ticket)
            port.push_json(_up("stall", id="1"))
            await _GATE["started"].wait()
            clock.offset += 31
            assert (await _frame(port))["error"] == "timeout"
            await _close(port, task)

    limits = {"REALTIME": {"UPSTREAM": {"TIMEOUT": 30}}}
    with (
        override_settings(NEXT_FRAMEWORK=limits),
        capture_signals(upstream_rejected) as recorder,
    ):
        asyncio.run(scenario())
    assert _rejections(recorder) == ["timeout"]


@pytest.mark.django_db(transaction=True)
def test_slow_rejected_receiver_never_stalls_the_reads() -> None:
    _register()
    ticket = _ticket()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def slow(**kwargs: object) -> None:
        entered.set()
        await release.wait()

    async def scenario() -> list[dict[str, object]]:
        port = MemoryPort()
        _, task = await _open(port, ticket)
        port.push_json(_up("typing", id="1"))
        port.push_json(_up("typing", id="2"))
        received = [await _frame(port), await _frame(port)]
        await entered.wait()
        port.push_json({"type": "pong"})
        port.push_json({"type": "pong"})
        received.append(await _frame(port))
        assert not task.done()
        release.set()
        async with asyncio.timeout(5):
            await task
        assert port.close_code == Close.RATE_LIMITED
        return received

    limits = {"REALTIME": {"UPSTREAM": {"RATE": 0.001, "BURST": 1}}}
    upstream_rejected.connect(slow, weak=False)
    try:
        with (
            override_settings(NEXT_FRAMEWORK=limits),
            capture_signals(upstream_rejected) as recorder,
        ):
            received = asyncio.run(scenario())
    finally:
        upstream_rejected.disconnect(slow)
    assert sorted(received, key=lambda frame: str(frame.get("id"))) == [
        {"type": "ack", "id": "1", "ok": True},
        {"type": "ack", "id": "2", "ok": False, "error": "limit"},
        {"type": "error", "code": "limit"},
    ]
    assert _rejections(recorder) == ["limit"]


def _broken_receiver(**kwargs: object) -> None:
    msg = "receiver failed"
    raise ValueError(msg)


@contextmanager
def _failing(*signals: Signal) -> Iterator[None]:
    for signal in signals:
        signal.connect(_broken_receiver, weak=False)
    try:
        yield
    finally:
        for signal in signals:
            signal.disconnect(_broken_receiver)


@pytest.mark.django_db(transaction=True)
def test_failing_receivers_leave_the_acks_as_they_were(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _register()
    ticket = _ticket()

    async def scenario() -> list[dict[str, object]]:
        port = MemoryPort()
        _, task = await _open(port, ticket)
        port.push_json(_up("typing", 1, id="1"))
        first = await _frame(port)
        port.push_json(_up("nobody", id="2"))
        second = await _frame(port)
        await _close(port, task)
        return [first, second]

    with (
        _failing(upstream_received, upstream_rejected),
        caplog.at_level("ERROR", "next.realtime.signals"),
    ):
        acks = asyncio.run(scenario())
    assert acks == [
        {"type": "ack", "id": "1", "ok": True},
        {"type": "ack", "id": "2", "ok": False, "error": "unknown"},
    ]
    assert [entry[1] for entry in _SEEN] == [1]
    logged = [
        record.getMessage()
        for record in caplog.records
        if record.name == "next.realtime.signals"
    ]
    assert len(logged) == 2
    assert "upstream_received" in logged[0]
    assert "upstream_rejected" in logged[1]
    assert "topic room:42" in logged[1]


@pytest.mark.django_db(transaction=True)
def test_timeout_raised_inside_the_handler_is_internal_not_timeout() -> None:
    @on("room:{room}", "raises")
    async def raises(upstream: Upstream) -> None:
        raise TimeoutError

    ticket = _ticket()

    async def scenario() -> dict[str, object]:
        port = MemoryPort()
        _, task = await _open(port, ticket)
        port.push_json(_up("raises", id="1"))
        ack = await _frame(port)
        await _close(port, task)
        return ack

    with capture_signals(upstream_rejected) as recorder:
        ack = asyncio.run(scenario())
    assert ack == {"type": "ack", "id": "1", "ok": False, "error": "internal"}
    assert _rejections(recorder) == ["internal"]


@pytest.mark.django_db(transaction=True)
def test_handler_sees_the_user_the_last_recheck_loaded(clock: ServerClock) -> None:
    user, session = signed_in_session("writer", "view_user")
    ticket = issue_session_ticket(authorizer_ref=_AUTHORIZER, session=session)
    allowed: list[bool] = []

    @on("room:{room}", "perm")
    def perm(upstream: Upstream) -> None:
        allowed.append(upstream.user.has_perm("auth.view_user"))

    async def scenario() -> None:
        port = MemoryPort()
        handshake = Handshake(ticket.credential, user, None)
        socket = TopicSocket(port, handshake, broker=MemoryBroker(), heartbeat=60)
        task = asyncio.create_task(socket.serve(port.inbound()))
        port.push_json(ticket.subscribe_frame())
        assert (await _frame(port))["type"] == "hello"
        port.push_json(_up("perm", id="1"))
        assert (await _frame(port))["ok"] is True
        await asyncio.to_thread(user.user_permissions.clear)
        port.push_json(_up("perm", id="2"))
        assert (await _frame(port))["ok"] is True
        clock.now = 60
        port.push_json(_up("perm", id="3"))
        assert (await _frame(port))["ok"] is True
        await _close(port, task)

    with (
        patch.object(
            socket_module, "SocketAccessCheck", partial(SocketAccessCheck, clock=clock)
        ),
        ThreadPoolExecutor(1) as pool,
        patch.object(handlers, "handler_pool", lambda: pool),
    ):
        asyncio.run(scenario())
    assert allowed == [True, True, False]
