import re
import threading

import pytest
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import BACKEND_SESSION_KEY, HASH_SESSION_KEY, SESSION_KEY
from django.contrib.auth.middleware import AuthenticationMiddleware
from django.contrib.auth.models import User
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.middleware import SessionMiddleware
from django.http import HttpRequest, HttpResponse
from django.middleware.csrf import (
    CsrfViewMiddleware,
    _unmask_cipher_token,
    get_token,
    rotate_token,
)
from django.template import Context, Template
from django.test import RequestFactory, override_settings
from django.utils.functional import empty

from next.defer.connections import Settled, effects, request_state, settle_request
from next.defer.markers import Effects
from tests.defer.connections.shared import ProjectSessions, dotted
from tests.support.waits import WAIT


BACKEND = "django.contrib.auth.backends.ModelBackend"
MIDDLEWARE_ALL = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]


def _through(request: HttpRequest) -> HttpRequest:
    def response(request: HttpRequest) -> HttpResponse:
        return HttpResponse()

    SessionMiddleware(response).process_request(request)
    AuthenticationMiddleware(response).process_request(request)
    MessageMiddleware(response).process_request(request)
    return request


@pytest.fixture()
def stored_session() -> SessionStore:
    """Return a saved session, so its key reads back as a visitor's cookie."""
    store = SessionStore()
    store["seen"] = True
    store.save()
    return SessionStore(store.session_key)


@pytest.fixture()
def loaded(rf: RequestFactory, stored_session: SessionStore) -> HttpRequest:
    """Return a GET through the middleware, carrying a stored session."""
    request = _through(rf.get("/", {"a": "1"}))
    request.session = stored_session
    return request


@pytest.fixture()
def bare(rf: RequestFactory) -> HttpRequest:
    """Return a GET through the middleware with no session cookie."""
    return _through(rf.get("/"))


@pytest.fixture()
def submitted(rf: RequestFactory) -> HttpRequest:
    """Return a POST no middleware has seen."""
    return rf.post("/", {"field": "value"})


@pytest.fixture()
def saved(
    bare: HttpRequest, stored_session: SessionStore
) -> tuple[HttpRequest, Settled]:
    """Return a request with a stored session and a message, with its settled record."""
    bare.session = stored_session
    messages.info(bare, "saved")
    return bare, settle_request(bare, stream=False)


@pytest.mark.django_db()
class TestSettleRequest:
    """Lazy request state is forced before the first job, each where it is installed."""

    def test_every_installed_piece_is_forced(self, loaded) -> None:
        messages.info(loaded, "saved")
        settled = settle_request(loaded, stream=False)
        assert loaded.user._wrapped is not empty
        assert hasattr(loaded.session, "_session_cache")
        storage = messages.get_messages(loaded)
        assert hasattr(storage, "_loaded_data")
        assert storage.used is False
        assert "CSRF_COOKIE_NEEDS_UPDATE" not in loaded.META
        assert settled == Settled(
            loaded.session.session_key, False, False, loaded.META["CSRF_COOKIE"]
        )

    def test_a_stream_also_mints_the_csrf_token(self, loaded) -> None:
        settle_request(loaded, stream=True)
        assert "CSRF_COOKIE" in loaded.META
        assert loaded.META["CSRF_COOKIE_NEEDS_UPDATE"] is True

    def test_parallel_jobs_share_one_seeded_csrf_secret(self, loaded) -> None:
        settle_request(loaded, stream=False)
        template = Template("{% csrf_token %}")
        tokens: list[str] = []

        def job() -> None:
            html = template.render(Context({"csrf_token": get_token(loaded)}))
            tokens.append(re.findall(r'value="([^"]+)"', html)[0])

        threads = [threading.Thread(target=job) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(WAIT)
            assert not thread.is_alive()
        secret = loaded.META["CSRF_COOKIE"]
        assert [_unmask_cipher_token(token) for token in tokens] == [secret, secret]
        assert loaded.META["CSRF_COOKIE_NEEDS_UPDATE"] is True

    def test_jobs_that_render_no_token_leave_the_response_shared(self, loaded) -> None:
        settle_request(loaded, stream=False)
        response = CsrfViewMiddleware(lambda _: HttpResponse()).process_response(
            loaded, HttpResponse()
        )
        assert settings.CSRF_COOKIE_NAME not in response.cookies
        assert not response.has_header("Vary")

    def test_a_request_carrying_its_secret_is_left_alone(self, loaded) -> None:
        loaded.META["CSRF_COOKIE"] = "held"
        settle_request(loaded, stream=False)
        assert loaded.META["CSRF_COOKIE"] == "held"
        assert "CSRF_COOKIE_NEEDS_UPDATE" not in loaded.META

    def test_an_unsafe_method_parses_the_body(self, submitted) -> None:
        request = _through(submitted)
        settle_request(request, stream=False)
        assert vars(request)["_post"]["field"] == "value"
        assert "_files" in vars(request)

    def test_a_safe_method_leaves_the_body(self, loaded) -> None:
        settle_request(loaded, stream=False)
        assert "_post" not in vars(loaded)

    @pytest.mark.parametrize(
        ("installed", "user", "session", "messages_loaded", "csrf"),
        [
            ([], False, False, False, False),
            ([MIDDLEWARE_ALL[0]], False, True, False, False),
            ([MIDDLEWARE_ALL[0], MIDDLEWARE_ALL[2]], True, True, False, False),
            ([MIDDLEWARE_ALL[0], MIDDLEWARE_ALL[3]], False, True, True, False),
            ([MIDDLEWARE_ALL[1]], False, False, False, True),
            ([dotted(ProjectSessions)], False, True, False, False),
        ],
        ids=["none", "sessions", "auth", "messages", "csrf", "subclass"],
    )
    def test_each_step_follows_its_middleware(
        self, loaded, installed: list[str], user, session, messages_loaded, csrf
    ) -> None:
        with override_settings(MIDDLEWARE=installed):
            settle_request(loaded, stream=True)
        assert (loaded.user._wrapped is not empty) is user
        assert hasattr(loaded.session, "_session_cache") is session
        assert hasattr(messages.get_messages(loaded), "_loaded_data") is messages_loaded
        assert ("CSRF_COOKIE" in loaded.META) is csrf

    def test_a_request_the_middleware_never_saw_is_tolerated(self, submitted) -> None:
        settled = settle_request(submitted, stream=True)
        assert settled == Settled(csrf_secret=submitted.META.get("CSRF_COOKIE"))

    def test_a_missing_session_cookie_creates_no_session(self, bare) -> None:
        settled = settle_request(bare, stream=False)
        assert settled.session_key is None
        assert bare.session.session_key is None

    def test_a_shared_page_loads_the_session_unmarked(self, loaded) -> None:
        settled = settle_request(loaded, stream=False, shared=True)
        assert hasattr(loaded.session, "_session_cache")
        assert loaded.session.accessed is False
        assert not hasattr(messages.get_messages(loaded), "_loaded_data")
        assert settled.session_key == loaded.session.session_key

    def test_a_session_the_user_load_rotates_stays_marked_read(
        self, rf: RequestFactory
    ) -> None:
        old, new = "a-retired-secret-" * 4, "the-current-secret-" * 4
        user = User.objects.create_user("ada", password="x")
        with override_settings(SECRET_KEY=old):
            stale = user.get_session_auth_hash()
        with override_settings(SECRET_KEY=new, SECRET_KEY_FALLBACKS=[old]):
            store = SessionStore()
            store.update(
                {
                    SESSION_KEY: str(user.pk),
                    BACKEND_SESSION_KEY: BACKEND,
                    HASH_SESSION_KEY: stale,
                }
            )
            store.save()
            request = _through(rf.get("/"))
            request.session = SessionStore(store.session_key)
            settle_request(request, stream=False, shared=True)
            assert request.session.session_key != store.session_key
            assert request.session.modified is True
            assert request.session.accessed is True
            response = SessionMiddleware(lambda _: HttpResponse()).process_response(
                request, HttpResponse()
            )
        assert "Cookie" in response["Vary"]

    def test_a_shared_page_user_marks_the_session_read_when_read(self, loaded) -> None:
        settle_request(loaded, stream=False, shared=True)
        assert loaded.session.accessed is False
        assert loaded.user.is_authenticated is False
        assert loaded.session.accessed is True

    def test_a_shared_page_whose_shell_read_the_session_resolves_as_usual(
        self, loaded
    ) -> None:
        loaded.session.get("seen")
        settle_request(loaded, stream=False, shared=True)
        assert loaded.user._wrapped is not empty
        assert loaded.session.accessed is True

    @override_settings(MIDDLEWARE=[MIDDLEWARE_ALL[0]])
    def test_a_shared_page_without_authentication_loads_the_session_alone(
        self, loaded
    ) -> None:
        settle_request(loaded, stream=False, shared=True)
        assert loaded.user._wrapped is empty
        assert loaded.session.accessed is False

    def test_a_shared_page_without_a_session_settles_the_csrf_secret_alone(
        self, submitted
    ) -> None:
        settled = settle_request(submitted, stream=False, shared=True)
        assert settled == Settled(csrf_secret=submitted.META["CSRF_COOKIE"])

    def test_the_state_reads_load_nothing(self, loaded) -> None:
        assert request_state(loaded) == Settled(loaded.session.session_key)
        assert loaded.session.accessed is False


@pytest.mark.django_db()
class TestEffects:
    """A job's writes to request state show against the settled record."""

    def test_reads_alone_are_no_effect(self, saved) -> None:
        request, before = saved
        request.session.get("seen")
        len(messages.get_messages(request))
        assert effects(request, before) == Effects()
        assert not effects(request, before)

    def test_each_write_is_named(self, saved) -> None:
        request, before = saved
        request.session["written"] = True
        request.session.cycle_key()
        list(messages.get_messages(request))
        assert effects(request, before) == Effects(
            session_modified=True, session_key_changed=True, messages_used=True
        )

    def test_a_write_before_the_settle_is_not_the_job(self, bare) -> None:
        bare.session["early"] = True
        list(messages.get_messages(bare))
        before = settle_request(bare, stream=False)
        assert effects(bare, before) == Effects()

    def test_a_rotated_csrf_secret_is_named(self, saved) -> None:
        request, _before = saved
        get_token(request)
        before = request_state(request)
        rotate_token(request)
        assert effects(request, before) == Effects(csrf_changed=True)

    def test_a_first_csrf_secret_or_a_reread_is_no_effect(self, saved) -> None:
        request, before = saved
        get_token(request)
        assert effects(request, before) == Effects()
        minted = request_state(request)
        get_token(request)
        assert effects(request, minted) == Effects()

    def test_no_session_and_no_messages_is_no_effect(self, submitted) -> None:
        assert effects(submitted, settle_request(submitted, stream=False)) == Effects()


@pytest.mark.django_db()
class TestListedMiddleware:
    """The request state follows the middleware list, asked again once it moves."""

    def test_a_moved_list_is_asked_again(self, loaded) -> None:
        key = loaded.session.session_key
        assert request_state(loaded).session_key == key
        with override_settings(MIDDLEWARE=[]):
            assert request_state(loaded).session_key is None
        assert request_state(loaded).session_key == key
