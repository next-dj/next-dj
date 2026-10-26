import asyncio
import logging
import sys
import threading
from collections.abc import Iterator, Mapping
from pathlib import Path
from types import MappingProxyType, ModuleType

import pytest
from asgiref.typing import ASGIReceiveCallable, ASGISendCallable, Scope
from django.conf.urls.i18n import i18n_patterns
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured
from django.http import HttpRequest, HttpResponse
from django.test import override_settings
from django.urls import path as route
from django.utils import translation

from next.forms.transport import ACTION_KIND, DISPATCHED_SCOPE_KEY, DispatchMark
from next.realtime import application
from next.realtime.asgi import WebSocketApp
from next.realtime.backends import MemoryBroker
from next.realtime.frames import Refusal, Reply, Request
from next.realtime.limits import SocketLimits, TunnelLimits
from next.realtime.socket import Handshake, TopicSocket
from next.realtime.tunnel import DjangoTunnel, TunnelHints
from next.testing.realtime import MemoryPort, SocketTicket, override_websocket_mount
from tests.support.realtime import issue_session_ticket, websocket_scope


_ACTION = "/_next/form/abc/"
_CSRF = b"csrftoken=secret"
_MARK = (b"X-Next-Transport", b"any")
_MARKED = {"x-next-transport": "any"}


@pytest.fixture(autouse=True)
def unmounted() -> Iterator[None]:
    with override_websocket_mount():
        yield


_CSRF_COOKIE = ((b"cookie", _CSRF),)


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
    tunnel = tunnel_class(
        app, scope or websocket_scope(_CSRF_COOKIE), _limits(reply=limit)
    )
    return asyncio.run(tunnel(request, connection="conn"))


def test_http_scope_keeps_the_handshake_environment() -> None:
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
    scope = websocket_scope(
        handshake,
        scheme="wss",
        state={"pool": 1},
        http_version="2",
        client=("10.0.0.1", 5000),
        subprotocols=["x"],
        extensions={"websocket.http.response": {}},
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
def test_csrf_header_follows_the_setting() -> None:
    app = _App()
    request = _request(headers={"X-XSRF-Token": "a", "X-CSRFToken": "b"})
    _run(app, request, websocket_scope([(b"cookie", _CSRF), (b"x-xsrf-token", b"old")]))
    assert app.scope is not None
    headers = dict(app.scope["headers"])
    assert headers[b"x-xsrf-token"] == b"a"
    assert b"x-csrftoken" not in headers


@override_settings(CSRF_HEADER_NAME="CSRF_TOKEN_META")
def test_csrf_meta_key_admits_no_header() -> None:
    app = _App()
    _run(app, _request(headers={"csrf-token-meta": "a"}))
    assert app.scope is not None
    assert b"csrf-token-meta" not in dict(app.scope["headers"])


def test_scope_without_cookies_or_scheme_builds_plain_http() -> None:
    app = _App()
    scope = websocket_scope([])
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
def test_malformed_request_is_refused_before_the_app(
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
def test_mounted_socket_tunnels_only_below_its_root(path: str, *, passes: bool) -> None:
    app = _App()
    answer = _run(app, _request(path), websocket_scope(_CSRF_COOKIE, root_path="/app"))
    assert isinstance(answer, Reply) is passes


@override_settings(FORCE_SCRIPT_NAME="/site")
@pytest.mark.parametrize(
    ("path", "passes"), [("/site/_next/form/x/", True), ("/_next/form/x/", False)]
)
def test_script_prefix_follows_force_script_name(path: str, *, passes: bool) -> None:
    app = _App()
    answer = _run(
        app, _request(path), websocket_scope(_CSRF_COOKIE, root_path="/ignored")
    )
    assert isinstance(answer, Reply) is passes


def test_path_below_the_prefix_is_resolved(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []
    monkeypatch.setattr(
        DjangoTunnel, "routes_to_action", lambda _self, path: seen.append(path)
    )
    app = _App()
    tunnel = DjangoTunnel(
        app, websocket_scope(_CSRF_COOKIE, root_path="/app"), _limits()
    )
    answer = asyncio.run(tunnel(_request("/app/_next/form/x/"), connection="c"))
    assert answer is Refusal.TRANSPORT
    assert seen == ["/_next/form/x/"]


def _action_view(request: HttpRequest) -> HttpResponse:
    return HttpResponse()


_action_view.next_access_kind = ACTION_KIND


def _translated_urls(*, prefix_default_language: bool) -> ModuleType:
    urls = ModuleType("translated_tunnel_urls")
    urls.urlpatterns = i18n_patterns(
        route("_next/form/x/", _action_view),
        prefix_default_language=prefix_default_language,
    )
    return urls


@pytest.mark.parametrize(
    ("prefixed", "path", "routes"),
    [
        (True, "/en/_next/form/x/", True),
        (True, "/de/_next/form/x/", True),
        (True, "/fr/_next/form/x/", False),
        (True, "/_next/form/x/", False),
        (False, "/_next/form/x/", True),
        (False, "/de/_next/form/x/", True),
        (False, "/en/_next/form/x/", False),
    ],
)
def test_translated_action_routes_under_the_language_of_its_path(
    *, prefixed: bool, path: str, routes: bool
) -> None:
    tunnel = DjangoTunnel(_App(), websocket_scope(_CSRF_COOKIE), _limits())
    with (
        override_settings(
            ROOT_URLCONF=_translated_urls(prefix_default_language=prefixed),
            USE_I18N=True,
            LANGUAGE_CODE="en",
            LANGUAGES=[("en", "English"), ("de", "German")],
        ),
        translation.override("de" if path.startswith("/en/") else "en"),
    ):
        assert tunnel.routes_to_action(path) is routes


def test_handshake_without_the_csrf_cookie_is_stale() -> None:
    app = _App()
    assert _run(app, _request(), websocket_scope([(b"cookie", b"sessionid=k")])) is (
        Refusal.STALE
    )
    assert app.scope is None


@override_settings(CSRF_USE_SESSIONS=True)
def test_session_csrf_needs_no_csrf_cookie() -> None:
    assert isinstance(_run(_App(), _request(), websocket_scope([])), Reply)


@pytest.mark.parametrize("path", ["/", "/missing/"])
def test_path_outside_the_form_dispatch_is_refused(path: str) -> None:
    app = _App()
    assert _run(app, _request(path), tunnel_class=DjangoTunnel) is Refusal.TRANSPORT
    assert app.scope is None


def test_cold_urlconf_imports_off_the_event_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    gate = ModuleType("tunnel_gate")
    gate.entered = threading.Event()
    gate.release = threading.Event()
    gate.released = False
    monkeypatch.setitem(sys.modules, "tunnel_gate", gate)
    (tmp_path / "slow_tunnel_urls.py").write_text(
        "import tunnel_gate\n\n"
        "tunnel_gate.entered.set()\n"
        "tunnel_gate.released = tunnel_gate.release.wait(5)\n"
        "urlpatterns = []\n"
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    app = _App()
    tunnel = DjangoTunnel(app, websocket_scope(_CSRF_COOKIE), _limits())

    async def scenario() -> Reply | Refusal:
        call = asyncio.ensure_future(tunnel(_request(), connection="conn"))
        assert await asyncio.to_thread(gate.entered.wait, 5)
        gate.release.set()
        return await call

    try:
        with override_settings(ROOT_URLCONF="slow_tunnel_urls"):
            answer = asyncio.run(scenario())
    finally:
        sys.modules.pop("slow_tunnel_urls", None)
    assert answer is Refusal.TRANSPORT
    assert gate.released
    assert app.scope is None


def test_reply_keeps_the_allowed_headers_and_decodes_the_body() -> None:
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
def test_reply_the_socket_cannot_carry_is_refused(
    events: tuple[dict[str, object], ...], refusal: Refusal
) -> None:
    app = _App(*events) if events else _NoReply()
    assert _run(app, _request()) is refusal


def test_reply_body_of_exactly_the_limit_is_carried() -> None:
    app = _App(_start(), _body(b"x" * 8, more=True), _body(b"y" * 2))
    assert _run(app, _request(), limit=10) == Reply(200, _MARKED, "x" * 8 + "y" * 2)
    assert _run(_App(_start(), _body(b"x" * 11)), _request(), limit=10) is (
        Refusal.TOO_BIG
    )


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
def test_reply_past_the_dispatch_is_the_action_answer(
    events: tuple[dict[str, object], ...], answer: Reply | Refusal
) -> None:
    assert _run(_Dispatched(*events), _request()) == answer


def test_marked_421_is_a_plain_reply() -> None:
    answer = _run(_App(_start(421), _body()), _request())
    assert answer == Reply(421, _MARKED, "")


def test_failing_app_is_internal(caplog: pytest.LogCaptureFixture) -> None:
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
        (b"bad@name=1", False),
        (b"csrftoken=secret, bad@name=1", False),
    ],
)
def test_set_cookie_passes_only_when_it_repeats_the_cookie(
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


def test_cookie_warning_names_cookies_never_values(
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


@pytest.mark.parametrize(
    ("events", "refusal"),
    [
        ((_bare(403, (b"set-cookie", b"csrftoken=new")), _body()), Refusal.STALE),
        (
            (_bare(421, (b"x-next-transport", b"http"), (b"set-cookie", b"t=1")),),
            Refusal.TRANSPORT,
        ),
        ((_bare(200, (b"set-cookie", b"theme=dark")), _body()), Refusal.TRANSPORT),
    ],
)
def test_refusal_of_an_unserved_reply_wins_over_its_cookies(
    events: tuple[dict[str, object], ...], refusal: Refusal
) -> None:
    assert _run(_App(*events), _request()) is refusal
    assert refusal.retry is True


class _SlowRouting(_Routed):
    """A tunnel whose URL resolution waits on the test, as a cold URLconf import."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.entered = threading.Event()
        self.release = threading.Event()

    def routes_to_action(self, path_info: str) -> bool:
        self.entered.set()
        self.release.wait(5)
        return True


def test_a_request_still_routing_holds_its_slot() -> None:
    async def scenario() -> list[Reply | Refusal]:
        tunnel = _SlowRouting(
            _App(), websocket_scope(_CSRF_COOKIE), _limits(concurrency=1)
        )
        first = asyncio.create_task(tunnel(_request(), connection="c"))
        await asyncio.to_thread(tunnel.entered.wait, 5)
        burst = await tunnel(_request(), connection="c")
        tunnel.release.set()
        return [burst, await first, await tunnel(_request(), connection="c")]

    answers = asyncio.run(scenario())
    assert answers == [
        Refusal.LIMIT,
        Reply(200, _MARKED, "ok"),
        Reply(200, _MARKED, "ok"),
    ]


def test_a_cancelled_routing_frees_its_slot() -> None:
    async def scenario() -> Reply | Refusal:
        tunnel = _SlowRouting(
            _App(), websocket_scope(_CSRF_COOKIE), _limits(concurrency=1)
        )
        first = asyncio.create_task(tunnel(_request(), connection="c"))
        await asyncio.to_thread(tunnel.entered.wait, 5)
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        tunnel.release.set()
        return await tunnel(_request(), connection="c")

    assert asyncio.run(scenario()) == Reply(200, _MARKED, "ok")


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
def test_cancelled_request_disconnects_and_never_cancels_the_app(
    *, stubborn: bool
) -> None:
    app = _Lingering(stubborn=stubborn)

    async def scenario() -> None:
        tunnel = _Routed(app, websocket_scope(_CSRF_COOKIE), _limits())
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


def test_detached_app_keeps_its_slot_until_it_ends() -> None:
    app = _Held()

    async def scenario() -> list[Reply | Refusal]:
        tunnel = _Routed(app, websocket_scope(_CSRF_COOKIE), _limits(concurrency=1))
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


def test_hints_name_the_kept_headers() -> None:
    class _Wide(DjangoTunnel):
        request_headers = frozenset({"content-type", "accept", "x-requested-with"})

    with override_settings(CSRF_HEADER_NAME="HTTP_X_XSRF_TOKEN"):
        hints = _Wide(_plain_app, websocket_scope(_CSRF_COOKIE), _limits()).hints()
    assert hints == TunnelHints(
        frozenset({"content-type", "accept", "x-requested-with"}),
        ("x-next-",),
        "x-xsrf-token",
    )


def test_subclass_may_admit_more_cookies() -> None:
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
    assert isinstance(bound.tunnel(websocket_scope(_CSRF_COOKIE)), DjangoTunnel)
    explicit = WebSocketApp(http_app=other)
    explicit.bind(_plain_app)
    tunnel = explicit.tunnel(websocket_scope(_CSRF_COOKIE))
    assert tunnel is not None
    assert asyncio.run(tunnel(_request(), connection="c")) == Reply(200, _MARKED, "ok")
    assert other.scope is not None
    assert WebSocketApp().tunnel(websocket_scope(_CSRF_COOKIE)) is None


@override_settings(NEXT_FRAMEWORK={"REALTIME": {"TUNNEL": False}})
def test_disabled_tunnel_builds_none() -> None:
    assert (
        WebSocketApp(http_app=_plain_app).tunnel(websocket_scope(_CSRF_COOKIE)) is None
    )


@override_settings(NEXT_FRAMEWORK={"REALTIME": {"TUNNEL": {"MAX_REPLY_BYTES": 5}}})
def test_built_tunnel_takes_the_reply_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(DjangoTunnel, "routes_to_action", lambda _self, _path: True)
    app = _App(_start(), _body(b"x" * 6))
    tunnel = WebSocketApp(http_app=app).tunnel(websocket_scope(_CSRF_COOKIE))
    assert tunnel is not None
    assert asyncio.run(tunnel(_request(), connection="c")) is Refusal.TOO_BIG


@override_settings(NEXT_FRAMEWORK={"REALTIME": {"TUNNEL": {"TIMEOUT": 0}}})
def test_bad_limit_fails_when_the_app_is_built() -> None:
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
    "tunnel",
    [_ScriptedTunnel(), _ScriptedTunnel(fail=True), _ScriptedTunnel(hang=True)],
    ids=["reply", "failure", "timeout"],
)
def test_finished_request_frees_its_slot(tunnel: _ScriptedTunnel) -> None:
    ticket = issue_session_ticket()
    tunnel.release.set()

    async def scenario() -> tuple[list[object], int]:
        port = MemoryPort()
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
        errors = []
        for index in range(2):
            port.push_json(_http(str(index)))
            errors.append((await _frame(port)).get("error"))
            for _ in range(5):
                await asyncio.sleep(0)
        held = socket._requests
        await _close(port, task)
        return errors, held

    settings = {"TUNNEL": {"CONCURRENCY": 1, "TIMEOUT": 0.05}}
    with override_settings(NEXT_FRAMEWORK={"REALTIME": settings}):
        errors, held = asyncio.run(scenario())
    assert "limit" not in errors
    assert held == 0


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
def test_request_without_an_answer_gets_a_refusal(
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
def test_empty_bucket_refuses_a_request_with_retry() -> None:
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
def test_slot_is_held_until_its_reply_is_sent() -> None:
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
def test_hello_carries_the_limits_of_a_custom_tunnel() -> None:
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
def test_disabled_tunnel_reports_no_origin(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="next.realtime.asgi"):
        WebSocketApp(origins=["https://partner.example"])
    assert not [record for record in caplog.records if "W146" in record.getMessage()]
