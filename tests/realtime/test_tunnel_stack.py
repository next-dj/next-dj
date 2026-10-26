import asyncio
import json
import logging
import pickle
import threading
from collections.abc import Callable, Iterator
from importlib import import_module
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

import pytest
from asgiref.sync import async_to_sync
from asgiref.typing import ASGI3Application, WebSocketScope
from django import forms as django_forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import (
    HASH_SESSION_KEY,
    SESSION_KEY,
    get_user_model,
    login,
    logout,
)
from django.contrib.auth.models import Permission
from django.contrib.sessions.backends.db import SessionStore
from django.core.exceptions import PermissionDenied
from django.core.handlers.asgi import ASGIHandler
from django.db import connections
from django.http import Http404, HttpRequest, HttpResponse
from django.middleware.csrf import get_token
from django.test import AsyncClient, RequestFactory, override_settings
from django.urls import path

from next.forms import Form, SessionRotationError, action
from next.forms.manager import form_action_manager
from next.realtime import WebSocketApp, application
from next.realtime.tunnel import DjangoTunnel, Tunnel
from next.testing.realtime import (
    SocketTicket,
    WebSocketCommunicator,
    issue_socket_ticket,
    override_websocket_mount,
)
from tests.support.realtime import (
    ROOM_AUTHORIZER_REF,
    issue_session_ticket,
    signed_in_session,
)


_ORIGIN = "http://testserver"


@action("tunnel_echo", transport="any")
def tunnel_echo(request: HttpRequest) -> HttpResponse:
    return HttpResponse(f"echo {request.POST.get('n')}")


@action("tunnel_guarded", transport="any", login_required=True)
def tunnel_guarded() -> HttpResponse:
    return HttpResponse("secret")


_RUNS: dict[str, int] = {}


@action("tunnel_plain")
def tunnel_plain() -> HttpResponse:
    _RUNS["tunnel_plain"] = _RUNS.get("tunnel_plain", 0) + 1
    return HttpResponse("plain")


@action("tunnel_denied", transport="any")
def tunnel_denied() -> HttpResponse:
    _RUNS["tunnel_denied"] = _RUNS.get("tunnel_denied", 0) + 1
    raise PermissionDenied


@action("tunnel_missing", transport="any")
def tunnel_missing() -> HttpResponse:
    _RUNS["tunnel_missing"] = _RUNS.get("tunnel_missing", 0) + 1
    raise Http404


@action("tunnel_perm", transport="any", permission_required="auth.add_user")
def tunnel_perm() -> HttpResponse:
    return HttpResponse("permitted")


@action("tunnel_whoami", transport="any")
def tunnel_whoami(request: HttpRequest) -> HttpResponse:
    return HttpResponse(
        f"{request.user.is_authenticated} {request.session.get('seen')}"
    )


@action("tunnel_write", transport="any")
def tunnel_write(request: HttpRequest) -> HttpResponse:
    request.session["seen"] = "tunnel"
    return HttpResponse("wrote")


@action("tunnel_pickle", transport="any")
def tunnel_pickle(request: HttpRequest) -> HttpResponse:
    return HttpResponse(str(bool(pickle.dumps(request.session))))


@action("tunnel_inbox", transport="any")
def tunnel_inbox(request: HttpRequest) -> HttpResponse:
    return HttpResponse(",".join(str(note) for note in messages.get_messages(request)))


@action("tunnel_token", transport="any")
def tunnel_token(request: HttpRequest) -> HttpResponse:
    get_token(request)
    return HttpResponse("token")


@action("tunnel_cookie", transport="any")
def tunnel_cookie() -> HttpResponse:
    response = HttpResponse("cookie")
    response.set_cookie("theme", "dark")
    return response


@action("tunnel_login", transport="any")
def tunnel_login(request: HttpRequest) -> HttpResponse:
    user = get_user_model().objects.create_user("tunnel-user")
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    return HttpResponse("in")


@action("tunnel_logout", transport="any")
def tunnel_logout(request: HttpRequest) -> HttpResponse:
    logout(request)
    return HttpResponse("out")


@action("tunnel_swallow", transport="any")
def tunnel_swallow(request: HttpRequest) -> HttpResponse:
    try:
        request.session.cycle_key()
    except SessionRotationError:
        return HttpResponse("kept")
    return HttpResponse("rotated")


@action("tunnel_flash", transport="any")
def tunnel_flash(request: HttpRequest) -> HttpResponse:
    messages.success(request, "saved")
    return HttpResponse("flashed")


class TunnelSignupForm(Form):
    """A form the tunnel and plain HTTP both post."""

    email = django_forms.EmailField()

    class Meta:
        """Admits the tunnel and redirects after a valid post."""

        transport = "any"
        success_url = "/done/"


@pytest.fixture()
def app() -> Iterator[ASGI3Application]:
    with override_websocket_mount():
        yield application(ASGIHandler())


@pytest.fixture()
def ticket(transactional_db: None) -> SocketTicket:
    session = SessionStore()
    session.create()
    return issue_socket_ticket(
        ("room:42",), authorizer_ref=ROOM_AUTHORIZER_REF, session=session
    )


@pytest.fixture()
def csrf() -> tuple[str, str]:
    request = RequestFactory().get("/")
    token = get_token(request)
    return request.META["CSRF_COOKIE"], token


@pytest.fixture()
def signed_in(transactional_db: None) -> tuple[SocketTicket, str]:
    user, session = signed_in_session("browser-user")
    return issue_session_ticket(session=session), str(user.pk)


def _url(name: str) -> str:
    return form_action_manager.get_action_url(name)


def _cookie(ticket: SocketTicket, secret: str | None) -> str:
    _, value = ticket.cookie
    cookie = value.decode()
    return cookie if secret is None else f"{cookie}; csrftoken={secret}"


async def _tunnel(
    app: ASGI3Application,
    ticket: SocketTicket,
    frame: dict[str, object],
    *,
    secret: str | None,
) -> dict[str, object]:
    headers = [
        (b"origin", _ORIGIN.encode()),
        (b"host", b"testserver"),
        (b"cookie", _cookie(ticket, secret).encode()),
    ]
    communicator = WebSocketCommunicator(app, "/_next/ws/", headers=headers, timeout=10)
    connected, _ = await communicator.connect()
    assert connected
    assert (await communicator.subscribe(ticket))["type"] == "hello"
    await communicator.send_json_to({"type": "http", "id": "1", **frame})
    answer = await communicator.receive_json_from()
    await communicator.disconnect()
    return answer


def _frame(
    path: str, data: dict[str, str], headers: dict[str, str] | None = None
) -> dict[str, object]:
    return {
        "method": "POST",
        "path": path,
        "headers": {
            "content-type": "application/x-www-form-urlencoded;charset=UTF-8",
            **(headers or {}),
        },
        "body": urlencode(data),
    }


def _http(
    ticket: SocketTicket,
    path: str,
    data: dict[str, str],
    *,
    secret: str | None,
    headers: dict[str, str] | None = None,
) -> HttpResponse:
    client = AsyncClient(enforce_csrf_checks=True)
    client.cookies.load(_cookie(ticket, secret))
    return async_to_sync(client.post)(
        path, data, headers={"Origin": _ORIGIN, **(headers or {})}
    )


def _comparable(response: HttpResponse) -> dict[str, object]:
    kept = {
        name.lower(): value
        for name, value in response.headers.items()
        if name.lower() in {"content-type", "location"}
        or name.lower().startswith("x-next-")
    }
    return {
        "type": "http",
        "id": "1",
        "status": response.status_code,
        "headers": kept,
        "body": response.content.decode(),
    }


@pytest.mark.parametrize("email", ["not-an-email", "user@example.com"])
def test_form_answers_the_same_envelope_over_http_and_the_tunnel(
    app: ASGI3Application, ticket: SocketTicket, csrf: tuple[str, str], email: str
) -> None:
    secret, token = csrf
    path = _url("tunnel_signup_form")
    headers = {"X-Next-Request": "1", "X-CSRFToken": token}
    plain = _http(ticket, path, {"email": email}, secret=secret, headers=headers)
    tunneled = async_to_sync(_tunnel)(
        app, ticket, _frame(path, {"email": email}, headers), secret=secret
    )
    assert plain["Content-Type"] == "application/vnd.next.patches+json"
    assert tunneled == _comparable(plain)


@pytest.mark.parametrize("name", ["tunnel_echo", "tunnel_guarded"])
def test_post_that_fails_csrf_is_stale_and_retried_over_http(
    app: ASGI3Application, ticket: SocketTicket, csrf: tuple[str, str], name: str
) -> None:
    secret, _ = csrf
    plain = _http(ticket, _url(name), {"n": "1"}, secret=secret)
    tunneled = async_to_sync(_tunnel)(
        app, ticket, _frame(_url(name), {"n": "1"}), secret=secret
    )
    assert plain.status_code == 403
    assert tunneled == {
        "type": "http",
        "id": "1",
        "status": 0,
        "error": "stale",
        "retry": True,
    }


def test_anonymous_guarded_action_answers_as_over_http(
    app: ASGI3Application, ticket: SocketTicket, csrf: tuple[str, str]
) -> None:
    secret, token = csrf
    data = {"csrfmiddlewaretoken": token}
    plain = _http(ticket, _url("tunnel_guarded"), data, secret=secret)
    tunneled = async_to_sync(_tunnel)(
        app, ticket, _frame(_url("tunnel_guarded"), data), secret=secret
    )
    assert plain.status_code in {302, 403}
    assert tunneled == _comparable(plain)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("tunnel_echo", {"status": 200, "body": "echo 1"}),
        ("tunnel_token", {"status": 200, "body": "token"}),
        ("tunnel_cookie", {"status": 0, "error": "http_only", "retry": False}),
        ("tunnel_login", {"status": 0, "error": "http_only", "retry": False}),
        ("tunnel_flash", {"status": 0, "error": "http_only", "retry": False}),
        ("tunnel_plain", {"status": 0, "error": "transport", "retry": True}),
    ],
)
def test_tunnel_answers_by_what_the_reply_needs(
    app: ASGI3Application,
    ticket: SocketTicket,
    csrf: tuple[str, str],
    name: str,
    expected: dict[str, object],
) -> None:
    secret, token = csrf
    data = {"csrfmiddlewaretoken": token, "n": "1"}
    answer = async_to_sync(_tunnel)(
        app, ticket, _frame(_url(name), data), secret=secret
    )
    assert {key: answer[key] for key in expected} == expected


@override_settings(
    MESSAGE_STORAGE="django.contrib.messages.storage.session.SessionStorage"
)
def test_session_messages_pass_the_tunnel(
    app: ASGI3Application, ticket: SocketTicket, csrf: tuple[str, str]
) -> None:
    secret, token = csrf
    data = {"csrfmiddlewaretoken": token}
    answer = async_to_sync(_tunnel)(
        app, ticket, _frame(_url("tunnel_flash"), data), secret=secret
    )
    assert (answer["status"], answer["body"]) == (200, "flashed")


def test_cookie_guard_logs_the_dropped_names(
    app: ASGI3Application,
    ticket: SocketTicket,
    csrf: tuple[str, str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret, token = csrf
    data = {"csrfmiddlewaretoken": token}
    with caplog.at_level(logging.WARNING, logger="next.realtime.tunnel"):
        async_to_sync(_tunnel)(
            app, ticket, _frame(_url("tunnel_cookie"), data), secret=secret
        )
    assert "['theme']" in caplog.records[-1].getMessage()


@pytest.mark.parametrize(
    ("frame", "secret", "error"),
    [
        (_frame("/", {}), "s", "transport"),
        (
            {
                "method": "POST",
                "path": "/_next/form/x/",
                "headers": {"content-type": "multipart/form-data; boundary=b"},
                "body": "--b--",
            },
            "s",
            "body",
        ),
        ({"method": "GET", "path": "/_next/form/x/"}, "s", "method"),
        (_frame("/_next/form/x/", {}), None, "stale"),
    ],
)
def test_request_the_tunnel_cannot_run_is_refused_with_retry(
    app: ASGI3Application,
    ticket: SocketTicket,
    frame: dict[str, object],
    secret: str | None,
    error: str,
) -> None:
    answer = async_to_sync(_tunnel)(app, ticket, frame, secret=secret)
    assert answer == {
        "type": "http",
        "id": "1",
        "status": 0,
        "error": error,
        "retry": True,
    }


def test_socket_without_an_app_is_disabled(
    ticket: SocketTicket, csrf: tuple[str, str]
) -> None:
    secret, token = csrf
    with override_websocket_mount():
        bare = WebSocketApp()
        answer = async_to_sync(_tunnel)(
            bare,
            ticket,
            _frame(_url("tunnel_echo"), {"csrfmiddlewaretoken": token}),
            secret=secret,
        )
    assert (answer["error"], answer["retry"]) == ("disabled", True)


def tenant_view(request: HttpRequest, uid: str) -> HttpResponse:
    return HttpResponse(f"tenant {uid}")


urlpatterns = [path("_next/form/<str:uid>/", tenant_view)]


class TenantUrlconf:
    """Route every request through this module, as a multi-tenant middleware does."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        """Wrap the next handler."""
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        request.urlconf = __name__
        return self.get_response(request)


def test_request_urlconf_cannot_reach_a_view_outside_the_dispatch(
    ticket: SocketTicket, csrf: tuple[str, str]
) -> None:
    secret, token = csrf
    middleware = [*settings.MIDDLEWARE, f"{__name__}.TenantUrlconf"]
    with override_settings(MIDDLEWARE=middleware), override_websocket_mount():
        app = application(ASGIHandler())
        answer = async_to_sync(_tunnel)(
            app,
            ticket,
            _frame(_url("tunnel_echo"), {"csrfmiddlewaretoken": token}),
            secret=secret,
        )
    assert answer == {
        "type": "http",
        "id": "1",
        "status": 0,
        "error": "transport",
        "retry": True,
    }


def test_rotated_csrf_token_is_stale(
    app: ASGI3Application, ticket: SocketTicket, csrf: tuple[str, str]
) -> None:
    secret, _ = csrf
    rotated = get_token(RequestFactory().get("/"))
    answer = async_to_sync(_tunnel)(
        app,
        ticket,
        _frame(_url("tunnel_echo"), {}, {"X-CSRFToken": rotated}),
        secret=secret,
    )
    assert (answer["error"], answer["retry"]) == ("stale", True)


_SLOW: dict[str, threading.Event] = {}


@action("tunnel_slow", transport="any")
def tunnel_slow() -> HttpResponse:
    _SLOW["started"].set()
    _SLOW["released"] = _SLOW["release"].wait(5)
    _SLOW["finished"].set()
    return HttpResponse("slow")


class _QuickTunnel(DjangoTunnel):
    grace = 0.05


class _QuickSocket(WebSocketApp):
    def __init__(self) -> None:
        with override_websocket_mount():
            super().__init__()

    def tunnel(self, scope: WebSocketScope) -> Tunnel | None:
        limits = self.limits.tunnel
        assert limits is not None
        return _QuickTunnel(ASGIHandler(), scope, limits)


async def _connect(
    app: ASGI3Application, ticket: SocketTicket, secret: str
) -> tuple[WebSocketCommunicator, dict[str, object]]:
    headers = [
        (b"origin", _ORIGIN.encode()),
        (b"host", b"testserver"),
        (b"cookie", _cookie(ticket, secret).encode()),
    ]
    communicator = WebSocketCommunicator(app, "/_next/ws/", headers=headers)
    connected, _ = await communicator.connect()
    assert connected
    await communicator.send_json_to(ticket.subscribe_frame())
    hello = json.loads((await communicator.receive_output(5))["text"])
    assert hello["type"] == "hello"
    return communicator, hello


def test_sync_view_past_the_timeout_never_blocks_the_loop(
    ticket: SocketTicket, csrf: tuple[str, str]
) -> None:
    secret, token = csrf
    _SLOW.update(
        started=threading.Event(), release=threading.Event(), finished=threading.Event()
    )
    frame = _frame(_url("tunnel_slow"), {"csrfmiddlewaretoken": token})

    async def scenario() -> list[dict[str, object]]:
        communicator, _ = await _connect(_QuickSocket(), ticket, secret)
        await communicator.send_json_to({"type": "http", "id": "1", **frame})
        first = json.loads((await communicator.receive_output(5))["text"])
        await communicator.send_json_to({"type": "http", "id": "2", **frame})
        second = json.loads((await communicator.receive_output(5))["text"])
        await communicator.disconnect()
        _SLOW["release"].set()
        assert await asyncio.to_thread(_SLOW["finished"].wait, 5)
        return [first, second]

    tunnel = {"TIMEOUT": 0.2, "CONCURRENCY": 1}
    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"TUNNEL": tunnel}}):
        answers = asyncio.run(scenario())
    assert [answer["error"] for answer in answers] == ["timeout", "limit"]
    assert _SLOW["released"] is True


def test_closing_the_socket_leaves_a_running_view_alone(
    ticket: SocketTicket, csrf: tuple[str, str]
) -> None:
    secret, token = csrf
    _SLOW.update(
        started=threading.Event(), release=threading.Event(), finished=threading.Event()
    )
    frame = _frame(_url("tunnel_slow"), {"csrfmiddlewaretoken": token})

    async def scenario() -> bool:
        communicator, _ = await _connect(_QuickSocket(), ticket, secret)
        await communicator.send_json_to({"type": "http", "id": "1", **frame})
        assert await asyncio.to_thread(_SLOW["started"].wait, 5)
        await communicator.disconnect()
        still_running = not _SLOW["finished"].is_set()
        _SLOW["release"].set()
        assert await asyncio.to_thread(_SLOW["finished"].wait, 5)
        return still_running

    assert asyncio.run(scenario())
    assert _SLOW["released"] is True


def test_hello_tells_the_client_the_tunnel_contract(
    app: ASGI3Application, ticket: SocketTicket, csrf: tuple[str, str]
) -> None:
    secret, _ = csrf

    async def scenario() -> dict[str, object]:
        communicator, hello = await _connect(app, ticket, secret)
        await communicator.disconnect()
        return hello

    hello = asyncio.run(scenario())
    assert hello["upstream"] == {"frame": 16384}
    assert hello["tunnel"] == {
        "timeout": 30.0,
        "frame": 65536,
        "headers": ["accept", "content-type"],
        "prefixes": ["x-next-"],
        "csrf": "x-csrftoken",
    }


@override_settings(NEXT_FRAMEWORK={"REALTIME": {"TUNNEL": False}})
def test_disabled_tunnel_leaves_its_hello_key_out(
    ticket: SocketTicket, csrf: tuple[str, str]
) -> None:
    secret, _ = csrf

    async def scenario() -> dict[str, object]:
        with override_websocket_mount():
            app = application(ASGIHandler())
        communicator, hello = await _connect(app, ticket, secret)
        await communicator.disconnect()
        return hello

    hello = asyncio.run(scenario())
    assert "tunnel" not in hello
    assert hello["upstream"] == {"frame": 16384}


_ROTATED = {
    "type": "http",
    "id": "1",
    "status": 0,
    "error": "http_only",
    "retry": False,
}


@pytest.mark.parametrize("name", ["tunnel_login", "tunnel_logout"])
def test_tunneled_rotation_keeps_the_signed_in_browser_session(
    app: ASGI3Application,
    signed_in: tuple[SocketTicket, str],
    csrf: tuple[str, str],
    name: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ticket, user_id = signed_in
    secret, token = csrf
    key = ticket.session.session_key
    data = {"csrfmiddlewaretoken": token}
    with caplog.at_level(logging.WARNING, logger="next.forms.dispatch"):
        answer = async_to_sync(_tunnel)(
            app, ticket, _frame(_url(name), data), secret=secret
        )
    stored = SessionStore(key).load()
    assert answer == _ROTATED
    assert stored[SESSION_KEY] == user_id
    assert (
        stored[HASH_SESSION_KEY]
        == get_user_model().objects.get(pk=user_id).get_session_auth_hash()
    )
    assert f"action {name!r} rotated the session" in caplog.text


def test_tunneled_login_keeps_an_anonymous_browser_session(
    app: ASGI3Application, ticket: SocketTicket, csrf: tuple[str, str]
) -> None:
    secret, token = csrf
    key = ticket.session.session_key
    data = {"csrfmiddlewaretoken": token}
    answer = async_to_sync(_tunnel)(
        app, ticket, _frame(_url("tunnel_login"), data), secret=secret
    )
    assert answer == _ROTATED
    assert SessionStore().exists(key)
    assert SESSION_KEY not in SessionStore(key).load()


@pytest.mark.parametrize("atomic", [True, False])
def test_atomic_requests_roll_back_what_ran_before_the_rotation(
    app: ASGI3Application, ticket: SocketTicket, csrf: tuple[str, str], *, atomic: bool
) -> None:
    secret, token = csrf
    data = {"csrfmiddlewaretoken": token}
    database = connections.settings["default"]
    with patch.dict(database, {"ATOMIC_REQUESTS": atomic}):
        answer = async_to_sync(_tunnel)(
            app, ticket, _frame(_url("tunnel_login"), data), secret=secret
        )
    created = get_user_model().objects.filter(username="tunnel-user").exists()
    assert answer == _ROTATED
    assert created is not atomic


def test_caught_rotation_still_refuses_the_reply(
    app: ASGI3Application, ticket: SocketTicket, csrf: tuple[str, str]
) -> None:
    secret, token = csrf
    data = {"csrfmiddlewaretoken": token}
    answer = async_to_sync(_tunnel)(
        app, ticket, _frame(_url("tunnel_swallow"), data), secret=secret
    )
    assert answer == _ROTATED
    assert SessionStore().exists(ticket.session.session_key)


@pytest.mark.parametrize("name", ["tunnel_denied", "tunnel_missing"])
def test_action_that_raises_answers_as_over_http_and_runs_once(
    app: ASGI3Application, ticket: SocketTicket, csrf: tuple[str, str], name: str
) -> None:
    secret, token = csrf
    data = {"csrfmiddlewaretoken": token}
    _RUNS[name] = 0
    tunneled = async_to_sync(_tunnel)(
        app, ticket, _frame(_url(name), data), secret=secret
    )
    assert _RUNS[name] == 1
    plain = _http(ticket, _url(name), data, secret=secret)
    assert plain.status_code in {403, 404}
    assert tunneled == _comparable(plain)


def test_http_only_action_never_runs_over_the_tunnel(
    app: ASGI3Application, ticket: SocketTicket, csrf: tuple[str, str]
) -> None:
    secret, token = csrf
    _RUNS["tunnel_plain"] = 0
    answer = async_to_sync(_tunnel)(
        app,
        ticket,
        _frame(_url("tunnel_plain"), {"csrfmiddlewaretoken": token}),
        secret=secret,
    )
    assert (answer["error"], answer["retry"]) == ("transport", True)
    assert _RUNS["tunnel_plain"] == 0


_ENGINES = [
    "django.contrib.sessions.backends.db",
    "django.contrib.sessions.backends.file",
    "django.contrib.sessions.backends.cached_db",
    "django.contrib.sessions.backends.cache",
    "django.contrib.sessions.backends.signed_cookies",
]
_SIGNED = "django.contrib.sessions.backends.signed_cookies"


@pytest.fixture(params=_ENGINES, ids=lambda engine: engine.rpartition(".")[2])
def engine(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[str]:
    with override_settings(SESSION_ENGINE=request.param, SESSION_FILE_PATH=tmp_path):
        yield request.param


@pytest.fixture()
def member(transactional_db: None, engine: str) -> tuple[SocketTicket, str]:
    assert engine in _ENGINES
    user, session = signed_in_session("engine-user")
    return issue_session_ticket(session=session), str(user.pk)


def _both(
    app: ASGI3Application, ticket: SocketTicket, csrf: tuple[str, str], name: str
) -> tuple[dict[str, object], HttpResponse]:
    secret, token = csrf
    data = {"csrfmiddlewaretoken": token}
    tunneled = async_to_sync(_tunnel)(
        app, ticket, _frame(_url(name), data), secret=secret
    )
    return tunneled, _http(ticket, _url(name), data, secret=secret)


@pytest.fixture()
def engine_app(engine: str) -> Iterator[ASGI3Application]:
    """Build the app under the engine, since the session middleware reads it once."""
    assert engine in _ENGINES
    with override_websocket_mount():
        yield application(ASGIHandler())


@pytest.mark.parametrize("name", ["tunnel_guarded", "tunnel_perm"])
def test_engine_action_answers_as_over_http(
    engine_app: ASGI3Application,
    member: tuple[SocketTicket, str],
    csrf: tuple[str, str],
    name: str,
) -> None:
    tunneled, plain = _both(engine_app, member[0], csrf, name)
    assert plain.status_code in {200, 403}
    assert tunneled == _comparable(plain)


@pytest.mark.parametrize(
    "engine",
    [
        "django.contrib.sessions.backends.db",
        "django.contrib.sessions.backends.file",
        _SIGNED,
    ],
    ids=["db", "file", "signed_cookies"],
    indirect=True,
)
def test_engine_session_pickles_as_over_http(
    engine_app: ASGI3Application,
    member: tuple[SocketTicket, str],
    csrf: tuple[str, str],
) -> None:
    tunneled, plain = _both(engine_app, member[0], csrf, "tunnel_pickle")
    assert (tunneled["status"], tunneled["body"]) == (200, "True")
    assert tunneled == _comparable(plain)


def test_engine_granted_permission_passes_the_tunnel(
    engine_app: ASGI3Application,
    member: tuple[SocketTicket, str],
    csrf: tuple[str, str],
) -> None:
    ticket, user_id = member
    user = get_user_model().objects.get(pk=user_id)
    user.user_permissions.add(Permission.objects.get(codename="add_user"))
    tunneled, plain = _both(engine_app, ticket, csrf, "tunnel_perm")
    assert (tunneled["status"], tunneled["body"]) == (200, "permitted")
    assert tunneled == _comparable(plain)


def test_engine_tunneled_write_reaches_the_next_request(
    engine_app: ASGI3Application,
    member: tuple[SocketTicket, str],
    csrf: tuple[str, str],
    engine: str,
) -> None:
    ticket, user_id = member
    written, _ = _both(engine_app, ticket, csrf, "tunnel_write")
    _, seen = _both(engine_app, ticket, csrf, "tunnel_whoami")
    stored = import_module(engine).SessionStore(ticket.session.session_key).load()
    assert stored[SESSION_KEY] == user_id
    if engine == _SIGNED:
        assert written["error"] == "http_only"
        assert seen.content == b"True None"
        return
    assert (written["status"], written["body"]) == (200, "wrote")
    assert seen.content == b"True tunnel"
    assert stored["seen"] == "tunnel"


@override_settings(
    MESSAGE_STORAGE="django.contrib.messages.storage.session.SessionStorage"
)
def test_engine_tunneled_message_reaches_the_next_request(
    engine_app: ASGI3Application,
    member: tuple[SocketTicket, str],
    csrf: tuple[str, str],
    engine: str,
) -> None:
    ticket, _ = member
    secret, token = csrf
    data = {"csrfmiddlewaretoken": token}
    flashed = async_to_sync(_tunnel)(
        engine_app, ticket, _frame(_url("tunnel_flash"), data), secret=secret
    )
    inbox = _http(ticket, _url("tunnel_inbox"), data, secret=secret)
    if engine == _SIGNED:
        assert flashed["error"] == "http_only"
        assert inbox.content == b""
        return
    assert (flashed["status"], flashed["body"]) == (200, "flashed")
    assert inbox.content == b"saved"


@pytest.mark.parametrize("name", ["tunnel_login", "tunnel_logout"])
def test_engine_rotation_keeps_the_session_whole(
    engine_app: ASGI3Application,
    member: tuple[SocketTicket, str],
    csrf: tuple[str, str],
    engine: str,
    name: str,
) -> None:
    ticket, user_id = member
    secret, token = csrf
    answer = async_to_sync(_tunnel)(
        engine_app,
        ticket,
        _frame(_url(name), {"csrfmiddlewaretoken": token}),
        secret=secret,
    )
    _, seen = _both(engine_app, ticket, csrf, "tunnel_whoami")
    stored = import_module(engine).SessionStore(ticket.session.session_key).load()
    assert answer == _ROTATED
    assert stored[SESSION_KEY] == user_id
    assert seen.content == b"True None"
