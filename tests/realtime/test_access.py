import asyncio
import math
import threading
from dataclasses import dataclass
from unittest.mock import Mock

import pytest
from django.contrib.auth import HASH_SESSION_KEY, SESSION_KEY, get_user_model, login
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.db import SessionStore
from django.test import RequestFactory, override_settings

from next.realtime import access as access_module
from next.realtime.access import Access, AccessCheck, heartbeat_seconds, run_detached


@dataclass
class FakeClock:
    now: float = 0.0

    def __call__(self) -> float:
        return self.now


def _logged_in(username: str = "reader"):
    user = get_user_model().objects.create_user(username=username, password="secret")
    request = RequestFactory().get("/")
    request.session = SessionStore()
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    request.session.save()
    return user, request.session


@pytest.mark.parametrize(
    ("value", "expected"),
    [(5, 5.0), (0.5, 0.5), (0, 25.0), (-1, 25.0), (math.inf, 25.0), (True, 25.0)],
)
def test_heartbeat_seconds_invalid_value_falls_back_to_default(
    value: object, expected: float
) -> None:
    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"HEARTBEAT": value}}):
        assert heartbeat_seconds() == expected


def test_heartbeat_seconds_non_mapping_setting_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        access_module,
        "next_framework_settings",
        Mock(spec=access_module.next_framework_settings, REALTIME="invalid"),
    )
    assert heartbeat_seconds() == 25.0


def test_access_check_throttles_the_authorizer_to_one_call_per_interval() -> None:
    calls: list[str] = []
    clock = FakeClock()

    def authorizer(_user: object, topic: str) -> bool:
        calls.append(topic)
        return True

    check = AccessCheck(
        ("a", "b"),
        authorizer=authorizer,
        session_key=None,
        user=AnonymousUser(),
        interval=10,
        clock=clock,
    )
    assert check.check() is Access.GRANTED
    clock.now = 9.9
    assert check.check() is Access.GRANTED
    assert calls == ["a", "b"]
    clock.now = 10.0
    assert check.check() is Access.GRANTED
    assert calls == ["a", "b", "a", "b"]


@pytest.mark.parametrize("answer", [False, None, 1, "yes"])
def test_access_check_non_true_authorizer_answer_denies(answer: object) -> None:
    check = AccessCheck(
        ("a",),
        authorizer=lambda _user, _topic: answer,
        session_key=None,
        user=None,
        interval=1,
    )
    assert check.check() is Access.TOPIC_DENIED


def test_access_check_denied_topic_is_not_asked_again() -> None:
    calls: list[str] = []
    clock = FakeClock()

    def authorizer(_user: object, topic: str) -> bool:
        calls.append(topic)
        return False

    check = AccessCheck(
        ("a",),
        authorizer=authorizer,
        session_key=None,
        user=None,
        interval=1,
        clock=clock,
    )
    assert check.check() is Access.TOPIC_DENIED
    clock.now = 100
    assert check.check() is Access.TOPIC_DENIED
    assert calls == ["a"]


def test_access_check_expires_the_session_without_touching_the_store() -> None:
    clock = FakeClock()
    check = AccessCheck(
        ("a",),
        authorizer=None,
        session_key="never-loaded",
        user=None,
        interval=100,
        expires_at=5,
        clock=clock,
    )
    check._due_at = math.inf
    clock.now = 5
    assert check.check() is Access.SESSION_REVOKED
    assert asyncio.run(check.acheck()) is Access.SESSION_REVOKED


def test_async_access_check_nothing_to_check_grants_without_thread_hop() -> None:
    check = AccessCheck(
        ("a",), authorizer=None, session_key=None, user=None, interval=1
    )
    assert asyncio.run(check.acheck()) is Access.GRANTED


@pytest.mark.django_db(transaction=True)
def test_async_access_check_runs_off_the_event_loop_thread() -> None:
    threads: list[threading.Thread] = []

    def authorizer(_user: object, _topic: str) -> bool:
        threads.append(threading.current_thread())
        return True

    check = AccessCheck(
        ("a",), authorizer=authorizer, session_key=None, user=None, interval=1
    )
    assert asyncio.run(check.acheck()) is Access.GRANTED
    assert threads
    assert threads[0] is not threading.main_thread()


@pytest.mark.django_db(transaction=True)
def test_access_check_passes_the_reloaded_user_to_the_authorizer() -> None:
    user, session = _logged_in()
    seen: list[object] = []

    def authorizer(current: object, _topic: str) -> bool:
        seen.append(current)
        return True

    check = AccessCheck(
        ("a",),
        authorizer=authorizer,
        session_key=session.session_key,
        user=user,
        interval=1,
    )
    assert check.check() is Access.GRANTED
    assert getattr(seen[0], "pk", None) == user.pk


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    "revocation", ["flush", "rotate", "password", "inactive", "swap"]
)
def test_access_check_invalidated_session_or_user_is_revoked(revocation: str) -> None:
    user, session = _logged_in()
    key = session.session_key
    if revocation == "flush":
        session.flush()
    elif revocation == "rotate":
        session.cycle_key()
    elif revocation == "password":
        user.set_password("changed")
        user.save(update_fields=["password"])
    elif revocation == "inactive":
        get_user_model().objects.filter(pk=user.pk).update(is_active=False)
    else:
        other = get_user_model().objects.create_user(username="other", password="x")
        store = SessionStore(session_key=key)
        store[SESSION_KEY] = str(other.pk)
        store[HASH_SESSION_KEY] = other.get_session_auth_hash()
        store.save()
    check = AccessCheck(("a",), authorizer=None, session_key=key, user=user, interval=1)
    assert asyncio.run(check.acheck()) is Access.SESSION_REVOKED


@pytest.mark.django_db(transaction=True)
def test_access_check_anonymous_session_stays_granted() -> None:
    session = SessionStore()
    session.create()
    check = AccessCheck(
        ("a",),
        authorizer=None,
        session_key=session.session_key,
        user=AnonymousUser(),
        interval=1,
    )
    assert check.check() is Access.GRANTED


@pytest.mark.django_db(transaction=True)
def test_run_detached_returns_the_result_from_a_pool_thread() -> None:
    def where(value: int) -> tuple[int, bool]:
        return value * 2, threading.current_thread() is threading.main_thread()

    assert asyncio.run(run_detached(where, 21)) == (42, False)


@pytest.mark.django_db(transaction=True)
def test_access_check_unbinds_a_stale_anonymous_cookie_at_open() -> None:
    seen: list[object] = []

    def authorizer(user: object, _topic: str) -> bool:
        seen.append(user)
        return True

    clock = FakeClock()
    check = AccessCheck(
        ("a",),
        authorizer=authorizer,
        session_key="stale-session-key-0001",
        user=AnonymousUser(),
        interval=1,
        clock=clock,
    )
    assert check.check() is Access.GRANTED
    assert check._session_key is None
    clock.now = 2
    assert check.check() is Access.GRANTED
    assert all(not getattr(user, "is_authenticated", True) for user in seen)


@pytest.mark.django_db(transaction=True)
def test_access_check_revokes_an_anonymous_session_lost_after_open() -> None:
    session = SessionStore()
    session.create()
    clock = FakeClock()
    check = AccessCheck(
        ("a",),
        authorizer=None,
        session_key=session.session_key,
        user=AnonymousUser(),
        interval=1,
        clock=clock,
    )
    assert check.check() is Access.GRANTED
    session.delete()
    clock.now = 2
    assert check.check() is Access.SESSION_REVOKED
