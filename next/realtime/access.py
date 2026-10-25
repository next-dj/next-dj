"""Throttled recheck of session liveness and topic grants for a live stream.

Both transports share it, so a revoked session or topic closes SSE and WS alike.
"""

import enum
import functools
import math
import time
from collections.abc import Callable, Mapping, Sequence
from typing import Any, cast, override

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib import auth
from django.contrib.sessions.backends.base import SessionBase
from django.contrib.sessions.backends.db import SessionStore as DatabaseStore
from django.db import close_old_connections
from django.http import HttpRequest, parse_cookie
from django.utils.module_loading import import_string

from next.conf import next_framework_settings


_DEFAULT_HEARTBEAT = 25.0

type TopicAuthorizer = Callable[[object, str], bool]


class Access(enum.Enum):
    """Outcome of one access recheck."""

    GRANTED = "granted"
    SESSION_REVOKED = "session_revoked"
    TOPIC_DENIED = "topic_denied"


def heartbeat_seconds() -> float:
    """Return the configured realtime heartbeat, or the default for a bad value."""
    realtime = getattr(next_framework_settings, "REALTIME", {})
    value = (
        realtime.get("HEARTBEAT", _DEFAULT_HEARTBEAT)
        if isinstance(realtime, Mapping)
        else _DEFAULT_HEARTBEAT
    )
    if (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value > 0
    ):
        return float(value)
    return _DEFAULT_HEARTBEAT


async def run_detached[**P, R](
    func: Callable[P, R], *args: P.args, **kwargs: P.kwargs
) -> R:
    """Run blocking subscriber code on the shared pool instead of the one sync thread.

    A long-lived socket opens no thread-sensitive context, so such calls serialize.
    """

    def call() -> R:
        close_old_connections()
        try:
            return func(*args, **kwargs)
        finally:
            close_old_connections()

    return await sync_to_async(call, thread_sensitive=False)()


class AccessCheck:
    """Recheck one connection's session and topic grants at most once per interval."""

    def __init__(
        self,
        topics: Sequence[str],
        *,
        authorizer: TopicAuthorizer | None,
        session_key: str | None,
        user: object,
        interval: float,
        expires_at: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Remember the grant baseline taken when the connection opened."""
        self._topics = tuple(topics)
        self._authorizer = authorizer
        self._session_key = session_key
        self._user = user
        self._interval = interval
        self._expires_at = expires_at
        self._clock = clock
        self._due_at = -math.inf
        self._verdict = Access.GRANTED
        self._bound = False

    def bind(self) -> None:
        """Revoke once the session is gone, since a handshake already found it live."""
        self._bound = True

    def check(self) -> Access:
        """Recheck in the calling thread when the interval has elapsed."""
        if not self._due():
            return self._verdict
        return self._record(self._evaluate())

    async def acheck(self) -> Access:
        """Recheck on the shared thread pool when the interval has elapsed."""
        if not self._due():
            return self._verdict
        if self._authorizer is None and self._session_key is None:
            return self._record(Access.GRANTED)
        return self._record(await run_detached(self._evaluate))

    def _due(self) -> bool:
        """Whether to evaluate again, at the interval or at the session's deadline."""
        if self._verdict is not Access.GRANTED:
            return False
        now = self._clock()
        deadline = self._expires_at
        return now >= self._due_at or (deadline is not None and now >= deadline)

    def _record(self, verdict: Access) -> Access:
        """Store the verdict and schedule the next evaluation."""
        self._verdict = verdict
        self._due_at = self._clock() + self._interval
        return verdict

    def _evaluate(self) -> Access:
        """Reload the session user, then ask the authorizer about every topic.

        A dead session at open binds nothing for an anonymous visitor's stale cookie.
        """
        user = self._user
        if self._session_key is not None:
            live = self._live_user(self._session_key)
            if live is not None:
                user = live
            elif self._bound or getattr(self._user, "is_authenticated", False):
                return Access.SESSION_REVOKED
            else:
                self._session_key = None
                self._expires_at = None
        self._bound = True
        if self._authorizer is not None:
            for topic in self._topics:
                if self._authorizer(user, topic) is not True:
                    return Access.TOPIC_DENIED
        return Access.GRANTED

    def _live_user(self, session_key: str) -> object | None:
        """Return the session's current user, or None once it is gone or a user swapped.

        The deadline follows the store, so a session renewed over HTTP stays open.
        """
        live = live_session(session_key)
        if live is None:
            return None
        user, left = live
        previous = self._user
        if getattr(previous, "is_authenticated", False) and (
            not getattr(user, "is_authenticated", False)
            or getattr(previous, "pk", None) != getattr(user, "pk", None)
        ):
            return None
        self._expires_at = None if left is None else self._clock() + left
        return user


class _SessionSnapshot(SessionBase):
    """An in-memory copy of a stored session that ignores key rotation and flush."""

    def __init__(self, store: SessionBase) -> None:
        """Copy the stored data under the same key, loading it once."""
        super().__init__(store.session_key)
        self._data = dict(store.items())

    @override
    def load(self) -> dict[str, Any]:
        return self._data

    @override
    def cycle_key(self) -> None:
        return

    @override
    def flush(self) -> None:
        return


def session_user(store: SessionBase) -> object:
    """Load a session's user the way Django's `get_user` does, without writing to it.

    A socket has no response to carry a rotated cookie, so rotation stays with HTTP.
    """
    request = HttpRequest()
    request.session = _SessionSnapshot(store)
    return auth.get_user(request)


class _ReadOnlyStore(SessionBase):
    """Turn the writes a session engine makes while it loads into no-ops.

    The file engine replaces an expired session on load with a new one under a new key.
    """

    @override
    def create(self) -> None:
        self._session_key = None

    @override
    def save(self, must_create: bool = False) -> None:
        return

    @override
    def delete(self, session_key: str | None = None) -> None:
        return


@functools.cache
def _read_only(engine: type[SessionBase]) -> type[SessionBase]:
    """Return the engine's store class with every write turned into a no-op.

    It keeps the engine's qualified name, the salt Django signs the session data with.
    """
    bases = (_ReadOnlyStore, engine)
    namespace = {"__qualname__": engine.__qualname__}
    return cast("type[SessionBase]", type(engine.__name__, bases, namespace))


def _seconds_left(store: SessionBase, expiry: object) -> int | None:
    """Return the seconds a loaded session has left, or None when the store cannot tell.

    A database row keeps the deadline of every session, a cache keeps it out of reach.
    """
    if isinstance(store, DatabaseStore):
        rows = store.get_model_class().objects.filter(session_key=store.session_key)
        stored = rows.values_list("expire_date", flat=True).first()
        return 0 if stored is None else store.get_expiry_age(expiry=stored)
    if isinstance(expiry, str):
        return store.get_expiry_age(expiry=expiry)
    return None


def cookie_session_key(cookie: str) -> str | None:
    """Return the session key a raw `Cookie` header carries, or None without one."""
    return parse_cookie(cookie).get(settings.SESSION_COOKIE_NAME) or None


def _live_store(session_key: str) -> tuple[SessionBase, int | None] | None:
    """Return a stored session and its seconds left, or None once it is gone.

    A failed load drops the key, the one signal signed cookies share with every engine.
    """
    engine = import_string(f"{settings.SESSION_ENGINE}.SessionStore")
    store = _read_only(engine)(session_key)
    expiry = store.get("_session_expiry")
    if store.session_key != session_key:
        return None
    left = _seconds_left(store, expiry)
    if left is not None and left <= 0:
        return None
    return store, left


def session_alive(session_key: str) -> bool:
    """Whether a handshake would find the stored session live, without its user."""
    return _live_store(session_key) is not None


def live_session(session_key: str) -> tuple[object, int | None] | None:
    """Return a stored session's user and seconds left, or None once it is gone."""
    live = _live_store(session_key)
    if live is None:
        return None
    store, left = live
    return session_user(store), left


__all__ = [
    "Access",
    "AccessCheck",
    "TopicAuthorizer",
    "cookie_session_key",
    "heartbeat_seconds",
    "live_session",
    "run_detached",
    "session_alive",
    "session_user",
]
