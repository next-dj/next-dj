import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator, Mapping
from importlib import import_module

import pytest
from asgiref.typing import ASGIReceiveCallable, ASGISendCallable, Scope
from django.contrib.auth import (
    BACKEND_SESSION_KEY,
    HASH_SESSION_KEY,
    SESSION_KEY,
    get_user_model,
)
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.http import StreamingHttpResponse
from django.test import override_settings

from next.conf import next_framework_settings
from next.partial import Patches
from next.realtime import MemoryBroker, apublish, publish
from next.realtime.binding import SessionBinding
from next.realtime.frames import Refusal, Reply
from next.realtime.manager import broker_manager
from next.realtime.socket import Handshake, TopicSocket
from next.realtime.tickets import verify_ticket
from next.testing import (
    MemoryPort,
    PublishedMessages,
    SocketTicket,
    SseEvent,
    SseReader,
    WebsocketCommunicator,
    aissue_socket_ticket,
    capture_published,
    issue_socket_ticket,
    memory_broker,
)
from tests.support.realtime import ROOM_AUTHORIZER_REF


type App = Callable[[Scope, ASGIReceiveCallable, ASGISendCallable], Awaitable[None]]


def _scripted(*replies: Mapping[str, object], scopes: list[Scope] | None = None) -> App:
    """Answer connect with `replies`, then echo every frame until the client leaves."""

    async def app(
        scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        if scopes is not None:
            scopes.append(scope)
        await receive()
        for reply in replies:
            await send(reply)
        while (message := await receive())["type"] == "websocket.receive":
            await send({**message, "type": "websocket.send"})

    return app


_ACCEPT = {"type": "websocket.accept", "subprotocol": None}


class TestWebsocketCommunicator:
    """The communicator speaks the ASGI WebSocket events of any application."""

    def test_scope_carries_the_path_query_headers_and_subprotocols(self) -> None:
        scopes: list[Scope] = []

        async def scenario() -> None:
            communicator = WebsocketCommunicator(
                _scripted(_ACCEPT, scopes=scopes),
                "/_next/ws/a%20b/?x=1",
                headers=[(b"origin", b"http://testserver")],
                subprotocols=["next"],
            )
            assert await communicator.connect() == (True, None)
            await communicator.disconnect()

        asyncio.run(scenario())
        scope = scopes[0]
        assert scope["type"] == "websocket"
        assert scope["path"] == "/_next/ws/a b/"
        assert scope["raw_path"] == b"/_next/ws/a%20b/"
        assert scope["query_string"] == b"x=1"
        assert scope["headers"] == [(b"origin", b"http://testserver")]
        assert scope["subprotocols"] == ["next"]

    def test_connect_returns_the_accepted_subprotocol(self) -> None:
        async def scenario() -> None:
            accept = {"type": "websocket.accept", "subprotocol": "next"}
            communicator = WebsocketCommunicator(_scripted(accept), "/")
            assert await communicator.connect() == (True, "next")
            await communicator.disconnect()

        asyncio.run(scenario())

    @pytest.mark.parametrize(
        ("close", "code"),
        [
            ({"type": "websocket.close", "code": 4401}, 4401),
            ({"type": "websocket.close"}, 1000),
        ],
    )
    def test_connect_refused_returns_the_close_code(
        self, close: dict[str, object], code: int
    ) -> None:
        async def scenario() -> None:
            communicator = WebsocketCommunicator(_scripted(close), "/")
            assert await communicator.connect() == (False, code)
            await communicator.disconnect()

        asyncio.run(scenario())

    def test_connect_rejects_an_unexpected_event(self) -> None:
        async def scenario() -> None:
            send = {"type": "websocket.send", "text": "early"}
            communicator = WebsocketCommunicator(_scripted(send), "/")
            with pytest.raises(AssertionError, match=r"websocket\.accept"):
                await communicator.connect()
            await communicator.disconnect()

        asyncio.run(scenario())

    def test_frames_round_trip_as_text_bytes_and_json(self) -> None:
        async def scenario() -> None:
            communicator = WebsocketCommunicator(_scripted(_ACCEPT), "/")
            await communicator.connect()
            await communicator.send_to("hi")
            assert await communicator.receive_from() == "hi"
            await communicator.send_to(bytes_data=b"\x00")
            assert await communicator.receive_from() == b"\x00"
            await communicator.send_json_to({"type": "pong"})
            assert await communicator.receive_json_from() == {"type": "pong"}
            await communicator.disconnect()

        asyncio.run(scenario())

    @pytest.mark.parametrize(
        "frame", [{"text": "[1]"}, {"bytes": b"{}"}], ids=["array", "binary"]
    )
    def test_receive_json_from_rejects_anything_but_an_object(
        self, frame: dict[str, object]
    ) -> None:
        async def scenario() -> None:
            communicator = WebsocketCommunicator(_scripted(_ACCEPT), "/")
            await communicator.connect()
            await communicator.send_to(
                text_data=frame.get("text"), bytes_data=frame.get("bytes")
            )
            with pytest.raises(AssertionError, match="JSON object"):
                await communicator.receive_json_from()
            await communicator.disconnect()

        asyncio.run(scenario())

    def test_receive_from_rejects_a_close(self) -> None:
        async def scenario() -> None:
            close = {"type": "websocket.close", "code": 4400}
            communicator = WebsocketCommunicator(_scripted(_ACCEPT, close), "/")
            await communicator.connect()
            with pytest.raises(AssertionError, match=r"websocket\.send"):
                await communicator.receive_from()
            await communicator.disconnect()

        asyncio.run(scenario())

    @pytest.mark.parametrize(
        ("close", "code"),
        [
            ({"type": "websocket.close", "code": 4403}, 4403),
            ({"type": "websocket.close"}, 1000),
        ],
    )
    def test_receive_close_returns_the_code(
        self, close: dict[str, object], code: int
    ) -> None:
        async def scenario() -> None:
            communicator = WebsocketCommunicator(_scripted(_ACCEPT, close), "/")
            await communicator.connect()
            assert await communicator.receive_close() == code
            await communicator.disconnect()

        asyncio.run(scenario())

    def test_receive_close_rejects_a_frame(self) -> None:
        async def scenario() -> None:
            communicator = WebsocketCommunicator(_scripted(_ACCEPT), "/")
            await communicator.connect()
            await communicator.send_to("hi")
            with pytest.raises(AssertionError, match=r"websocket\.close"):
                await communicator.receive_close()
            await communicator.disconnect()

        asyncio.run(scenario())


class TestMemoryPort:
    """The memory port records what a socket sends and feeds what a client says."""

    def test_sent_frames_are_recorded_and_awaitable(self) -> None:
        async def scenario() -> None:
            port = MemoryPort()
            await port.send('{"type": "ping"}')
            await port.send("plain")
            assert await port.receive_json() == {"type": "ping"}
            assert await port.receive() == "plain"
            assert port.sent == ['{"type": "ping"}', "plain"]

        asyncio.run(scenario())

    def test_receive_json_rejects_a_non_object(self) -> None:
        async def scenario() -> None:
            port = MemoryPort()
            await port.send("[]")
            with pytest.raises(AssertionError, match="JSON object"):
                await port.receive_json()

        asyncio.run(scenario())

    def test_close_keeps_the_first_code_and_refuses_later_sends(self) -> None:
        async def scenario() -> None:
            port = MemoryPort()
            await port.close(4401)
            await port.close(1011)
            assert port.close_code == 4401
            with pytest.raises(OSError, match="closed"):
                await port.send("late")
            assert port.sent == []

        asyncio.run(scenario())

    def test_inbound_yields_pushed_frames_until_disconnect(self) -> None:
        async def scenario() -> list[str | bytes]:
            port = MemoryPort()
            port.push("a")
            port.push(b"b")
            port.push_json({"type": "pong"})
            port.disconnect()
            with pytest.raises(OSError, match="closed"):
                await port.send("after")
            return [item async for item in port.inbound()]

        assert asyncio.run(scenario()) == ["a", b"b", json.dumps({"type": "pong"})]

    @pytest.mark.django_db(transaction=True)
    def test_drives_a_topic_socket_from_subscribe_to_hello(self) -> None:
        ticket = issue_socket_ticket(("room:1",), authorizer_ref=ROOM_AUTHORIZER_REF)
        handshake = Handshake(ticket.credential, AnonymousUser(), None)

        async def scenario() -> dict[str, object]:
            port = MemoryPort()
            port.push_json(ticket.subscribe_frame())
            serving = asyncio.create_task(
                TopicSocket(port, handshake).serve(port.inbound())
            )
            hello = await port.receive_json()
            port.disconnect()
            await serving
            return hello

        hello = asyncio.run(scenario())
        assert hello["type"] == "hello"
        assert list(hello["topics"]) == ["room:1"]


@pytest.mark.django_db()
class TestIssueSocketTicket:
    """The ticket helper signs through the negotiation path, never by hand."""

    def test_issues_a_ticket_under_a_fresh_session(self) -> None:
        ticket = issue_socket_ticket(
            ("room:1", "room:2"),
            authorizer_ref=ROOM_AUTHORIZER_REF,
            transform_ref="tests.support.realtime.refresh_envelope",
        )
        assert isinstance(ticket, SocketTicket)
        assert ticket.session.exists(ticket.credential)
        claims = verify_ticket(ticket.grant.token, ticket.credential)
        assert claims.topics == ("room:1", "room:2")
        assert claims.authorizer_ref == ROOM_AUTHORIZER_REF
        assert claims.transform_ref == "tests.support.realtime.refresh_envelope"

    @pytest.mark.django_db(transaction=True)
    def test_the_async_twin_issues_from_an_event_loop(self) -> None:
        session = SessionStore()
        session.create()

        async def scenario() -> tuple[SocketTicket, SocketTicket]:
            fresh = await aissue_socket_ticket(
                ("room:1",), authorizer_ref=ROOM_AUTHORIZER_REF
            )
            bound = await aissue_socket_ticket(
                ("room:2",),
                authorizer_ref=ROOM_AUTHORIZER_REF,
                transform_ref="tests.support.realtime.refresh_envelope",
                session=session,
            )
            return fresh, bound

        fresh, bound = asyncio.run(scenario())
        assert fresh.session.exists(fresh.credential)
        assert verify_ticket(fresh.grant.token, fresh.credential).topics == ("room:1",)
        assert bound.session is session
        claims = verify_ticket(bound.grant.token, session.session_key)
        assert claims.topics == ("room:2",)
        assert claims.transform_ref == "tests.support.realtime.refresh_envelope"

    def test_binds_to_a_given_session(self) -> None:
        session = SessionStore()
        session.create()
        ticket = issue_socket_ticket(
            ("room:1",), authorizer_ref=ROOM_AUTHORIZER_REF, session=session
        )
        assert ticket.session is session
        assert ticket.credential == session.session_key

    @override_settings(SESSION_COOKIE_NAME="sid")
    def test_cookie_carries_the_session_under_its_cookie_name(self) -> None:
        ticket = issue_socket_ticket(("room:1",), authorizer_ref=ROOM_AUTHORIZER_REF)
        assert ticket.cookie == (b"cookie", f"sid={ticket.credential}".encode())

    def test_subscribe_frame_presents_the_ticket_and_an_optional_cursor(self) -> None:
        ticket = issue_socket_ticket(("room:1",), authorizer_ref=ROOM_AUTHORIZER_REF)
        token = ticket.grant.token
        assert ticket.subscribe_frame() == {"type": "sub", "ticket": token}
        cursor = {"room:1": {"epoch": "e1", "seq": 3}}
        assert ticket.subscribe_frame(cursor) == {
            "type": "sub",
            "ticket": token,
            "last_seq": cursor,
        }


def _protocol_app() -> App:
    """Answer `sub`, `up` and `http` frames the way a topic socket does, noisily."""

    async def app(
        scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        await receive()
        await send(_ACCEPT)
        while (message := await receive())["type"] == "websocket.receive":
            frame = json.loads(message["text"])
            await send({"type": "websocket.send", "text": "not json"})
            await send({"type": "websocket.send", "bytes": b"\x00"})
            for reply in _protocol_replies(frame):
                await send({"type": "websocket.send", "text": json.dumps(reply)})

    return app


def _protocol_replies(frame: dict[str, object]) -> list[dict[str, object]]:
    match frame["type"]:
        case "sub":
            return [{"type": "patches", "data": {}}, {"type": "hello", "topics": {}}]
        case "up":
            ack: dict[str, object] = {"type": "ack", "id": frame["id"], "ok": True}
            if frame["name"] == "bare":
                ack = {**ack, "ok": False}
            elif frame["name"] != "typing":
                ack = {**ack, "ok": False, "error": frame["name"]}
            return [{"type": "ack", "id": "other", "ok": True}, ack]
    if frame["path"] == "/refused/":
        return [{"type": "http", "id": frame["id"], "status": 0, "error": "limit"}]
    return [
        {
            "type": "http",
            "id": frame["id"],
            "status": 201,
            "headers": {"content-type": "text/plain"},
            "body": f"{frame['method']} {frame['body']}",
        }
    ]


class TestCommunicatorProtocol:
    """The communicator subscribes, sends events and tunnels requests by id."""

    @pytest.mark.django_db()
    def test_subscribe_returns_the_hello_and_holds_what_came_before(self) -> None:
        ticket = issue_socket_ticket(("room:1",), authorizer_ref=ROOM_AUTHORIZER_REF)

        async def scenario() -> None:
            communicator = WebsocketCommunicator(_protocol_app(), "/")
            await communicator.connect()
            hello = await communicator.subscribe(ticket, {"room:1": 3})
            assert hello == {"type": "hello", "topics": {}}
            assert await communicator.receive_from() == "not json"
            assert await communicator.receive_from() == b"\x00"
            assert await communicator.receive_json_from() == {
                "type": "patches",
                "data": {},
            }
            await communicator.disconnect()

        asyncio.run(scenario())

    def test_send_up_returns_none_or_the_ack_error(self) -> None:
        async def scenario() -> None:
            communicator = WebsocketCommunicator(_protocol_app(), "/")
            await communicator.connect()
            assert await communicator.send_up("room:1", "typing", {"on": True}) is None
            assert await communicator.send_up("room:1", "unknown") == "unknown"
            await communicator.disconnect()

        asyncio.run(scenario())

    @pytest.mark.parametrize("name", ["bare", "bogus"])
    def test_send_up_fails_readably_on_an_ack_without_a_known_error(
        self, name: str
    ) -> None:
        async def scenario() -> None:
            communicator = WebsocketCommunicator(_protocol_app(), "/")
            await communicator.connect()
            with pytest.raises(AssertionError, match="names a known error"):
                await communicator.send_up("room:1", name)
            await communicator.disconnect()

        asyncio.run(scenario())

    def test_a_held_answer_is_found_before_waiting(self) -> None:
        async def scenario() -> None:
            communicator = WebsocketCommunicator(_protocol_app(), "/")
            await communicator.connect()
            await communicator.send_json_to(
                {"type": "up", "topic": "room:1", "name": "typing", "id": "2"}
            )
            assert await communicator.send_up("room:1", "typing") is None
            assert await communicator.send_up("room:1", "unknown") is None
            assert await communicator.receive_from() == "not json"
            await communicator.disconnect()

        asyncio.run(scenario())

    def test_request_returns_the_reply_or_the_refusal(self) -> None:
        async def scenario() -> None:
            communicator = WebsocketCommunicator(_protocol_app(), "/", timeout=5)
            await communicator.connect()
            reply = await communicator.request(
                "POST", "/form/", headers={"accept": "*/*"}, body="a=1"
            )
            assert reply == Reply(201, {"content-type": "text/plain"}, "POST a=1")
            refusal = await communicator.request("GET", "/refused/")
            assert refusal is Refusal.LIMIT
            await communicator.disconnect()

        asyncio.run(scenario())

    def test_a_close_while_waiting_fails_the_wait_and_stays_held(self) -> None:
        event = {"type": "websocket.send", "text": json.dumps({"type": "event"})}
        close = {"type": "websocket.close", "code": 4401}

        async def scenario() -> None:
            communicator = WebsocketCommunicator(
                _scripted(_ACCEPT, event, close), "/", timeout=0.2
            )
            await communicator.connect()
            with pytest.raises(AssertionError, match="'ack' frame"):
                await communicator.send_up("room:1", "typing")
            with pytest.raises(AssertionError, match="'http' frame"):
                await communicator.request("GET", "/")
            assert await communicator.receive_json_from() == {"type": "event"}
            assert await communicator.receive_close() == 4401
            await communicator.disconnect()

        asyncio.run(scenario())

    def test_a_missing_answer_times_out(self) -> None:
        async def scenario() -> None:
            communicator = WebsocketCommunicator(_scripted(_ACCEPT), "/", timeout=0.05)
            await communicator.connect()
            with pytest.raises(TimeoutError):
                await communicator.send_up("room:1", "typing")

        asyncio.run(scenario())


def _response(
    *chunks: bytes, finished: list[str] | None = None
) -> StreamingHttpResponse:
    def body() -> Iterator[bytes]:
        try:
            yield from chunks
        finally:
            if finished is not None:
                finished.append("closed")

    return StreamingHttpResponse(body())


def _async_response(
    *chunks: bytes, finished: list[str], idle: bool = True
) -> StreamingHttpResponse:
    async def body() -> AsyncIterator[bytes]:
        try:
            for chunk in chunks:
                yield chunk
            if idle:
                await asyncio.Event().wait()
        except asyncio.CancelledError:
            finished.append("cancelled")
            raise
        finally:
            finished.append("closed")

    return StreamingHttpResponse(body())


_FRAMES = (
    b"retry: 3000\n\n",
    b": heartbeat\n\n",
    b'id: e:1\nevent: next-patches\ndata: {"seq": 1}\n\n',
    b"retry: soon\nevent: note\n\n",
    b"data: a\r\ndata:b\r\n",
    b"\r\nunknown: field\n\n",
    b"data: tail",
)


class TestSseReader:
    """The reader yields dispatched events and closes the stream as a server does."""

    def test_events_skip_heartbeats_retry_hints_and_empty_blocks(self) -> None:
        with SseReader(_response(*_FRAMES)) as events:
            assert list(events) == [
                SseEvent("next-patches", '{"seq": 1}', "e:1"),
                SseEvent("message", "a\nb"),
            ]
            assert events.retry == 3000

    def test_an_event_decodes_its_json_object(self) -> None:
        assert SseEvent("message", '{"a": 1}').json() == {"a": 1}
        with pytest.raises(AssertionError, match="JSON object"):
            SseEvent("message", "[1]").json()

    def test_closing_runs_the_stream_cleanup(self) -> None:
        finished: list[str] = []
        with SseReader(_response(*_FRAMES, finished=finished)) as events:
            assert next(events).id == "e:1"
        assert finished == ["closed"]

    @pytest.mark.django_db()
    def test_closing_keeps_the_test_database_connection(self) -> None:
        SseReader(_response(*_FRAMES)).close()
        assert not get_user_model().objects.exists()

    def test_async_events_and_the_cancelling_close(self) -> None:
        finished: list[str] = []

        async def scenario() -> list[SseEvent]:
            async with SseReader(
                _async_response(*_FRAMES, finished=finished)
            ) as events:
                return [await anext(events), await anext(events)]

        assert asyncio.run(scenario()) == [
            SseEvent("next-patches", '{"seq": 1}', "e:1"),
            SseEvent("message", "a\nb"),
        ]
        assert finished == ["cancelled", "closed"]

    def test_closing_an_async_stream_drains_frames_that_land_first(self) -> None:
        finished: list[str] = []

        async def scenario() -> None:
            response = _async_response(*_FRAMES, finished=finished, idle=False)
            reader = SseReader(response)
            assert (await anext(reader)).id == "e:1"
            await reader.aclose()

        asyncio.run(scenario())
        assert finished == ["closed"]

    def test_closing_an_unread_async_stream_never_starts_it(self) -> None:
        finished: list[str] = []

        async def scenario() -> None:
            await SseReader(_async_response(*_FRAMES, finished=finished)).aclose()

        asyncio.run(scenario())
        assert finished == []

    @pytest.mark.parametrize("delay", [0, 0.01])
    def test_a_cancelled_close_keeps_the_cancellation(self, delay: float) -> None:
        async def body() -> AsyncIterator[bytes]:
            yield _FRAMES[2]
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.sleep(0.05)
                raise

        async def scenario() -> bool:
            reader = SseReader(StreamingHttpResponse(body()))
            assert (await anext(reader)).id == "e:1"
            closing = asyncio.ensure_future(reader.aclose())
            await asyncio.sleep(delay)
            closing.cancel()
            with pytest.raises(asyncio.CancelledError):
                await closing
            return closing.cancelled()

        assert asyncio.run(scenario()) is True

    def test_iteration_matches_the_response_kind(self) -> None:
        sync_reader = SseReader(_response())
        async_reader = SseReader(_async_response(finished=[], idle=False))
        with pytest.raises(TypeError, match="async for"):
            next(async_reader)
        with pytest.raises(TypeError, match="`for`"):
            asyncio.run(anext(sync_reader))
        with pytest.raises(TypeError, match="`close`"):
            asyncio.run(sync_reader.aclose())
        asyncio.run(async_reader.aclose())


def _publish_then_fail(published: PublishedMessages) -> None:
    with published:
        publish("room:1", Patches(None).refresh(zone="a"), on_commit=False)
        raise RuntimeError


class TestCapturePublished:
    """Published messages are recorded on any broker, sync and async."""

    def test_records_sync_and_async_publishes_on_the_chosen_topics(self) -> None:
        with memory_broker(), capture_published("room:1") as published:
            publish("room:1", Patches(None).refresh(zone="a"), on_commit=False)
            publish("room:2", Patches(None).refresh(zone="b"), on_commit=False)
            asyncio.run(apublish("room:1", Patches(None).refresh(zone="c")))
        assert len(published) == 2
        assert [message.seq for message in published] == [1, 2]
        assert {message.topic for message in published} == {"room:1"}

    def test_records_every_topic_without_a_filter_and_stops(self) -> None:
        recorder = PublishedMessages()
        with memory_broker():
            with recorder.start():
                assert recorder.start() is recorder
                publish("room:1", Patches(None).refresh(zone="a"), on_commit=False)
                publish("room:2", Patches(None).refresh(zone="b"), on_commit=False)
            recorder.stop()
            publish("room:3", Patches(None).refresh(zone="c"), on_commit=False)
        assert [message.topic for message in recorder.messages] == ["room:1", "room:2"]

    def test_stops_recording_when_the_block_raises(self) -> None:
        published = capture_published()
        with memory_broker():
            with pytest.raises(RuntimeError):
                _publish_then_fail(published)
            publish("room:2", Patches(None).refresh(zone="b"), on_commit=False)
        assert [message.topic for message in published] == ["room:1"]


class TestMemoryBroker:
    """The block runs on a fresh memory broker and leaves the settings as they were."""

    @override_settings(NEXT_FRAMEWORK={"REALTIME": {"HEARTBEAT": 5.0}})
    def test_installs_a_fresh_broker_with_options_and_keeps_other_keys(self) -> None:
        before = broker_manager.get()
        with memory_broker(QUEUE_SIZE=8) as broker:
            assert broker_manager.get() is broker
            assert broker is not before
            assert broker.options == {"QUEUE_SIZE": 8}
            assert next_framework_settings.REALTIME["HEARTBEAT"] == 5.0
        assert broker_manager.get() is not broker

    @override_settings(NEXT_FRAMEWORK=None)
    def test_works_without_framework_settings(self) -> None:
        with memory_broker() as broker:
            assert isinstance(broker, MemoryBroker)


class _BlindBinding(SessionBinding):
    """Issue under the session but read no credential back from the cookies."""

    def credential(self, cookies: Mapping[str, str]) -> str | None:
        del cookies


_SIGNED = {"REALTIME": {"TICKET_BINDING": "next.realtime.SignedCookieBinding"}}
_SIGNED_ENGINE = "django.contrib.sessions.backends.signed_cookies"


@pytest.mark.django_db(transaction=True)
class TestTicketBindings:
    """The ticket helper presents what the configured binding reads."""

    @override_settings(NEXT_FRAMEWORK=_SIGNED, SESSION_ENGINE=_SIGNED_ENGINE)
    def test_an_anonymous_visitor_carries_the_signed_visitor_cookie(self) -> None:
        ticket = issue_socket_ticket(("room:1",), authorizer_ref=ROOM_AUTHORIZER_REF)
        assert list(ticket.cookies) == ["next_visitor"]
        assert ticket.credential.startswith("visitor:")
        name, value = ticket.cookie
        assert name == b"cookie"
        assert value.startswith(b"next_visitor=")
        handshake = Handshake(ticket.credential, AnonymousUser(), None)

        async def scenario() -> dict[str, object]:
            port = MemoryPort()
            port.push_json(ticket.subscribe_frame())
            serving = asyncio.create_task(
                TopicSocket(port, handshake).serve(port.inbound())
            )
            hello = await port.receive_json()
            port.disconnect()
            await serving
            return hello

        assert asyncio.run(scenario())["type"] == "hello"

    @override_settings(NEXT_FRAMEWORK=_SIGNED, SESSION_ENGINE=_SIGNED_ENGINE)
    def test_a_signed_in_session_binds_the_account(self) -> None:
        user = get_user_model().objects.create_user("socket-user")
        session = import_module(_SIGNED_ENGINE).SessionStore()
        session[SESSION_KEY] = str(user.pk)
        session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
        session[HASH_SESSION_KEY] = user.get_session_auth_hash()
        session.save()
        ticket = issue_socket_ticket(
            ("room:1",), authorizer_ref=ROOM_AUTHORIZER_REF, session=session
        )
        assert list(ticket.cookies) == ["sessionid"]
        assert ticket.credential.startswith("user:")
        assert ticket.credential.endswith(f":{user.pk}")
        claims = verify_ticket(ticket.grant.token, ticket.credential)
        assert claims.topics == ("room:1",)

    @override_settings(
        NEXT_FRAMEWORK={
            "REALTIME": {"TICKET_BINDING": "tests.testing.test_realtime._BlindBinding"}
        }
    )
    def test_a_binding_that_reads_no_credential_fails_loudly(self) -> None:
        with pytest.raises(AssertionError, match="no credential"):
            issue_socket_ticket(("room:1",), authorizer_ref=ROOM_AUTHORIZER_REF)
