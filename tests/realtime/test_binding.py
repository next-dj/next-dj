import asyncio
import json
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
import time_machine
from django.conf import settings
from django.contrib.auth import get_user_model, login, logout
from django.contrib.auth.middleware import AuthenticationMiddleware
from django.contrib.auth.models import AbstractBaseUser
from django.contrib.sessions.backends.base import SessionBase
from django.contrib.sessions.backends.file import SessionStore as FileStore
from django.contrib.sessions.middleware import SessionMiddleware
from django.core import signing
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.http import HttpRequest, HttpResponse, parse_cookie
from django.http.response import HttpResponseBase
from django.test import RequestFactory, override_settings
from django.test.utils import CaptureQueriesContext

from next.realtime import (
    Handshake,
    LiveIdentity,
    SessionBinding,
    SignedCookieBinding,
    TicketBinding,
    TicketIntrospectionView,
    TopicStream,
    WebSocketApp,
    socket as socket_module,
)
from next.realtime.backends import MemoryBroker
from next.realtime.binding import binding_class, session_user, ticket_binding
from next.realtime.errors import InvalidTicketError
from next.realtime.frames import Close
from next.realtime.manager import broker_manager
from next.realtime.tickets import issue_ticket, verify_ticket
from next.testing import SseReader, override_next_settings
from next.testing.realtime import WebSocketCommunicator, override_websocket_mount
from tests.support.realtime import ROOM_AUTHORIZER_REF


_SIGNED_BINDING = "next.realtime.SignedCookieBinding"
_DEVICE_BINDING = "tests.realtime.test_binding.DeviceBinding"
_SIGNED_ENGINE = "django.contrib.sessions.backends.signed_cookies"
_ENGINES = (
    *(
        f"django.contrib.sessions.backends.{name}"
        for name in ("db", "cache", "cached_db", "file")
    ),
    _SIGNED_ENGINE,
)
_TOKEN = "gateway-secret"
_TOPIC = "room:1"


class DeviceBinding(TicketBinding):
    def issue(self, request: HttpRequest) -> str:
        device = request.COOKIES.get("device")
        if not device:
            msg = "No device cookie."
            raise InvalidTicketError(msg)
        return f"device:{device}"

    def credential(self, cookies: Mapping[str, str]) -> str | None:
        return cookies.get("device")

    def subject(self, credential: str) -> str | None:
        return f"device:{credential}"

    def live(self, credential: str) -> LiveIdentity | None:
        return LiveIdentity(session_user(SessionBase()), None)


class AbstractBinding(TicketBinding):
    pass


NOT_A_CLASS = "binding"


class Browser:
    def __init__(self) -> None:
        """Start without cookies."""
        self.cookies: dict[str, str] = {}

    def visit(
        self, view: Callable[[HttpRequest], HttpResponseBase], **headers: str
    ) -> HttpResponseBase:
        request = RequestFactory().get("/room/", **headers)
        request.COOKIES.update(self.cookies)
        response = SessionMiddleware(AuthenticationMiddleware(view))(request)
        for name, morsel in response.cookies.items():
            if morsel.value:
                self.cookies[name] = morsel.value
            else:
                self.cookies.pop(name, None)
        return response

    def negotiate(self) -> tuple[int, dict[str, object]]:
        response = self.visit(_stream, HTTP_X_NEXT_TRANSPORT="ws")
        body = json.loads(b"".join(response.streaming_content))
        return response.status_code, body

    def ticket(self) -> str:
        status, body = self.negotiate()
        assert status == 200, body
        return str(body["ticket"])

    def header(self) -> str:
        return "; ".join(f"{name}={value}" for name, value in self.cookies.items())

    def sign_in(self, user: AbstractBaseUser) -> None:
        def view(request: HttpRequest) -> HttpResponse:
            login(request, user, backend="django.contrib.auth.backends.ModelBackend")
            return HttpResponse()

        self.visit(view)

    def sign_out(self) -> None:
        def view(request: HttpRequest) -> HttpResponse:
            logout(request)
            return HttpResponse()

        self.visit(view)


def _stream(request: HttpRequest) -> TopicStream:
    return TopicStream(request, _TOPIC, authorize=ROOM_AUTHORIZER_REF)


async def _asubscribe(cookie: str, ticket: str) -> str | int:
    communicator = WebSocketCommunicator(
        WebSocketApp(),
        "/_next/ws/",
        headers=[
            (b"host", b"testserver"),
            (b"origin", b"http://testserver"),
            (b"cookie", cookie.encode("latin-1")),
        ],
    )
    connected, _ = await communicator.connect()
    assert connected
    await communicator.send_json_to({"type": "sub", "ticket": ticket})
    frame = await communicator.receive_json_from()
    if frame["type"] == "hello":
        await communicator.disconnect()
        return "hello"
    assert frame == {"type": "error", "code": "auth"}
    code = await communicator.receive_close()
    await communicator.wait()
    return code


def _subscribe(browser: Browser, ticket: str) -> str | int:
    return asyncio.run(_asubscribe(browser.header(), ticket))


def _introspect(browser: Browser, ticket: str) -> tuple[int, dict[str, object]]:
    request = RequestFactory().post(
        "/_next/ticket/",
        json.dumps({"ticket": ticket, "cookie": browser.header()}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {_TOKEN}",
    )
    response = TicketIntrospectionView.as_view()(request)
    return response.status_code, json.loads(response.content)


def _sse(browser: Browser) -> SseReader:
    response = browser.visit(
        lambda request: TopicStream(
            request, _TOPIC, authorize=ROOM_AUTHORIZER_REF, heartbeat_seconds=0.001
        )
    )
    assert isinstance(response, TopicStream)
    return SseReader(response)


@pytest.fixture()
def engine(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[str]:
    monkeypatch.setattr(FileStore, "_storage_path", str(tmp_path), raising=False)
    with override_settings(
        SESSION_ENGINE=request.param,
        SESSION_FILE_PATH=str(tmp_path),
        PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
    ):
        yield request.param


@pytest.fixture()
def realtime(transactional_db: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(broker_manager, "_backend", MemoryBroker())
    with (
        override_websocket_mount(mounted=True),
        override_next_settings(
            REALTIME={"TICKET_BINDING": _SIGNED_BINDING, "GATEWAY_TOKEN": _TOKEN}
        ),
    ):
        yield


@pytest.fixture()
def user() -> AbstractBaseUser:
    return get_user_model().objects.create_user(username="ada", password="pw")


every_engine = pytest.mark.parametrize("engine", _ENGINES, indirect=True)


@every_engine
@pytest.mark.usefixtures("realtime")
def test_a_first_visit_without_a_session_subscribes(engine: str) -> None:
    browser = Browser()
    ticket = browser.ticket()
    assert set(browser.cookies) == {"next_visitor"}
    assert _subscribe(browser, ticket) == "hello"
    assert _introspect(browser, ticket) == (200, {"topics": [_TOPIC], "user": None})


@every_engine
@pytest.mark.usefixtures("realtime")
def test_a_visitor_keeps_its_cookie_across_negotiations(engine: str) -> None:
    browser = Browser()
    first = browser.ticket()
    cookie = browser.cookies["next_visitor"]
    second = browser.ticket()
    assert browser.cookies["next_visitor"].split(":")[0] == cookie.split(":")[0]
    assert _subscribe(browser, first) == "hello"
    assert _subscribe(browser, second) == "hello"


@every_engine
@pytest.mark.usefixtures("realtime")
def test_a_signed_in_ticket_binds_the_user(engine: str, user: AbstractBaseUser) -> None:
    browser = Browser()
    browser.sign_in(user)
    ticket = browser.ticket()
    assert "next_visitor" not in browser.cookies
    assert _subscribe(browser, ticket) == "hello"
    assert _introspect(browser, ticket) == (
        200,
        {"topics": [_TOPIC], "user": str(user.pk)},
    )


@every_engine
@pytest.mark.usefixtures("realtime")
def test_a_ticket_survives_a_session_save_after_it_was_issued(
    engine: str, user: AbstractBaseUser
) -> None:
    browser = Browser()
    browser.sign_in(user)
    ticket = browser.ticket()

    def write(request: HttpRequest) -> HttpResponse:
        request.session["seen"] = True
        request.session.cycle_key()
        return HttpResponse()

    browser.visit(write)
    assert _subscribe(browser, ticket) == "hello"


@every_engine
@pytest.mark.usefixtures("realtime")
def test_login_and_logout_move_the_binding(engine: str, user: AbstractBaseUser) -> None:
    browser = Browser()
    anonymous = browser.ticket()
    browser.sign_in(user)
    assert _subscribe(browser, anonymous) == Close.SESSION
    signed_in = browser.ticket()
    assert _subscribe(browser, signed_in) == "hello"
    browser.sign_out()
    assert _subscribe(browser, signed_in) == Close.SESSION
    assert _introspect(browser, signed_in) == (403, {"error": "denied"})
    assert _subscribe(browser, browser.ticket()) == "hello"


@every_engine
@pytest.mark.usefixtures("realtime")
def test_a_password_change_ends_the_signed_in_binding(
    engine: str, user: AbstractBaseUser
) -> None:
    browser = Browser()
    browser.sign_in(user)
    ticket = browser.ticket()
    user.set_password("changed")
    user.save()
    assert _subscribe(browser, ticket) == Close.SESSION


@every_engine
@pytest.mark.usefixtures("realtime")
def test_a_negotiation_after_a_password_change_elsewhere_binds_a_visitor(
    engine: str, user: AbstractBaseUser
) -> None:
    browser = Browser()
    browser.sign_in(user)
    user.set_password("changed")
    user.save()
    ticket = browser.ticket()
    assert "next_visitor" in browser.cookies
    assert _subscribe(browser, ticket) == "hello"
    assert _introspect(browser, ticket) == (200, {"topics": [_TOPIC], "user": None})


@every_engine
@pytest.mark.usefixtures("realtime")
@pytest.mark.parametrize(
    "forged",
    ["forged", "abc:def:ghi", signing.TimestampSigner(salt="other").sign("x")],
    ids=["plain", "unsigned", "foreign-salt"],
)
def test_a_forged_visitor_cookie_is_replaced_and_refused(
    engine: str, forged: str
) -> None:
    browser = Browser()
    ticket = browser.ticket()
    browser.cookies["next_visitor"] = forged
    assert _subscribe(browser, ticket) == Close.SESSION
    assert _introspect(browser, ticket) == (403, {"error": "denied"})
    fresh = browser.ticket()
    assert browser.cookies["next_visitor"] != forged
    assert _subscribe(browser, fresh) == "hello"


@every_engine
@pytest.mark.usefixtures("realtime")
def test_an_expired_visitor_cookie_is_refused_and_renewed(engine: str) -> None:
    browser = Browser()
    start = datetime(2030, 1, 1, tzinfo=UTC)
    with override_settings(SESSION_COOKIE_AGE=30):
        with time_machine.travel(start, tick=False):
            ticket = browser.ticket()
            stale = browser.cookies["next_visitor"]
        with time_machine.travel(start + timedelta(seconds=45), tick=False):
            assert _subscribe(browser, ticket) == Close.SESSION
            fresh = browser.ticket()
            assert fresh != ticket
            assert browser.cookies["next_visitor"].split(":")[0] != stale.split(":")[0]
            assert _subscribe(browser, fresh) == "hello"


@every_engine
@pytest.mark.usefixtures("realtime")
def test_a_rotated_secret_key_keeps_bindings_through_its_fallbacks(
    engine: str, user: AbstractBaseUser
) -> None:
    visitor = Browser()
    visitor_ticket = visitor.ticket()
    member = Browser()
    member.sign_in(user)
    member_ticket = member.ticket()
    previous = settings.SECRET_KEY
    with override_settings(SECRET_KEY="rotated-secret-key"):
        assert _subscribe(visitor, visitor_ticket) == Close.SESSION
        assert _subscribe(member, member_ticket) == Close.SESSION
    with override_settings(
        SECRET_KEY="rotated-secret-key", SECRET_KEY_FALLBACKS=[previous]
    ):
        assert _subscribe(visitor, visitor_ticket) == "hello"
        assert _subscribe(member, member_ticket) == "hello"
        visitor.ticket()
        renewed = visitor.cookies["next_visitor"]
    with override_settings(SECRET_KEY="rotated-secret-key"):
        assert SignedCookieBinding()._visitor(renewed) is not None


@every_engine
@pytest.mark.usefixtures("realtime")
def test_a_custom_binding_named_by_dotted_path_binds_its_own_cookie(
    engine: str,
) -> None:
    browser = Browser()
    browser.cookies["device"] = "phone"
    with override_next_settings(
        REALTIME={"TICKET_BINDING": _DEVICE_BINDING, "GATEWAY_TOKEN": _TOKEN}
    ):
        assert isinstance(ticket_binding(), DeviceBinding)
        ticket = browser.ticket()
        assert _subscribe(browser, ticket) == "hello"
        assert _introspect(browser, ticket)[0] == 200
        browser.cookies["device"] = "laptop"
        assert _subscribe(browser, ticket) == Close.SESSION
        browser.cookies.pop("device")
        assert browser.negotiate() == (503, {"error": "session_unavailable"})


@pytest.mark.parametrize(
    ("engine", "binding", "signed_in"),
    [
        (_ENGINES[0], "next.realtime.SessionBinding", False),
        (_ENGINES[0], "next.realtime.SessionBinding", True),
        (_SIGNED_ENGINE, _SIGNED_BINDING, False),
        (_SIGNED_ENGINE, _SIGNED_BINDING, True),
        (_SIGNED_ENGINE, _DEVICE_BINDING, False),
    ],
    indirect=["engine"],
)
@pytest.mark.usefixtures("realtime")
def test_a_gateway_handshake_from_cookies_matches_the_builtin_endpoint(
    engine: str, binding: str, user: AbstractBaseUser, *, signed_in: bool
) -> None:
    browser = Browser()
    browser.cookies["device"] = "phone"
    with override_next_settings(REALTIME={"TICKET_BINDING": binding}):
        if signed_in:
            browser.sign_in(user)
        ticket = browser.ticket()
        cookies = parse_cookie(browser.header())
        handshake = asyncio.run(Handshake.from_cookies(cookies))
        scope = {
            "type": "websocket",
            "headers": [(b"cookie", browser.header().encode())],
        }
        builtin = asyncio.run(WebSocketApp().authenticate(scope))
        assert handshake is not None
        assert builtin is not None
        assert handshake.credential == builtin.credential
        assert getattr(handshake.user, "pk", None) == getattr(builtin.user, "pk", None)
        assert verify_ticket(ticket, handshake.credential).topics == (_TOPIC,)
        assert asyncio.run(Handshake.from_cookies({})) is None


@pytest.mark.parametrize("engine", [_ENGINES[0]], indirect=True)
@pytest.mark.usefixtures("realtime")
def test_a_gateway_handshake_refuses_a_credential_that_is_gone(engine: str) -> None:
    cookies = {settings.SESSION_COOKIE_NAME: "abcdefghijklmnopqrstuvwxyz012345"}
    with override_next_settings(
        REALTIME={"TICKET_BINDING": "next.realtime.SessionBinding"}
    ):
        assert asyncio.run(Handshake.from_cookies(cookies)) is None


@pytest.mark.parametrize("engine", _ENGINES[:4], indirect=True)
@pytest.mark.usefixtures("realtime")
def test_the_session_binding_still_serves_stored_engines(engine: str) -> None:
    browser = Browser()
    with override_next_settings(REALTIME={"TRANSPORTS": ["ws", "sse"]}):
        assert isinstance(ticket_binding(), SessionBinding)
        ticket = browser.ticket()
        assert set(browser.cookies) == {settings.SESSION_COOKIE_NAME}
        assert _subscribe(browser, ticket) == "hello"


@pytest.mark.parametrize("engine", [_SIGNED_ENGINE], indirect=True)
@pytest.mark.usefixtures("realtime")
def test_the_session_binding_cannot_serve_a_first_signed_cookie_visit(
    engine: str,
) -> None:
    with override_next_settings(REALTIME={"TRANSPORTS": ["ws", "sse"]}):
        assert Browser().negotiate() == (503, {"error": "session_unavailable"})


@every_engine
@pytest.mark.usefixtures("realtime")
@pytest.mark.parametrize("binding", ["next.realtime.SessionBinding", _SIGNED_BINDING])
def test_sse_streams_a_first_visit_without_a_session(engine: str, binding: str) -> None:
    with (
        override_next_settings(REALTIME={"TICKET_BINDING": binding}),
        _sse(Browser()) as events,
    ):
        assert next(events).event == "next-hello"


@every_engine
@pytest.mark.usefixtures("realtime")
@pytest.mark.parametrize("binding", ["next.realtime.SessionBinding", _SIGNED_BINDING])
def test_sse_of_a_signed_in_user_ends_on_a_password_change(
    engine: str, binding: str, user: AbstractBaseUser
) -> None:
    browser = Browser()
    browser.sign_in(user)
    with (
        override_next_settings(REALTIME={"TICKET_BINDING": binding}),
        _sse(browser) as events,
    ):
        assert next(events).event == "next-hello"
        user.set_password("changed")
        user.save()
        assert list(events) == []


@every_engine
@pytest.mark.usefixtures("realtime")
def test_sse_with_a_forged_session_cookie_streams_anonymously(engine: str) -> None:
    browser = Browser()
    browser.cookies[settings.SESSION_COOKIE_NAME] = "forged:session:cookie"
    with _sse(browser) as events:
        assert next(events).event == "next-hello"


@pytest.mark.parametrize(
    ("overrides", "max_age", "secure"),
    [
        ({}, 1209600, ""),
        ({"SESSION_COOKIE_SECURE": True}, 1209600, True),
        ({"SESSION_EXPIRE_AT_BROWSER_CLOSE": True}, "", ""),
    ],
    ids=["default", "secure", "browser-session"],
)
def test_the_visitor_cookie_follows_the_session_cookie_policy(
    overrides: dict[str, object], max_age: object, secure: object
) -> None:
    binding = SignedCookieBinding()
    request = RequestFactory().get("/")
    response = HttpResponse()
    with override_settings(
        SESSION_COOKIE_DOMAIN="example.com", SESSION_COOKIE_PATH="/app/", **overrides
    ):
        subject = binding.issue(request)
        binding.respond(request, response)
    morsel = response.cookies["next_visitor"]
    assert subject == f"visitor:{binding._visitor(morsel.value)}"
    assert morsel["httponly"] is True
    assert morsel["samesite"] == "Lax"
    assert morsel["path"] == "/app/"
    assert morsel["domain"] == "example.com"
    assert morsel["max-age"] == max_age
    assert morsel["secure"] == secure


def test_the_visitor_lives_on_the_request_not_on_the_shared_binding() -> None:
    request = RequestFactory().get("/")
    subject = SignedCookieBinding().issue(request)
    first, second = HttpResponse(), HttpResponse()
    SignedCookieBinding().respond(request, first)
    with override_next_settings(REALTIME={"TICKET_BINDING": _SIGNED_BINDING}):
        ticket_binding().respond(request, second)
    binding = SignedCookieBinding()
    assert vars(binding) == {}
    assert subject == f"visitor:{binding._visitor(first.cookies['next_visitor'].value)}"
    assert binding._visitor(second.cookies["next_visitor"].value) is not None


def test_a_reply_without_a_negotiation_sets_no_visitor_cookie() -> None:
    response = HttpResponse()
    SignedCookieBinding().respond(RequestFactory().get("/"), response)
    assert not response.cookies


@pytest.mark.parametrize("engine", [_ENGINES[0], _SIGNED_ENGINE], indirect=True)
@pytest.mark.usefixtures("realtime")
def test_a_planted_visitor_cookie_ends_with_the_first_signed_in_negotiation(
    engine: str, user: AbstractBaseUser
) -> None:
    attacker = Browser()
    attacker.ticket()
    planted = attacker.cookies["next_visitor"]
    victim = Browser()
    victim.cookies["next_visitor"] = planted
    victim.ticket()
    assert victim.cookies["next_visitor"] == planted
    victim.sign_in(user)
    victim.ticket()
    assert "next_visitor" not in victim.cookies
    victim.sign_out()
    victim.ticket()
    binding = SignedCookieBinding()
    fresh = binding._visitor(victim.cookies["next_visitor"])
    assert fresh is not None
    assert fresh != binding._visitor(planted)


@pytest.mark.parametrize(
    ("engine", "binding"),
    [(_ENGINES[0], "next.realtime.SessionBinding"), (_SIGNED_ENGINE, _SIGNED_BINDING)],
    indirect=["engine"],
)
@pytest.mark.usefixtures("realtime")
def test_the_handshake_subject_is_stable_and_opaque_across_reconnects(
    engine: str, binding: str, user: AbstractBaseUser
) -> None:
    def handshake(browser: Browser) -> Handshake:
        found = asyncio.run(Handshake.from_cookies(parse_cookie(browser.header())))
        assert found is not None
        return found

    with override_next_settings(REALTIME={"TICKET_BINDING": binding}):
        visitor, member = Browser(), Browser()
        visitor.ticket()
        member.sign_in(user)
        member.ticket()
        first, again = handshake(visitor), handshake(visitor)
        signed_in = handshake(member)
        assert first.subject == again.subject
        assert first.subject != signed_in.subject
        assert signed_in.subject == handshake(member).subject
        for found in (first, signed_in):
            assert len(found.subject) == 64
            assert found.credential not in found.subject
            assert found.credential not in repr(found)


def test_a_handshake_whose_credential_proves_no_subject_has_an_empty_one() -> None:
    with override_next_settings(REALTIME={"TICKET_BINDING": _SIGNED_BINDING}):
        assert Handshake("gone", None, None).subject == ""


def test_a_handshake_without_cookies_is_refused_before_the_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pooled = Mock(side_effect=AssertionError("pooled"))
    monkeypatch.setattr(socket_module, "run_detached", pooled)
    assert asyncio.run(Handshake.from_cookies({})) is None
    pooled.assert_not_called()


@pytest.mark.parametrize("engine", [_ENGINES[0]], indirect=True)
@pytest.mark.usefixtures("realtime")
def test_a_signed_cookie_credential_reads_the_session_row_once(
    engine: str, user: AbstractBaseUser
) -> None:
    browser = Browser()
    browser.sign_in(user)
    key = browser.cookies[settings.SESSION_COOKIE_NAME]
    with CaptureQueriesContext(connection) as queries:
        credential = SignedCookieBinding().credential({"sessionid": key})
    assert credential is not None
    assert credential.endswith(f":{user.pk}")
    reads = [query for query in queries if "django_session" in query["sql"]]
    assert len(reads) == 1


@pytest.mark.parametrize("engine", [_ENGINES[0]], indirect=True)
@pytest.mark.usefixtures("realtime")
def test_a_signed_cookie_binding_reads_an_sse_session_key_apart_from_a_credential(
    engine: str, user: AbstractBaseUser
) -> None:
    browser = Browser()
    browser.sign_in(user)
    key = browser.cookies[settings.SESSION_COOKIE_NAME]
    binding = SignedCookieBinding()
    assert binding.live(key) is None
    live = binding.live_session(key)
    assert isinstance(live, LiveIdentity)
    assert getattr(live.user, "pk", None) == user.pk
    assert live.seconds_left is not None


def test_a_session_binding_reply_carries_no_cookie() -> None:
    response = HttpResponse()
    SessionBinding().respond(RequestFactory().get("/"), response)
    assert not response.cookies


@pytest.mark.django_db()
def test_a_signed_cookie_credential_needs_a_signed_in_session_or_a_visitor() -> None:
    binding = SignedCookieBinding()
    assert binding.credential({}) is None
    assert binding.credential({settings.SESSION_COOKIE_NAME: "gone"}) is None
    assert binding.subject("gone") is None
    assert binding.live("gone") is None
    assert binding.live("user:hash:missing.Backend:1") is None


def test_a_visitor_credential_is_anonymous_without_a_deadline() -> None:
    binding = SignedCookieBinding()
    value = binding._signer().sign("visitor-id")
    credential = binding.credential({"next_visitor": value})
    assert credential == "visitor:visitor-id"
    user, left = binding.live(credential)
    assert user.is_authenticated is False
    assert left is None
    assert binding.subject(credential) == credential


def test_issue_without_session_middleware_binds_a_visitor() -> None:
    with pytest.raises(InvalidTicketError, match="SessionMiddleware"):
        issue_ticket(
            RequestFactory().get("/"),
            (_TOPIC,),
            transform_ref=None,
            authorizer_ref=ROOM_AUTHORIZER_REF,
        )
    with override_next_settings(REALTIME={"TICKET_BINDING": _SIGNED_BINDING}):
        request = RequestFactory().get("/")
        grant = issue_ticket(
            request, (_TOPIC,), transform_ref=None, authorizer_ref=ROOM_AUTHORIZER_REF
        )
        response = HttpResponse()
        ticket_binding().respond(request, response)
        cookie = response.cookies["next_visitor"].value
        credential = ticket_binding().credential({"next_visitor": cookie})
        assert credential is not None
        assert verify_ticket(grant.token, credential).topics == (_TOPIC,)


@pytest.mark.parametrize(
    "path",
    [
        None,
        3,
        "tests.realtime.missing_module.Binding",
        "tests.realtime.test_binding.NOT_A_CLASS",
        "tests.realtime.test_binding.Browser",
        "tests.realtime.test_binding.AbstractBinding",
    ],
    ids=["none", "int", "missing", "not-a-class", "foreign-class", "abstract"],
)
def test_an_unusable_binding_path_is_refused(path: object) -> None:
    assert binding_class(path) is None
    with (
        override_next_settings(REALTIME={"TICKET_BINDING": path}),
        pytest.raises(ImproperlyConfigured, match="TICKET_BINDING"),
    ):
        ticket_binding()


def test_the_default_binding_is_built_once() -> None:
    assert ticket_binding() is ticket_binding()
    assert type(ticket_binding()) is SessionBinding
    assert binding_class(_SIGNED_BINDING) is SignedCookieBinding


@pytest.mark.parametrize("digest", [None, 5, "0" * 64])
def test_a_ticket_without_a_matching_digest_belongs_to_another_session(
    digest: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    sign_object = signing.TimestampSigner.sign_object

    def forged(
        signer: signing.TimestampSigner, payload: dict[str, object], **options: bool
    ) -> str:
        payload = {key: value for key, value in payload.items() if key != "session"}
        if digest is not None:
            payload["session"] = digest
        return sign_object(signer, payload, **options)

    with override_next_settings(REALTIME={"TICKET_BINDING": _SIGNED_BINDING}):
        request = RequestFactory().get("/")
        monkeypatch.setattr(signing.TimestampSigner, "sign_object", forged)
        grant = issue_ticket(
            request, (_TOPIC,), transform_ref=None, authorizer_ref=ROOM_AUTHORIZER_REF
        )
        monkeypatch.undo()
        response = HttpResponse()
        ticket_binding().respond(request, response)
        cookie = response.cookies["next_visitor"].value
        credential = ticket_binding().credential({"next_visitor": cookie})
        assert credential is not None
        with pytest.raises(InvalidTicketError, match="another session"):
            verify_ticket(grant.token, credential)
        request.COOKIES["next_visitor"] = cookie
        genuine = issue_ticket(
            request, (_TOPIC,), transform_ref=None, authorizer_ref=ROOM_AUTHORIZER_REF
        )
        assert verify_ticket(genuine.token, credential).topics == (_TOPIC,)
