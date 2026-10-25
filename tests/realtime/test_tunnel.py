import asyncio
import logging
import sys
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import MappingProxyType

import pytest
from asgiref.typing import ASGIReceiveCallable, ASGISendCallable, Scope
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings

from next.forms.transport import DISPATCHED_SCOPE_KEY, DispatchMark
from next.realtime import application
from next.realtime.asgi import WebSocketApp
from next.realtime.backends import MemoryBroker
from next.realtime.frames import Refusal, Reply, Request
from next.realtime.limits import SocketLimits, TunnelLimits
from next.realtime.socket import Handshake, TopicSocket
from next.realtime.tunnel import DjangoTunnel, TunnelHints
from next.testing.realtime import MemoryPort, SocketTicket, override_websocket_mount
from tests.support.realtime import issue_session_ticket


_ACTION = "/_next/form/abc/"
_CSRF = b"csrftoken=secret"
_MARK = (b"X-Next-Transport", b"any")
_MARKED = {"x-next-transport": "any"}


@pytest.fixture(autouse=True)
def unmounted() -> Iterator[None]:
    with override_websocket_mount():
        yield


def _ws_scope(
    headers: list[tuple[bytes, bytes]] | None = None, **extra: object
) -> Scope:
    scope: dict[str, object] = {
        "type": "websocket",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "scheme": "ws",
        "path": "/_next/ws/",
        "raw_path": b"/_next/ws/",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"cookie", _CSRF)] if headers is None else headers,
        "client": ("10.0.0.1", 5000),
        "server": ("testserver", 80),
        "subprotocols": ["x"],
        "extensions": {"websocket.http.response": {}},
    }
    scope.update(extra)
    return scope


def _request(
    path: str = _ACTION,
    *,
    method: str = "POST",
    headers: Mapping[str, str] | None = None,
    body: str = "",
) -> Request:
    return Request("1", method, path, MappingProxyType(dict(headers or {})), body)


class _Routed(DjangoTunnel):
    """A tunnel that treats every path as the form action dispatch."""

    grace = 0.05

    def routes_to_action(self, path_info: str) -> bool:
        return True


def _limits(*, reply: int = 1024, concurrency: int = 4) -> TunnelLimits:
    return TunnelLimits(concurrency, 30.0, reply)


class _App:
    """A raw ASGI app that records its scope and answers with scripted events."""

    def __init__(self, *events: dict[str, object], fail: bool = False) -> None:
        self.events = events or (
            {"type": "http.response.start", "status": 200, "headers": [_MARK]},
            {"type": "http.response.body", "body": b"ok", "more_body": False},
        )
        self.fail = fail
        self.scope: Scope | None = None
        self.received: list[dict[str, object]] = []

    async def __call__(
        self, scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        self.scope = scope
        self.received.append(dict(await receive()))
        if self.fail:
            msg = "app failed"
            raise RuntimeError(msg)
        for event in self.events:
            await send(event)


def _start(status: int = 200, *headers: tuple[bytes, bytes]) -> dict[str, object]:
    return {
        "type": "http.response.start",
        "status": status,
        "headers": [*headers, _MARK],
    }


def _bare(status: int, *headers: tuple[bytes, bytes]) -> dict[str, object]:
    return {"type": "http.response.start", "status": status, "headers": list(headers)}


def _body(body: bytes = b"", *, more: bool = False) -> dict[str, object]:
    return {"type": "http.response.body", "body": body, "more_body": more}


def _run(
    app: object,
    request: Request,
    scope: Scope | None = None,
    *,
    limit: int = 1024,
    tunnel_class: type[DjangoTunnel] = _Routed,
) -> Reply | Refusal:
    tunnel = tunnel_class(app, scope or _ws_scope(), _limits(reply=limit))
    return asyncio.run(tunnel(request, connection="conn"))


def test_the_http_scope_keeps_the_handshake_environment() -> None:
    app = _App()
    handshake = [
        (b"host", b"example.com"),
        (b"origin", b"https://example.com"),
        (b"x-forwarded-proto", b"https"),
        (b"cookie", b"csrftoken=secret; "),
        (b"cookie", b"sessionid=key"),
        (b"sec-websocket-key", b"k"),
        (b"accept-encoding", b"gzip"),
        (b"if-none-match", b"etag"),
        (b"x-next-zone", b"stale"),
        (b"x-csrftoken", b"stale"),
        (b"upgrade", b"websocket"),
        (b"referer", b"https://example.com/old/"),
    ]
    scope = _ws_scope(
        handshake, scheme="wss", state={"pool": 1}, http_version="2", root_path=""
    )
    request = _request(
        f"{_ACTION}?a=%20b",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/html",
            "X-CSRFToken": "token",
            "X-Next-Zone": "list",
            "Cookie": "sessionid=forged",
            "Host": "evil.example",
            "X_Next_Odd": "1",
        },
        body="n=1",
    )
    assert isinstance(_run(app, request, scope), Reply)
    assert app.scope is not None
    built = dict(app.scope)
    assert built["type"] == "http"
    assert built["method"] == "POST"
    assert built["scheme"] == "https"
    assert built["http_version"] == "2"
    assert built["path"] == _ACTION
    assert built["raw_path"] == _ACTION.encode()
    assert built["query_string"] == b"a=%20b"
    assert built["client"] == ("10.0.0.1", 5000)
    assert built["state"] == {"pool": 1}
    assert "extensions" not in built
    assert built["next.transport"] == "ws"
    assert "subprotocols" not in built
    assert built["headers"] == [
        (b"host", b"example.com"),
        (b"origin", b"https://example.com"),
        (b"x-forwarded-proto", b"https"),
        (b"cookie", b"csrftoken=secret; sessionid=key"),
        (b"content-type", b"application/x-www-form-urlencoded"),
        (b"accept", b"text/html"),
        (b"x-csrftoken", b"token"),
        (b"x-next-zone", b"list"),
        (b"content-length", b"3"),
    ]
    assert app.received == [
        {"type": "http.request", "body": b"n=1", "more_body": False}
    ]


@override_settings(CSRF_HEADER_NAME="HTTP_X_XSRF_TOKEN")
def test_the_csrf_header_follows_the_setting() -> None:
    app = _App()
    request = _request(headers={"X-XSRF-Token": "a", "X-CSRFToken": "b"})
    _run(app, request, _ws_scope([(b"cookie", _CSRF), (b"x-xsrf-token", b"old")]))
    assert app.scope is not None
    headers = dict(app.scope["headers"])
    assert headers[b"x-xsrf-token"] == b"a"
    assert b"x-csrftoken" not in headers


@override_settings(CSRF_HEADER_NAME="CSRF_TOKEN_META")
def test_a_csrf_meta_key_admits_no_header() -> None:
    app = _App()
    _run(app, _request(headers={"csrf-token-meta": "a"}))
    assert app.scope is not None
    assert b"csrf-token-meta" not in dict(app.scope["headers"])


def test_a_scope_without_cookies_or_scheme_builds_plain_http() -> None:
    app = _App()
    scope = _ws_scope([])
    del scope["scheme"]
    with override_settings(CSRF_USE_SESSIONS=True):
        _run(app, _request(), scope)
    assert app.scope is not None
    assert app.scope["scheme"] == "http"
    assert b"cookie" not in dict(app.scope["headers"])
    assert "state" not in app.scope


@pytest.mark.parametrize(
    ("request_", "refusal"),
    [
        (_request(method="GET"), Refusal.METHOD),
        (_request(method="post"), Refusal.METHOD),
        (_request("relative/"), Refusal.PATH),
        (_request("//evil.example/"), Refusal.PATH),
        (_request("/a\\b/"), Refusal.PATH),
        (_request("/a#b"), Refusal.PATH),
        (_request("/a b/"), Refusal.PATH),
        (_request("/é/"), Refusal.PATH),
        (_request("/" + "a" * 2048), Refusal.PATH),
        (_request(headers={f"x-next-{n}": "1" for n in range(33)}), Refusal.HEADERS),
        (_request(headers={"accept": "a\r\nb"}), Refusal.HEADERS),
        (_request(headers={"accept": "a\0"}), Refusal.HEADERS),
        (_request(headers={"accept": "x" * 4097}), Refusal.HEADERS),
        (_request(headers={"accept": "ā"}), Refusal.HEADERS),
        (_request(headers={"x-next-€": "1"}), Refusal.HEADERS),
        (_request(headers={"x-next-a\r\nhost": "1"}), Refusal.HEADERS),
        (_request(headers={"x-next-a b": "1"}), Refusal.HEADERS),
        (_request(headers={"": "1"}), Refusal.HEADERS),
        (_request(body="\ud800"), Refusal.BODY),
        (
            _request(headers={"content-type": "multipart/form-data; boundary=x"}),
            Refusal.BODY,
        ),
        (_request(headers={"content-type": "text/plain"}), Refusal.BODY),
    ],
)
def test_a_malformed_request_is_refused_before_the_app(
    request_: Request, refusal: Refusal
) -> None:
    app = _App()
    assert _run(app, request_) is refusal
    assert refusal.retry is True
    assert app.scope is None


@pytest.mark.parametrize(
    "content_type", ["application/json", "Application/X-WWW-Form-Urlencoded; charset=x"]
)
def test_urlencoded_and_json_bodies_pass(content_type: str) -> None:
    assert isinstance(
        _run(_App(), _request(headers={"content-type": content_type})), Reply
    )


@pytest.mark.parametrize(
    ("path", "passes"),
    [
        ("/app", True),
        ("/app/_next/form/x/", True),
        ("/application/x", False),
        ("/x", False),
    ],
)
def test_a_mounted_socket_tunnels_only_below_its_root(
    path: str, *, passes: bool
) -> None:
    app = _App()
    answer = _run(app, _request(path), _ws_scope(root_path="/app"))
    assert isinstance(answer, Reply) is passes


@override_settings(FORCE_SCRIPT_NAME="/site")
@pytest.mark.parametrize(
    ("path", "passes"), [("/site/_next/form/x/", True), ("/_next/form/x/", False)]
)
def test_the_script_prefix_follows_force_script_name(
    path: str, *, passes: bool
) -> None:
    app = _App()
    answer = _run(app, _request(path), _ws_scope(root_path="/ignored"))
    assert isinstance(answer, Reply) is passes


def test_the_path_below_the_prefix_is_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(
        DjangoTunnel, "routes_to_action", lambda _self, path: seen.append(path)
    )
    app = _App()
    tunnel = DjangoTunnel(app, _ws_scope(root_path="/app"), _limits())
    answer = asyncio.run(tunnel(_request("/app/_next/form/x/"), connection="c"))
    assert answer is Refusal.TRANSPORT
    assert seen == ["/_next/form/x/"]


def test_a_handshake_without_the_csrf_cookie_is_stale() -> None:
    app = _App()
    assert _run(app, _request(), _ws_scope([(b"cookie", b"sessionid=k")])) is (
        Refusal.STALE
    )
    assert app.scope is None


@override_settings(CSRF_USE_SESSIONS=True)
def test_session_csrf_needs_no_csrf_cookie() -> None:
    assert isinstance(_run(_App(), _request(), _ws_scope([])), Reply)


@pytest.mark.parametrize("path", ["/", "/missing/"])
def test_a_path_outside_the_form_dispatch_is_refused(path: str) -> None:
    app = _App()
    assert _run(app, _request(path), tunnel_class=DjangoTunnel) is Refusal.TRANSPORT
    assert app.scope is None


def test_a_cold_urlconf_imports_off_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "slow_tunnel_urls.py").write_text(
        "import time\n\ntime.sleep(0.3)\nurlpatterns = []\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    app = _App()
    tunnel = DjangoTunnel(app, _ws_scope(), _limits())

    async def scenario() -> tuple[Reply | Refusal, int]:
        ticks = 0
        call = asyncio.ensure_future(tunnel(_request(), connection="conn"))
        while not call.done():
            await asyncio.sleep(0.01)
            ticks += 1
        return call.result(), ticks

    try:
        with override_settings(ROOT_URLCONF="slow_tunnel_urls"):
            answer, ticks = asyncio.run(scenario())
    finally:
        sys.modules.pop("slow_tunnel_urls", None)
    assert answer is Refusal.TRANSPORT
    assert ticks >= 10
    assert app.scope is None


def test_the_reply_keeps_the_allowed_headers_and_decodes_the_body() -> None:
    app = _App(
        _start(
            302,
            (b"Location", b"/next/"),
            (b"Content-Type", b"text/html"),
            (b"X-Next-Form", b"a"),
            (b"x-next-form", b"b"),
            (b"Vary", b"Cookie"),
            (b"Content-Length", b"3"),
        ),
        _body(b"\xff", more=True),
        _body(b"ok"),
        {"type": "http.response.trailers"},
    )
    assert _run(app, _request()) == Reply(
        302,
        {
            "location": "/next/",
            "content-type": "text/html",
            "x-next-form": "a, b",
            **_MARKED,
        },
        "�ok",
    )


@pytest.mark.parametrize(
    ("events", "refusal"),
    [
        (
            (
                _start(),
                _body(b"x" * 600, more=True),
                _body(b"x" * 600, more=True),
                _body(b"x"),
            ),
            Refusal.TOO_BIG,
        ),
        ((_start(200, (b"content-encoding", b"gzip")), _body()), Refusal.ENCODING),
        ((_start(), _start()), Refusal.INTERNAL),
        ((_body(b"x"),), Refusal.INTERNAL),
        ((), Refusal.INTERNAL),
        ((_bare(421, (b"x-next-transport", b"http")), _body()), Refusal.TRANSPORT),
        ((_bare(421), _body()), Refusal.TRANSPORT),
        ((_bare(200), _body(b"secret")), Refusal.TRANSPORT),
        ((_bare(200, (b"x-next-transport", b"http")), _body()), Refusal.TRANSPORT),
        ((_bare(403), _body(b"CSRF")), Refusal.STALE),
        ((_bare(500), _body()), Refusal.INTERNAL),
    ],
)
def test_a_reply_the_socket_cannot_carry_is_refused(
    events: tuple[dict[str, object], ...], refusal: Refusal
) -> None:
    app = _App(*events) if events else _NoReply()
    assert _run(app, _request()) is refusal


class _NoReply(_App):
    async def __call__(
        self, scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        await receive()


class _Dispatched(_App):
    """An app that flips the dispatch mark of the scope before it answers."""

    async def __call__(
        self, scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        mark = dict(scope)[DISPATCHED_SCOPE_KEY]
        assert isinstance(mark, DispatchMark)
        mark.reached = True
        await super().__call__(scope, receive, send)


@pytest.mark.parametrize(
    ("events", "answer"),
    [
        ((_bare(403), _body(b"denied")), Reply(403, {}, "denied")),
        ((_bare(404), _body(b"missing")), Reply(404, {}, "missing")),
        ((_bare(500), _body()), Reply(500, {}, "")),
        ((_bare(421, (b"x-next-transport", b"http")), _body()), Refusal.TRANSPORT),
        ((_bare(421, (b"x-next-transport", b"session")), _body()), Refusal.HTTP_ONLY),
    ],
)
def test_a_reply_past_the_dispatch_is_the_action_answer(
    events: tuple[dict[str, object], ...], answer: Reply | Refusal
) -> None:
    assert _run(_Dispatched(*events), _request()) == answer


def test_a_marked_421_is_a_plain_reply() -> None:
    answer = _run(_App(_start(421), _body()), _request())
    assert answer == Reply(421, _MARKED, "")


def test_a_failing_app_is_internal(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.ERROR, logger="next.realtime.tunnel"):
        assert _run(_App(fail=True), _request()) is Refusal.INTERNAL
    assert caplog.records[0].getMessage() == f"Realtime tunnel app failed on {_ACTION}"


@pytest.mark.parametrize(
    ("cookie", "harmless"),
    [
        (b"csrftoken=secret; Max-Age=31449600; Path=/; SameSite=Lax", True),
        (b"csrftoken=secret; Path=/", True),
        (b"csrftoken=secret; expires=Thu, 01 Jan 2099 00:00:00 GMT", True),
        (b"csrftoken=other; Path=/", False),
        (b"theme=dark; Path=/", False),
        (b"csrftoken=secret; Max-Age=0", False),
        (b"csrftoken=secret; Max-Age=soon", False),
        (b'csrftoken=""; expires=Thu, 01 Jan 1970 00:00:00 GMT; Max-Age=0', False),
        (b"csrftoken=secret; expires=Thu, 01 Jan 1970 00:00:00 GMT", False),
        (b"csrftoken=secret; expires=whenever", False),
        (b"", False),
        (b"bad name=1", False),
    ],
)
def test_a_set_cookie_passes_only_when_it_repeats_the_cookie(
    cookie: bytes, *, harmless: bool, caplog: pytest.LogCaptureFixture
) -> None:
    app = _App(_start(200, (b"set-cookie", cookie)), _body(b"ok"))
    with caplog.at_level(logging.DEBUG, logger="next.realtime.tunnel"):
        answer = _run(app, _request())
    if harmless:
        assert answer == Reply(200, _MARKED, "ok")
        assert caplog.records[-1].levelno == logging.DEBUG
    else:
        assert answer is Refusal.HTTP_ONLY
        assert Refusal.HTTP_ONLY.retry is False
        warning = caplog.records[-1]
        assert warning.levelno == logging.WARNING
        assert b"secret" not in warning.getMessage().encode()


def test_the_cookie_warning_names_cookies_never_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = _App(
        _start(
            200,
            (b"set-cookie", b"sessionid=new; HttpOnly"),
            (b"set-cookie", b"messages=abc; HttpOnly"),
        ),
        _body(),
    )
    with caplog.at_level(logging.WARNING, logger="next.realtime.tunnel"):
        assert _run(app, _request()) is Refusal.HTTP_ONLY
    assert caplog.records[0].getMessage() == (
        "Realtime tunnel dropped a reply that sets cookies ['messages', 'sessionid'] "
        f"on POST {_ACTION}, status 200"
    )


class _Lingering(_App):
    """An app that ends only when the request is disconnected, or never."""

    def __init__(self, *, stubborn: bool = False) -> None:
        super().__init__()
        self.stubborn = stubborn
        self.started = asyncio.Event()
        self.cancelled = False

    async def __call__(
        self, scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        await receive()
        self.started.set()
        self.received.append(dict(await receive()))
        if self.stubborn:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                self.cancelled = True
                raise


@pytest.mark.parametrize("stubborn", [False, True])
def test_a_cancelled_request_disconnects_and_never_cancels_the_app(
    *, stubborn: bool
) -> None:
    app = _Lingering(stubborn=stubborn)

    async def scenario() -> None:
        tunnel = _Routed(app, _ws_scope(), _limits())
        task = asyncio.create_task(tunnel(_request(), connection="c"))
        await app.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        assert app.received[-1] == {"type": "http.disconnect"}
        assert app.cancelled is False

    asyncio.run(scenario())


class _Held(_App):
    """An app that ignores the disconnect and ends when the test lets it."""

    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(
        self, scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        await receive()
        self.started.set()
        await self.release.wait()
        for event in self.events:
            await send(event)


def test_a_detached_app_keeps_its_slot_until_it_ends() -> None:
    app = _Held()

    async def scenario() -> list[Reply | Refusal]:
        tunnel = _Routed(app, _ws_scope(), _limits(concurrency=1))
        first = asyncio.create_task(tunnel(_request(), connection="c"))
        await app.started.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        held = await tunnel(_request(), connection="c")
        app.release.set()
        for _ in range(5):
            await asyncio.sleep(0)
        app.started.clear()
        return [held, await tunnel(_request(), connection="c")]

    answers = asyncio.run(scenario())
    assert answers == [Refusal.LIMIT, Reply(200, _MARKED, "ok")]


def test_the_hints_name_the_kept_headers() -> None:
    class _Wide(DjangoTunnel):
        request_headers = frozenset({"content-type", "accept", "x-requested-with"})

    with override_settings(CSRF_HEADER_NAME="HTTP_X_XSRF_TOKEN"):
        hints = _Wide(_plain_app, _ws_scope(), _limits()).hints()
    assert hints == TunnelHints(
        frozenset({"content-type", "accept", "x-requested-with"}),
        ("x-next-",),
        "x-xsrf-token",
    )


def test_a_subclass_may_admit_more_cookies() -> None:
    class _Lenient(_Routed):
        def cookie_allowed(self, name: str, morsel: object) -> bool:
            return name == "theme" or super().cookie_allowed(name, morsel)

    app = _App(_start(200, (b"set-cookie", b"theme=dark; Path=/")), _body(b"ok"))
    assert _run(app, _request(), tunnel_class=_Lenient) == Reply(200, _MARKED, "ok")


async def _plain_app(
    scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
) -> None:
    await send({"type": "http.response.start", "status": 204, "headers": []})
    await send({"type": "http.response.body", "body": b"", "more_body": False})


def test_application_binds_its_app_unless_the_socket_has_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(DjangoTunnel, "routes_to_action", lambda _self, _path: True)
    other = _App()
    bound = WebSocketApp()
    application(_plain_app, websocket=bound)
    assert isinstance(bound.tunnel(_ws_scope()), DjangoTunnel)
    explicit = WebSocketApp(http_app=other)
    explicit.bind(_plain_app)
    tunnel = explicit.tunnel(_ws_scope())
    assert tunnel is not None
    assert asyncio.run(tunnel(_request(), connection="c")) == Reply(200, _MARKED, "ok")
    assert other.scope is not None
    assert WebSocketApp().tunnel(_ws_scope()) is None


@override_settings(NEXT_FRAMEWORK={"REALTIME": {"TUNNEL": False}})
def test_a_disabled_tunnel_builds_none() -> None:
    assert WebSocketApp(http_app=_plain_app).tunnel(_ws_scope()) is None


@override_settings(NEXT_FRAMEWORK={"REALTIME": {"TUNNEL": {"MAX_REPLY_BYTES": 5}}})
def test_the_built_tunnel_takes_the_reply_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(DjangoTunnel, "routes_to_action", lambda _self, _path: True)
    app = _App(_start(), _body(b"x" * 6))
    tunnel = WebSocketApp(http_app=app).tunnel(_ws_scope())
    assert tunnel is not None
    assert asyncio.run(tunnel(_request(), connection="c")) is Refusal.TOO_BIG


@override_settings(NEXT_FRAMEWORK={"REALTIME": {"TUNNEL": {"TIMEOUT": 0}}})
def test_a_bad_limit_fails_when_the_app_is_built() -> None:
    with pytest.raises(ImproperlyConfigured):
        WebSocketApp()


class _ScriptedTunnel:
    """A tunnel port whose answers the test releases by hand."""

    def __init__(self, *, fail: bool = False, hang: bool = False) -> None:
        self.release = asyncio.Event()
        self.entered = 0
        self.fail = fail
        self.hang = hang

    async def __call__(self, request: Request, *, connection: str) -> Reply | Refusal:
        self.entered += 1
        if self.fail:
            msg = "tunnel failed"
            raise RuntimeError(msg)
        if self.hang:
            await asyncio.sleep(10)
        await self.release.wait()
        return Reply(200, {"content-type": "text/plain"}, request.id)

    def hints(self) -> TunnelHints:
        return TunnelHints(frozenset({"accept"}), ("x-app-",), None)


def _http(frame_id: str) -> dict[str, object]:
    return {"type": "http", "id": frame_id, "method": "POST", "path": "/x/"}


async def _frame(port: MemoryPort) -> dict[str, object]:
    async with asyncio.timeout(5):
        return await port.receive_json()


async def _open(
    ticket: SocketTicket, tunnel: object
) -> tuple[MemoryPort, asyncio.Task[None]]:
    port = MemoryPort()
    handshake = Handshake(ticket.credential, AnonymousUser(), None)
    socket = TopicSocket(
        port, handshake, broker=MemoryBroker(), heartbeat=60, tunnel=tunnel
    )
    task = asyncio.create_task(socket.serve(port.inbound()))
    port.push_json(ticket.subscribe_frame())
    assert (await _frame(port))["type"] == "hello"
    return port, task


async def _close(port: MemoryPort, task: asyncio.Task[None]) -> None:
    port.disconnect()
    async with asyncio.timeout(5):
        await task


@pytest.mark.django_db(transaction=True)
def test_requests_past_the_concurrency_are_refused_with_retry() -> None:
    ticket = issue_session_ticket()

    async def scenario() -> None:
        tunnel = _ScriptedTunnel()
        port, task = await _open(ticket, tunnel)
        for index in range(5):
            port.push_json(_http(str(index)))
        assert await _frame(port) == {
            "type": "http",
            "id": "4",
            "status": 0,
            "error": "limit",
            "retry": True,
        }
        assert tunnel.entered == 4
        tunnel.release.set()
        replies = sorted([await _frame(port) for _ in range(4)], key=str)
        assert [reply["body"] for reply in replies] == ["0", "1", "2", "3"]
        assert replies[0] == {
            "type": "http",
            "id": "0",
            "status": 200,
            "headers": {"content-type": "text/plain"},
            "body": "0",
        }
        await _close(port, task)

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("tunnel", "error", "settings"),
    [
        (None, "disabled", {}),
        (_ScriptedTunnel(), "disabled", {"TUNNEL": False}),
        (_ScriptedTunnel(fail=True), "internal", {}),
        (_ScriptedTunnel(hang=True), "timeout", {"TUNNEL": {"TIMEOUT": 0.05}}),
    ],
)
def test_a_request_without_an_answer_gets_a_refusal(
    tunnel: object, error: str, settings: dict[str, object]
) -> None:
    ticket = issue_session_ticket()

    async def scenario() -> None:
        port, task = await _open(ticket, tunnel)
        port.push_json(_http("7"))
        frame = await _frame(port)
        await _close(port, task)
        assert frame["error"] == error
        assert frame["retry"] is (error == "disabled")

    with override_settings(NEXT_FRAMEWORK={"REALTIME": settings}):
        asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_an_empty_bucket_refuses_a_request_with_retry() -> None:
    ticket = issue_session_ticket()

    async def scenario() -> None:
        port, task = await _open(ticket, None)
        port.push_json(_http("1"))
        assert (await _frame(port))["error"] == "disabled"
        port.push_json(_http("2"))
        assert await _frame(port) == {
            "type": "http",
            "id": "2",
            "status": 0,
            "error": "limit",
            "retry": True,
        }
        await _close(port, task)

    upstream = {"UPSTREAM": {"RATE": 0.001, "BURST": 1}}
    with override_settings(NEXT_FRAMEWORK={"REALTIME": upstream}):
        asyncio.run(scenario())


class _SlowReader(MemoryPort):
    """A client that stops reading `http` replies until the test lets it."""

    def __init__(self) -> None:
        super().__init__()
        self.reading = asyncio.Event()

    async def send(self, text: str) -> None:
        if '"type":"http"' in text:
            await self.reading.wait()
        await super().send(text)


class _Instant:
    def __init__(self) -> None:
        self.entered = 0

    async def __call__(self, request: Request, *, connection: str) -> Reply:
        self.entered += 1
        return Reply(200, {}, "x" * 1000)

    def hints(self) -> TunnelHints:
        return TunnelHints(frozenset(), (), None)


@pytest.mark.django_db(transaction=True)
def test_a_slot_is_held_until_its_reply_is_sent() -> None:
    ticket = issue_session_ticket()

    async def scenario() -> int:
        port = _SlowReader()
        tunnel = _Instant()
        socket = TopicSocket(
            port,
            Handshake(ticket.credential, AnonymousUser(), None),
            broker=MemoryBroker(),
            heartbeat=60,
            tunnel=tunnel,
        )
        task = asyncio.create_task(socket.serve(port.inbound()))
        port.push_json(ticket.subscribe_frame())
        assert (await _frame(port))["type"] == "hello"
        for index in range(12):
            port.push_json(_http(str(index)))
        for _ in range(20):
            await asyncio.sleep(0)
        entered = tunnel.entered
        port.reading.set()
        replies = [await _frame(port) for _ in range(12)]
        await _close(port, task)
        assert len(replies) == 12
        return entered

    assert asyncio.run(scenario()) == 4


@pytest.mark.django_db(transaction=True)
def test_the_hello_carries_the_limits_of_a_custom_tunnel() -> None:
    ticket = issue_session_ticket()

    async def scenario() -> dict[str, object]:
        port = MemoryPort()
        socket = TopicSocket(
            port,
            Handshake(ticket.credential, AnonymousUser(), None),
            broker=MemoryBroker(),
            heartbeat=60,
            tunnel=_Instant(),
            limits=SocketLimits.configured(),
        )
        task = asyncio.create_task(socket.serve(port.inbound()))
        port.push_json(ticket.subscribe_frame())
        hello = await _frame(port)
        await _close(port, task)
        return hello

    tunnel = {"TIMEOUT": 7.5}
    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"TUNNEL": tunnel}}):
        hello = asyncio.run(scenario())
    assert hello["tunnel"] == {
        "timeout": 7.5,
        "frame": 65536,
        "headers": [],
        "prefixes": [],
        "csrf": None,
    }


@override_settings(
    CSRF_TRUSTED_ORIGINS=["https://*.trusted.example"],
    ALLOWED_HOSTS=["testserver", ".own.example", "App.own.example", "*"],
)
def test_explicit_origins_outside_csrf_are_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    origins = [
        "https://a.trusted.example",
        "https://app.own.example:8443",
        "https://other.own.example",
        "https://partner.example",
    ]
    with caplog.at_level(logging.WARNING, logger="next.realtime.asgi"):
        WebSocketApp(origins=origins)
    [warning] = [record for record in caplog.records if "W146" in record.getMessage()]
    message = warning.getMessage()
    assert "'https://other.own.example', 'https://partner.example'" in message
    assert "trusted.example'" not in message
    assert "app.own.example" not in message


@override_settings(NEXT_FRAMEWORK={"REALTIME": {"TUNNEL": False}})
def test_a_disabled_tunnel_reports_no_origin(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="next.realtime.asgi"):
        WebSocketApp(origins=["https://partner.example"])
    assert not [record for record in caplog.records if "W146" in record.getMessage()]
