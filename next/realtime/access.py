"""Throttled recheck of session liveness and topic grants for a live stream.

Both transports share it, so a revoked session or topic closes SSE and WS alike.
"""

import enum
import math
import time
from collections.abc import Callable, Mapping, Sequence

from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib import auth
from django.db import close_old_connections
from django.http import HttpRequest
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

    Channels opens no thread-sensitive context, so thread-sensitive calls serialize.
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
        self._opened = False

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
        """Whether to evaluate again, expiring the session at its deadline."""
        if self._verdict is not Access.GRANTED:
            return False
        now = self._clock()
        if self._expires_at is not None and now >= self._expires_at:
            self._verdict = Access.SESSION_REVOKED
            return False
        return now >= self._due_at

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
            elif self._opened or getattr(self._user, "is_authenticated", False):
                return Access.SESSION_REVOKED
            else:
                self._session_key = None
        self._opened = True
        if self._authorizer is not None:
            for topic in self._topics:
                if self._authorizer(user, topic) is not True:
                    return Access.TOPIC_DENIED
        return Access.GRANTED

    def _live_user(self, session_key: str) -> object | None:
        """Return the session's current user, or None after logout, rotation or swap.

        A changed password flushes the session and an inactive user loads as anonymous.
        """
        engine = import_string(f"{settings.SESSION_ENGINE}.SessionStore")
        store = engine(session_key=session_key)
        if not store.exists(session_key):
            return None
        request = HttpRequest()
        request.session = store
        user = auth.get_user(request)
        if store.session_key != session_key:
            return None
        previous = self._user
        if getattr(previous, "is_authenticated", False) and (
            not user.is_authenticated or getattr(previous, "pk", None) != user.pk
        ):
            return None
        return user


__all__ = [
    "Access",
    "AccessCheck",
    "TopicAuthorizer",
    "heartbeat_seconds",
    "run_detached",
]
