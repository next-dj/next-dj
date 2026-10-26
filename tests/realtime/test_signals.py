import asyncio
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

import pytest
from django.contrib.auth.models import AnonymousUser
from django.dispatch import Signal
from django.test import AsyncRequestFactory, RequestFactory

from next import signals as framework_signals
from next.partial import Patches
from next.realtime import TopicStream, apublish, pool as pool_module, publish
from next.realtime.access import AccessCheck, SocketAccessCheck, TopicAuthorizer
from next.realtime.backends import MemoryBroker
from next.realtime.errors import BrokerConnectionError
from next.realtime.lifecycle import StreamLifecycle
from next.realtime.messages import Message
from next.realtime.signals import (
    UnsubscribeReason,
    stream_subscribed,
    stream_unsubscribed,
)
from next.realtime.socket import Handshake, TopicSocket
from next.realtime.tickets import TicketClaims
from next.testing import (
    MemoryPort,
    SignalRecorder,
    SocketTicket,
    SseReader,
    capture_signals,
    memory_broker,
)
from tests.support.realtime import ServerClock, issue_session_ticket


_HERE = "tests.realtime.test_signals"
_GRANTS = {"room": True}


def _room_authorizer(_user: object, topic: str) -> bool:
    return _GRANTS["room"] and topic.startswith("room:")


@pytest.fixture(autouse=True)
def _grants() -> Iterator[None]:
    _GRANTS["room"] = True
    yield
    _GRANTS["room"] = True


@pytest.fixture()
def broker() -> Iterator[MemoryBroker]:
    with memory_broker() as backend:
        yield backend


@pytest.fixture()
def recorder() -> Iterator[SignalRecorder]:
    with capture_signals(stream_subscribed, stream_unsubscribed) as recording:
        yield recording


class _EndingBroker(MemoryBroker):
    """A memory broker whose subscriptions end on their own, as a server shutdown."""

    def subscribe(self, topics, *, timeout):
        return _ended()

    def asubscribe(self, topics, *, timeout):
        return _aended()


@contextmanager
def _ended() -> Iterator[Iterator[Message | None]]:
    yield iter(())


@asynccontextmanager
async def _aended() -> AsyncIterator[AsyncIterator[Message | None]]:
    yield _nothing()


async def _nothing() -> AsyncIterator[Message | None]:
    for item in ():
        yield item


class _DroppingBroker(MemoryBroker):
    """A memory broker whose live subscription loses its connection after hello."""

    def subscribe(self, topics, *, timeout):
        return _dropped()

    def asubscribe(self, topics, *, timeout):
        return _adropped()


@contextmanager
def _dropped() -> Iterator[Iterator[Message | None]]:
    yield _sync_drop()


def _sync_drop() -> Iterator[Message | None]:
    msg = "lost"
    raise BrokerConnectionError(msg)
    yield


@asynccontextmanager
async def _adropped() -> AsyncIterator[AsyncIterator[Message | None]]:
    yield _async_drop()


async def _async_drop() -> AsyncIterator[Message | None]:
    msg = "lost"
    raise BrokerConnectionError(msg)
    yield


class _BreakingBroker(MemoryBroker):
    """A memory broker whose live subscription raises an unexpected error after hello."""

    def subscribe(self, topics, *, timeout):
        return _broken()

    def asubscribe(self, topics, *, timeout):
        return _abroken()


@contextmanager
def _broken() -> Iterator[Iterator[Message | None]]:
    yield _sync_break()


def _sync_break() -> Iterator[Message | None]:
    msg = "subscription failed"
    raise RuntimeError(msg)
    yield


@asynccontextmanager
async def _abroken() -> AsyncIterator[AsyncIterator[Message | None]]:
    yield _async_break()


async def _async_break() -> AsyncIterator[Message | None]:
    msg = "subscription failed"
    raise RuntimeError(msg)
    yield


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


def _refresh() -> Patches:
    return Patches(None).refresh(zone="results")


def _reasons(recorder: SignalRecorder) -> list[str]:
    return [
        event.kwargs["reason"] for event in recorder.events_for(stream_unsubscribed)
    ]


async def _reported(recorder: SignalRecorder) -> None:
    for _ in range(500):
        if _reasons(recorder):
            return
        await asyncio.sleep(0.01)
    pytest.fail("The subscription never reported its end within five seconds.")


class TestAggregate:
    """The stream signals reach `next.signals` with the other realtime signals."""

    def test_the_stream_signals_are_reexported(self) -> None:
        assert framework_signals.stream_subscribed is stream_subscribed
        assert framework_signals.stream_unsubscribed is stream_unsubscribed

    def test_the_reasons_compare_as_their_wire_strings(self) -> None:
        assert [reason.value for reason in UnsubscribeReason] == [
            "client",
            "cancelled",
            "revoked",
            "error",
            "server",
        ]
        assert UnsubscribeReason.CLIENT == "client"


class TestLifecycle:
    """Each stream sends one subscribe and at most one matching unsubscribe."""

    def test_nothing_is_sent_without_receivers(self) -> None:
        lifecycle = StreamLifecycle(object(), "sse")
        lifecycle.open(("room:1",))
        with capture_signals(stream_unsubscribed) as recording:
            lifecycle.close(UnsubscribeReason.CLIENT)
        assert len(recording) == 0

    def test_an_unannounced_subscription_never_reports_its_end(self) -> None:
        async def scenario() -> SignalRecorder:
            lifecycle = StreamLifecycle(object(), "ws")
            await lifecycle.aopen(("room:1",))
            with capture_signals(stream_unsubscribed) as recording:
                await lifecycle.aclose(UnsubscribeReason.CLIENT)
            return recording

        assert len(asyncio.run(scenario())) == 0

    def test_the_unsubscribe_is_sent_once_with_the_noted_reason(
        self, recorder: SignalRecorder
    ) -> None:
        connection = object()
        lifecycle = StreamLifecycle(connection, "sse")
        lifecycle.open(("room:1", "room:2"), request="request")
        lifecycle.reason = UnsubscribeReason.REVOKED
        lifecycle.close(UnsubscribeReason.CLIENT)
        lifecycle.close(UnsubscribeReason.ERROR)
        opened = recorder.first_for(stream_subscribed)
        assert opened.sender is object
        assert opened.kwargs == {
            "transport": "sse",
            "topics": ("room:1", "room:2"),
            "connection": connection,
            "request": "request",
            "subject": None,
            "user": None,
        }
        (closed,) = recorder.events_for(stream_unsubscribed)
        assert closed.kwargs["reason"] is UnsubscribeReason.REVOKED
        assert closed.kwargs["topics"] == ("room:1", "room:2")
        assert closed.kwargs["connection"] is connection
        assert closed.kwargs["duration"] >= 0

    def test_an_unsubscribe_receiver_alone_hears_the_end(self) -> None:
        lifecycle = StreamLifecycle(object(), "sse")
        with capture_signals(stream_unsubscribed) as recording:
            lifecycle.open(("room:1",))
            lifecycle.close(UnsubscribeReason.SERVER)
        (closed,) = recording
        assert closed.kwargs["reason"] is UnsubscribeReason.SERVER
        assert closed.kwargs["topics"] == ("room:1",)

    def test_a_failing_receiver_is_logged_and_the_others_still_hear(
        self, recorder: SignalRecorder, caplog: pytest.LogCaptureFixture
    ) -> None:
        lifecycle = StreamLifecycle(object(), "sse")
        with _failing_receivers(), caplog.at_level("ERROR", "next.realtime"):
            lifecycle.open(("room:1", "room:2"))
            lifecycle.close(UnsubscribeReason.CLIENT)
        assert _reasons(recorder) == ["client"]
        assert len(recorder.events_for(stream_subscribed)) == 1
        logged = [
            record.getMessage()
            for record in caplog.records
            if record.name == "next.realtime.signals"
        ]
        assert len(logged) == 2
        assert "stream_subscribed" in logged[0]
        assert "stream_unsubscribed" in logged[1]
        assert "sse topics room:1, room:2" in logged[1]


class TestSyncStream:
    """A WSGI `TopicStream` reports its subscription on every way out."""

    def test_a_closed_response_is_the_client_leaving(
        self, broker: MemoryBroker, recorder: SignalRecorder
    ) -> None:
        request = RequestFactory().get("/stream/")
        stream = TopicStream(request, "room:1", heartbeat_seconds=0.01)
        with SseReader(stream) as events:
            assert next(events).event == "next-hello"
            assert len(recorder.events_for(stream_unsubscribed)) == 0
        opened = recorder.first_for(stream_subscribed)
        assert opened.sender is TopicStream
        assert opened.kwargs["transport"] == "sse"
        assert opened.kwargs["topics"] == ("room:1",)
        assert opened.kwargs["request"] is request
        assert opened.kwargs["subject"] is None
        assert opened.kwargs["user"] is None
        assert opened.kwargs["connection"] is stream
        closed = recorder.first_for(stream_unsubscribed)
        assert closed.kwargs["reason"] is UnsubscribeReason.CLIENT
        assert closed.kwargs["connection"] is stream

    def test_a_revoked_grant_ends_as_revoked(
        self, broker: MemoryBroker, recorder: SignalRecorder
    ) -> None:
        request = RequestFactory().get("/stream/")
        stream = TopicStream(
            request, "room:1", authorize=_room_authorizer, heartbeat_seconds=0.001
        )
        clock = ServerClock(0.0)
        stream._access._clock = clock
        with SseReader(stream) as events:
            assert next(events).event == "next-hello"
            _GRANTS["room"] = False
            clock.advance(0.01)
            publish("room:1", _refresh(), on_commit=False)
            assert next(events).event == "next-end"
        assert _reasons(recorder) == ["revoked"]

    def test_a_lost_broker_connection_is_an_error(
        self, recorder: SignalRecorder
    ) -> None:
        stream = TopicStream(RequestFactory().get("/"), "room:1", heartbeat_seconds=1)
        items = list(stream._sync_messages(_DroppingBroker()))
        assert [type(item).__name__ for item in items] == ["StreamHello"]
        assert _reasons(recorder) == ["error"]

    def test_a_failing_subscription_is_an_error_that_propagates(
        self, recorder: SignalRecorder
    ) -> None:
        stream = TopicStream(RequestFactory().get("/"), "room:1")
        source = stream._sync_messages(_BreakingBroker())
        assert type(next(source)).__name__ == "StreamHello"
        with pytest.raises(RuntimeError, match="subscription failed"):
            list(source)
        assert _reasons(recorder) == ["error"]

    def test_a_subscription_the_broker_ends_is_the_server(
        self, recorder: SignalRecorder
    ) -> None:
        stream = TopicStream(RequestFactory().get("/"), "room:1", heartbeat_seconds=1)
        list(stream._sync_messages(_EndingBroker()))
        assert _reasons(recorder) == ["server"]

    def test_a_denied_stream_never_subscribes(
        self, broker: MemoryBroker, recorder: SignalRecorder
    ) -> None:
        _GRANTS["room"] = False
        stream = TopicStream(
            RequestFactory().get("/"), "room:1", authorize=_room_authorizer
        )
        list(stream._sync_messages(broker))
        assert len(recorder) == 0

    def test_a_failing_receiver_leaves_the_stream_live(
        self, broker: MemoryBroker, recorder: SignalRecorder
    ) -> None:
        stream = TopicStream(RequestFactory().get("/"), "room:1", heartbeat_seconds=1)
        with _failing_receivers(), SseReader(stream) as events:
            assert next(events).event == "next-hello"
            publish("room:1", _refresh(), on_commit=False)
            assert next(events).event == "next-patches"
        assert _reasons(recorder) == ["client"]


class TestAsyncStream:
    """An ASGI `TopicStream` reports its subscription, its cancellation too."""

    def test_a_cancelled_response_is_the_client_leaving(
        self, broker: MemoryBroker, recorder: SignalRecorder
    ) -> None:
        async def scenario() -> TopicStream:
            request = AsyncRequestFactory().get("/stream/")
            stream = TopicStream(request, "room:1", heartbeat_seconds=0.01)
            async with SseReader(stream) as events:
                assert (await anext(events)).event == "next-hello"
            return stream

        stream = asyncio.run(scenario())
        opened = recorder.first_for(stream_subscribed)
        assert opened.kwargs["connection"] is stream
        assert opened.kwargs["transport"] == "sse"
        assert _reasons(recorder) == ["client"]

    def test_a_closed_source_is_the_client_leaving(
        self, broker: MemoryBroker, recorder: SignalRecorder
    ) -> None:
        async def scenario() -> None:
            stream = TopicStream(AsyncRequestFactory().get("/"), "room:1")
            source = stream._async_messages(broker)
            assert type(await anext(source)).__name__ == "StreamHello"
            await source.aclose()

        asyncio.run(scenario())
        assert _reasons(recorder) == ["client"]

    def test_a_revoked_grant_ends_as_revoked(
        self, broker: MemoryBroker, recorder: SignalRecorder
    ) -> None:
        async def scenario() -> list[str]:
            stream = TopicStream(
                AsyncRequestFactory().get("/"),
                "room:1",
                authorize=_room_authorizer,
                heartbeat_seconds=0.001,
            )
            clock = ServerClock(0.0)
            stream._access._clock = clock
            source = stream._async_messages(broker)
            assert type(await anext(source)).__name__ == "StreamHello"
            _GRANTS["room"] = False
            clock.advance(0.01)
            await apublish("room:1", _refresh())
            return [type(item).__name__ async for item in source if item is not None]

        assert asyncio.run(scenario()) == ["StreamEnd"]
        assert _reasons(recorder) == ["revoked"]

    def test_a_lost_broker_connection_is_an_error(
        self, recorder: SignalRecorder
    ) -> None:
        async def scenario() -> list[object]:
            stream = TopicStream(AsyncRequestFactory().get("/"), "room:1")
            return [item async for item in stream._async_messages(_DroppingBroker())]

        assert [type(item).__name__ for item in asyncio.run(scenario())] == [
            "StreamHello"
        ]
        assert _reasons(recorder) == ["error"]

    def test_a_failing_subscription_is_an_error_that_propagates(
        self, recorder: SignalRecorder
    ) -> None:
        async def scenario() -> None:
            stream = TopicStream(AsyncRequestFactory().get("/"), "room:1")
            source = stream._async_messages(_BreakingBroker())
            assert type(await anext(source)).__name__ == "StreamHello"
            with pytest.raises(RuntimeError, match="subscription failed"):
                await anext(source)

        asyncio.run(scenario())
        assert _reasons(recorder) == ["error"]

    def test_a_subscription_the_broker_ends_is_the_server(
        self, recorder: SignalRecorder
    ) -> None:
        async def scenario() -> None:
            stream = TopicStream(AsyncRequestFactory().get("/"), "room:1")
            async for _ in stream._async_messages(_EndingBroker()):
                pass

        asyncio.run(scenario())
        assert _reasons(recorder) == ["server"]


def _broken_receiver(**kwargs: object) -> None:
    msg = "receiver failed"
    raise ValueError(msg)


@contextmanager
def _failing_receivers() -> Iterator[None]:
    stream_subscribed.connect(_broken_receiver, weak=False)
    stream_unsubscribed.connect(_broken_receiver, weak=False)
    try:
        yield
    finally:
        stream_subscribed.disconnect(_broken_receiver)
        stream_unsubscribed.disconnect(_broken_receiver)


async def _serving(
    socket: TopicSocket, port: MemoryPort, ticket: SocketTicket
) -> asyncio.Task[None]:
    task = asyncio.create_task(socket.serve(port.inbound()))
    port.push_json(ticket.subscribe_frame())
    async with asyncio.timeout(5):
        assert (await port.receive_json())["type"] == "hello"
    return task


def _socket(
    port: MemoryPort,
    ticket: SocketTicket,
    broker: MemoryBroker,
    socket_class: type[TopicSocket] = TopicSocket,
) -> TopicSocket:
    handshake = Handshake(ticket.credential, AnonymousUser(), None)
    return socket_class(port, handshake, broker=broker, heartbeat=60)


@pytest.mark.django_db(transaction=True)
class TestSocket:
    """A `TopicSocket` reports its subscription on every way out."""

    def test_a_client_that_leaves_is_the_client(self, recorder: SignalRecorder) -> None:
        ticket = issue_session_ticket()

        async def scenario() -> TopicSocket:
            port = MemoryPort()
            socket = _socket(port, ticket, MemoryBroker())
            task = await _serving(socket, port, ticket)
            port.disconnect()
            await task
            return socket

        socket = asyncio.run(scenario())
        opened = recorder.first_for(stream_subscribed)
        assert opened.sender is TopicSocket
        assert opened.kwargs["transport"] == "ws"
        assert opened.kwargs["topics"] == ("room:42",)
        assert opened.kwargs["subject"] == socket.handshake.subject
        assert opened.kwargs["user"] is socket.handshake.user
        assert opened.kwargs["request"] is None
        assert opened.kwargs["connection"] is socket
        assert "handshake" not in opened.kwargs
        assert _reasons(recorder) == ["client"]

    def test_no_signal_argument_carries_the_credential(
        self, recorder: SignalRecorder
    ) -> None:
        ticket = issue_session_ticket()

        async def scenario() -> None:
            port = MemoryPort()
            task = await _serving(_socket(port, ticket, MemoryBroker()), port, ticket)
            port.disconnect()
            await task

        asyncio.run(scenario())
        assert len(recorder) == 2
        for event in recorder:
            for value in event.kwargs.values():
                assert value is not ticket.session
                assert ticket.credential not in repr(value)

    def test_a_cancelled_serve_is_cancelled(self, recorder: SignalRecorder) -> None:
        ticket = issue_session_ticket()

        async def scenario() -> None:
            port = MemoryPort()
            task = await _serving(_socket(port, ticket, MemoryBroker()), port, ticket)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        asyncio.run(scenario())
        assert _reasons(recorder) == ["cancelled"]

    def test_a_failing_subscribe_receiver_leaves_the_socket_live(
        self, recorder: SignalRecorder
    ) -> None:
        ticket = issue_session_ticket()

        async def scenario() -> dict[str, object]:
            broker = MemoryBroker()
            port = MemoryPort()
            task = await _serving(_socket(port, ticket, broker), port, ticket)
            await broker.apublish("room:42", _refresh().envelope())
            async with asyncio.timeout(5):
                frame = await port.receive_json()
            port.disconnect()
            await task
            assert port.close_code is None
            return frame

        with _failing_receivers():
            assert asyncio.run(scenario())["type"] == "patches"
        assert _reasons(recorder) == ["client"]

    def test_a_failing_unsubscribe_receiver_keeps_the_cancellation(
        self, recorder: SignalRecorder
    ) -> None:
        ticket = issue_session_ticket()

        async def scenario() -> bool:
            port = MemoryPort()
            task = await _serving(_socket(port, ticket, MemoryBroker()), port, ticket)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            return task.cancelled()

        with _failing_receivers():
            assert asyncio.run(scenario()) is True
        assert _reasons(recorder) == ["cancelled"]

    def test_an_unsubscribe_receiver_alone_hears_the_client(self) -> None:
        ticket = issue_session_ticket()

        async def scenario() -> None:
            port = MemoryPort()
            task = await _serving(_socket(port, ticket, MemoryBroker()), port, ticket)
            port.disconnect()
            await task

        with capture_signals(stream_unsubscribed) as recording:
            asyncio.run(scenario())
        assert [event.kwargs["reason"] for event in recording] == ["client"]

    def test_a_revoked_grant_is_revoked(self, recorder: SignalRecorder) -> None:
        ticket = issue_session_ticket(authorizer_ref=f"{_HERE}._room_authorizer")

        async def scenario() -> None:
            broker = MemoryBroker()
            port = MemoryPort()
            socket = _socket(port, ticket, broker, _Rechecking)
            task = await _serving(socket, port, ticket)
            _GRANTS["room"] = False
            await broker.apublish("room:42", _refresh().envelope())
            await task

        asyncio.run(scenario())
        assert _reasons(recorder) == ["revoked"]

    def test_a_lost_broker_connection_is_an_error(
        self, recorder: SignalRecorder
    ) -> None:
        ticket = issue_session_ticket()

        async def scenario() -> None:
            port = MemoryPort()
            task = await _serving(
                _socket(port, ticket, _DroppingBroker()), port, ticket
            )
            await task

        asyncio.run(scenario())
        assert _reasons(recorder) == ["error"]

    def test_a_protocol_close_is_the_server(self, recorder: SignalRecorder) -> None:
        ticket = issue_session_ticket()

        async def scenario() -> None:
            port = MemoryPort()
            task = await _serving(_socket(port, ticket, MemoryBroker()), port, ticket)
            port.push("{")
            await task

        asyncio.run(scenario())
        assert _reasons(recorder) == ["server"]

    def test_a_subscription_the_broker_ends_reports_once(
        self, recorder: SignalRecorder
    ) -> None:
        ticket = issue_session_ticket()

        async def scenario() -> None:
            port = MemoryPort()
            task = await _serving(_socket(port, ticket, _EndingBroker()), port, ticket)
            await _reported(recorder)
            assert _reasons(recorder) == ["server"]
            port.disconnect()
            await task

        asyncio.run(scenario())
        assert _reasons(recorder) == ["server"]

    def test_a_refused_ticket_never_subscribes(self, recorder: SignalRecorder) -> None:
        ticket = issue_session_ticket()

        async def scenario() -> None:
            port = MemoryPort()
            socket = _socket(port, ticket, MemoryBroker())
            task = asyncio.create_task(socket.serve(port.inbound()))
            port.push_json({"type": "sub", "ticket": "forged"})
            await task

        asyncio.run(scenario())
        assert len(recorder) == 0


class TestPooledSend:
    """An async send runs sync receivers on the shared pool, never on one thread."""

    def test_two_streams_run_their_sync_receivers_side_by_side(self) -> None:
        barrier = threading.Barrier(2, timeout=5)
        threads: list[int] = []

        def receiver(**_kwargs: object) -> None:
            barrier.wait()
            threads.append(threading.get_ident())

        async def scenario() -> None:
            first = StreamLifecycle(object(), "ws")
            second = StreamLifecycle(object(), "ws")
            await asyncio.gather(first.aopen(("room:1",)), second.aopen(("room:2",)))

        stream_subscribed.connect(receiver, weak=False)
        try:
            asyncio.run(scenario())
        finally:
            stream_subscribed.disconnect(receiver)
        assert len(set(threads)) == 2
        assert threading.get_ident() not in threads

    def test_each_sync_call_is_wrapped_in_connection_cleanup(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        steps: list[str] = []
        monkeypatch.setattr(
            pool_module, "close_old_connections", lambda: steps.append("clean")
        )

        def receiver(**_kwargs: object) -> None:
            steps.append("receiver")

        stream_unsubscribed.connect(receiver, weak=False)
        try:
            asyncio.run(stream_unsubscribed.asend_robust(sender=object))
        finally:
            stream_unsubscribed.disconnect(receiver)
        assert steps == ["clean", "receiver", "clean"]

    def test_async_receivers_are_awaited_on_the_loop_and_failures_returned(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        threads: list[int] = []

        async def listening(**_kwargs: object) -> str:
            threads.append(threading.get_ident())
            return "heard"

        async def failing(**_kwargs: object) -> None:
            msg = "receiver failed"
            raise LookupError(msg)

        stream_subscribed.connect(listening, weak=False)
        stream_subscribed.connect(failing, weak=False)
        try:
            with caplog.at_level("ERROR", "django.dispatch"):
                responses = asyncio.run(stream_subscribed.asend_robust(sender=object))
        finally:
            stream_subscribed.disconnect(listening)
            stream_subscribed.disconnect(failing)
        assert threads == [threading.get_ident()]
        assert responses[0] == (listening, "heard")
        assert responses[1][0] is failing
        assert isinstance(responses[1][1], LookupError)
        assert "failing" in caplog.text

    def test_a_failing_sync_receiver_is_returned_and_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        stream_subscribed.connect(_broken_receiver, weak=False)
        try:
            with caplog.at_level("ERROR", "django.dispatch"):
                responses = asyncio.run(stream_subscribed.asend_robust(sender=object))
        finally:
            stream_subscribed.disconnect(_broken_receiver)
        [(receiver, error)] = responses
        assert receiver is _broken_receiver
        assert isinstance(error, ValueError)
        assert "_broken_receiver" in caplog.text

    @pytest.mark.parametrize("mode", ["sync", "async"])
    def test_a_failure_logs_the_message_django_logs(
        self, caplog: pytest.LogCaptureFixture, mode: str
    ) -> None:
        async def failing(**_kwargs: object) -> None:
            _broken_receiver()

        receiver = failing if mode == "async" else _broken_receiver
        plain = Signal()
        for signal in (plain, stream_subscribed):
            signal.connect(receiver, weak=False)
        try:
            with caplog.at_level("ERROR", "django.dispatch"):
                plain.send_robust(sender=object)
                asyncio.run(stream_subscribed.asend_robust(sender=object))
        finally:
            for signal in (plain, stream_subscribed):
                signal.disconnect(receiver)
        django, pooled = caplog.records
        assert pooled.getMessage() == django.getMessage()
        assert pooled.levelname == django.levelname == "ERROR"
        assert pooled.exc_info is not None
        assert isinstance(pooled.exc_info[1], ValueError)

    def test_django_splits_the_live_receivers_into_sync_and_async(self) -> None:
        def blocking(**_kwargs: object) -> None:
            return None

        async def waiting(**_kwargs: object) -> None:
            return None

        stream_subscribed.connect(blocking, weak=False)
        stream_subscribed.connect(waiting, weak=False)
        try:
            sync, asynchronous = stream_subscribed._live_receivers(object)
        finally:
            stream_subscribed.disconnect(blocking)
            stream_subscribed.disconnect(waiting)
        assert blocking in sync
        assert waiting in asynchronous
        assert waiting not in sync

    def test_a_signal_without_receivers_answers_nothing(self) -> None:
        assert asyncio.run(stream_subscribed.asend_robust(sender=object)) == []
