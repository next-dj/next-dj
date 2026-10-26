"""Throttled recheck of session liveness and topic grants for a live stream.

Both transports share it, so a revoked session or topic closes SSE and WS alike.
"""

import asyncio
import enum
import math
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from typing import override

from asgiref.sync import sync_to_async
from django.db import close_old_connections

from next.conf import next_framework_settings

from .binding import LiveIdentity, ticket_binding


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
    call = _fresh_links(func, *args, **kwargs)
    return await sync_to_async(call, thread_sensitive=False)()


async def run_pooled[**P, R](
    executor: ThreadPoolExecutor,
    func: Callable[P, R],
    *args: P.args,
    **kwargs: P.kwargs,
) -> R:
    """Run blocking code on its own pool, so it never queues the shared one."""
    call = _fresh_links(func, *args, **kwargs)
    return await sync_to_async(call, thread_sensitive=False, executor=executor)()


def _fresh_links[**P, R](
    func: Callable[P, R], *args: P.args, **kwargs: P.kwargs
) -> Callable[[], R]:
    """Wrap a call in `close_old_connections`, as a request cycle would."""

    def call() -> R:
        close_old_connections()
        try:
            return func(*args, **kwargs)
        finally:
            close_old_connections()

    return call


class AccessCheck:
    """Recheck one SSE request's session and topic grants at most once per interval."""

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
        self._current = user
        self._interval = interval
        self._expires_at = expires_at
        self._clock = clock
        self._due_at = -math.inf
        self._verdict = Access.GRANTED
        self._bound = False
        self._rechecking = asyncio.Lock()

    @property
    def user(self) -> object:
        """Return the user the last granted recheck loaded, the baseline before one."""
        return self._current

    def bind(self) -> None:
        """Revoke once the session is gone, since a handshake already found it live."""
        self._bound = True

    def check(self) -> Access:
        """Recheck in the calling thread when the interval has elapsed."""
        if not self._due():
            return self._verdict
        return self._record(self._evaluate())

    async def acheck(self) -> Access:
        """Recheck on the shared thread pool when the interval has elapsed.

        Concurrent callers share one evaluation, the later ones read its verdict.
        """
        if not self._due():
            return self._verdict
        if self._authorizer is None and self._session_key is None:
            return self._record(Access.GRANTED)
        async with self._rechecking:
            if not self._due():
                return self._verdict
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
        self._current = user
        return Access.GRANTED

    def _live_user(self, key: str) -> object | None:
        """Return the session's current user, or None once it is gone or a user swapped.

        The deadline follows the store, so a session renewed over HTTP stays open.
        """
        live = self.reload(key)
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

    def reload(self, key: str) -> LiveIdentity | None:
        """Return the live user of the session key, as the ticket binding reads it."""
        return ticket_binding().live_session(key)


class SocketAccessCheck(AccessCheck):
    """Recheck a socket, whose `session_key` is the handshake credential instead."""

    @override
    def reload(self, key: str) -> LiveIdentity | None:
        """Return the live user the credential proves, as the binding reads it."""
        return ticket_binding().live(key)


__all__ = [
    "Access",
    "AccessCheck",
    "SocketAccessCheck",
    "TopicAuthorizer",
    "heartbeat_seconds",
    "run_detached",
    "run_pooled",
]
