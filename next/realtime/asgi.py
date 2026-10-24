"""The ASGI WebSocket endpoint that serves the realtime topic protocol."""

import functools
import logging
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from typing import Self
from urllib.parse import urlsplit

from asgiref.typing import (
    ASGI3Application,
    ASGIReceiveCallable,
    ASGIReceiveEvent,
    ASGISendCallable,
    ASGISendEvent,
    LifespanScope,
    LifespanShutdownCompleteEvent,
    LifespanShutdownFailedEvent,
    Scope,
    WebSocketScope,
)
from django.conf import settings
from django.core.exceptions import DisallowedHost
from django.core.handlers.asgi import ASGIHandler, get_script_prefix
from django.http import HttpRequest
from django.utils.http import is_same_domain

from . import frames
from .access import cookie_session_key
from .checks import websocket_deployment_messages
from .frames import Close
from .manager import broker_manager
from .socket import Handshake, SocketPort, TopicSocket
from .tickets import websocket_path
from .transports import announce_websocket


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class _Origins:
    """Origins in the `CSRF_TRUSTED_ORIGINS` format, exact or with a `*.` subdomain."""

    exact: frozenset[str]
    subdomains: Mapping[str, tuple[str, ...]]

    @classmethod
    def parse(cls, origins: Iterable[str]) -> Self:
        """Split entries the way Django's CSRF middleware does."""
        exact: set[str] = set()
        subdomains: defaultdict[str, list[str]] = defaultdict(list)
        for origin in origins:
            if "*" in origin:
                parsed = urlsplit(origin)
                subdomains[parsed.scheme].append(parsed.netloc.lstrip("*"))
            else:
                exact.add(origin)
        return cls(
            frozenset(exact), {key: tuple(value) for key, value in subdomains.items()}
        )

    def __contains__(self, origin: object) -> bool:
        """Whether an Origin header matches one of the entries."""
        if not isinstance(origin, str):
            return False
        if origin in self.exact:
            return True
        try:
            parsed = urlsplit(origin)
        except ValueError:
            return False
        return any(
            is_same_domain(parsed.netloc, host)
            for host in self.subdomains.get(parsed.scheme, ())
        )


def _explicit_origins(origins: Sequence[str]) -> _Origins:
    """Validate an explicit allowlist so a typo fails at startup, not per socket."""
    if isinstance(origins, str):
        msg = "WebSocket origins must be a list like ['https://example.com']."
        raise TypeError(msg)
    if not origins:
        msg = "Name at least one WebSocket origin or omit origins for the default."
        raise ValueError(msg)
    for origin in origins:
        try:
            parsed = urlsplit(origin)
        except ValueError as exc:
            msg = f"WebSocket origin {origin!r} is not a valid URL."
            raise ValueError(msg) from exc
        if not parsed.scheme or not parsed.netloc:
            msg = f"WebSocket origin {origin!r} needs a scheme and a host."
            raise ValueError(msg)
    return _Origins.parse(origins)


@functools.cache
def _trusted_origins() -> _Origins:
    """Parse `CSRF_TRUSTED_ORIGINS` once per settings state."""
    return _Origins.parse(settings.CSRF_TRUSTED_ORIGINS)


def forget_trusted_origins(**kwargs: object) -> None:
    """Drop the parsed `CSRF_TRUSTED_ORIGINS` when settings change."""
    _trusted_origins.cache_clear()


class _SocketRequest(HttpRequest):
    """A bodiless request whose scheme follows the socket scope, like `ASGIRequest`."""

    def __init__(self, scheme: str) -> None:
        super().__init__()
        self._socket_scheme = scheme

    def _get_scheme(self) -> str:
        return self._socket_scheme


def _header(scope: WebSocketScope, name: bytes) -> list[str]:
    """Return every value the scope carries for one lowercase header name."""
    return [
        value.decode("latin1")
        for key, value in scope.get("headers", ())
        if key.lower() == name
    ]


def _cookie_header(scope: WebSocketScope) -> str:
    """Join the cookie headers with a semicolon, which Django 5.2 does not do itself."""
    return "; ".join(value.rstrip("; ") for value in _header(scope, b"cookie"))


def _origin(scope: WebSocketScope) -> str | None:
    """Return the Origin header, joined the way Django joins a repeated header."""
    return ",".join(_header(scope, b"origin")) or None


def _request(scope: WebSocketScope) -> HttpRequest:
    """Build a bodiless request from the scope headers and scheme, like ASGIRequest."""
    secure = scope.get("scheme") in {"wss", "https"}
    request = _SocketRequest("https" if secure else "http")
    headers: defaultdict[str, list[str]] = defaultdict(list)
    for raw_name, raw_value in scope.get("headers", ()):
        name = raw_name.decode("latin1")
        if "_" in name:
            continue
        key = f"HTTP_{name.upper().replace('-', '_')}"
        if key != "HTTP_COOKIE":
            headers[key].append(raw_value.decode("latin1"))
    cookies = _cookie_header(scope)
    if cookies:
        request.META["HTTP_COOKIE"] = cookies
    request.META.update({key: ",".join(value) for key, value in headers.items()})
    server = scope.get("server")
    if server is not None:
        request.META["SERVER_NAME"] = server[0]
        request.META["SERVER_PORT"] = str(server[1] or 0)
    else:
        request.META["SERVER_NAME"] = "unknown"
        request.META["SERVER_PORT"] = "0"
    return request


def _same_origin(origin: str, request: HttpRequest) -> bool:
    """Whether the Origin is this site's scheme and validated Host, as CSRF checks it.

    Behind a TLS proxy the scheme comes from `SECURE_PROXY_SSL_HEADER`, as for CSRF.
    """
    try:
        host = request.get_host()
    except DisallowedHost:
        return False
    return origin == f"{'https' if request.is_secure() else 'http'}://{host}"


class _Port:
    """The `SocketPort` over an accepted ASGI WebSocket."""

    __slots__ = ("_closed", "_send")

    def __init__(self, send: ASGISendCallable) -> None:
        self._send = send
        self._closed = False

    async def send(self, text: str) -> None:
        """Send one text frame, raising `OSError` once the peer is gone."""
        await self._send({"type": "websocket.send", "bytes": None, "text": text})

    async def close(self, code: int) -> None:
        """Close once, ignoring a peer that already left."""
        if self._closed:
            return
        self._closed = True
        with suppress(OSError):
            await self._send({"type": "websocket.close", "code": code, "reason": None})


class _Inbound:
    """The client's frames, ending when the server reports the disconnect."""

    __slots__ = ("_receive",)

    def __init__(self, receive: ASGIReceiveCallable) -> None:
        self._receive = receive

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> str | bytes:
        message = await self._receive()
        if message["type"] != "websocket.receive":
            raise StopAsyncIteration
        text = message.get("text")
        if text is not None:
            return text
        return message.get("bytes") or b""


async def _refuse(receive: ASGIReceiveCallable, send: ASGISendCallable) -> None:
    """Answer the handshake with a close, which the server turns into HTTP 403."""
    message = await receive()
    if message["type"] == "websocket.connect":
        await send({"type": "websocket.close", "code": 1000, "reason": None})


class WebSocketApp:
    """ASGI WebSocket endpoint that serves the realtime topic protocol.

    Creating one marks this process as serving `ws`, wherever the app is mounted.
    """

    def __init__(self, *, origins: Sequence[str] | None = None) -> None:
        """Take an explicit Origin allowlist that replaces the CSRF-parity default."""
        self._origins = _explicit_origins(origins) if origins is not None else None
        announce_websocket()
        for message in websocket_deployment_messages():
            hint = f" {message.hint}" if message.hint else ""
            logger.warning("(%s) %s%s", message.id, message.msg, hint)

    def allow_origin(self, origin: str | None, scope: WebSocketScope) -> bool:
        """Accept the scheme and validated Host or `CSRF_TRUSTED_ORIGINS`, like CSRF.

        A missing Origin is refused, since only browsers carry ambient cookies.
        """
        if origin is None:
            return False
        if self._origins is not None:
            return origin in self._origins
        if _same_origin(origin, _request(scope)):
            return True
        return origin in _trusted_origins()

    async def authenticate(self, scope: WebSocketScope) -> Handshake | None:
        """Load the Django session named by the cookie, or None without one."""
        session_key = cookie_session_key(_cookie_header(scope))
        if session_key is None:
            return None
        return await Handshake.from_session(session_key)

    def topic_socket(
        self, port: SocketPort, handshake: Handshake, scope: WebSocketScope
    ) -> TopicSocket:
        """Build the connection a subclass may swap for its own `TopicSocket`."""
        del scope
        return TopicSocket(port, handshake)

    async def __call__(
        self, scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        """Check the Origin, accept, authenticate and serve the topic protocol."""
        if scope["type"] != "websocket":
            msg = f"WebSocketApp serves WebSocket connections, not {scope['type']}."
            raise ValueError(msg)
        message = await receive()
        if message["type"] != "websocket.connect":
            return
        if not self.allow_origin(_origin(scope), scope):
            await send({"type": "websocket.close", "code": 1000, "reason": None})
            return
        await send({"type": "websocket.accept", "subprotocol": None, "headers": []})
        port = _Port(send)
        try:
            handshake = await self.authenticate(scope)
        except Exception:
            logger.exception("Realtime WebSocket authentication failed")
            await port.close(Close.INTERNAL)
            return
        if handshake is None:
            with suppress(OSError):
                await port.send(frames.error("auth"))
            await port.close(Close.SESSION)
            return
        await self.topic_socket(port, handshake, scope).serve(_Inbound(receive))


class _Application:
    """Route HTTP to the project's app and the realtime path to the WebSocket app."""

    __slots__ = ("http_app", "websocket")

    def __init__(self, http_app: ASGI3Application, websocket: WebSocketApp) -> None:
        self.http_app = http_app
        self.websocket = websocket

    async def __call__(
        self, scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        """Dispatch one connection by its scope type."""
        if scope["type"] == "http":
            await self.http_app(scope, receive, send)
        elif scope["type"] == "websocket":
            if _path_info(scope) == websocket_path():
                await self.websocket(scope, receive, send)
            else:
                await _refuse(receive, send)
        else:
            await self._lifespan(scope, receive, send)

    async def _lifespan(
        self, scope: LifespanScope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        """Pass lifespan to an app that speaks it, else answer it, closing the broker.

        An app that fails or returns before sending anything, like Django's, rejects it.
        """
        if isinstance(self.http_app, ASGIHandler):
            await _answer_lifespan(receive, send, None)
            return
        pending: ASGIReceiveEvent | None = None
        answered = False

        async def receiving() -> ASGIReceiveEvent:
            nonlocal pending
            pending = await receive()
            return pending

        async def sending(message: ASGISendEvent) -> None:
            nonlocal answered
            answered = True
            if message["type"] == "lifespan.shutdown.complete":
                message = await _close_broker()
            await send(message)

        try:
            await self.http_app(scope, receiving, sending)
        except Exception:
            if answered:
                raise
            logger.debug("The HTTP app rejected lifespan", exc_info=True)
        if not answered:
            await _answer_lifespan(receive, send, pending)


async def _answer_lifespan(
    receive: ASGIReceiveCallable,
    send: ASGISendCallable,
    pending: ASGIReceiveEvent | None,
) -> None:
    """Complete startup at once and close the broker on shutdown."""
    message = pending if pending is not None else await receive()
    while message["type"] != "lifespan.shutdown":
        await send({"type": "lifespan.startup.complete"})
        message = await receive()
    await send(await _close_broker())


async def _close_broker() -> (
    LifespanShutdownCompleteEvent | LifespanShutdownFailedEvent
):
    """Close the broker and report the shutdown, failed if the broker raised."""
    try:
        await broker_manager.aclose()
    except Exception:
        logger.exception("Realtime broker failed to close on shutdown")
        return {"type": "lifespan.shutdown.failed", "message": "Broker close failed."}
    return {"type": "lifespan.shutdown.complete"}


def _path_info(scope: WebSocketScope) -> str:
    """Return the path below the script prefix, the way Django's ASGI request does."""
    path = scope["path"]
    script_name = get_script_prefix(scope).rstrip("/")
    if script_name and (path.startswith(f"{script_name}/") or path == script_name):
        return path[len(script_name) :]
    return path


def application(
    http_app: ASGI3Application, *, websocket: WebSocketApp | None = None
) -> ASGI3Application:
    """Serve `http_app` and the realtime WebSocket at `REALTIME['WS_PATH']`."""
    return _Application(
        http_app, websocket if websocket is not None else WebSocketApp()
    )


__all__ = ["WebSocketApp", "application", "forget_trusted_origins"]
