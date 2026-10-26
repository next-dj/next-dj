import asyncio
import math
import os
import threading
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path
from unittest.mock import DEFAULT, patch

import pytest
from django.contrib.auth import HASH_SESSION_KEY, SESSION_KEY, get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.sessions.backends.cache import SessionStore as CacheStore
from django.contrib.sessions.backends.cached_db import SessionStore as CachedDbStore
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.backends.file import SessionStore as FileStore
from django.contrib.sessions.models import Session
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone

from next.realtime import binding as binding_module
from next.realtime.access import Access, AccessCheck
from next.realtime.binding import session_user, stored_session
from next.realtime.limits import heartbeat_seconds
from next.realtime.pool import run_detached
from tests.support.realtime import ServerClock, signed_in_session


_CACHE = "django.contrib.sessions.backends.cache"
_CACHED_DB = "django.contrib.sessions.backends.cached_db"
_FILE = "django.contrib.sessions.backends.file"
_SIGNED = "django.contrib.sessions.backends.signed_cookies"
_ENGINES = ("django.contrib.sessions.backends.db", _CACHE, _CACHED_DB, _FILE, _SIGNED)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(5, 5.0), (0.5, 0.5), (0, 25.0), (-1, 25.0), (math.inf, 25.0), (True, 25.0)],
)
def test_heartbeat_seconds_invalid_value_falls_back_to_default(
    value: object, expected: float
) -> None:
    with override_settings(NEXT_FRAMEWORK={"REALTIME": {"HEARTBEAT": value}}):
        assert heartbeat_seconds() == expected


def test_heartbeat_seconds_non_mapping_setting_falls_back_to_default() -> None:
    with override_settings(NEXT_FRAMEWORK={"REALTIME": "invalid"}):
        assert heartbeat_seconds() == 25.0


def test_access_check_throttles_the_authorizer_to_one_call_per_interval(
    clock: ServerClock,
) -> None:
    calls: list[str] = []

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


def test_concurrent_async_rechecks_share_one_evaluation(clock: ServerClock) -> None:
    calls: list[str] = []
    entered = threading.Event()
    release = threading.Event()

    def authorizer(_user: object, topic: str) -> bool:
        calls.append(topic)
        entered.set()
        release.wait(5)
        return True

    check = AccessCheck(
        ("a",),
        authorizer=authorizer,
        session_key=None,
        user=AnonymousUser(),
        interval=10,
        clock=clock,
    )

    async def scenario() -> list[Access]:
        first = asyncio.create_task(check.acheck())
        await asyncio.to_thread(entered.wait, 5)
        second = asyncio.create_task(check.acheck())
        await asyncio.sleep(0.01)
        release.set()
        return [await first, await second]

    assert asyncio.run(scenario()) == [Access.GRANTED, Access.GRANTED]
    assert calls == ["a"]


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


def test_access_check_denied_topic_is_not_asked_again(clock: ServerClock) -> None:
    calls: list[str] = []

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


@pytest.mark.django_db(transaction=True)
def test_access_check_rechecks_at_the_deadline_and_follows_a_renewed_session(
    clock: ServerClock,
) -> None:
    user, session = signed_in_session()
    rows = Session.objects.filter(session_key=session.session_key)
    rows.update(expire_date=timezone.now() + timedelta(seconds=10))
    calls: list[str] = []

    def authorizer(_user: object, topic: str) -> bool:
        calls.append(topic)
        return True

    check = AccessCheck(
        ("a",),
        authorizer=authorizer,
        session_key=session.session_key,
        user=user,
        interval=10_000,
        clock=clock,
    )
    assert check.check() is Access.GRANTED
    clock.now = 5
    assert check.check() is Access.GRANTED
    assert calls == ["a"]
    rows.update(expire_date=timezone.now() + timedelta(seconds=1000))
    clock.now = 10
    assert check.check() is Access.GRANTED
    assert calls == ["a", "a"]
    clock.now = 500
    assert check.check() is Access.GRANTED
    assert calls == ["a", "a"]
    rows.update(expire_date=timezone.now() - timedelta(seconds=1))
    clock.now = 1010
    assert check.check() is Access.SESSION_REVOKED


@pytest.mark.django_db(transaction=True)
def test_access_check_revokes_an_anonymous_session_expired_after_open(
    clock: ServerClock,
) -> None:
    session = SessionStore()
    session.create()
    check = AccessCheck(
        ("a",),
        authorizer=None,
        session_key=session.session_key,
        user=AnonymousUser(),
        interval=1,
        clock=clock,
    )
    assert check.check() is Access.GRANTED
    Session.objects.filter(session_key=session.session_key).update(
        expire_date=timezone.now() - timedelta(seconds=1)
    )
    clock.now = 2
    assert check.check() is Access.SESSION_REVOKED


@pytest.mark.django_db(transaction=True)
def test_bound_access_check_revokes_a_session_gone_before_its_first_check() -> None:
    check = AccessCheck(
        ("a",),
        authorizer=None,
        session_key="stale-session-key-0001",
        user=AnonymousUser(),
        interval=1,
    )
    check.bind()
    assert check.check() is Access.SESSION_REVOKED


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
    user, session = signed_in_session()
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
    user, session = signed_in_session()
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
def test_access_check_unbinds_a_stale_anonymous_cookie_at_open(
    clock: ServerClock,
) -> None:
    seen: list[object] = []

    def authorizer(user: object, _topic: str) -> bool:
        seen.append(user)
        return True

    check = AccessCheck(
        ("a",),
        authorizer=authorizer,
        session_key="stale-session-key-0001",
        user=AnonymousUser(),
        interval=1,
        expires_at=0.5,
        clock=clock,
    )
    assert check.check() is Access.GRANTED
    assert check._session_key is None
    assert check._expires_at is None
    clock.now = 2
    assert check.check() is Access.GRANTED
    assert all(not getattr(user, "is_authenticated", True) for user in seen)


@pytest.mark.django_db(transaction=True)
def test_access_check_revokes_an_anonymous_session_lost_after_open(
    clock: ServerClock,
) -> None:
    session = SessionStore()
    session.create()
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


_OLD_SECRET = "old-secret-key-for-session-hashes-0000000000000000"
_NEW_SECRET = "new-secret-key-for-session-hashes-1111111111111111"
_WRITES = ("cycle_key", "flush", "save", "delete")


def _stored_rows() -> dict[str, str]:
    return dict(Session.objects.values_list("session_key", "session_data"))


@pytest.mark.django_db(transaction=True)
def test_session_user_loads_the_logged_in_user() -> None:
    user, session = signed_in_session()
    loaded = session_user(SessionStore(session_key=session.session_key))
    assert getattr(loaded, "pk", None) == user.pk
    assert getattr(loaded, "is_authenticated", False) is True


@pytest.mark.django_db(transaction=True)
def test_session_user_of_a_session_without_login_is_anonymous() -> None:
    session = SessionStore()
    session["note"] = "kept"
    session.create()
    assert isinstance(session_user(SessionStore(session.session_key)), AnonymousUser)


@pytest.mark.django_db(transaction=True)
def test_session_user_matching_a_fallback_secret_leaves_the_store_untouched() -> None:
    with override_settings(SECRET_KEY=_OLD_SECRET, SECRET_KEY_FALLBACKS=[]):
        user, session = signed_in_session()
    key = session.session_key
    rows = _stored_rows()
    store = SessionStore(session_key=key)
    with (
        override_settings(SECRET_KEY=_NEW_SECRET, SECRET_KEY_FALLBACKS=[_OLD_SECRET]),
        patch.multiple(SessionStore, **dict.fromkeys(_WRITES, DEFAULT)) as writes,
    ):
        loaded = session_user(store)
    assert getattr(loaded, "pk", None) == user.pk
    assert all(not writes[name].called for name in _WRITES)
    assert store.session_key == key
    assert store.modified is False
    assert _stored_rows() == rows


@pytest.mark.django_db(transaction=True)
def test_session_user_with_a_stale_hash_is_anonymous_without_a_flush() -> None:
    user, session = signed_in_session()
    user.set_password("changed")
    user.save(update_fields=["password"])
    key = session.session_key
    rows = _stored_rows()
    store = SessionStore(session_key=key)
    with patch.multiple(SessionStore, **dict.fromkeys(_WRITES, DEFAULT)) as writes:
        loaded = session_user(store)
    assert isinstance(loaded, AnonymousUser)
    assert all(not writes[name].called for name in _WRITES)
    assert store.session_key == key
    assert _stored_rows() == rows


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("mode", ["sync", "async"])
def test_access_check_under_a_fallback_secret_stays_granted_and_keeps_the_session(
    mode: str,
) -> None:
    with override_settings(SECRET_KEY=_OLD_SECRET, SECRET_KEY_FALLBACKS=[]):
        user, session = signed_in_session()
    rows = _stored_rows()
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
    with override_settings(SECRET_KEY=_NEW_SECRET, SECRET_KEY_FALLBACKS=[_OLD_SECRET]):
        verdict = check.check() if mode == "sync" else asyncio.run(check.acheck())
    assert verdict is Access.GRANTED
    assert [getattr(current, "pk", None) for current in seen] == [user.pk]
    assert _stored_rows() == rows


@pytest.fixture()
def file_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    monkeypatch.setattr(FileStore, "_storage_path", str(tmp_path), raising=False)
    with override_settings(SESSION_ENGINE=_FILE, SESSION_FILE_PATH=str(tmp_path)):
        yield tmp_path


def _files(directory: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in directory.iterdir()}


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("store_class", [SessionStore, CachedDbStore])
def test_stored_session_reads_the_deadline_of_the_stored_row(
    store_class: type[SessionStore],
) -> None:
    session = store_class()
    session["note"] = "kept"
    session.create()
    Session.objects.filter(session_key=session.session_key).update(
        expire_date=timezone.now() + timedelta(seconds=10)
    )
    with override_settings(
        SESSION_ENGINE=_CACHED_DB
        if store_class is CachedDbStore
        else "django.contrib.sessions.backends.db"
    ):
        live = stored_session(session.session_key)
    assert live is not None
    user, left = live
    assert isinstance(user, AnonymousUser)
    assert left is not None
    assert 0 < left <= 10


@pytest.mark.django_db(transaction=True)
def test_stored_session_of_a_row_deleted_after_the_load_is_gone() -> None:
    session = SessionStore()
    session.create()
    key = session.session_key
    loaded = SessionStore.load

    def load_then_delete(store: SessionStore) -> dict[str, object]:
        data = loaded(store)
        Session.objects.filter(session_key=key).delete()
        return data

    with patch.object(SessionStore, "load", load_then_delete):
        assert stored_session(key) is None


@pytest.mark.django_db(transaction=True)
def test_stored_session_of_an_expired_row_is_gone_and_left_in_place() -> None:
    session = SessionStore()
    session.create()
    rows = Session.objects.filter(session_key=session.session_key)
    rows.update(expire_date=timezone.now() - timedelta(seconds=1))
    stored = _stored_rows()
    assert stored_session(session.session_key) is None
    assert _stored_rows() == stored


@override_settings(SESSION_ENGINE=_CACHE)
def test_stored_session_of_a_cache_session_has_no_deadline() -> None:
    session = CacheStore()
    session.create()
    assert stored_session(session.session_key) == (AnonymousUser(), None)


@override_settings(SESSION_ENGINE=_CACHE)
def test_stored_session_of_a_cache_session_reads_an_explicit_expiry_date() -> None:
    session = CacheStore()
    session.set_expiry(timezone.now() + timedelta(seconds=60))
    session.create()
    live = stored_session(session.session_key)
    assert live is not None
    assert live[1] is not None
    assert 0 < live[1] <= 60


@override_settings(SESSION_ENGINE=_CACHE)
def test_stored_session_of_a_cache_session_past_its_expiry_date_is_gone() -> None:
    session = CacheStore()
    session.create()
    past = (timezone.now() - timedelta(seconds=1)).isoformat()
    cache.set(session.cache_key, {"_session_expiry": past}, 60)
    assert stored_session(session.session_key) is None


@pytest.mark.django_db(transaction=True)
def test_stored_session_of_a_file_session_has_no_deadline(file_sessions: Path) -> None:
    session = FileStore()
    session.create()
    files = _files(file_sessions)
    assert stored_session(session.session_key) == (AnonymousUser(), None)
    assert _files(file_sessions) == files


@pytest.mark.django_db(transaction=True)
def test_stored_session_of_an_expired_file_session_writes_no_new_file(
    file_sessions: Path,
) -> None:
    session = FileStore()
    session.create()
    (path,) = file_sessions.iterdir()
    old = path.stat().st_mtime - session.get_session_cookie_age() - 10
    os.utime(path, (old, old))
    files = _files(file_sessions)
    assert stored_session(session.session_key) is None
    assert _files(file_sessions) == files


@pytest.mark.django_db(transaction=True)
def test_stored_session_of_a_corrupt_file_session_is_anonymous_and_untouched(
    file_sessions: Path,
) -> None:
    session = FileStore()
    session.create()
    (path,) = file_sessions.iterdir()
    path.write_text("not a signed session", encoding="ascii")
    files = _files(file_sessions)
    assert stored_session(session.session_key) == (AnonymousUser(), None)
    assert _files(file_sessions) == files


@pytest.mark.usefixtures("file_sessions")
def test_stored_session_of_a_key_outside_the_file_alphabet_is_gone() -> None:
    assert stored_session("../../outside-the-store") is None


@pytest.mark.django_db(transaction=True)
def test_read_only_store_drops_every_write() -> None:
    session = SessionStore()
    session["note"] = "kept"
    session.create()
    key = session.session_key
    stored = _stored_rows()
    store = binding_module._read_only(SessionStore)(key)
    store["note"] = "changed"
    store.save()
    store.delete()
    store.create()
    assert store.session_key is None
    assert _stored_rows() == stored


class _Engines:
    class SessionStore(SessionStore):
        pass


def test_read_only_store_keeps_the_signing_salt_of_a_nested_engine() -> None:
    engine = _Engines.SessionStore
    assert binding_module._read_only(engine)().key_salt == engine().key_salt


@pytest.fixture(params=_ENGINES)
def session_engine(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[str]:
    monkeypatch.setattr(FileStore, "_storage_path", str(tmp_path), raising=False)
    with override_settings(
        SESSION_ENGINE=request.param, SESSION_FILE_PATH=str(tmp_path)
    ):
        yield request.param


@pytest.mark.django_db(transaction=True)
def test_stored_session_loads_the_logged_in_user_of_every_engine(
    session_engine: str,
) -> None:
    user, session = signed_in_session()
    key = session.session_key
    live = stored_session(key)
    assert live is not None
    assert getattr(live[0], "pk", None) == user.pk


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("mode", ["sync", "async"])
def test_access_check_keeps_a_logged_in_session_of_every_engine_granted(
    session_engine: str, mode: str, clock: ServerClock
) -> None:
    user, session = signed_in_session()
    key = session.session_key
    seen: list[object] = []

    def authorizer(current: object, _topic: str) -> bool:
        seen.append(getattr(current, "pk", None))
        return True

    check = AccessCheck(
        ("a",),
        authorizer=authorizer,
        session_key=key,
        user=user,
        interval=1,
        clock=clock,
    )
    check.bind()
    for now in (0, 2, 4):
        clock.now = now
        verdict = check.check() if mode == "sync" else asyncio.run(check.acheck())
        assert verdict is Access.GRANTED
    assert seen == [user.pk] * 3


@pytest.mark.django_db(transaction=True)
def test_access_check_revokes_a_changed_password_under_every_engine(
    session_engine: str, clock: ServerClock
) -> None:
    user, session = signed_in_session()
    key = session.session_key
    check = AccessCheck(
        ("a",), authorizer=None, session_key=key, user=user, interval=1, clock=clock
    )
    assert check.check() is Access.GRANTED
    user.set_password("changed")
    user.save(update_fields=["password"])
    clock.now = 2
    assert check.check() is Access.SESSION_REVOKED


@pytest.mark.django_db(transaction=True)
@override_settings(SESSION_ENGINE=_SIGNED)
def test_stored_session_of_a_tampered_signed_cookie_is_gone() -> None:
    _user, session = signed_in_session()
    key = session.session_key
    assert stored_session(f"{key}x") is None


@pytest.mark.django_db(transaction=True)
@override_settings(SESSION_ENGINE=_SIGNED, SESSION_COOKIE_AGE=-1)
def test_stored_session_of_an_expired_signed_cookie_is_gone() -> None:
    _user, session = signed_in_session()
    key = session.session_key
    assert stored_session(key) is None
