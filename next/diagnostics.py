"""Containment of failures in user and third-party code, logged at a bounded rate."""

from __future__ import annotations

import logging
import threading
from contextvars import ContextVar, copy_context
from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING, Final
from weakref import WeakSet

from django.core.exceptions import BadRequest, PermissionDenied, SuspiciousOperation
from django.http import Http404

from next.caches import BoundedCache
from next.conf.settings import fail_loudly
from next.conf.signals import settings_reloaded


if TYPE_CHECKING:
    from collections.abc import Callable, Hashable


# A view raises these to answer 400, 403 or 404, and Django converts each into its own
# response and log entry, so a containment must let them propagate.
INTENDED_EXCEPTIONS: tuple[type[BaseException], ...] = (
    Http404,
    PermissionDenied,
    SuspiciousOperation,
    BadRequest,
)

QUIET_PERIOD: Final = 600.0
"""Seconds a logged failure key stays quiet before its next occurrence is logged."""

_DEGRADED: ContextVar[bool] = ContextVar("next_degraded", default=False)


def watch_degraded() -> None:
    """Mark the current render as not degraded.

    A page view calls it before it renders, so `degraded` reports on that render alone.
    """
    _DEGRADED.set(False)


def degraded() -> bool:
    """Return whether a failure was contained since the last `watch_degraded` call.

    A degraded page lacks the output of its failing source, so its response must not
    be stored in a shared cache.
    """
    return _DEGRADED.get()


def mark_degraded() -> None:
    """Mark the current render as degraded, for a memoised fallback it reuses."""
    _DEGRADED.set(True)


def isolated_build[T](build: Callable[[], T]) -> tuple[T, bool]:
    """Return what `build` returns and whether it contained a failure.

    The build runs in a copy of the context, so the current render is not marked by
    it. A memo stores the flag and calls `mark_degraded` on every render that reads
    the value, so each of those renders is treated as the first one was.
    """
    context = copy_context()
    context.run(watch_degraded)
    value = context.run(build)
    return value, context.run(degraded)


_FAILED = (
    "%s failed to report its %s, so it contributes nothing to the watcher. "
    f"The same failure is not logged again for {QUIET_PERIOD:.0f} seconds or until "
    "the framework is reconfigured."
)

_MALFORMED = (
    "%s reported %s of the wrong type, so it contributes nothing to the watcher. "
    f"The same failure is not logged again for {QUIET_PERIOD:.0f} seconds or until "
    "the framework is reconfigured."
)

_SUPPRESSED = " The same failure occurred %d more times since it was last logged."


@dataclass(slots=True)
class _Report:
    """When a key was last logged and how often it failed since then."""

    logged_at: float
    suppressed: int = 0


_LOGS: WeakSet[FailureLog] = WeakSet()


class FailureLog:
    """Log a failure key once, then again after each quiet period it keeps failing in.

    A failing user callable runs on every request, so logging every occurrence would
    repeat one traceback per request, while logging it only once would hide a failure
    that recurs for the life of the process. The record that ends a quiet period
    carries the number of occurrences left out in its `suppressed` attribute and in
    its message. Keys are held in a bounded cache, and a key is still drawn from a
    finite set such as a source path or a setting name, never from request data.
    """

    def __init__(self, logger: logging.Logger) -> None:
        """Log through `logger` and forget every reported key on `settings_reloaded`."""
        self._logger = logger
        self._reports: BoundedCache[Hashable, _Report] = BoundedCache()
        self._lock = threading.Lock()
        _LOGS.add(self)
        settings_reloaded.connect(self.clear)

    def clear(self, **kwargs: object) -> None:
        """Forget every reported key, so each failure is logged again."""
        self._reports.clear()

    def _due(self, key: Hashable) -> int | None:
        """Return the occurrences left out since `key` was logged, `None` to stay quiet.

        Every call records one occurrence of `key`.
        """
        now = monotonic()
        with self._lock:
            report = self._reports.get(key)
            if report is None:
                self._reports[key] = _Report(now)
                return 0
            if now - report.logged_at < QUIET_PERIOD:
                report.suppressed += 1
                return None
            suppressed = report.suppressed
            report.logged_at = now
            report.suppressed = 0
            return suppressed

    def first_failure(self, *key: Hashable) -> bool:
        """Return whether a failure under `key` is logged now, recording the occurrence.

        It answers `True` for the first occurrence and for the first one after each
        quiet period, so a caller that logs on its own logs at the same rate.
        """
        return self._due(key) is not None

    def _log(
        self,
        level: int,
        key: Hashable,
        message: str,
        args: tuple[object, ...],
        exc_info: BaseException | None = None,
    ) -> None:
        """Log `message` when `key` is due, naming the occurrences left out."""
        suppressed = self._due(key)
        if suppressed is None:
            return
        if suppressed:
            # The appended count turns on %-formatting, so a literal % is escaped.
            text = message if args else message.replace("%", "%%")
            message, args = text + _SUPPRESSED, (*args, suppressed)
        self._logger.log(
            level, message, *args, exc_info=exc_info, extra={"suppressed": suppressed}
        )

    def contain(
        self,
        exc: BaseException,
        key: Hashable,
        message: str,
        *args: object,
        pass_through: tuple[type[BaseException], ...] = INTENDED_EXCEPTIONS,
    ) -> None:
        """Re-raise `exc` under `DEBUG` or `STRICT_LOADING`, else log it and degrade.

        Call it from the `except` block that caught `exc`, then return the caller's
        fallback. An exception in `pass_through` is re-raised in every mode. The
        default, `INTENDED_EXCEPTIONS`, lets Django answer with its own 400, 403 or
        404. Django converts those exceptions only when a view raises them, so a call
        site outside any view, such as URL resolution or a middleware, passes
        `pass_through=()` and contains them like any other failure. A re-raised
        failure carries `message` as a note, so the technical error page names the
        source and the fix. Otherwise the render is marked degraded, and `message` is
        logged with the traceback on the first failure of `key` and after each quiet
        period.
        """
        if isinstance(exc, pass_through):
            raise  # noqa: PLE0704 - re-raises the exception the caller is handling
        if fail_loudly():
            exc.add_note(message % args if args else message)
            raise  # noqa: PLE0704 - re-raises the exception the caller is handling
        _DEGRADED.set(True)
        self._log(logging.ERROR, (key,), message, args, exc)

    def warn(self, key: Hashable, message: str, *args: object) -> None:
        """Log `message` for `key` at a bound rate, for a failure with no traceback."""
        self._log(logging.WARNING, (key,), message, args)


def reset_failure_logs() -> None:
    """Forget the reported keys of every failure log, so each failure is logged again.

    A test that asserts on a log record calls it, so the outcome does not depend on
    whether an earlier test in the same process logged the same key.
    """
    for log in list(_LOGS):
        log.clear()


class BackendReadLog(FailureLog):
    """Read a value from a backend, returning a default when the read fails.

    A backend is read on every reloader tick and static lookup, so a failing read is
    logged per backend class and subject at the rate of `FailureLog`. Unlike
    `contain`, a failing read is never re-raised, under `DEBUG` included.
    """

    def read[T](
        self,
        backend: object,
        subject: str,
        read: Callable[[], T],
        *,
        valid: Callable[[T], bool],
        default: T,
    ) -> T:
        """Return what `read` returns, `default` when it raises or `valid` refuses it.

        `valid` runs inside the same guard, since inspecting the result can run backend
        code that raises.
        """
        source = type(backend).__name__
        try:
            answer = read()
            sound = valid(answer)
        except Exception as exc:  # noqa: BLE001 - logged, a backend may raise anything
            self._log(logging.ERROR, (source, subject), _FAILED, (source, subject), exc)
            return default
        if not sound:
            self._log(logging.ERROR, (source, subject), _MALFORMED, (source, subject))
            return default
        return answer


__all__ = [
    "INTENDED_EXCEPTIONS",
    "QUIET_PERIOD",
    "BackendReadLog",
    "FailureLog",
    "degraded",
    "isolated_build",
    "mark_degraded",
    "reset_failure_logs",
    "watch_degraded",
]
