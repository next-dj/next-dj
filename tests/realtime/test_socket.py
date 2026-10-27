import asyncio
import json
import logging
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from functools import partial
from unittest.mock import Mock

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.test import override_settings

from next.partial import JsonPartialProtocolBackend, Patches, StreamMeta
from next.partial.manager import partial_backend_manager
from next.realtime import frames, socket as socket_module
from next.realtime.access import SocketAccessCheck
from next.realtime.backends import MemoryBroker
from next.realtime.errors import BrokerConnectionError
from next.realtime.frames import Close, Reply, Request
from next.realtime.manager import broker_manager
from next.realtime.messages import Message, Position
from next.realtime.socket import Handshake, SocketClock, TopicSocket, _Writer
from next.realtime.tunnel import TunnelHints
from next.testing.realtime import MemoryPort, SocketTicket
from tests.support.cases.realtime import (
    MALFORMED_SUBSCRIPTION_CASES,
    RAW_FRAME_CASES,
    TEXT_FRAME_CASES,
    UNUSABLE_REFERENCE_CASES,
    RefusedFrameCase,
    TextFrameCase,
    TicketReferenceCase,
)
from tests.support.realtime import (
    LiveStreams,
    ScriptedBroker,
    ServerClock,
    issue_session_ticket,
    signed_in_session,
)


_HERE = "tests.realtime.test_socket"
_SERVER_SECONDS = 1_700_000_000.25
_SERVER_TIME = 1_700_000_000_250
_GRANTS = {"topic": True}
_AUTHORIZED: list[str] = []
_BARRIER = threading.Barrier(2, timeout=5)
_TRANSFORM_ENTERED = threading.Event()
_TRANSFORM_RELEASE = threading.Event()
_TRANSFORM_THREADS: list[threading.Thread] = []


def _toggled_authorizer(_user: object, topic: str) -> bool:
    return _GRANTS["topic"] and topic.startswith("room:")


def _counting_authorizer(_user: object, topic: str) -> bool:
    _AUTHORIZED.append(topic)
    return True


def _user_authorizer(user: object, _topic: str) -> bool:
    return bool(user.is_authenticated and user.has_perm("auth.view_user"))


def _event_transform(message: Message) -> Patches:
    return Patches(None).event("room:update", {"seq": message.seq})


async def _async_event_transform(message: Message) -> Patches:
    _TRANSFORM_THREADS.append(threading.current_thread())
    return _event_transform(message)


def _skip_transform(_message: Message) -> None:
    return None


def _failing_transform(_message: Message) -> None:
    msg = "transform failed"
    raise RuntimeError(msg)


def _rendezvous_transform(message: Message) -> Patches:
    _BARRIER.wait()
    return _event_transform(message)


def _blocking_transform(message: Message) -> Patches:
    _TRANSFORM_ENTERED.set()
    _TRANSFORM_RELEASE.wait(5)
    return _event_transform(message)


@pytest.fixture(autouse=True)
def _reset_module_state():
    _GRANTS["topic"] = True
    _AUTHORIZED.clear()
    _BARRIER.reset()
    _TRANSFORM_ENTERED.clear()
    _TRANSFORM_RELEASE.clear()
    yield
    _TRANSFORM_RELEASE.set()


@pytest.fixture()
def access_clock(monkeypatch: pytest.MonkeyPatch, clock: ServerClock) -> ServerClock:
    """Drive the access recheck interval by hand instead of waiting for it."""
    monkeypatch.setattr(
        socket_module, "SocketAccessCheck", partial(SocketAccessCheck, clock=clock)
    )
    return clock


class FailingBroker(MemoryBroker):
    """A memory broker whose positions read fails inside a live subscription."""

    def __init__(self, failure: BaseException) -> None:
        """Remember the failure the positions read raises."""
        super().__init__()
        self.failure = failure

    async def apositions(self, topics: Sequence[str]) -> dict[str, Position]:
        raise self.failure


class RecordingSocket(TopicSocket):
    """Log the catch-up and tick hooks into the broker's call log."""

    def __init__(self, port: MemoryPort, handshake: Handshake, **kwargs) -> None:
        """Start with no hooks run."""
        super().__init__(port, handshake, **kwargs)
        self.calls: list[str] = []
        self.caught_up: list[Mapping[str, Position]] = []
        self.ticks = 0

    async def catch_up(self, subscription, positions: Mapping[str, Position]) -> None:
        self.calls.append("catch_up")
        self.caught_up.append(positions)
        await super().catch_up(subscription, positions)

    async def tick(self) -> None:
        self.ticks += 1
        await super().tick()


class RecordingPort(MemoryPort):
    """A memory port that logs each sent frame type into a shared call log."""

    def __init__(self, calls: list[str]) -> None:
        """Log into the shared call list."""
        super().__init__()
        self.calls = calls

    async def send(self, text: str) -> None:
        await super().send(text)
        self.calls.append(json.loads(text)["type"])


class LeavingPort(MemoryPort):
    """A memory port whose peer can vanish while its inbound frames stay open."""

    def __init__(self) -> None:
        """Start with the peer still present."""
        super().__init__()
        self.gone = False

    async def send(self, text: str) -> None:
        if self.gone:
            raise ConnectionResetError
        await super().send(text)


class GatedPort(MemoryPort):
    """A memory port whose sends block until the test opens the gate."""

    def __init__(self) -> None:
        """Start with the gate shut and an empty log."""
        super().__init__()
        self.log: list[str] = []
        self.entered = asyncio.Event()
        self.gate = asyncio.Event()

    async def send(self, text: str) -> None:
        self.log.append(f"enter {text}")
        self.entered.set()
        await self.gate.wait()
        self.log.append(f"exit {text}")
        await super().send(text)

    async def close(self, code: int) -> None:
        self.log.append(f"close {code}")
        await super().close(code)


def _handshake(
    ticket: SocketTicket, user: object = None, expires_at: float | None = None
) -> Handshake:
    return Handshake(
        ticket.credential, AnonymousUser() if user is None else user, expires_at
    )


def _socket(
    port: MemoryPort,
    ticket: SocketTicket,
    broker: MemoryBroker | None = None,
    *,
    heartbeat: float | None = 60,
    socket_class: type[TopicSocket] = TopicSocket,
    monotonic: Callable[[], float] = time.monotonic,
    **handshake: object,
) -> TopicSocket:
    return socket_class(
        port,
        _handshake(ticket, **handshake),
        broker=broker,
        heartbeat=heartbeat,
        clock=SocketClock(wall=lambda: _SERVER_SECONDS, monotonic=monotonic),
    )


async def _frame(port: MemoryPort) -> dict[str, object]:
    async with asyncio.timeout(5):
        return await port.receive_json()


async def _finish(task: asyncio.Task[None]) -> None:
    async with asyncio.timeout(5):
        await task


async def _open(
    socket: TopicSocket, port: MemoryPort, ticket: SocketTicket, **sub: object
) -> tuple[asyncio.Task[None], dict[str, object]]:
    task = asyncio.create_task(socket.serve(port.inbound()))
    port.push_json(ticket.subscribe_frame(**sub))
    hello = await _frame(port)
    assert hello["type"] == "hello"
    return task, hello


async def _refused(
    port: MemoryPort, socket: TopicSocket, *items: str | bytes
) -> list[dict[str, object]]:
    for item in items:
        port.push(item)
    await _finish(asyncio.create_task(socket.serve(port.inbound())))
    return [json.loads(text) for text in port.sent]


async def _subscribed(
    port: MemoryPort, ticket: SocketTicket, broker: MemoryBroker
) -> tuple[asyncio.Task[None], dict[str, object]]:
    return await _open(_socket(port, ticket, broker), port, ticket)


def _envelope():
    return Patches(None).refresh(zone="score").envelope()


@pytest.mark.django_db(transaction=True)
def test_serve_catches_up_between_positions_and_hello(
    session_ticket: SocketTicket,
) -> None:
    async def scenario() -> None:
        calls: list[str] = []
        broker = ScriptedBroker(calls)
        port = RecordingPort(calls)
        socket = _socket(port, session_ticket, broker, socket_class=RecordingSocket)
        socket.calls = calls
        task, _ = await _open(socket, port, session_ticket)
        assert calls == ["asubscribe", "apositions", "catch_up", "hello"]
        assert socket.caught_up == [{"room:42": Position("", 0)}]
        port.disconnect()
        await _finish(task)

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_hello_carries_positions_heartbeat_and_the_clock(streams: LiveStreams) -> None:
    ticket = issue_session_ticket(("room:42", "room:43"))

    async def scenario() -> None:
        broker = MemoryBroker()
        published = await broker.apublish("room:43", _envelope())
        port = MemoryPort()
        socket = _socket(port, ticket, broker, heartbeat=7.5)
        task, hello = await _open(socket, port, ticket)
        assert hello == {
            "type": "hello",
            "protocol": 2,
            "server_time": _SERVER_TIME,
            "topics": {
                "room:42": {"epoch": "", "seq": 0},
                "room:43": {"epoch": published.epoch, "seq": published.seq},
            },
            "transports": ["ws", "sse"],
            "heartbeat": 7.5,
            "connection": socket.connection_id,
            "upstream": {"frame": 16384, "timeout": 10.0},
        }
        port.disconnect()
        await _finish(task)
        assert port.close_code is None
        assert streams.live() == 0

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_tick_runs_on_every_heartbeat_of_the_subscription(
    session_ticket: SocketTicket,
) -> None:
    async def scenario() -> None:
        broker = ScriptedBroker()
        port = MemoryPort()
        socket = _socket(port, session_ticket, broker, socket_class=RecordingSocket)
        task, _ = await _open(socket, port, session_ticket)
        for count in range(1, 4):
            broker.feed.put_nowait(None)
            assert await _frame(port) == {"type": "ping", "server_time": _SERVER_TIME}
            assert socket.ticks == count
        assert broker.timeouts == [60]
        port.disconnect()
        await _finish(task)

    asyncio.run(scenario())


def test_connection_id_is_unique_per_socket() -> None:
    handshake = Handshake("key", AnonymousUser(), None)
    ids = {TopicSocket(MemoryPort(), handshake).connection_id for _ in range(50)}
    assert len(ids) == 50
    assert all(isinstance(value, str) and len(value) >= 16 for value in ids)


@pytest.mark.django_db(transaction=True)
def test_default_broker_and_heartbeat_come_from_settings(
    session_ticket: SocketTicket, streams: LiveStreams
) -> None:
    async def scenario() -> None:
        port = MemoryPort()
        socket = _socket(port, session_ticket, heartbeat=None)
        task, hello = await _open(socket, port, session_ticket)
        assert hello["heartbeat"] == 7.5
        broker = await broker_manager.aget()
        await broker.apublish("room:42", _envelope())
        assert (await _frame(port))["type"] == "patches"
        port.disconnect()
        await _finish(task)
        assert streams.live() == 0

    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"HEARTBEAT": 7.5}}):
        asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_subscribe_frame_cursor_is_kept_on_the_socket(
    session_ticket: SocketTicket,
) -> None:
    async def scenario() -> None:
        port = MemoryPort()
        socket = _socket(port, session_ticket, MemoryBroker())
        assert socket.cursor == {}
        last_seq = {"room:42": 3, "room:43": {"epoch": "e1", "seq": 9}}
        task, _ = await _open(socket, port, session_ticket, last_seq=last_seq)
        assert socket.cursor == {"room:42": 3, "room:43": Position("e1", 9)}
        port.disconnect()
        await _finish(task)

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_published_message_is_the_json_backend_patches_frame(
    session_ticket: SocketTicket,
) -> None:
    async def scenario() -> None:
        broker = MemoryBroker()
        port = MemoryPort()
        task, _ = await _subscribed(port, session_ticket, broker)
        published = await broker.apublish("room:42", _envelope())
        async with asyncio.timeout(5):
            text = await port.receive()
        backend = JsonPartialProtocolBackend()
        assert text == frames.patches(published, backend, server_time=_SERVER_TIME)
        meta = StreamMeta(
            server_time=_SERVER_TIME,
            topic="room:42",
            seq=published.seq,
            epoch=published.epoch,
        )
        assert json.loads(text)["data"] == backend.stream_data(published.envelope, meta)
        port.disconnect()
        await _finish(task)

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("case", TEXT_FRAME_CASES, ids=lambda case: case.id)
def test_custom_codec_frame_carries_its_stream_text(
    monkeypatch: pytest.MonkeyPatch, session_ticket: SocketTicket, case: TextFrameCase
) -> None:
    data_of = case.backend.stream_data
    spy = Mock(wraps=data_of)
    monkeypatch.setattr(case.backend, "stream_data", spy)
    monkeypatch.setattr(partial_backend_manager, "get", lambda: case.backend)
    envelope = Patches.versioned("v7").refresh(zone="score").envelope()

    async def scenario() -> None:
        broker = MemoryBroker()
        port = MemoryPort()
        task, _ = await _subscribed(port, session_ticket, broker)
        published = await broker.apublish("room:42", envelope)
        frame = await _frame(port)
        sent, meta = spy.call_args.args
        assert sent == envelope
        assert (meta.topic, meta.seq, meta.epoch, meta.server_time) == (
            "room:42",
            published.seq,
            published.epoch,
            _SERVER_TIME,
        )
        assert frame == {"type": "patches", "data": data_of(sent, meta)}
        assert isinstance(frame["data"], str)
        port.disconnect()
        await _finish(task)

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_transform_rewrites_the_patches_off_the_event_loop() -> None:
    ticket = issue_session_ticket(transform_ref=f"{_HERE}._event_transform")

    async def scenario() -> None:
        broker = MemoryBroker()
        port = MemoryPort()
        task, _ = await _open(_socket(port, ticket, broker), port, ticket)
        published = await broker.apublish("room:42", _envelope())
        frame = await _frame(port)
        assert frame["type"] == "patches"
        assert frame["data"]["ops"][0]["name"] == "room:update"
        assert frame["data"]["seq"] == published.seq
        port.disconnect()
        await _finish(task)

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_an_async_transform_runs_on_the_event_loop() -> None:
    ticket = issue_session_ticket(transform_ref=f"{_HERE}._async_event_transform")
    _TRANSFORM_THREADS.clear()

    async def scenario() -> None:
        broker = MemoryBroker()
        port = MemoryPort()
        task, _ = await _open(_socket(port, ticket, broker), port, ticket)
        await broker.apublish("room:42", _envelope())
        frame = await _frame(port)
        assert frame["data"]["ops"][0]["name"] == "room:update"
        port.disconnect()
        await _finish(task)

    asyncio.run(scenario())
    assert list(_TRANSFORM_THREADS) == [threading.main_thread()]


@pytest.mark.django_db(transaction=True)
def test_transform_skipping_a_message_sends_skip_with_epoch() -> None:
    ticket = issue_session_ticket(transform_ref=f"{_HERE}._skip_transform")

    async def scenario() -> None:
        broker = MemoryBroker()
        port = MemoryPort()
        task, _ = await _open(_socket(port, ticket, broker), port, ticket)
        published = await broker.apublish("room:42", _envelope())
        assert await _frame(port) == {
            "type": "skip",
            "topic": "room:42",
            "epoch": published.epoch,
            "seq": published.seq,
        }
        port.disconnect()
        await _finish(task)

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_failing_transform_skips_the_message_and_keeps_the_socket(
    caplog: pytest.LogCaptureFixture, streams: LiveStreams
) -> None:
    ticket = issue_session_ticket(transform_ref=f"{_HERE}._failing_transform")

    async def scenario() -> None:
        broker = MemoryBroker()
        port = MemoryPort()
        task, _ = await _open(_socket(port, ticket, broker), port, ticket)
        published = await broker.apublish("room:42", _envelope())
        assert await _frame(port) == {
            "type": "skip",
            "topic": "room:42",
            "epoch": published.epoch,
            "seq": published.seq,
        }
        port.disconnect()
        await _finish(task)
        assert port.close_code is None
        assert streams.live() == 0

    asyncio.run(scenario())
    assert "Realtime transform failed on room:42 seq 1, skipping it" in caplog.text
    assert "Realtime WebSocket stream failed" not in caplog.text


@pytest.mark.django_db(transaction=True)
def test_concurrent_transforms_run_on_separate_threads(streams: LiveStreams) -> None:
    tickets = [
        issue_session_ticket(transform_ref=f"{_HERE}._rendezvous_transform")
        for _ in range(2)
    ]

    async def scenario() -> None:
        broker = MemoryBroker()
        ports = [MemoryPort(), MemoryPort()]
        tasks = [
            (await _open(_socket(port, ticket, broker), port, ticket))[0]
            for port, ticket in zip(ports, tickets, strict=True)
        ]
        await broker.apublish("room:42", _envelope())
        for port in ports:
            assert (await _frame(port))["type"] == "patches"
            port.disconnect()
        for task in tasks:
            await _finish(task)
        assert streams.live() == 0

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_message_burst_rechecks_access_once_per_heartbeat() -> None:
    ticket = issue_session_ticket(authorizer_ref=f"{_HERE}._counting_authorizer")

    async def scenario() -> None:
        broker = MemoryBroker()
        port = MemoryPort()
        task, _ = await _open(_socket(port, ticket, broker), port, ticket)
        for _ in range(5):
            await broker.apublish("room:42", _envelope())
            assert (await _frame(port))["type"] == "patches"
        port.disconnect()
        await _finish(task)

    asyncio.run(scenario())
    assert _AUTHORIZED == ["room:42"]


@pytest.mark.django_db(transaction=True)
def test_denied_topic_closes_before_subscribing_to_the_broker() -> None:
    _GRANTS["topic"] = False
    ticket = issue_session_ticket(authorizer_ref=f"{_HERE}._toggled_authorizer")

    async def scenario() -> None:
        broker = ScriptedBroker()
        port = MemoryPort()
        frames_sent = await _refused(
            port, _socket(port, ticket, broker), json.dumps(ticket.subscribe_frame())
        )
        assert frames_sent == [{"type": "error", "code": "auth"}]
        assert port.close_code == Close.TOPIC_DENIED
        assert broker.calls == []

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_topic_revoked_while_subscribed_closes_topic_denied(
    access_clock: ServerClock, streams: LiveStreams
) -> None:
    ticket = issue_session_ticket(authorizer_ref=f"{_HERE}._toggled_authorizer")

    async def scenario() -> None:
        broker = MemoryBroker()
        port = MemoryPort()
        task, _ = await _open(_socket(port, ticket, broker), port, ticket)
        _GRANTS["topic"] = False
        access_clock.now = 60
        await broker.apublish("room:42", _envelope())
        assert await _frame(port) == {"type": "error", "code": "auth"}
        await _finish(task)
        assert port.close_code == Close.TOPIC_DENIED
        assert streams.live() == 0

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_heartbeat_rechecks_access_before_its_ping(
    access_clock: ServerClock, session_ticket: SocketTicket, streams: LiveStreams
) -> None:
    async def scenario() -> None:
        broker = ScriptedBroker()
        port = MemoryPort()
        socket = _socket(port, session_ticket, broker, socket_class=RecordingSocket)
        task, _ = await _open(socket, port, session_ticket)
        await asyncio.to_thread(session_ticket.session.cycle_key)
        access_clock.now = 60
        broker.feed.put_nowait(None)
        assert await _frame(port) == {"type": "error", "code": "auth"}
        await _finish(task)
        assert socket.ticks == 0
        assert port.close_code == Close.SESSION
        assert streams.live() == 0

    asyncio.run(scenario())


def _signed_in_ticket() -> tuple[object, SocketTicket]:
    user, session = signed_in_session("reader", "view_user")
    ticket = issue_session_ticket(
        authorizer_ref=f"{_HERE}._user_authorizer", session=session
    )
    return user, ticket


def _revoke(user: object, revocation: str) -> None:
    if revocation == "permission":
        user.user_permissions.clear()
    elif revocation == "password":
        current = get_user_model().objects.get(pk=user.pk)
        current.set_password("after")
        current.save(update_fields=["password"])
    else:
        get_user_model().objects.filter(pk=user.pk).update(is_active=False)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("revocation", "code"),
    [
        ("permission", Close.TOPIC_DENIED),
        ("password", Close.SESSION),
        ("inactive", Close.SESSION),
    ],
)
def test_user_revoked_while_subscribed_closes_before_delivery(
    access_clock: ServerClock, revocation: str, code: Close, streams: LiveStreams
) -> None:
    user, ticket = _signed_in_ticket()

    async def scenario() -> None:
        broker = MemoryBroker()
        port = MemoryPort()
        socket = _socket(port, ticket, broker, user=user)
        task, _ = await _open(socket, port, ticket)
        await asyncio.to_thread(_revoke, user, revocation)
        access_clock.now = 60
        await broker.apublish("room:42", _envelope())
        assert await _frame(port) == {"type": "error", "code": "auth"}
        await _finish(task)
        assert port.close_code == code
        assert streams.live() == 0

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_handshake_deadline_rechecks_a_renewed_session_and_stays_open(
    session_ticket: SocketTicket,
) -> None:
    async def scenario() -> None:
        broker = MemoryBroker()
        port = MemoryPort()
        socket = _socket(port, session_ticket, broker, expires_at=0.0)
        task, _ = await _open(socket, port, session_ticket)
        await broker.apublish("room:42", _envelope())
        assert (await _frame(port))["type"] == "patches"
        port.disconnect()
        await _finish(task)
        assert port.close_code is None

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_expired_handshake_session_closes_before_subscribing(
    session_ticket: SocketTicket,
) -> None:
    session_ticket.session.delete()

    async def scenario() -> None:
        broker = ScriptedBroker()
        port = MemoryPort()
        socket = _socket(port, session_ticket, broker, expires_at=0.0)
        frames_sent = await _refused(
            port, socket, json.dumps(session_ticket.subscribe_frame())
        )
        assert frames_sent == [{"type": "error", "code": "auth"}]
        assert port.close_code == Close.SESSION
        assert broker.calls == []

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("token", ["", "forged", 7])
def test_invalid_ticket_closes_session(
    session_ticket: SocketTicket, token: object
) -> None:
    async def scenario() -> None:
        port = MemoryPort()
        socket = _socket(port, session_ticket, ScriptedBroker())
        frame = json.dumps({"type": "sub", "ticket": token})
        assert await _refused(port, socket, frame) == [
            {"type": "error", "code": "auth"}
        ]
        assert port.close_code == Close.SESSION

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_ticket_of_another_session_closes_session(session_ticket: SocketTicket) -> None:
    other = issue_session_ticket()

    async def scenario() -> None:
        port = MemoryPort()
        socket = _socket(port, session_ticket, ScriptedBroker())
        frame = json.dumps(other.subscribe_frame())
        assert await _refused(port, socket, frame) == [
            {"type": "error", "code": "auth"}
        ]
        assert port.close_code == Close.SESSION

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("case", UNUSABLE_REFERENCE_CASES, ids=lambda case: case.id)
def test_unusable_ticket_reference_closes_with_auth_error(
    case: TicketReferenceCase,
) -> None:
    ticket = issue_session_ticket(
        transform_ref=case.transform_ref, authorizer_ref=case.authorizer_ref
    )

    async def scenario() -> None:
        port = MemoryPort()
        socket = _socket(port, ticket, ScriptedBroker())
        frame = json.dumps(ticket.subscribe_frame())
        assert await _refused(port, socket, frame) == [
            {"type": "error", "code": case.error}
        ]
        assert port.close_code == case.close_code

    asyncio.run(scenario())


def _received(case: RefusedFrameCase) -> str | bytes:
    text = case.receive.get("text")
    return text if text is not None else case.receive["bytes"]


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "case",
    [
        *MALFORMED_SUBSCRIPTION_CASES,
        *(case for case in RAW_FRAME_CASES if case.id != "deeply_nested"),
        RefusedFrameCase("nested_under_the_limit", {"text": "[" * 60_000}),
    ],
    ids=lambda case: case.id,
)
def test_malformed_first_frame_closes_with_its_code(
    session_ticket: SocketTicket, case: RefusedFrameCase
) -> None:
    async def scenario() -> None:
        broker = ScriptedBroker()
        port = MemoryPort()
        socket = _socket(port, session_ticket, broker)
        frames_sent = await _refused(port, socket, _received(case), "{}")
        assert frames_sent == [{"type": "error", "code": case.error}]
        assert port.close_code == case.close_code
        assert broker.calls == []

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "item",
    [b"x" * (frames.MAX_INBOUND_BYTES + 1), "[" * 100_000],
    ids=["binary", "deeply_nested"],
)
def test_oversized_frame_closes_too_big_without_an_error_frame(
    item: str | bytes,
) -> None:
    async def scenario() -> None:
        port = MemoryPort()
        socket = TopicSocket(port, Handshake("key", AnonymousUser(), None))
        assert await _refused(port, socket, item) == []
        assert port.close_code == Close.TOO_BIG

    asyncio.run(scenario())


def test_no_subscription_before_the_timeout_closes_protocol(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(frames, "SUB_TIMEOUT", 0)

    async def scenario() -> None:
        port = MemoryPort()
        socket = TopicSocket(port, Handshake("key", AnonymousUser(), None))
        assert await _refused(port, socket) == [{"type": "error", "code": "protocol"}]
        assert port.close_code == Close.PROTOCOL

    asyncio.run(scenario())


def test_client_leaving_before_subscribing_closes_nothing() -> None:
    async def scenario() -> None:
        port = MemoryPort()
        socket = TopicSocket(port, Handshake("key", AnonymousUser(), None))
        port.disconnect()
        assert await _refused(port, socket) == []
        assert port.close_code is None

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_malformed_frame_after_subscribing_stops_the_stream(
    session_ticket: SocketTicket, streams: LiveStreams
) -> None:
    async def scenario() -> None:
        broker = MemoryBroker()
        port = MemoryPort()
        task, _ = await _subscribed(port, session_ticket, broker)
        port.push("{")
        assert await _frame(port) == {"type": "error", "code": "protocol"}
        await _finish(task)
        assert port.close_code == Close.PROTOCOL
        assert streams.live() == 0

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_control_frames_accept_pong_and_refuse_the_rest(
    session_ticket: SocketTicket,
) -> None:
    async def scenario() -> None:
        broker = MemoryBroker()
        port = MemoryPort()
        task, _ = await _subscribed(port, session_ticket, broker)
        port.push_json({"type": "pong"})
        port.push_json({"type": "ping"})
        port.push_json(session_ticket.subscribe_frame())
        assert await _frame(port) == {"type": "error", "code": "unsupported"}
        assert await _frame(port) == {"type": "error", "code": "unsupported"}
        await broker.apublish("room:42", _envelope())
        assert (await _frame(port))["type"] == "patches"
        port.disconnect()
        await _finish(task)
        assert port.close_code is None

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("failure", "log", "code"),
    [
        (
            BrokerConnectionError("reconnected"),
            "lost its broker subscription",
            Close.TRY_AGAIN,
        ),
        (ConnectionRefusedError(), "broker connection failed", Close.INTERNAL),
        (OSError(), "broker connection failed", Close.INTERNAL),
        (RuntimeError("boom"), "Realtime WebSocket stream failed", Close.INTERNAL),
    ],
    ids=["dropped", "refused", "os", "unexpected"],
)
def test_broker_failure_closes_with_its_code_without_a_leak(
    caplog: pytest.LogCaptureFixture,
    session_ticket: SocketTicket,
    failure: BaseException,
    log: str,
    code: Close,
    streams: LiveStreams,
) -> None:
    caplog.set_level(logging.INFO, logger=socket_module.__name__)

    async def scenario() -> None:
        broker = FailingBroker(failure)
        port = MemoryPort()
        socket = _socket(port, session_ticket, broker)
        frames_sent = await _refused(
            port, socket, json.dumps(session_ticket.subscribe_frame())
        )
        assert frames_sent == []
        assert port.close_code == code
        assert streams.live() == 0

    asyncio.run(scenario())
    assert log in caplog.text


@pytest.mark.django_db(transaction=True)
def test_cancelling_serve_propagates_and_releases_the_subscription(
    session_ticket: SocketTicket, streams: LiveStreams
) -> None:
    async def scenario() -> None:
        broker = MemoryBroker()
        port = MemoryPort()
        task, _ = await _subscribed(port, session_ticket, broker)
        assert streams.live() == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert streams.live() == 0
        assert port.close_code is None

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_disconnect_while_waiting_releases_the_subscription(
    session_ticket: SocketTicket, streams: LiveStreams
) -> None:
    async def scenario() -> None:
        broker = MemoryBroker()
        port = MemoryPort()
        task, _ = await _subscribed(port, session_ticket, broker)
        assert streams.live("room:42") == 1
        port.disconnect()
        await _finish(task)
        assert streams.live() == 0
        assert port.close_code is None

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_disconnect_during_a_sync_transform_releases_the_subscription(
    streams: LiveStreams,
) -> None:
    ticket = issue_session_ticket(transform_ref=f"{_HERE}._blocking_transform")

    async def scenario() -> None:
        broker = MemoryBroker()
        port = MemoryPort()
        task, _ = await _open(_socket(port, ticket, broker), port, ticket)
        await broker.apublish("room:42", _envelope())
        assert await asyncio.to_thread(_TRANSFORM_ENTERED.wait, 5)
        port.disconnect()
        _TRANSFORM_RELEASE.set()
        await _finish(task)
        assert streams.live() == 0
        assert port.close_code is None
        assert len(port.sent) == 1

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_peer_gone_mid_send_drops_without_close(
    session_ticket: SocketTicket, streams: LiveStreams
) -> None:
    async def scenario() -> None:
        broker = MemoryBroker()
        port = LeavingPort()
        task, _ = await _subscribed(port, session_ticket, broker)
        port.gone = True
        await broker.apublish("room:42", _envelope())
        await _finish(task)
        assert port.close_code is None
        assert streams.live() == 0

    asyncio.run(scenario())


def _monotonic(*readings: float) -> Callable[[], float]:
    """Build a monotonic clock that returns `readings`, repeating the last one."""
    queue = list(readings)

    def monotonic() -> float:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return monotonic


@pytest.mark.django_db(transaction=True)
def test_watchdog_drops_a_client_that_stopped_reading(
    caplog: pytest.LogCaptureFixture,
    session_ticket: SocketTicket,
    streams: LiveStreams,
    clock: ServerClock,
) -> None:
    caplog.set_level(logging.INFO, logger=socket_module.__name__)

    async def scenario() -> None:
        broker = MemoryBroker()
        port = GatedPort()
        socket = _socket(port, session_ticket, broker, heartbeat=0.001, monotonic=clock)
        task = asyncio.create_task(socket.serve(port.inbound()))
        port.push_json(session_ticket.subscribe_frame())
        async with asyncio.timeout(5):
            await port.entered.wait()
        clock.advance(10)
        await _finish(task)
        assert port.entered.is_set()
        assert port.sent == []
        assert port.close_code is None
        assert streams.live() == 0

    asyncio.run(scenario())
    assert "dropped a client that stopped reading" in caplog.text


def test_writer_serializes_concurrent_sends() -> None:
    async def scenario() -> None:
        port = GatedPort()
        writer = _Writer(port, time.monotonic)
        first = asyncio.create_task(writer.send("a"))
        second = asyncio.create_task(writer.send("b"))
        closing = asyncio.create_task(writer.close(Close.GOING_AWAY))
        await port.entered.wait()
        assert port.log == ["enter a"]
        port.gate.set()
        await asyncio.gather(first, second, closing)
        assert port.log == ["enter a", "exit a", "enter b", "exit b", "close 1001"]
        assert port.sent == ["a", "b"]

    asyncio.run(scenario())


def test_writer_tracks_a_send_in_flight() -> None:
    async def scenario() -> None:
        port = GatedPort()
        writer = _Writer(port, _monotonic(5.0, 5.5, 7.0))
        assert not writer.stalled(1)
        sending = asyncio.create_task(writer.send("a"))
        await port.entered.wait()
        assert writer.sending_since == 5.0
        assert not writer.stalled(1)
        assert writer.stalled(1)
        port.gate.set()
        await sending
        assert writer.sending_since is None
        assert not writer.stalled(1)

    asyncio.run(scenario())


def test_writer_close_ignores_a_peer_that_left() -> None:
    class BrokenPort(MemoryPort):
        async def close(self, code: int) -> None:
            raise ConnectionResetError

    asyncio.run(_Writer(BrokenPort(), time.monotonic).close(Close.INTERNAL))


@pytest.mark.django_db(transaction=True)
def test_every_frame_and_close_goes_through_the_writer(
    monkeypatch: pytest.MonkeyPatch, session_ticket: SocketTicket
) -> None:
    written: list[str] = []
    closed: list[int] = []
    send, close = _Writer.send, _Writer.close

    async def recording_send(self: _Writer, text: str) -> None:
        written.append(text)
        await send(self, text)

    async def recording_close(self: _Writer, code: int) -> None:
        closed.append(code)
        await close(self, code)

    monkeypatch.setattr(_Writer, "send", recording_send)
    monkeypatch.setattr(_Writer, "close", recording_close)

    async def scenario() -> MemoryPort:
        broker = ScriptedBroker()
        port = MemoryPort()
        task, _ = await _subscribed(port, session_ticket, broker)
        broker.feed.put_nowait(None)
        broker.feed.put_nowait(Message("room:42", 1, _envelope(), 1.0, "e1"))
        port.push_json({"type": "ping"})
        port.push("{")
        await _finish(task)
        return port

    port = asyncio.run(scenario())
    assert written == port.sent
    assert {json.loads(text)["type"] for text in written} >= {"hello", "error"}
    assert closed == [port.close_code] == [Close.PROTOCOL]


class _TimingOutTunnel:
    async def __call__(self, request: Request, *, connection: str) -> Reply:
        raise TimeoutError

    def hints(self) -> TunnelHints:
        return TunnelHints(frozenset(), (), None)


@pytest.mark.django_db(transaction=True)
def test_timeout_raised_inside_the_tunnel_is_internal_not_timeout(
    session_ticket: SocketTicket,
) -> None:
    async def scenario() -> dict[str, object]:
        port = MemoryPort()
        socket = TopicSocket(
            port,
            _handshake(session_ticket),
            broker=MemoryBroker(),
            heartbeat=60,
            tunnel=_TimingOutTunnel(),
        )
        task, _ = await _open(socket, port, session_ticket)
        port.push_json({"type": "http", "id": "1", "method": "POST", "path": "/x/"})
        reply = await _frame(port)
        port.disconnect()
        await _finish(task)
        return reply

    reply = asyncio.run(scenario())
    assert reply["error"] == "internal"
