import copy
import pickle
from importlib import import_module
from typing import Any, ClassVar, cast
from unittest.mock import patch

import pytest
from asgiref.sync import async_to_sync
from django import forms as django_forms
from django.conf import settings
from django.contrib.auth import (
    BACKEND_SESSION_KEY,
    HASH_SESSION_KEY,
    SESSION_KEY,
    aget_user,
    alogin,
    alogout,
    get_user_model,
)
from django.contrib.sessions.backends.base import SessionBase
from django.db import connection, connections, transaction
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBase,
    HttpResponseRedirect,
)
from django.template import Context, Template
from django.test import AsyncClient, RequestFactory, override_settings
from django.urls import ResolverMatch

from next.forms import Form, FormWizard, SessionRotationError, action
from next.forms.checks import (
    actions as action_checks,
    check_invalid_action_transport,
    check_tunneled_cookie_messages,
)
from next.forms.manager import form_action_manager
from next.forms.registration import registration_diagnostics
from next.forms.transport import (
    DISPATCHED_SCOPE_KEY,
    MISDIRECTED,
    TRANSPORT_HEADER,
    TRANSPORT_SCOPE_KEY,
    DispatchMark,
    SessionPin,
    Transport,
    http_only,
    mark_dispatched,
    pin_session,
    roll_back,
    tunneled,
)
from tests.support import check_ids


@action("socket_vote", transport="any")
def socket_vote() -> HttpResponse:
    return HttpResponse("voted")


@action("plain_vote")
def plain_vote() -> HttpResponse:
    return HttpResponse("voted")


class SocketSignupForm(Form):
    """A form the runtime may post through an open WebSocket."""

    email = django_forms.EmailField()

    class Meta:
        """Admits the tunnel."""

        transport = "any"


class SocketStep(Form):
    """The one step of the socket wizard."""

    name = django_forms.CharField()

    class Meta:
        """A step, never a standalone action."""

        abstract = True


class SocketWizard(FormWizard):
    """A wizard whose steps may post through an open WebSocket."""

    class Meta:
        """One step, admitting the tunnel."""

        steps: ClassVar = [("name", SocketStep)]
        transport = "any"

    def done(
        self, request: HttpRequest, cleaned_data: dict[str, Any]
    ) -> HttpResponseRedirect:
        return HttpResponseRedirect("/done/")


def _meta(name: str) -> dict[str, Any]:
    meta = form_action_manager.get_action_meta(name)
    assert meta is not None
    return dict(meta)


def _post(url: str, **kwargs: object) -> HttpResponse:
    return async_to_sync(AsyncClient().post)(url, **kwargs)


def _render(body: str) -> str:
    request = RequestFactory().get("/")
    return Template("{% load forms %}" + body).render(Context({"request": request}))


class TestDeclaration:
    """`transport` reaches the action meta from `@action` and from `Meta`."""

    @pytest.mark.parametrize(
        "name",
        ["socket_vote", "socket_signup_form", "socket_wizard"],
        ids=["action-kwarg", "form-meta", "wizard-meta"],
    )
    def test_an_opted_in_action_records_any(self, name: str) -> None:
        assert _meta(name)["transport"] == "any"

    def test_the_default_leaves_the_meta_without_the_key(self) -> None:
        assert "transport" not in _meta("plain_vote")

    def test_an_explicit_http_leaves_the_meta_without_the_key(self) -> None:
        @action("explicit_http_vote", transport="http")
        def explicit_http_vote() -> HttpResponse:
            return HttpResponse("ok")

        assert "transport" not in _meta("explicit_http_vote")


class TestUnknownValue:
    """An unknown value registers the action for HTTP and feeds `next.E200`."""

    def test_an_action_kwarg_falls_back_to_http(self) -> None:
        @action("odd_vote", transport=cast("Transport", "ws"))
        def odd_vote() -> HttpResponse:
            return HttpResponse("ok")

        assert "transport" not in _meta("odd_vote")
        assert registration_diagnostics.invalid_transport[-1][1] == "ws"
        assert "odd_vote" in registration_diagnostics.invalid_transport[-1][0]

    def test_a_form_meta_falls_back_to_http(self) -> None:
        class OddTransportForm(Form):
            email = django_forms.EmailField()

            class Meta:
                transport = "socket"

        assert "transport" not in _meta("odd_transport_form")
        owner, value = registration_diagnostics.invalid_transport[-1]
        assert "OddTransportForm" in owner
        assert value == "socket"

    def test_the_check_reports_each_unknown_value(self) -> None:
        @action("checked_vote", transport=cast("Transport", "always"))
        def checked_vote() -> HttpResponse:
            return HttpResponse("ok")

        messages = check_invalid_action_transport()
        assert check_ids(messages) == ["next.E200"]
        assert "'always'" in messages[0].msg

    def test_the_check_is_quiet_without_unknown_values(self) -> None:
        assert check_invalid_action_transport() == []


class TestFormTag:
    """`{% form %}` marks an opted-in action for the client runtime."""

    @pytest.mark.parametrize("name", ["socket_vote", "socket_signup_form"])
    def test_an_opted_in_form_carries_the_marker(self, name: str) -> None:
        html = _render(f'{{% form "{name}" %}}{{% endform %}}')
        assert 'data-next-transport="any"' in html

    def test_a_default_form_carries_no_marker(self) -> None:
        html = _render('{% form "plain_vote" %}{% endform %}')
        assert "data-next-transport" not in html

    def test_action_url_stays_a_bare_url(self) -> None:
        html = _render('{% action_url "socket_vote" %}')
        assert html == form_action_manager.get_action_url("socket_vote")


class TestTunneled:
    """A request counts as tunneled only through the scope key of its tunnel."""

    def test_a_request_without_a_scope_is_plain(self) -> None:
        assert tunneled(RequestFactory().post("/")) is False

    @pytest.mark.parametrize(
        ("scope", "expected"),
        [
            ({}, False),
            ({TRANSPORT_SCOPE_KEY: "http"}, False),
            ({TRANSPORT_SCOPE_KEY: "ws"}, True),
        ],
        ids=["unmarked", "http", "ws"],
    )
    def test_the_scope_key_decides(self, scope: dict[str, str], expected) -> None:
        request = RequestFactory().post("/")
        request.scope = scope
        assert tunneled(request) is expected

    def test_the_refusal_names_the_transport_to_retry_with(self) -> None:
        response = http_only()
        assert response.status_code == MISDIRECTED
        assert response[TRANSPORT_HEADER] == "http"


def _dispatch(name: str, transport: str | None = None) -> HttpResponseBase:
    request = RequestFactory().post("/")
    if transport is not None:
        request.scope = {TRANSPORT_SCOPE_KEY: transport}
    return form_action_manager.default_backend.dispatch(request, _meta(name)["uid"])


@pytest.mark.django_db()
class TestDispatch:
    """Dispatch refuses a tunnel to an HTTP action and labels an opted-in reply."""

    def test_a_tunnel_to_an_http_action_is_refused_before_it_runs(self) -> None:
        response = _dispatch("plain_vote", "ws")
        assert response.status_code == MISDIRECTED
        assert response[TRANSPORT_HEADER] == "http"
        assert response.content == b""

    def test_a_tunnel_to_an_opted_in_action_runs_it(self) -> None:
        response = _dispatch("socket_vote", "ws")
        assert response.status_code == 200
        assert response.content == b"voted"
        assert response[TRANSPORT_HEADER] == "any"

    def test_plain_http_to_an_opted_in_action_announces_it(self) -> None:
        response = _post(form_action_manager.get_action_url("socket_vote"))
        assert response.content == b"voted"
        assert response[TRANSPORT_HEADER] == "any"

    def test_plain_http_to_an_http_action_stays_unlabelled(self) -> None:
        response = _post(form_action_manager.get_action_url("plain_vote"))
        assert response.status_code == 200
        assert TRANSPORT_HEADER not in response

    def test_a_client_header_cannot_claim_the_tunnel(self) -> None:
        url = form_action_manager.get_action_url("plain_vote")
        response = _post(
            url, headers={TRANSPORT_SCOPE_KEY: "ws", "X-Next-Transport": "ws"}
        )
        assert response.status_code == 200
        assert response.content == b"voted"


class TestCookieMessagesCheck:
    """A socket-capable action warns about messages kept in a cookie."""

    @pytest.mark.parametrize(
        "storage",
        [
            "django.contrib.messages.storage.fallback.FallbackStorage",
            "django.contrib.messages.storage.cookie.CookieStorage",
        ],
    )
    def test_cookie_storage_warns(self, storage: str) -> None:
        with override_settings(MESSAGE_STORAGE=storage):
            messages = check_tunneled_cookie_messages()
        assert check_ids(messages) == ["next.W145"]
        assert "'socket_vote'" in messages[0].msg

    @pytest.mark.parametrize(
        "storage",
        ["django.contrib.messages.storage.session.SessionStorage", "missing.Storage"],
    )
    def test_other_storage_is_quiet(self, storage: str) -> None:
        with override_settings(MESSAGE_STORAGE=storage):
            assert check_tunneled_cookie_messages() == []

    def test_no_socket_action_is_quiet(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(action_checks, "iter_registered_actions", lambda: iter(()))
        assert check_tunneled_cookie_messages() == []

    def test_cookie_storage_without_the_messages_app_is_quiet(self) -> None:
        storage = "django.contrib.messages.storage.cookie.CookieStorage"
        apps = [
            app for app in settings.INSTALLED_APPS if app != "django.contrib.messages"
        ]
        with override_settings(MESSAGE_STORAGE=storage, INSTALLED_APPS=apps):
            assert check_tunneled_cookie_messages() == []


_ENGINES = [
    "django.contrib.sessions.backends.db",
    "django.contrib.sessions.backends.cache",
    "django.contrib.sessions.backends.cached_db",
    "django.contrib.sessions.backends.file",
    "django.contrib.sessions.backends.signed_cookies",
]


def _pinned_request(session: SessionBase) -> tuple[HttpRequest, SessionPin]:
    request = RequestFactory().post("/")
    request.session = session
    return request, pin_session(request)


@pytest.mark.django_db()
class TestPinnedSession:
    """A pinned session refuses every rotation and flush before the store writes."""

    @pytest.mark.parametrize("engine", _ENGINES)
    def test_any_engine_keeps_its_key_and_data(self, engine: str) -> None:
        store = import_module(engine).SessionStore
        session = store()
        session["seed"] = "kept"
        session.save()
        key = session.session_key
        _, pin = _pinned_request(session)
        for refused in (session.cycle_key, session.flush):
            with pytest.raises(SessionRotationError):
                refused()
        for refused_async in (session.acycle_key, session.aflush):
            with pytest.raises(SessionRotationError):
                async_to_sync(refused_async)()
        assert type(session) is store
        assert session.session_key == key
        assert session["seed"] == "kept"
        assert pin.rotated is True

    @pytest.mark.parametrize("engine", _ENGINES)
    def test_any_engine_reads_and_writes_under_its_own_salt(self, engine: str) -> None:
        store = import_module(engine).SessionStore
        seeded = store()
        seeded["seed"] = "kept"
        seeded.save()
        session = store(seeded.session_key)
        salt = session.key_salt
        _pinned_request(session)
        assert session["seed"] == "kept"
        session["added"] = "new"
        session.save()
        assert session.key_salt == salt
        assert dict(store(session.session_key).items()) == {
            "seed": "kept",
            "added": "new",
        }

    def test_a_pinned_session_pickles_and_keeps_its_refusals(self) -> None:
        session = import_module(settings.SESSION_ENGINE).SessionStore()
        session["seed"] = "kept"
        _pinned_request(session)
        assert pickle.dumps(session)
        clone = copy.deepcopy(session)
        assert type(clone) is type(session)
        assert clone["seed"] == "kept"
        with pytest.raises(SessionRotationError):
            clone.cycle_key()

    def test_django_auth_paths_raise_before_writing(self) -> None:
        user = get_user_model().objects.create_user("pinned-user")
        session = import_module(settings.SESSION_ENGINE).SessionStore()
        session.create()
        key = session.session_key
        request, _ = _pinned_request(session)
        with pytest.raises(SessionRotationError):
            async_to_sync(alogin)(request, user)
        with pytest.raises(SessionRotationError):
            async_to_sync(alogout)(request)
        session[SESSION_KEY] = str(user.pk)
        session[BACKEND_SESSION_KEY] = "django.contrib.auth.backends.ModelBackend"
        session[HASH_SESSION_KEY] = "stale"
        with pytest.raises(SessionRotationError):
            async_to_sync(aget_user)(request)
        assert session.exists(key)

    def test_pinning_twice_keeps_one_pin(self) -> None:
        session = import_module(settings.SESSION_ENGINE).SessionStore()
        request, pin = _pinned_request(session)
        assert pin_session(request) is pin
        assert pin.rotated is False

    def test_a_request_without_a_session_is_left_alone(self) -> None:
        request = RequestFactory().post("/")
        pin = pin_session(request)
        assert not hasattr(request, "session")
        assert pin.rotated is False


class TestDispatchMark:
    """`mark_dispatched` flips only the mark the tunnel put in the scope."""

    def test_the_tunnel_mark_is_reached(self) -> None:
        mark = DispatchMark()
        request = RequestFactory().post("/")
        request.scope = {DISPATCHED_SCOPE_KEY: mark}
        mark_dispatched(request)
        assert mark.reached is True

    @pytest.mark.parametrize("scope", [None, {}, {DISPATCHED_SCOPE_KEY: True}])
    def test_anything_else_is_left_alone(self, scope: object) -> None:
        request = RequestFactory().post("/")
        request.scope = scope
        mark_dispatched(request)
        assert request.scope == scope


@pytest.mark.django_db(transaction=True)
class TestRollBack:
    """`roll_back` marks only the request transaction `ATOMIC_REQUESTS` opened."""

    def _request(self, exempt: tuple[str, ...]) -> HttpRequest:
        def view() -> None:
            """Stand for the resolved view."""

        for alias in exempt:
            view = transaction.non_atomic_requests(using=alias)(view)
        request = RequestFactory().post("/")
        request.resolver_match = ResolverMatch(view, (), {})
        return request

    @pytest.mark.parametrize(
        ("atomic", "exempt", "expected"),
        [(True, (), True), (True, ("default",), False), (False, (), False)],
        ids=["atomic", "exempt", "plain"],
    )
    def test_the_request_block_is_marked(
        self, *, atomic: bool, exempt: tuple[str, ...], expected: bool
    ) -> None:
        with (
            patch.dict(connections.settings["default"], {"ATOMIC_REQUESTS": atomic}),
            transaction.atomic(),
        ):
            roll_back(self._request(exempt))
            assert connection.get_rollback() is expected

    def test_outside_a_block_nothing_happens(self) -> None:
        with patch.dict(connections.settings["default"], {"ATOMIC_REQUESTS": True}):
            roll_back(RequestFactory().post("/"))
        assert connection.in_atomic_block is False
