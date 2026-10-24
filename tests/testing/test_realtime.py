import asyncio
import json
from collections.abc import Awaitable, Callable, Mapping

import pytest
from asgiref.typing import ASGIReceiveCallable, ASGISendCallable, Scope
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import override_settings

from next.realtime.socket import Handshake, TopicSocket
from next.realtime.tickets import verify_ticket
from next.testing import (
    MemoryPort,
    SocketTicket,
    WebsocketCommunicator,
    issue_socket_ticket,
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
        handshake = Handshake(ticket.session_key, AnonymousUser(), None)

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
        assert ticket.session.exists(ticket.session_key)
        claims = verify_ticket(ticket.grant.token, ticket.session_key)
        assert claims.topics == ("room:1", "room:2")
        assert claims.authorizer_ref == ROOM_AUTHORIZER_REF
        assert claims.transform_ref == "tests.support.realtime.refresh_envelope"

    def test_binds_to_a_given_session(self) -> None:
        session = SessionStore()
        session.create()
        ticket = issue_socket_ticket(
            ("room:1",), authorizer_ref=ROOM_AUTHORIZER_REF, session=session
        )
        assert ticket.session is session
        assert ticket.session_key == session.session_key

    @override_settings(SESSION_COOKIE_NAME="sid")
    def test_cookie_carries_the_session_under_its_cookie_name(self) -> None:
        ticket = issue_socket_ticket(("room:1",), authorizer_ref=ROOM_AUTHORIZER_REF)
        assert ticket.cookie == (b"cookie", f"sid={ticket.session_key}".encode())

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
