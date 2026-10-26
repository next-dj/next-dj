"""Drive realtime streams in tests, over ASGI, a memory port or a streaming response."""

import asyncio
import itertools
import json
from collections import deque
from collections.abc import AsyncIterator, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from importlib import import_module
from types import MappingProxyType, TracebackType
from typing import Self, cast
from urllib.parse import unquote, urlparse

from asgiref.sync import sync_to_async
from asgiref.testing import ApplicationCommunicator
from asgiref.typing import ASGIApplication, WebSocketScope
from django.conf import settings
from django.contrib.sessions.backends.base import SessionBase
from django.core.signals import request_finished
from django.db import close_old_connections
from django.http import HttpResponse, StreamingHttpResponse
from django.test import RequestFactory

from next.realtime.backends import MemoryBroker
from next.realtime.binding import ticket_binding
from next.realtime.frames import Refusal, Reply, UpstreamError
from next.realtime.manager import broker_manager
from next.realtime.messages import Message
from next.realtime.signals import message_published
from next.realtime.tickets import TicketGrant, issue_ticket
from next.realtime.transports import announce_websocket, withdraw_websocket

from .capture import SignalRecorder
from .patching import override_next_settings


_MEMORY_BROKER = "next.realtime.MemoryBroker"


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

    Frames passed over while waiting for a hello, ack or reply stay for later receives.
    """

    def __init__(
        self,
        application: ASGIApplication,
        path: str,
        headers: Iterable[tuple[bytes, bytes]] = (),
        subprotocols: Iterable[str] = (),
        *,
        timeout: float = 1,
    ) -> None:
        """Build the websocket scope from the path, its query and request headers.

        `timeout` bounds each receive, and each wait for a hello, ack or reply in total.
        """
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
        self.timeout = timeout
        self._held: deque[dict[str, object]] = deque()
        self._ids = itertools.count(1)

    async def connect(self) -> tuple[bool, int | str | None]:
        """Return True and the accepted subprotocol, or False and the close code."""
        await self.send_input({"type": "websocket.connect"})
        response = await self.receive_output(self.timeout)
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
        return _frame_body(_sent(await self._next_output()))

    async def receive_json_from(self) -> dict[str, object]:
        """Receive one text frame and decode its JSON object."""
        return _json_object(await self.receive_from())

    async def receive_close(self) -> int:
        """Receive the application's close and return its code."""
        response = await self._next_output()
        if response["type"] != "websocket.close":
            msg = f"Expected websocket.close, got {response!r}."
            raise AssertionError(msg)
        return cast("int", response.get("code", 1000))

    async def subscribe(
        self, ticket: "SocketTicket", last_seq: Mapping[str, object] | None = None
    ) -> dict[str, object]:
        """Present a ticket and return the hello, replayed frames before it held."""
        await self.send_json_to(ticket.subscribe_frame(last_seq))
        return await self._reply_to("hello", None)

    async def send_up(
        self, topic: str, name: str, data: object = None
    ) -> UpstreamError | None:
        """Send an `up` event and return the error its ack names, None once delivered."""
        frame_id = str(next(self._ids))
        frame = {"type": "up", "topic": topic, "name": name, "data": data}
        await self.send_json_to({**frame, "id": frame_id})
        ack = await self._reply_to("ack", frame_id)
        if ack.get("ok") is True:
            return None
        error = ack.get("error")
        known = {member.value: member for member in UpstreamError}
        if isinstance(error, str) and error in known:
            return known[error]
        msg = f"Expected an ack that names a known error, got {ack!r}."
        raise AssertionError(msg)

    async def request(
        self,
        method: str,
        path: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: str = "",
    ) -> Reply | Refusal:
        """Tunnel one HTTP request over the socket and return its reply or refusal."""
        frame_id = str(next(self._ids))
        await self.send_json_to(
            {
                "type": "http",
                "id": frame_id,
                "method": method,
                "path": path,
                "headers": dict(headers or {}),
                "body": body,
            }
        )
        answer = await self._reply_to("http", frame_id)
        if answer.get("status") == 0:
            return Refusal(str(answer["error"]))
        return Reply(
            cast("int", answer["status"]),
            cast("dict[str, str]", answer.get("headers", {})),
            str(answer.get("body", "")),
        )

    async def _next_output(self) -> dict[str, object]:
        """Return the oldest held output, or wait for the next one."""
        if self._held:
            return self._held.popleft()
        return cast("dict[str, object]", await self.receive_output(self.timeout))

    async def _reply_to(self, kind: str, frame_id: str | None) -> dict[str, object]:
        """Wait for the frame of `kind` and id, holding every other frame meanwhile.

        A close stays held as well, so `receive_close` still reads it after the failure.
        """
        for index, output in enumerate(self._held):
            _refuse_closed(output, kind)
            frame = _answer(output, kind, frame_id)
            if frame is not None:
                del self._held[index]
                return frame
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self.timeout
        while True:
            output = await self.receive_output(max(deadline - loop.time(), 0))
            frame = _answer(output, kind, frame_id)
            if frame is not None:
                return frame
            self._held.append(output)
            _refuse_closed(output, kind)

    async def disconnect(self, code: int = 1000) -> None:
        """Close the socket from the client and wait for the application to exit."""
        await self.send_input({"type": "websocket.disconnect", "code": code})
        await self.wait()


def _refuse_closed(output: dict[str, object], kind: str) -> None:
    """Fail a wait for a `kind` frame on any output other than a sent frame."""
    if output["type"] != "websocket.send":
        msg = f"Expected a {kind!r} frame, got {output!r}."
        raise AssertionError(msg)


def _sent(response: dict[str, object]) -> dict[str, object]:
    """Return a `websocket.send` output, refusing any other event."""
    if response["type"] != "websocket.send":
        msg = f"Expected websocket.send, got {response!r}."
        raise AssertionError(msg)
    return response


def _frame_body(response: dict[str, object]) -> str | bytes:
    text = response.get("text")
    return cast("str | bytes", text if text is not None else response["bytes"])


def _answer(
    output: dict[str, object], kind: str, frame_id: str | None
) -> dict[str, object] | None:
    """Decode a sent text frame of `kind` that carries `frame_id`, else None."""
    text = output.get("text")
    if not isinstance(text, str):
        return None
    try:
        frame = json.loads(text)
    except ValueError:
        return None
    if (
        isinstance(frame, dict)
        and frame.get("type") == kind
        and (frame_id is None or frame.get("id") == frame_id)
    ):
        return cast("dict[str, object]", frame)
    return None


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
    """A WebSocket ticket, its session and the cookies its handshake presents.

    `credential` is what the configured `TICKET_BINDING` reads from those cookies.
    """

    session: SessionBase
    grant: TicketGrant
    cookies: Mapping[str, str]
    credential: str

    @property
    def cookie(self) -> tuple[bytes, bytes]:
        """The `cookie` header that carries the binding on the WebSocket handshake."""
        value = "; ".join(f"{name}={value}" for name, value in self.cookies.items())
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
    presence: bool | str = False,
    session: SessionBase | None = None,
) -> SocketTicket:
    """Issue a WebSocket ticket through the negotiation path of any ticket binding.

    The cookies are the session cookie and those the binding sets on the reply.
    """
    if session is None:
        session = import_module(settings.SESSION_ENGINE).SessionStore()
    request = RequestFactory().get("/")
    if session.session_key is not None:
        request.COOKIES[settings.SESSION_COOKIE_NAME] = session.session_key
    request.session = session
    grant = issue_ticket(
        request,
        topics,
        transform_ref=transform_ref,
        authorizer_ref=authorizer_ref,
        presence=presence,
    )
    binding = ticket_binding()
    reply = HttpResponse()
    binding.respond(request, reply)
    cookies = {name: morsel.value for name, morsel in reply.cookies.items()}
    if session.session_key is not None:
        cookies = {settings.SESSION_COOKIE_NAME: session.session_key, **cookies}
    credential = binding.credential(cookies)
    if credential is None:
        msg = "The ticket binding reads no credential from the negotiated cookies."
        raise AssertionError(msg)
    return SocketTicket(session, grant, MappingProxyType(cookies), credential)


async def aissue_socket_ticket(
    topics: tuple[str, ...],
    *,
    authorizer_ref: str,
    transform_ref: str | None = None,
    presence: bool | str = False,
    session: SessionBase | None = None,
) -> SocketTicket:
    """Issue a WebSocket ticket from an event loop, the session work off it."""
    return await sync_to_async(issue_socket_ticket)(
        topics,
        authorizer_ref=authorizer_ref,
        transform_ref=transform_ref,
        presence=presence,
        session=session,
    )


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


@dataclass(frozen=True, slots=True)
class SseEvent:
    """One dispatched server-sent event, its data lines joined as an EventSource does."""

    event: str
    data: str
    id: str | None = None

    def json(self) -> dict[str, object]:
        """Decode the data as one JSON object."""
        return _json_object(self.data)


class SseReader:
    """Read the events of a streaming SSE response, sync or async as it streams.

    Heartbeats, other comments and the `retry` hint never surface, whatever the timing.
    """

    def __init__(self, response: StreamingHttpResponse) -> None:
        """Take over the response body, which is read only once."""
        self._response = response
        self._content = response.streaming_content
        self._buffer = ""
        self._closed = False
        self._started = False
        self.retry: int | None = None

    def __iter__(self) -> Self:
        """Iterate the events of a sync response."""
        return self

    def __next__(self) -> SseEvent:
        """Return the next event of a sync response, StopIteration once it ends."""
        content = self._content
        if not isinstance(content, Iterator):
            msg = "SseReader iterates an async response with `async for`."
            raise TypeError(msg)
        while (event := self._pop()) is None:
            self._feed(next(content))
        return event

    def __aiter__(self) -> Self:
        """Iterate the events of an async response."""
        return self

    async def __anext__(self) -> SseEvent:
        """Return the next event of an async response."""
        content = self._content
        if not isinstance(content, AsyncIterator):
            msg = "SseReader iterates a sync response with `for`."
            raise TypeError(msg)
        self._started = True
        while (event := self._pop()) is None:
            self._feed(await anext(content))
        return event

    def close(self) -> None:
        """Close a sync response as a WSGI server does when its client leaves.

        The test client keeps the database connection open the same way.
        """
        request_finished.disconnect(close_old_connections)
        try:
            self._response.close()
        finally:
            request_finished.connect(close_old_connections)

    async def aclose(self) -> None:
        """Cancel the pending read of an async response, as Django does on disconnect.

        A response never read stays unstarted, and a cancellation of the caller wins.
        """
        content = self._content
        if not isinstance(content, AsyncIterator):
            msg = "SseReader closes a sync response with `close`."
            raise TypeError(msg)
        self._closed = self._closed or not self._started
        while not self._closed:
            self._closed = await _cancelled(asyncio.ensure_future(anext(content)))

    def __enter__(self) -> Self:
        """Read a sync response in a block that closes it."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the sync response."""
        self.close()

    async def __aenter__(self) -> Self:
        """Read an async response in a block that closes it."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Close the async response."""
        await self.aclose()

    def _feed(self, chunk: bytes) -> None:
        text = chunk.decode()
        self._buffer += text.replace("\r\n", "\n").replace("\r", "\n")

    def _pop(self) -> SseEvent | None:
        """Dispatch the next complete event block, skipping blocks with no data."""
        while "\n\n" in self._buffer:
            block, self._buffer = self._buffer.split("\n\n", 1)
            event = self._dispatch(block)
            if event is not None:
                return event
        return None

    def _dispatch(self, block: str) -> SseEvent | None:
        fields: dict[str, str] = {}
        data: list[str] = []
        for line in block.split("\n"):
            if not line or line.startswith(":"):
                continue
            name, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if name == "data":
                data.append(value)
            elif name == "retry" and value.isdigit():
                self.retry = int(value)
            else:
                fields[name] = value
        if not data:
            return None
        return SseEvent(
            fields.get("event", "message"), "\n".join(data), fields.get("id")
        )


async def _cancelled(pull: "asyncio.Future[bytes]") -> bool:
    """Cancel one pending read and return whether the response ended.

    A frame that lands before the cancellation is dropped, so the caller reads anew.
    """
    try:
        await asyncio.sleep(0)
    finally:
        pull.cancel()
    try:
        await pull
    except StopAsyncIteration:
        return True
    except asyncio.CancelledError:
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise
        return True
    return False


class PublishedMessages:
    """Record the messages `message_published` reports, in publish order, until stopped.

    It sees what `publish`, `apublish` and presence snapshots send, on any broker.
    """

    def __init__(self, *topics: str) -> None:
        """Keep only messages on `topics`, every message when none is given."""
        self.topics = frozenset(topics)
        self.recorder = SignalRecorder(message_published)

    @property
    def messages(self) -> list[Message]:
        """The recorded messages on the kept topics, in publish order."""
        return [
            message
            for event in self.recorder.events
            if isinstance(message := event.kwargs.get("message"), Message)
            and (not self.topics or message.topic in self.topics)
        ]

    def start(self) -> Self:
        """Connect to `message_published` and return self."""
        self.recorder.start()
        return self

    def stop(self) -> None:
        """Disconnect from `message_published`, keeping what was recorded."""
        self.recorder.stop()

    def __enter__(self) -> Self:
        """Record for the block."""
        return self.start()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Stop recording."""
        self.stop()

    def __iter__(self) -> Iterator[Message]:
        """Iterate the recorded messages in publish order."""
        return iter(self.messages)

    def __len__(self) -> int:
        """Return how many messages were recorded."""
        return len(self.messages)


def capture_published(*topics: str) -> PublishedMessages:
    """Return a started `PublishedMessages`, a context manager for one block."""
    return PublishedMessages(*topics).start()


@contextmanager
def memory_broker(**options: object) -> Iterator[MemoryBroker]:
    """Serve realtime from a fresh `MemoryBroker` in the block, `options` its `OPTIONS`.

    The configured broker is built anew on its first use after the block.
    """
    configured = getattr(settings, "NEXT_FRAMEWORK", None) or {}
    realtime = configured.get("REALTIME") or {}
    broker = {"BACKEND": _MEMORY_BROKER, "OPTIONS": dict(options)}
    with override_next_settings(REALTIME={**realtime, "BROKER": broker}):
        yield cast("MemoryBroker", broker_manager.get())


__all__ = [
    "MemoryPort",
    "PublishedMessages",
    "SocketTicket",
    "SseEvent",
    "SseReader",
    "WebsocketCommunicator",
    "aissue_socket_ticket",
    "capture_published",
    "issue_socket_ticket",
    "memory_broker",
    "override_websocket_mount",
]
