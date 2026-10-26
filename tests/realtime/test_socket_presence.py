import asyncio
import logging
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model, login
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory

from next.partial import Patches
from next.realtime import WebSocketApp, socket as socket_module
from next.realtime.attendance import Attendance
from next.realtime.backends import BrokerBackend, MemoryBroker
from next.realtime.frames import Close
from next.realtime.manager import broker_manager
from next.realtime.messages import Message
from next.realtime.roster import EVENT
from next.realtime.socket import Handshake, SocketClock, TopicSocket
from next.testing.realtime import (
    MemoryPort,
    SocketTicket,
    WebSocketCommunicator,
    issue_socket_ticket,
    override_websocket_mount,
)
from tests.support.realtime import (
    ROOM_AUTHORIZER_REF,
    LiveStreams,
    PlainBroker,
    issue_session_ticket,
    manual_schedule,
    refresh_envelope,
)


_HERE = "tests.realtime.test_socket_presence"
_TOPIC = "room:42"
_IDENTIFY = f"{_HERE}.identify_guest"

pytestmark = pytest.mark.django_db(transaction=True)


def identify_guest(user: object, topic: str) -> dict[str, object]:
    name = getattr(user, "username", "") or "guest"
    return {"name": name, "topic": topic}


def identify_visitor(user: object, topic: str, subject: str) -> dict[str, object]:
    return {"name": f"Guest {subject[:4]}"}


def failing_transform(message: Message) -> Patches:
    if message.envelope.ops[0].op == "event":
        return Patches(None).event("room:update", {"seq": message.seq})
    msg = "transform failed"
    raise RuntimeError(msg)


def _broker() -> MemoryBroker:
    return MemoryBroker(schedule=manual_schedule(lambda: 0.0))


def _socket(
    port: MemoryPort,
    ticket: SocketTicket,
    broker: BrokerBackend,
    *,
    heartbeat: float = 60,
) -> TopicSocket:
    handshake = Handshake(ticket.credential, AnonymousUser(), None)
    return TopicSocket(port, handshake, broker=broker, heartbeat=heartbeat)


async def _frame(port: MemoryPort) -> dict[str, object]:
    async with asyncio.timeout(5):
        return await port.receive_json()


async def _finish(task: asyncio.Task[None]) -> None:
    async with asyncio.timeout(5):
        await task


async def _open(
    port: MemoryPort, ticket: SocketTicket, broker: BrokerBackend
) -> tuple[TopicSocket, asyncio.Task[None], dict[str, object]]:
    socket = _socket(port, ticket, broker)
    task = asyncio.create_task(socket.serve(port.inbound()))
    port.push_json(ticket.subscribe_frame())
    hello = await _frame(port)
    assert hello["type"] == "hello"
    return socket, task, hello


def _presence_detail(frame: dict[str, object]) -> object:
    assert frame["type"] == "patches"
    (op,) = frame["data"]["ops"]
    assert op["op"] == "event"
    assert op["name"] == EVENT
    return op["detail"]


def test_ticket_presence_joins_before_hello_under_the_connection_id() -> None:
    ticket = issue_session_ticket(presence=_IDENTIFY)

    async def scenario() -> None:
        broker = _broker()
        port = MemoryPort()
        socket, task, _ = await _open(port, ticket, broker)
        assert broker.presence(_TOPIC).count == 1
        assert broker.presence(_TOPIC).members == ({"name": "guest", "topic": _TOPIC},)
        assert set(broker._rosters[_TOPIC].identified) == {socket.connection_id}
        port.disconnect()
        await _finish(task)
        assert broker.presence(_TOPIC).count == 0

    asyncio.run(scenario())


def test_an_identify_taking_the_subject_names_an_anonymous_member() -> None:
    ticket = issue_session_ticket(presence=f"{_HERE}.identify_visitor")

    async def scenario() -> None:
        broker = _broker()
        port = MemoryPort()
        socket, task, _ = await _open(port, ticket, broker)
        subject = socket.handshake.subject
        assert subject
        assert broker.presence(_TOPIC).members == ({"name": f"Guest {subject[:4]}"},)
        port.disconnect()
        await _finish(task)

    asyncio.run(scenario())


def test_two_visitors_are_two_members_under_their_own_subjects() -> None:
    reference = f"{_HERE}.identify_visitor"
    tickets = [issue_session_ticket(presence=reference) for _ in range(2)]

    async def scenario() -> None:
        broker = _broker()
        ports = [MemoryPort() for _ in tickets]
        opened = [
            await _open(port, ticket, broker)
            for port, ticket in zip(ports, tickets, strict=True)
        ]
        names = {f"Guest {socket.handshake.subject[:4]}" for socket, _, _ in opened}
        members = broker.presence(_TOPIC).members
        assert len(members) == 2
        assert {member["name"] for member in members} == names
        for port, (_, task, _) in zip(ports, opened, strict=True):
            port.disconnect()
            await _finish(task)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("presence", "detail"),
    [
        (True, {"count": 2}),
        (_IDENTIFY, {"count": 1, "members": [{"name": "guest", "topic": _TOPIC}]}),
    ],
    ids=["count", "members"],
)
def test_snapshot_reaches_the_other_subscriber_past_its_hello(
    detail: dict[str, object], *, presence: bool | str
) -> None:
    first = issue_session_ticket(presence=presence)
    second = issue_session_ticket(presence=presence)

    async def scenario() -> None:
        broker = _broker()
        early, late = MemoryPort(), MemoryPort()
        _, early_task, _ = await _open(early, first, broker)
        assert _presence_detail(await _frame(early))["count"] == 1
        _, late_task, _ = await _open(late, second, broker)
        assert _presence_detail(await _frame(late)) == detail
        assert broker.presence_schedule.run_due(10) == 1
        frame = await _frame(early)
        assert _presence_detail(frame) == detail
        assert {"seq", "epoch"}.isdisjoint(frame["data"])
        assert frame["data"]["topic"] == _TOPIC
        assert broker.positions([_TOPIC])[_TOPIC].seq == 0
        for port, task in ((early, early_task), (late, late_task)):
            port.disconnect()
            await _finish(task)

    asyncio.run(scenario())


def test_ticket_without_presence_stays_out_of_the_roster(
    session_ticket: SocketTicket,
) -> None:
    async def scenario() -> None:
        broker = _broker()
        port = MemoryPort()
        _, task, _ = await _open(port, session_ticket, broker)
        assert broker.presence(_TOPIC).count == 0
        assert broker.presence_schedule.pending() == ()
        port.disconnect()
        await _finish(task)

    asyncio.run(scenario())


def test_cancelling_serve_leaves_the_roster() -> None:
    ticket = issue_session_ticket(presence=True)

    async def scenario() -> None:
        broker = _broker()
        port = MemoryPort()
        _, task, _ = await _open(port, ticket, broker)
        assert broker.presence(_TOPIC).count == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert broker.presence(_TOPIC).count == 0

    asyncio.run(scenario())


def test_failing_transform_leaves_the_roster(caplog: pytest.LogCaptureFixture) -> None:
    ticket = issue_session_ticket(
        presence=True, transform_ref=f"{_HERE}.failing_transform"
    )

    async def scenario() -> None:
        broker = _broker()
        port = MemoryPort()
        _, task, _ = await _open(port, ticket, broker)
        await broker.apublish(_TOPIC, refresh_envelope())
        await _finish(task)
        assert port.close_code == Close.INTERNAL
        assert broker.presence(_TOPIC).count == 0

    asyncio.run(scenario())
    assert "Realtime WebSocket stream failed" in caplog.text


class StalledPort(MemoryPort):
    """A memory port whose peer never reads, so every send blocks."""

    def __init__(self) -> None:
        """Start with no send attempted."""
        super().__init__()
        self.entered = asyncio.Event()

    async def send(self, text: str) -> None:
        self.entered.set()
        await asyncio.Event().wait()


class _LongLived(Attendance):
    ttl_heartbeats = 60_000


class _PatientSocket(TopicSocket):
    async def attend(self, broker, claims):
        members = dict.fromkeys(claims.topics)
        return _LongLived(
            broker, members, heartbeat=self._heartbeat, connection=self.connection_id
        )


def test_watchdog_drop_leaves_the_roster(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    caplog.set_level(logging.INFO, logger=socket_module.__name__)
    ticket = issue_session_ticket(presence=True)
    readings = [0.0, 0.0]

    def monotonic() -> float:
        return readings.pop(0) if readings else 10.0

    monkeypatch.setattr(socket_module, "time", SimpleNamespace(monotonic=monotonic))

    async def scenario() -> None:
        broker = _broker()
        port = StalledPort()
        handshake = Handshake(ticket.credential, AnonymousUser(), None)
        socket = _PatientSocket(port, handshake, broker=broker, heartbeat=0.001)
        task = asyncio.create_task(socket.serve(port.inbound()))
        port.push_json(ticket.subscribe_frame())
        async with asyncio.timeout(5):
            await port.entered.wait()
        assert broker.presence(_TOPIC).count == 1
        await _finish(task)
        assert port.close_code is None
        assert broker.presence(_TOPIC).count == 0

    asyncio.run(scenario())
    assert "dropped a client that stopped reading" in caplog.text


class _ManualClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


_LIVENESS_HEARTBEAT = 0.05


def _live_socket(
    port: MemoryPort, ticket: SocketTicket, broker: BrokerBackend, clock: _ManualClock
) -> TopicSocket:
    handshake = Handshake(ticket.credential, AnonymousUser(), None)
    return TopicSocket(
        port,
        handshake,
        broker=broker,
        heartbeat=_LIVENESS_HEARTBEAT,
        clock=SocketClock(monotonic=clock),
    )


async def _ping(port: MemoryPort) -> None:
    async with asyncio.timeout(5):
        while (await port.receive_json())["type"] != "ping":
            pass


def test_a_peer_silent_two_heartbeats_past_a_ping_is_dropped_from_the_roster(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=socket_module.__name__)
    ticket = issue_session_ticket(presence=True)

    async def scenario() -> None:
        broker = _broker()
        clock = _ManualClock()
        port = MemoryPort()
        socket = _live_socket(port, ticket, broker, clock)
        task = asyncio.create_task(socket.serve(port.inbound()))
        port.push_json(ticket.subscribe_frame())
        await _ping(port)
        clock.now = 2 * _LIVENESS_HEARTBEAT
        await asyncio.sleep(4 * _LIVENESS_HEARTBEAT)
        assert not task.done()
        assert broker.presence(_TOPIC).count == 1
        clock.now += 0.001
        await _finish(task)
        assert port.close_code is None
        assert broker.presence(_TOPIC).count == 0

    asyncio.run(scenario())
    assert "dropped a client that stopped answering" in caplog.text


def test_a_peer_answering_every_ping_stays_in_the_roster() -> None:
    ticket = issue_session_ticket(presence=True)

    async def scenario() -> None:
        broker = _broker()
        clock = _ManualClock()
        port = MemoryPort()
        socket = _live_socket(port, ticket, broker, clock)
        task = asyncio.create_task(socket.serve(port.inbound()))
        port.push_json(ticket.subscribe_frame())
        for _ in range(6):
            await _ping(port)
            port.push_json({"type": "pong"})
            clock.now += _LIVENESS_HEARTBEAT
        assert not task.done()
        assert broker.presence(_TOPIC).count == 1
        port.disconnect()
        await _finish(task)

    asyncio.run(scenario())


async def _trickle(broker: BrokerBackend) -> None:
    while True:
        await broker.apublish(_TOPIC, refresh_envelope())
        await asyncio.sleep(_LIVENESS_HEARTBEAT / 5)


def test_a_silent_peer_on_a_trickling_topic_is_pinged_and_dropped(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger=socket_module.__name__)
    ticket = issue_session_ticket(presence=True)

    async def scenario() -> None:
        broker = _broker()
        clock = _ManualClock()
        port = MemoryPort()
        socket = _live_socket(port, ticket, broker, clock)
        task = asyncio.create_task(socket.serve(port.inbound()))
        port.push_json(ticket.subscribe_frame())
        assert (await _frame(port))["type"] == "hello"
        trickle = asyncio.create_task(_trickle(broker))
        await _ping(port)
        clock.now = 2 * _LIVENESS_HEARTBEAT
        await asyncio.sleep(4 * _LIVENESS_HEARTBEAT)
        assert not task.done()
        clock.now += 0.001
        await _finish(task)
        trickle.cancel()
        assert port.close_code is None
        assert broker.presence(_TOPIC).count == 0

    asyncio.run(scenario())
    assert "dropped a client that stopped answering" in caplog.text


def test_a_ponging_peer_on_a_trickling_topic_stays() -> None:
    ticket = issue_session_ticket(presence=True)

    async def scenario() -> None:
        broker = _broker()
        clock = _ManualClock()
        port = MemoryPort()
        socket = _live_socket(port, ticket, broker, clock)
        task = asyncio.create_task(socket.serve(port.inbound()))
        port.push_json(ticket.subscribe_frame())
        trickle = asyncio.create_task(_trickle(broker))
        for _ in range(4):
            await _ping(port)
            port.push_json({"type": "pong"})
            clock.now += 3 * _LIVENESS_HEARTBEAT
        assert not task.done()
        assert broker.presence(_TOPIC).count == 1
        trickle.cancel()
        port.disconnect()
        await _finish(task)

    asyncio.run(scenario())


def test_idle_heartbeat_touches_the_record_before_its_ping() -> None:
    ticket = issue_session_ticket(presence=True)

    async def scenario() -> None:
        broker = _broker()
        port = MemoryPort()
        socket = _socket(port, ticket, broker, heartbeat=0.01)
        task = asyncio.create_task(socket.serve(port.inbound()))
        port.push_json(ticket.subscribe_frame())
        assert (await _frame(port))["type"] == "hello"
        (record,) = broker._rosters[_TOPIC].anonymous.values()
        assert _presence_detail(await _frame(port)) == {"count": 1}
        assert (await _frame(port))["type"] == "ping"
        (touched,) = broker._rosters[_TOPIC].anonymous.values()
        assert touched > record
        port.disconnect()
        await _finish(task)

    asyncio.run(scenario())


def test_presence_on_a_broker_without_it_closes_internal(
    caplog: pytest.LogCaptureFixture, streams: LiveStreams
) -> None:
    ticket = issue_session_ticket(presence=True)

    async def scenario() -> None:
        broker = PlainBroker()
        port = MemoryPort()
        socket = _socket(port, ticket, broker)
        port.push_json(ticket.subscribe_frame())
        await _finish(asyncio.create_task(socket.serve(port.inbound())))
        assert port.sent == []
        assert port.close_code == Close.INTERNAL
        assert streams.live() == 0

    asyncio.run(scenario())
    assert "keeps no presence" in caplog.text


@pytest.mark.parametrize(
    "reference", [f"{_HERE}.missing", f"{_HERE}._TOPIC"], ids=["missing", "constant"]
)
def test_unusable_identify_reference_closes_with_auth_error(reference: str) -> None:
    ticket = issue_session_ticket(presence=reference)

    async def scenario() -> None:
        broker = _broker()
        port = MemoryPort()
        socket = _socket(port, ticket, broker)
        port.push_json(ticket.subscribe_frame())
        await _finish(asyncio.create_task(socket.serve(port.inbound())))
        assert port.close_code == Close.SESSION
        assert broker.presence(_TOPIC).count == 0

    asyncio.run(scenario())


def test_app_identifies_the_session_user_by_the_ticket_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user = get_user_model().objects.create_user(username="ada", password="pw")
    request = RequestFactory().get("/")
    request.session = SessionStore()
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    request.session.save()
    ticket = issue_socket_ticket(
        (_TOPIC,),
        authorizer_ref=ROOM_AUTHORIZER_REF,
        presence=_IDENTIFY,
        session=request.session,
    )
    broker = _broker()
    monkeypatch.setattr(broker_manager, "_backend", broker)

    async def scenario() -> None:
        with override_websocket_mount():
            communicator = WebSocketCommunicator(
                WebSocketApp(),
                "/_next/ws/",
                headers=[
                    (b"host", b"testserver"),
                    (b"origin", b"http://testserver"),
                    ticket.cookie,
                ],
            )
            assert (await communicator.connect())[0]
            await communicator.send_json_to(ticket.subscribe_frame())
            assert (await communicator.receive_json_from())["type"] == "hello"
            assert broker.presence(_TOPIC).members == (
                {"name": "ada", "topic": _TOPIC},
            )
            await communicator.disconnect()
        assert broker.presence(_TOPIC).count == 0

    asyncio.run(scenario())


class _SilentSocket(TopicSocket):
    async def attend(self, broker, claims):
        return None


def test_socket_attend_hook_can_keep_a_ticket_out_of_the_roster() -> None:
    ticket = issue_session_ticket(presence=True)

    async def scenario() -> None:
        broker = _broker()
        port = MemoryPort()
        handshake = Handshake(ticket.credential, AnonymousUser(), None)
        socket = _SilentSocket(port, handshake, broker=broker, heartbeat=60)
        task = asyncio.create_task(socket.serve(port.inbound()))
        port.push_json(ticket.subscribe_frame())
        assert (await _frame(port))["type"] == "hello"
        assert broker.presence(_TOPIC).count == 0
        port.disconnect()
        await _finish(task)

    asyncio.run(scenario())
