"""Drive realtime WebSockets in tests, through ASGI or straight through a port."""

import asyncio
import json
from collections.abc import AsyncIterator, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import import_module
from typing import cast
from urllib.parse import unquote, urlparse

from asgiref.testing import ApplicationCommunicator
from asgiref.typing import ASGIApplication, WebSocketScope
from django.conf import settings
from django.contrib.sessions.backends.base import SessionBase
from django.test import RequestFactory

from next.realtime.tickets import TicketGrant, issue_ticket
from next.realtime.transports import announce_websocket, withdraw_websocket


def _json_object(text: object) -> dict[str, object]:
    """Decode one text frame that must carry a JSON object."""
    match json.loads(text) if isinstance(text, str) else text:
        case dict() as payload:
            return cast("dict[str, object]", payload)
        case other:
            msg = f"Expected a JSON object text frame, got {other!r}."
            raise AssertionError(msg)


class WebsocketCommunicator(ApplicationCommunicator):
    """Drive any ASGI application through one WebSocket connection in a test.

    Every receive waits the one second `ApplicationCommunicator` allows by default.
    """

    def __init__(
        self,
        application: ASGIApplication,
        path: str,
        headers: Iterable[tuple[bytes, bytes]] = (),
        subprotocols: Iterable[str] = (),
    ) -> None:
        """Build the websocket scope from the path, its query and request headers."""
        parsed = urlparse(path)
        scope: WebSocketScope = {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "scheme": "ws",
            "path": unquote(parsed.path),
            "raw_path": parsed.path.encode(),
            "query_string": parsed.query.encode(),
            "root_path": "",
            "headers": list(headers),
            "client": ("127.0.0.1", 0),
            "server": ("testserver", 80),
            "subprotocols": list(subprotocols),
            "extensions": {},
        }
        super().__init__(application, scope)

    async def connect(self) -> tuple[bool, int | str | None]:
        """Return True and the accepted subprotocol, or False and the close code."""
        await self.send_input({"type": "websocket.connect"})
        response = await self.receive_output()
        if response["type"] == "websocket.close":
            return False, response.get("code", 1000)
        if response["type"] != "websocket.accept":
            msg = f"Expected websocket.accept, got {response!r}."
            raise AssertionError(msg)
        return True, response.get("subprotocol")

    async def send_to(
        self, text_data: str | None = None, bytes_data: bytes | None = None
    ) -> None:
        """Send one text or binary frame to the application."""
        message: dict[str, object] = {"type": "websocket.receive"}
        if text_data is not None:
            message["text"] = text_data
        if bytes_data is not None:
            message["bytes"] = bytes_data
        await self.send_input(message)

    async def send_json_to(self, data: object) -> None:
        """Send one JSON text frame to the application."""
        await self.send_to(json.dumps(data))

    async def receive_from(self) -> str | bytes:
        """Receive one frame the application sent, its text or its bytes."""
        response = await self.receive_output()
        if response["type"] != "websocket.send":
            msg = f"Expected websocket.send, got {response!r}."
            raise AssertionError(msg)
        text = response.get("text")
        return cast("str | bytes", text if text is not None else response["bytes"])

    async def receive_json_from(self) -> dict[str, object]:
        """Receive one text frame and decode its JSON object."""
        return _json_object(await self.receive_from())

    async def receive_close(self) -> int:
        """Receive the application's close and return its code."""
        response = await self.receive_output()
        if response["type"] != "websocket.close":
            msg = f"Expected websocket.close, got {response!r}."
            raise AssertionError(msg)
        return cast("int", response.get("code", 1000))

    async def disconnect(self, code: int = 1000) -> None:
        """Close the socket from the client and wait for the application to exit."""
        await self.send_input({"type": "websocket.disconnect", "code": code})
        await self.wait()


class MemoryPort:
    """A `SocketPort` held in memory, the client end of a `TopicSocket` in a test.

    Once `disconnect` runs the inbound frames end and every later send raises OSError.
    """

    def __init__(self) -> None:
        """Start with no frames sent, none to read and the socket open."""
        self.sent: list[str] = []
        self.close_code: int | None = None
        self._outbound: asyncio.Queue[str] = asyncio.Queue()
        self._inbound: asyncio.Queue[str | bytes | None] = asyncio.Queue()
        self._gone = False

    async def send(self, text: str) -> None:
        """Record one frame, refusing it once the socket is closed or the peer left."""
        if self._gone or self.close_code is not None:
            msg = "The memory WebSocket is closed."
            raise OSError(msg)
        self.sent.append(text)
        self._outbound.put_nowait(text)

    async def close(self, code: int) -> None:
        """Record the first close code."""
        if self.close_code is None:
            self.close_code = code

    def push(self, item: str | bytes) -> None:
        """Queue one frame from the client."""
        self._inbound.put_nowait(item)

    def push_json(self, data: object) -> None:
        """Queue one JSON text frame from the client."""
        self.push(json.dumps(data))

    def disconnect(self) -> None:
        """Leave as the client, ending the inbound frames after those queued."""
        self._gone = True
        self._inbound.put_nowait(None)

    async def inbound(self) -> AsyncIterator[str | bytes]:
        """Yield the client frames until the client leaves, for `TopicSocket.serve`."""
        while (item := await self._inbound.get()) is not None:
            yield item

    async def receive(self) -> str:
        """Wait for the next frame the socket sent."""
        return await self._outbound.get()

    async def receive_json(self) -> dict[str, object]:
        """Wait for the next frame the socket sent and decode its JSON object."""
        return _json_object(await self.receive())


@dataclass(frozen=True, slots=True)
class SocketTicket:
    """A WebSocket ticket and the saved session it is bound to."""

    session: SessionBase
    grant: TicketGrant

    @property
    def session_key(self) -> str:
        """The key a `Handshake` for this ticket carries."""
        return cast("str", self.session.session_key)

    @property
    def cookie(self) -> tuple[bytes, bytes]:
        """The `cookie` header that carries the session on the WebSocket handshake."""
        value = f"{settings.SESSION_COOKIE_NAME}={self.session_key}"
        return b"cookie", value.encode("latin-1")

    def subscribe_frame(
        self, last_seq: Mapping[str, object] | None = None
    ) -> dict[str, object]:
        """Build the `sub` frame that presents this ticket, with an optional cursor."""
        frame: dict[str, object] = {"type": "sub", "ticket": self.grant.token}
        if last_seq is not None:
            frame["last_seq"] = dict(last_seq)
        return frame


def issue_socket_ticket(
    topics: tuple[str, ...],
    *,
    authorizer_ref: str,
    transform_ref: str | None = None,
    session: SessionBase | None = None,
) -> SocketTicket:
    """Issue a WebSocket ticket through the negotiation path, under a saved session.

    Without `session` a fresh one is created in the `SESSION_ENGINE` store.
    """
    if session is None:
        session = import_module(settings.SESSION_ENGINE).SessionStore()
    request = RequestFactory().get("/")
    request.session = session
    grant = issue_ticket(
        request, topics, transform_ref=transform_ref, authorizer_ref=authorizer_ref
    )
    return SocketTicket(session, grant)


@contextmanager
def override_websocket_mount(*, mounted: bool = False) -> Iterator[None]:
    """Set whether this process serves the WebSocket, restoring the flag on exit.

    A `WebSocketApp` marks the whole process, so a later WSGI test would offer `ws`.
    """
    previous = withdraw_websocket()
    if mounted:
        announce_websocket()
    try:
        yield
    finally:
        withdraw_websocket()
        if previous:
            announce_websocket()


__all__ = [
    "MemoryPort",
    "SocketTicket",
    "WebsocketCommunicator",
    "issue_socket_ticket",
    "override_websocket_mount",
]
