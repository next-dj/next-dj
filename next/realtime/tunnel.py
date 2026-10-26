"""The request tunnel that runs form action posts from a WebSocket through Django."""

import asyncio
import logging
import re
import time
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from http import HTTPStatus
from http.cookies import Morsel, SimpleCookie
from typing import ClassVar, Final, Protocol, cast
from urllib.parse import unquote

from asgiref.typing import (
    ASGI3Application,
    ASGIReceiveEvent,
    ASGISendEvent,
    HTTPScope,
    WebSocketScope,
)
from django.conf import settings
from django.core.handlers.asgi import get_script_prefix
from django.http import parse_cookie
from django.urls import Resolver404, resolve
from django.utils.http import parse_http_date_safe
from django.utils.translation import get_language_from_path, override

from next.forms.transport import (
    DISPATCHED_SCOPE_KEY,
    ROTATION,
    TRANSPORT_HEADER,
    TRANSPORT_SCOPE_KEY,
    DispatchMark,
    dispatches_actions,
)

from .access import run_detached
from .frames import Refusal, Reply, Request
from .limits import TunnelLimits


logger = logging.getLogger(__name__)

_PATH: Final = re.compile(r"/(?!/)[!$-\[\]-~]*")
_TOKEN: Final = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")
_UNSAFE_VALUE: Final = re.compile(r"[\r\n\0]")
_MARKER: Final = TRANSPORT_HEADER.lower()
_LIVE: set["asyncio.Task[None]"] = set()


@dataclass(frozen=True, slots=True)
class TunnelHints:
    """The request headers a tunnel keeps, which the hello frame tells the client.

    `csrf` is the header `CSRF_HEADER_NAME` stands for, None for a plain META key.
    """

    headers: frozenset[str]
    prefixes: tuple[str, ...]
    csrf: str | None


class Tunnel(Protocol):
    """Run one tunneled HTTP request for a connection and return its answer.

    Limits, the timeout included, stay with the socket, so a cancellation may arrive.
    """

    async def __call__(self, request: Request, *, connection: str) -> Reply | Refusal:
        """Answer the request with its HTTP reply or the reason it got none."""

    def hints(self) -> TunnelHints:
        """Return the request headers the tunnel keeps, for the client to filter by."""


def scope_headers(scope: WebSocketScope, name: bytes) -> list[str]:
    """Return every value the scope carries for one lowercase header name."""
    return [
        value.decode("latin1")
        for key, value in scope.get("headers", ())
        if key.lower() == name
    ]


def cookie_header(scope: WebSocketScope) -> str:
    """Join the cookie headers with a semicolon, which Django 5.2 does not do itself."""
    return "; ".join(value.rstrip("; ") for value in scope_headers(scope, b"cookie"))


def csrf_header() -> str | None:
    """Return the header name `CSRF_HEADER_NAME` stands for, None for a META key."""
    name = settings.CSRF_HEADER_NAME
    if not name.startswith("HTTP_"):
        return None
    return name.removeprefix("HTTP_").lower().replace("_", "-")


class _Exchange:
    """The receive and send of one tunneled request, buffering a bounded reply.

    `gone` turns the next receive into `http.disconnect`, the way Django stops a view.
    """

    __slots__ = (
        "_limit",
        "_pending",
        "body",
        "broken",
        "gone",
        "headers",
        "overflow",
        "status",
    )

    def __init__(self, body: bytes, limit: int) -> None:
        self._pending: bytes | None = body
        self._limit = limit
        self.gone = asyncio.Event()
        self.status: int | None = None
        self.headers: list[tuple[bytes, bytes]] = []
        self.body = bytearray()
        self.overflow = False
        self.broken = False

    async def receive(self) -> ASGIReceiveEvent:
        """Hand over the whole body once, then wait for the request to end."""
        body, self._pending = self._pending, None
        if body is not None:
            return {"type": "http.request", "body": body, "more_body": False}
        await self.gone.wait()
        return {"type": "http.disconnect"}

    async def send(self, message: ASGISendEvent) -> None:
        """Collect the response start and body, dropping what passes the limit."""
        if message["type"] == "http.response.start":
            if self.status is not None:
                self._abort(broken=True)
                return
            self.status = message["status"]
            self.headers = list(message.get("headers", ()))
        elif message["type"] == "http.response.body":
            if self.status is None:
                self._abort(broken=True)
                return
            if self.gone.is_set():
                return
            self.body.extend(message.get("body", b""))
            if len(self.body) > self._limit:
                self._abort(broken=False)

    def _abort(self, *, broken: bool) -> None:
        if broken:
            self.broken = True
        else:
            self.overflow = True
        self.body.clear()
        self.gone.set()


def _deleted(morsel: "Morsel[str]") -> bool:
    """Whether a Set-Cookie removes its cookie, through Max-Age or a past Expires."""
    max_age = morsel["max-age"]
    if max_age:
        try:
            return int(max_age) <= 0
        except ValueError:
            return True
    expires = morsel["expires"]
    if expires:
        moment = parse_http_date_safe(expires)
        return moment is None or moment <= time.time()
    return False


class DjangoTunnel:
    """Run tunneled requests through the project's ASGI app as same-origin POSTs.

    The environment comes from the handshake, the frame adds only allowed headers.
    """

    grace: ClassVar[float] = 1.0
    max_path: ClassVar[int] = 2048
    max_headers: ClassVar[int] = 32
    max_header_value: ClassVar[int] = 4096
    request_headers: ClassVar[frozenset[str]] = frozenset({"content-type", "accept"})
    request_prefixes: ClassVar[tuple[str, ...]] = ("x-next-",)
    dropped_scope_headers: ClassVar[frozenset[str]] = frozenset(
        {
            "connection",
            "upgrade",
            "keep-alive",
            "te",
            "trailer",
            "transfer-encoding",
            "proxy-connection",
            "content-length",
            "content-type",
            "accept",
            "accept-encoding",
            "range",
            "expect",
            "referer",
            "cookie",
        }
    )
    dropped_scope_prefixes: ClassVar[tuple[str, ...]] = (
        "sec-websocket-",
        "if-",
        "x-next-",
    )
    response_headers: ClassVar[frozenset[str]] = frozenset({"content-type", "location"})
    response_prefixes: ClassVar[tuple[str, ...]] = ("x-next-",)
    content_types: ClassVar[frozenset[str]] = frozenset(
        {"application/x-www-form-urlencoded", "application/json"}
    )

    def __init__(
        self, http_app: ASGI3Application, scope: WebSocketScope, limits: TunnelLimits
    ) -> None:
        """Bind the app and the handshake scope every request of the socket reuses."""
        self._app = http_app
        self._scope = scope
        self._limits = limits
        self._cookie = cookie_header(scope)
        self._cookies = parse_cookie(self._cookie)
        self._running: set[asyncio.Task[None]] = set()

    def hints(self) -> TunnelHints:
        """Return the request headers the frame may carry, the CSRF header included."""
        return TunnelHints(self.request_headers, self.request_prefixes, csrf_header())

    async def __call__(self, request: Request, *, connection: str) -> Reply | Refusal:
        """Check the request, run it through the app and shape the reply.

        An app still running after a disconnect keeps its slot of `CONCURRENCY`.
        """
        del connection
        prepared = self.refuse(request) or self._prepare(request)
        if isinstance(prepared, Refusal):
            return prepared
        headers, body = prepared
        if len(self._running) >= self._limits.concurrency:
            return Refusal.LIMIT
        path, _, query = request.path.partition("?")
        path_info = unquote(path)[len(self._prefix()) :]
        if not await run_detached(self.routes_to_action, path_info):
            return Refusal.TRANSPORT
        mark = DispatchMark()
        scope = self.http_scope(path, query, headers)
        cast("dict[str, object]", scope)[DISPATCHED_SCOPE_KEY] = mark
        exchange = _Exchange(body, self._limits.max_reply_bytes)
        await self._run(scope, exchange)
        return self.shape(request, exchange, reached=mark.reached)

    def _prepare(
        self, request: Request
    ) -> tuple[list[tuple[bytes, bytes]], bytes] | Refusal:
        """Return the request headers and body, or why the request cannot run."""
        headers = self.frame_headers(request.headers)
        if headers is None:
            return Refusal.HEADERS
        try:
            body = request.body.encode()
        except UnicodeEncodeError:
            return Refusal.BODY
        if not self._accepts(headers):
            return Refusal.BODY
        if self._stale():
            return Refusal.STALE
        headers.append((b"content-length", str(len(body)).encode("ascii")))
        return headers, body

    def refuse(self, request: Request) -> Refusal | None:
        """Refuse a method or a path a tunneled request never takes."""
        if request.method != "POST":
            return Refusal.METHOD
        if len(request.path) > self.max_path or _PATH.fullmatch(request.path) is None:
            return Refusal.PATH
        path = request.path.partition("?")[0]
        prefix = self._prefix()
        if prefix and not (path == prefix or path.startswith(f"{prefix}/")):
            return Refusal.PATH
        return None

    def _prefix(self) -> str:
        """Return the script prefix Django strips from the path, a setting first."""
        return get_script_prefix(self._scope).rstrip("/")

    def frame_headers(
        self, headers: Mapping[str, str]
    ) -> list[tuple[bytes, bytes]] | None:
        """Keep the allowed request headers of a frame, None for a malformed one."""
        if len(headers) > self.max_headers:
            return None
        csrf = csrf_header()
        kept: list[tuple[bytes, bytes]] = []
        for raw_name, value in headers.items():
            if _TOKEN.fullmatch(raw_name) is None:
                return None
            if len(value) > self.max_header_value or _UNSAFE_VALUE.search(value):
                return None
            try:
                encoded = value.encode("latin-1")
            except UnicodeEncodeError:
                return None
            name = raw_name.lower()
            if self._allowed(name, csrf):
                kept.append((name.encode("ascii"), encoded))
        return kept

    def _allowed(self, name: str, csrf: str | None) -> bool:
        if "_" in name:
            return False
        return (
            name in self.request_headers
            or name == csrf
            or name.startswith(self.request_prefixes)
        )

    def _accepts(self, headers: list[tuple[bytes, bytes]]) -> bool:
        """Whether the body is urlencoded, JSON or absent, never multipart."""
        types = [value for name, value in headers if name == b"content-type"]
        return all(
            value.decode("latin-1").partition(";")[0].strip().lower()
            in self.content_types
            for value in types
        )

    def _stale(self) -> bool:
        """Whether the handshake cookie lacks the CSRF cookie a POST needs."""
        return (
            not settings.CSRF_USE_SESSIONS
            and settings.CSRF_COOKIE_NAME not in self._cookies
        )

    def routes_to_action(self, path_info: str) -> bool:
        """Whether the URLconf, under the language of the path, routes it to an action.

        It runs on the shared pool, since a cold URLconf import or a converter blocks.
        """
        language = get_language_from_path(path_info) or settings.LANGUAGE_CODE
        try:
            with override(language):
                match = resolve(path_info)
        except Resolver404:
            return False
        return dispatches_actions(match.func)

    def http_scope(
        self, path: str, query: str, headers: list[tuple[bytes, bytes]]
    ) -> HTTPScope:
        """Build the HTTP scope of a request from the handshake scope and headers."""
        source = self._scope
        csrf = csrf_header()
        kept = [
            (name, value)
            for name, value in source.get("headers", ())
            if not self._dropped(name.decode("latin-1").lower(), csrf)
        ]
        if self._cookie:
            kept.append((b"cookie", self._cookie.encode("latin-1")))
        scheme = source.get("scheme", "ws")
        scope: dict[str, object] = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": source.get("http_version", "1.1"),
            "method": "POST",
            "scheme": "https" if scheme in {"wss", "https"} else "http",
            "path": unquote(path),
            "raw_path": path.encode("ascii"),
            "root_path": source.get("root_path", ""),
            "query_string": query.encode("ascii"),
            "headers": [*kept, *headers],
            "client": source.get("client"),
            "server": source.get("server"),
            TRANSPORT_SCOPE_KEY: "ws",
        }
        state = cast("Mapping[str, object]", source).get("state")
        if isinstance(state, Mapping):
            scope["state"] = dict(state)
        return cast("HTTPScope", scope)

    def _dropped(self, name: str, csrf: str | None) -> bool:
        return (
            name in self.dropped_scope_headers
            or name == csrf
            or name.startswith(self.dropped_scope_prefixes)
        )

    async def _run(self, scope: HTTPScope, exchange: _Exchange) -> None:
        """Run the app in a task that is never cancelled, only disconnected.

        A cancel lands in Django's thread-sensitive context, whose exit blocks the loop.
        """
        task = asyncio.ensure_future(self._app(scope, exchange.receive, exchange.send))
        self._running.add(task)
        _LIVE.add(task)
        task.add_done_callback(self._settle)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            exchange.gone.set()
            await self._release(task)
            raise
        except Exception:
            logger.exception("Realtime tunnel app failed on %s", scope["path"])
            exchange.broken = True
        finally:
            exchange.gone.set()

    async def _release(self, task: "asyncio.Task[None]") -> None:
        """Give a disconnected app `grace` seconds, then leave it to finish alone."""
        await asyncio.wait({task}, timeout=self.grace)

    def _settle(self, task: "asyncio.Task[None]") -> None:
        """Free the slot of a finished app and retrieve its outcome for asyncio."""
        self._running.discard(task)
        _LIVE.discard(task)
        if not task.cancelled():
            task.exception()

    def shape(
        self, request: Request, exchange: _Exchange, *, reached: bool
    ) -> Reply | Refusal:
        """Turn the collected response into a reply, or the reason it is none.

        `reached` says the dispatch saw the request, so an unmarked reply is its error.
        """
        status = exchange.status
        if exchange.broken or status is None:
            return Refusal.INTERNAL
        if exchange.overflow:
            return Refusal.TOO_BIG
        headers: defaultdict[str, list[str]] = defaultdict(list)
        cookies: list[str] = []
        for raw_name, raw_value in exchange.headers:
            name = raw_name.decode("latin-1").lower()
            value = raw_value.decode("latin-1")
            if name == "set-cookie":
                cookies.append(value)
            elif name in self.response_headers or name.startswith(
                self.response_prefixes
            ):
                headers[name].append(value)
            elif name == "content-encoding":
                return Refusal.ENCODING
        if not self._harmless(cookies, request, status):
            return Refusal.HTTP_ONLY
        joined = {name: ", ".join(values) for name, values in headers.items()}
        marker = joined.get(_MARKER)
        if marker in {"http", ROTATION} or (marker != "any" and not reached):
            return _unmarked(marker, status)
        return Reply(status, joined, bytes(exchange.body).decode("utf-8", "replace"))

    def _harmless(self, cookies: list[str], request: Request, status: int) -> bool:
        """Whether every Set-Cookie of the reply passes `cookie_allowed`."""
        unsafe: list[str] = []
        for header in cookies:
            parsed: SimpleCookie = SimpleCookie()
            parsed.load(header)
            if not parsed:
                unsafe.append("?")
            unsafe.extend(
                name
                for name, morsel in parsed.items()
                if not self.cookie_allowed(name, morsel)
            )
        if unsafe:
            logger.warning(
                "Realtime tunnel dropped a reply that sets cookies %s on %s %s, "
                "status %s",
                sorted(set(unsafe)),
                request.method,
                request.path,
                status,
            )
            return False
        if cookies:
            logger.debug("Realtime tunnel dropped %d unchanged cookies", len(cookies))
        return True

    def cookie_allowed(self, name: str, morsel: "Morsel[str]") -> bool:
        """Whether a Set-Cookie repeats a handshake cookie, so dropping it is harmless.

        A new, changed or deleted cookie needs the browser, so the reply is refused.
        """
        return self._cookies.get(name) == morsel.value and not _deleted(morsel)


def _unmarked(marker: str | None, status: int) -> Refusal:
    """Refuse a reply no action served, by its mark or its status.

    A rotated session needs the browser, and a 403 before the dispatch is CSRF.
    """
    if marker == ROTATION:
        return Refusal.HTTP_ONLY
    if status == HTTPStatus.FORBIDDEN:
        return Refusal.STALE
    if status >= HTTPStatus.INTERNAL_SERVER_ERROR:
        return Refusal.INTERNAL
    return Refusal.TRANSPORT


__all__ = [
    "DjangoTunnel",
    "Tunnel",
    "TunnelHints",
    "cookie_header",
    "csrf_header",
    "scope_headers",
]
