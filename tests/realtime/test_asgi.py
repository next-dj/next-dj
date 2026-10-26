import asyncio
import logging
from collections.abc import Callable, Iterator
from datetime import timedelta

import pytest
from asgiref.typing import ASGIReceiveCallable, ASGISendCallable, Scope
from django.contrib.auth import get_user_model, login
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
from django.core.handlers.asgi import ASGIHandler
from django.core.handlers.wsgi import get_script_name
from django.test import RequestFactory, override_settings
from django.urls import clear_script_prefix, set_script_prefix

from next.realtime import WebSocketApp, application, asgi, transports
from next.realtime.frames import Close
from next.realtime.socket import Handshake, TopicSocket
from next.testing.realtime import (
    SocketTicket,
    WebSocketCommunicator,
    issue_socket_ticket,
    override_websocket_mount,
)
from tests.support.realtime import ROOM_AUTHORIZER_REF


_ORIGIN = (b"origin", b"http://testserver")
_HOST = (b"host", b"testserver")


@pytest.fixture(autouse=True)
def unmounted() -> Iterator[None]:
    with override_websocket_mount():
        yield


@pytest.fixture()
def ticket(transactional_db: None) -> SocketTicket:
    session = SessionStore()
    session.create()
    return issue_socket_ticket(
        ("room:42",), authorizer_ref=ROOM_AUTHORIZER_REF, session=session
    )


def _scope(
    headers: list[tuple[bytes, bytes]],
    *,
    server: tuple[str, int | None] | None = ("testserver", 80),
    scheme: str = "ws",
) -> Scope:
    return {
        "type": "websocket",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "scheme": scheme,
        "path": "/_next/ws/",
        "raw_path": b"/_next/ws/",
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": None,
        "server": server,
        "subprotocols": [],
        "extensions": {},
    }


async def _http_ok(
    scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
) -> None:
    await send(
        {"type": "http.response.start", "status": 204, "headers": [], "trailers": False}
    )
    await send({"type": "http.response.body", "body": b"", "more_body": False})


def test_creating_the_app_announces_the_websocket() -> None:
    assert transports.active_transports() == ("sse",)
    WebSocketApp()
    assert transports.active_transports() == ("ws", "sse")


def test_creating_the_app_logs_deployment_warnings(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = "django.contrib.sessions.backends.signed_cookies"
    with (
        override_settings(MIDDLEWARE=[], SESSION_ENGINE=engine),
        caplog.at_level(logging.WARNING, logger="next.realtime"),
    ):
        WebSocketApp()
    messages = [record.getMessage() for record in caplog.records]
    assert messages[0].startswith("(next.W141) WebSocket tickets require")
    assert messages[1].startswith("(next.W142) WebSocket tickets are bound to the")
    assert "next.realtime.SignedCookieBinding" in messages[1]


@pytest.mark.parametrize(
    ("origin", "allowed"),
    [
        ("http://testserver", True),
        ("https://testserver", False),
        ("HTTP://TESTSERVER", False),
        ("http://testserver:8000", False),
        ("http://evil.example", False),
        ("null", False),
        ("http://[broken", False),
        (None, False),
    ],
)
def test_default_origin_matches_the_validated_host(
    origin: str | None, *, allowed: bool
) -> None:
    app = WebSocketApp()
    assert app.allow_origin(origin, _scope([_HOST])) is allowed


@pytest.mark.parametrize(
    ("origin", "allowed"), [("https://testserver", True), ("http://testserver", False)]
)
def test_default_origin_on_a_secure_socket_requires_https(
    origin: str, *, allowed: bool
) -> None:
    scope = _scope([_HOST], scheme="wss")
    assert WebSocketApp().allow_origin(origin, scope) is allowed


@override_settings(SECURE_PROXY_SSL_HEADER=("HTTP_X_FORWARDED_PROTO", "https"))
@pytest.mark.parametrize(
    ("proto", "origin", "allowed"),
    [
        (b"https", "https://testserver", True),
        (b"https", "http://testserver", False),
        (b"http", "https://testserver", False),
    ],
)
def test_default_origin_takes_the_scheme_from_the_proxy_header(
    proto: bytes, origin: str, *, allowed: bool
) -> None:
    scope = _scope([_HOST, (b"x-forwarded-proto", proto)])
    assert WebSocketApp().allow_origin(origin, scope) is allowed


def test_default_origin_refuses_a_host_outside_allowed_hosts() -> None:
    app = WebSocketApp()
    scope = _scope([(b"host", b"evil.example")])
    assert app.allow_origin("http://evil.example", scope) is False


@override_settings(ALLOWED_HOSTS=[".example.com"])
@pytest.mark.parametrize(
    ("host", "origin", "allowed"),
    [
        (b"example.com", "https://example.com", True),
        (b"deep.app.example.com", "https://deep.app.example.com", True),
        (b"app.example.com", "https://other.example.com", False),
        (b"badexample.com", "https://badexample.com", False),
    ],
)
def test_default_origin_honours_a_leading_dot_allowed_host(
    host: bytes, origin: str, *, allowed: bool
) -> None:
    scope = _scope([(b"host", host)], scheme="wss")
    assert WebSocketApp().allow_origin(origin, scope) is allowed


def test_default_origin_falls_back_to_the_server_address() -> None:
    app = WebSocketApp()
    assert app.allow_origin("http://testserver", _scope([])) is True
    with override_settings(ALLOWED_HOSTS=["unknown"]):
        assert app.allow_origin("http://unknown:0", _scope([], server=None)) is True


@override_settings(USE_X_FORWARDED_HOST=True, ALLOWED_HOSTS=["public.example"])
def test_default_origin_follows_a_forwarded_host() -> None:
    app = WebSocketApp()
    scope = _scope([(b"host", b"internal"), (b"x-forwarded-host", b"public.example")])
    assert app.allow_origin("http://public.example", scope) is True


@override_settings(
    CSRF_TRUSTED_ORIGINS=["https://app.example", "https://*.example.org"]
)
@pytest.mark.parametrize(
    ("origin", "allowed"),
    [
        ("https://app.example", True),
        ("http://app.example", False),
        ("https://shop.example.org", True),
        ("https://example.org", True),
        ("http://shop.example.org", False),
        ("https://[broken", False),
    ],
)
def test_default_origin_accepts_csrf_trusted_origins(
    origin: str, *, allowed: bool
) -> None:
    app = WebSocketApp()
    assert app.allow_origin(origin, _scope([_HOST])) is allowed


def test_default_origin_rereads_csrf_trusted_origins_after_a_change() -> None:
    app = WebSocketApp()
    scope = _scope([_HOST])
    with override_settings(CSRF_TRUSTED_ORIGINS=["https://one.example"]):
        assert app.allow_origin("https://one.example", scope) is True
    with override_settings(CSRF_TRUSTED_ORIGINS=["https://two.example"]):
        assert app.allow_origin("https://one.example", scope) is False


def test_explicit_origins_replace_the_default() -> None:
    app = WebSocketApp(origins=["https://app.example", "https://*.example.org"])
    scope = _scope([_HOST])
    assert app.allow_origin("https://app.example", scope) is True
    assert app.allow_origin("https://a.example.org", scope) is True
    assert app.allow_origin("http://testserver", scope) is False


@pytest.mark.parametrize(
    ("origins", "error"),
    [
        ("https://app.example", TypeError),
        ([], ValueError),
        ((), ValueError),
        (["app.example"], ValueError),
        (["https://"], ValueError),
        (["https://[broken"], ValueError),
    ],
)
def test_explicit_origins_reject_unusable_entries(
    origins: object, error: type[Exception]
) -> None:
    with pytest.raises(error):
        WebSocketApp(origins=origins)


def test_origins_reject_a_non_string_value() -> None:
    assert 5 not in asgi._Origins.parse(["https://app.example"])


def test_request_meta_mirrors_django_header_naming() -> None:
    scope = _scope(
        [
            (b"cookie", b"a=1; "),
            (b"cookie", b"b=2"),
            (b"x_spoof", b"1"),
            (b"accept", b"x"),
            (b"accept", b"y"),
        ]
    )
    request = asgi._request(scope)
    meta = request.META
    assert request.scheme == "http"
    assert meta["HTTP_COOKIE"] == "a=1; b=2"
    assert meta["HTTP_ACCEPT"] == "x,y"
    assert "HTTP_X_SPOOF" not in meta
    assert meta["SERVER_PORT"] == "80"


@pytest.mark.django_db(transaction=True)
def test_authenticate_loads_the_session_named_by_the_cookie(
    ticket: SocketTicket,
) -> None:
    app = WebSocketApp()
    scope = _scope([(b"cookie", b"other=1"), ticket.cookie])
    handshake = asyncio.run(app.authenticate(scope))
    assert handshake is not None
    assert handshake.credential == ticket.credential
    assert handshake.user.is_authenticated is False
    assert handshake.expires_at is not None


@pytest.mark.django_db(transaction=True)
def test_authenticate_loads_the_logged_in_user_read_only() -> None:
    user = get_user_model().objects.create_user("ada", password="pw")
    request = RequestFactory().get("/")
    request.session = SessionStore()
    login(request, user)
    request.session.save()
    key = request.session.session_key
    stored = dict(SessionStore(key).load())
    with override_settings(
        SECRET_KEY="rotated", SECRET_KEY_FALLBACKS=["test-secret-key"]
    ):
        handshake = asyncio.run(
            WebSocketApp().authenticate(
                _scope([(b"cookie", f"sessionid={key}".encode())])
            )
        )
    assert handshake is not None
    assert handshake.user.pk == user.pk
    assert SessionStore(key).exists(key)
    assert dict(SessionStore(key).load()) == stored


@pytest.mark.parametrize("cookie", [None, b"sessionid=", b"other=1"])
def test_authenticate_without_a_session_cookie_returns_none(
    cookie: bytes | None,
) -> None:
    headers = [] if cookie is None else [(b"cookie", cookie)]
    assert asyncio.run(WebSocketApp().authenticate(_scope(headers))) is None


@pytest.mark.django_db(transaction=True)
def test_authenticate_refuses_a_missing_or_expired_session() -> None:
    app = WebSocketApp()
    missing = _scope([(b"cookie", b"sessionid=abcdefghijklmnopqrstuvwxyz012345")])
    assert asyncio.run(app.authenticate(missing)) is None
    expired = SessionStore()
    expired.set_expiry(-10)
    expired.create()
    scope = _scope([(b"cookie", f"sessionid={expired.session_key}".encode())])
    assert asyncio.run(app.authenticate(scope)) is None


@pytest.mark.django_db(transaction=True)
def test_authenticate_refuses_a_session_its_store_has_expired() -> None:
    session = SessionStore()
    session.create()
    key = session.session_key
    SessionStore.get_model_class().objects.filter(session_key=key).update(
        expire_date=session.get_expiry_date() - timedelta(days=60)
    )
    scope = _scope([(b"cookie", f"sessionid={key}".encode())])
    assert asyncio.run(WebSocketApp().authenticate(scope)) is None


def test_topic_socket_builds_the_default_connection() -> None:
    handshake = Handshake("key", object(), None)
    socket = WebSocketApp().topic_socket(asgi._Port(_noop_send), handshake, _scope([]))
    assert isinstance(socket, TopicSocket)
    assert socket.handshake is handshake


async def _noop_send(message: object) -> None:
    del message


def test_port_sends_text_and_closes_once() -> None:
    sent: list[object] = []

    async def send(message: object) -> None:
        sent.append(message)

    async def scenario() -> None:
        port = asgi._Port(send)
        await port.send("hi")
        await port.close(4401)
        await port.close(4401)

    asyncio.run(scenario())
    assert sent == [
        {"type": "websocket.send", "bytes": None, "text": "hi"},
        {"type": "websocket.close", "code": 4401, "reason": None},
    ]


def test_port_close_ignores_a_peer_that_left() -> None:
    async def send(message: object) -> None:
        raise OSError

    asyncio.run(asgi._Port(send).close(1000))


def test_inbound_yields_text_and_bytes_until_disconnect() -> None:
    events = iter(
        [
            {"type": "websocket.receive", "text": "a", "bytes": None},
            {"type": "websocket.receive", "bytes": b"b"},
            {"type": "websocket.receive", "text": None, "bytes": None},
            {"type": "websocket.disconnect", "code": 1000},
        ]
    )

    async def receive() -> object:
        return next(events)

    async def scenario() -> list[str | bytes]:
        return [item async for item in asgi._Inbound(receive)]

    assert asyncio.run(scenario()) == ["a", b"b", b""]


def _communicator(
    app: object, headers: list[tuple[bytes, bytes]], path: str = "/_next/ws/"
) -> WebSocketCommunicator:
    return WebSocketCommunicator(app, path, headers=[_HOST, *headers])


@pytest.mark.django_db(transaction=True)
def test_app_serves_hello_to_a_same_origin_session(ticket: SocketTicket) -> None:
    async def scenario() -> None:
        communicator = _communicator(WebSocketApp(), [_ORIGIN, ticket.cookie])
        connected, _ = await communicator.connect()
        assert connected
        await communicator.send_json_to(ticket.subscribe_frame())
        hello = await communicator.receive_json_from()
        assert hello["type"] == "hello"
        assert list(hello["topics"]) == ["room:42"]
        await communicator.disconnect()

    asyncio.run(scenario())


def test_app_refuses_a_foreign_origin_before_accepting() -> None:
    async def scenario() -> None:
        communicator = _communicator(WebSocketApp(), [(b"origin", b"http://evil")])
        assert await communicator.connect() == (False, 1000)
        await communicator.wait()

    asyncio.run(scenario())


def test_app_without_a_session_accepts_then_closes_4401() -> None:
    async def scenario() -> None:
        communicator = _communicator(WebSocketApp(), [_ORIGIN])
        connected, _ = await communicator.connect()
        assert connected
        assert await communicator.receive_json_from() == {
            "type": "error",
            "code": "auth",
        }
        assert await communicator.receive_close() == Close.SESSION
        await communicator.wait()

    asyncio.run(scenario())


def test_app_joins_a_repeated_origin_and_refuses_it() -> None:
    async def scenario() -> None:
        communicator = _communicator(WebSocketApp(), [_ORIGIN, _ORIGIN])
        assert await communicator.connect() == (False, 1000)
        await communicator.wait()

    asyncio.run(scenario())


def test_app_logs_a_failed_authentication_and_closes_1011(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Failing(WebSocketApp):
        async def authenticate(self, scope: Scope) -> Handshake | None:
            raise RuntimeError

    async def scenario() -> None:
        communicator = _communicator(Failing(), [_ORIGIN])
        connected, _ = await communicator.connect()
        assert connected
        assert await communicator.receive_close() == Close.INTERNAL
        await communicator.wait()

    with caplog.at_level(logging.ERROR, logger="next.realtime"):
        asyncio.run(scenario())
    assert [record.getMessage() for record in caplog.records] == [
        "Realtime WebSocket authentication failed"
    ]


def test_app_lets_a_cancelled_authentication_propagate() -> None:
    class Cancelled(WebSocketApp):
        async def authenticate(self, scope: Scope) -> Handshake | None:
            raise asyncio.CancelledError

    events = iter([{"type": "websocket.connect"}])

    async def receive() -> object:
        return next(events)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(Cancelled()(_scope([_HOST, _ORIGIN]), receive, _noop_send))


def test_app_without_a_session_tolerates_a_peer_that_left() -> None:
    sent: list[str] = []
    events = iter([{"type": "websocket.connect"}])

    async def receive() -> object:
        return next(events)

    async def send(message: dict[str, object]) -> None:
        sent.append(str(message["type"]))
        if message["type"] == "websocket.send":
            raise OSError

    scope = _scope([_HOST, _ORIGIN])
    asyncio.run(WebSocketApp()(scope, receive, send))
    assert sent == ["websocket.accept", "websocket.send", "websocket.close"]


def test_app_leaves_quietly_when_the_client_never_connects() -> None:
    async def receive() -> object:
        return {"type": "websocket.disconnect", "code": 1006}

    async def send(message: object) -> None:
        raise AssertionError(message)

    asyncio.run(WebSocketApp()(_scope([_HOST, _ORIGIN]), receive, send))


def test_app_rejects_a_non_websocket_scope() -> None:
    with pytest.raises(ValueError, match="not http"):
        asyncio.run(WebSocketApp()({"type": "http"}, _noop_receive, _noop_send))


async def _noop_receive() -> object:
    return {"type": "websocket.disconnect", "code": 1000}


def test_application_serves_http_through_the_project_app() -> None:
    sent: list[object] = []

    async def send(message: object) -> None:
        sent.append(message)

    router = application(_http_ok)
    asyncio.run(router({"type": "http"}, _noop_receive, send))
    assert sent[0]["status"] == 204


@pytest.mark.parametrize(
    ("path", "root_path", "accepted"),
    [
        ("/_next/ws/", "", True),
        ("/app/_next/ws/", "/app", True),
        ("/app", "/app", False),
        ("/other/_next/ws/", "/app", False),
        ("/elsewhere/", "", False),
    ],
)
def test_application_routes_only_the_configured_path(
    path: str, root_path: str, *, accepted: bool
) -> None:
    calls: list[str] = []

    class Recording(WebSocketApp):
        async def __call__(
            self, scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
        ) -> None:
            calls.append(scope["path"])

    async def scenario() -> None:
        communicator = WebSocketCommunicator(
            application(_http_ok, websocket=Recording()), path
        )
        communicator.scope["root_path"] = root_path
        if accepted:
            await communicator.wait()
        else:
            assert await communicator.connect() == (False, 1000)
            await communicator.wait()

    asyncio.run(scenario())
    assert calls == ([path] if accepted else [])


@pytest.mark.parametrize("route", ["/live", "/live/", "/"])
def test_application_routes_the_configured_websocket_path(route: str) -> None:
    calls: list[str] = []

    class Recording(WebSocketApp):
        async def __call__(
            self, scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
        ) -> None:
            calls.append(scope["path"])

    async def scenario() -> None:
        router = application(_http_ok, websocket=Recording())
        await WebSocketCommunicator(router, route).wait()
        refused = WebSocketCommunicator(router, "/_next/ws/")
        assert await refused.connect() == (False, 1000)
        await refused.wait()

    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"WS_PATH": route}}):
        asyncio.run(scenario())
    assert calls == [route]


@pytest.mark.django_db()
@override_settings(FORCE_SCRIPT_NAME="/app")
def test_a_ticket_under_a_script_prefix_names_the_path_the_router_serves() -> None:
    calls: list[str] = []

    class Recording(WebSocketApp):
        async def __call__(
            self, scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
        ) -> None:
            calls.append(scope["path"])

    set_script_prefix(get_script_name(RequestFactory().get("/").environ))
    try:
        url = issue_socket_ticket(
            ("room:42",), authorizer_ref=ROOM_AUTHORIZER_REF
        ).grant.websocket_path
    finally:
        clear_script_prefix()

    async def scenario() -> None:
        router = application(_http_ok, websocket=Recording())
        await WebSocketCommunicator(router, url).wait()

    asyncio.run(scenario())
    assert url == "/app/_next/ws/"
    assert calls == [url]


def test_application_refuse_ignores_a_client_that_left_first() -> None:
    async def send(message: object) -> None:
        raise AssertionError(message)

    scope = _scope([])
    scope["path"] = "/elsewhere/"
    asyncio.run(application(_http_ok)(scope, _noop_receive, send))


@pytest.mark.parametrize(
    "http_app",
    [ASGIHandler, lambda: ASGIStaticFilesHandler(ASGIHandler())],
    ids=["handler", "static"],
)
def test_application_answers_lifespan_for_django_and_closes_the_broker(
    http_app: Callable[[], ASGIHandler], monkeypatch: pytest.MonkeyPatch
) -> None:
    closed: list[str] = []

    async def aclose() -> None:
        closed.append("broker")

    monkeypatch.setattr(asgi.broker_manager, "aclose", aclose)
    events = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])
    sent: list[str] = []

    async def receive() -> object:
        return next(events)

    async def send(message: dict[str, object]) -> None:
        sent.append(f"{message['type']}:{len(closed)}")

    asyncio.run(application(http_app())({"type": "lifespan"}, receive, send))
    assert sent == ["lifespan.startup.complete:0", "lifespan.shutdown.complete:1"]


def test_application_passes_lifespan_through_and_closes_the_broker_last(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order: list[str] = []

    async def aclose() -> None:
        order.append("broker")

    async def lifespan_app(
        scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        await send({"type": "lifespan.startup.complete"})
        order.append("app")
        await send({"type": "lifespan.shutdown.complete"})

    async def send(message: dict[str, object]) -> None:
        order.append(str(message["type"]))

    monkeypatch.setattr(asgi.broker_manager, "aclose", aclose)
    asyncio.run(application(lifespan_app)({"type": "lifespan"}, _noop_receive, send))
    assert order == [
        "lifespan.startup.complete",
        "app",
        "broker",
        "lifespan.shutdown.complete",
    ]


def test_application_builds_a_default_websocket_app() -> None:
    router = application(_http_ok)
    assert isinstance(router.websocket, WebSocketApp)
    assert router.http_app is _http_ok
    assert transports.active_transports() == ("ws", "sse")


def _lifespan_run(
    http_app: object, monkeypatch: pytest.MonkeyPatch, *, fail_close: bool = False
) -> list[str]:
    log: list[str] = []

    async def aclose() -> None:
        log.append("broker")
        if fail_close:
            raise ConnectionError

    monkeypatch.setattr(asgi.broker_manager, "aclose", aclose)
    events = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])

    async def receive() -> object:
        return next(events)

    async def send(message: dict[str, object]) -> None:
        log.append(str(message["type"]))

    asyncio.run(application(http_app)({"type": "lifespan"}, receive, send))
    return log


_ANSWERED = ["lifespan.startup.complete", "broker", "lifespan.shutdown.complete"]


def test_application_answers_lifespan_for_middleware_around_django(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    django_app = ASGIHandler()

    async def middleware(
        scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        await django_app(scope, receive, send)

    assert _lifespan_run(middleware, monkeypatch) == _ANSWERED


def test_application_replays_the_startup_a_failing_app_consumed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def consuming(
        scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        await receive()
        raise ValueError

    assert _lifespan_run(consuming, monkeypatch) == _ANSWERED


def test_application_answers_lifespan_for_an_app_that_returns_silently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def silent(
        scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        del scope, receive, send

    assert _lifespan_run(silent, monkeypatch) == _ANSWERED


def test_application_reraises_a_lifespan_app_that_failed_after_answering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failing(
        scope: Scope, receive: ASGIReceiveCallable, send: ASGISendCallable
    ) -> None:
        await send({"type": "lifespan.startup.complete"})
        raise RuntimeError

    with pytest.raises(RuntimeError):
        _lifespan_run(failing, monkeypatch)


def test_application_reports_a_broker_that_fails_to_close(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.ERROR, logger="next.realtime"):
        log = _lifespan_run(ASGIHandler(), monkeypatch, fail_close=True)
    assert log == ["lifespan.startup.complete", "broker", "lifespan.shutdown.failed"]
    assert (
        caplog.records[0].getMessage() == "Realtime broker failed to close on shutdown"
    )
