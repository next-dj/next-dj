"""Containment of failures in user and third-party code, logged at a bounded rate."""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable, Generator, Hashable, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass
from time import monotonic
from typing import TYPE_CHECKING, Final, Self, override
from weakref import WeakSet

from django.core.exceptions import BadRequest, PermissionDenied, SuspiciousOperation
from django.core.signals import got_request_exception
from django.http import Http404
from django.http.multipartparser import MultiPartParserError

from next.caches import FRAMEWORK, BoundedCache
from next.conf.settings import fail_loudly


if TYPE_CHECKING:
    from types import TracebackType

    from django.http import HttpRequest


# Django answers each of these with its own response and log entry, so let them out.
INTENDED_EXCEPTIONS: tuple[type[BaseException], ...] = (
    Http404,
    PermissionDenied,
    SuspiciousOperation,
    BadRequest,
    MultiPartParserError,
)

QUIET_PERIOD: Final = 600.0
"""Seconds a logged failure key stays quiet before its next occurrence is logged."""

_CLEAN: Final = 0
_MARKED: Final = 1

_DEGRADED: ContextVar[int] = ContextVar("next_degraded", default=_CLEAN)


def watch_degraded() -> None:
    """Mark the current render as not degraded.

    A page view calls it before it renders, so `degraded` reports on that render alone.
    """
    _DEGRADED.set(_CLEAN)


def degraded() -> bool:
    """Return whether a failure was contained since the last `watch_degraded` call.

    A degraded page lacks its failing source's output, so no shared cache stores it.
    """
    return _DEGRADED.get() != _CLEAN


def degraded_status() -> int | None:
    """Return the status a contained failure gave the response, `None` for none."""
    held = _DEGRADED.get()
    return held if held > _MARKED else None


def mark_degraded(status: int | None = None) -> None:
    """Mark the current render as degraded, answering with `status` when one is given.

    A plain mark keeps a status an earlier failure of the same render set.
    """
    if status is not None:
        _DEGRADED.set(status)
    elif _DEGRADED.get() == _CLEAN:
        _DEGRADED.set(_MARKED)


@contextmanager
def status_kept() -> Generator[None]:
    """Keep a status a failure contained in the block sets off the response.

    The render still reads as degraded, so no shared cache stores it.
    """
    before = _DEGRADED.get()
    try:
        yield
    finally:
        if _DEGRADED.get() != before:
            _DEGRADED.set(max(before, _MARKED))


def isolated_build[T](build: Callable[[], T]) -> tuple[T, bool]:
    """Return what `build` returns and whether it contained a failure.

    It runs in a copied context, and a memo replays the flag through `mark_degraded`.
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


type Detail = Callable[[Exception], tuple[object, ...]]
"""Message args a guard draws from the exception, never its key, which stays bounded."""


def type_name(exc: Exception) -> tuple[str]:
    """Return the type name of `exc`, the detail of a guard whose message names it."""
    return (type(exc).__name__,)


def exception_text(exc: Exception) -> tuple[str]:
    """Return the text of `exc`, the detail of a guard whose message names it."""
    return (str(exc),)


class Capture:
    """Hold an exception of `catch` the block raises in `error`, and suppress it."""

    __slots__ = ("_catch", "error")

    def __init__(self, catch: tuple[type[Exception], ...] = (Exception,)) -> None:
        """Catch the exceptions of `catch`, every other one propagating."""
        self._catch = catch
        self.error: Exception | None = None

    @property
    def failed(self) -> bool:
        """Whether the block raised an exception the boundary caught."""
        return self.error is not None

    def __enter__(self) -> Self:
        """Enter the block."""
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        """Answer whether the exception of the block is suppressed."""
        if exc is None or not isinstance(exc, self._catch):
            return False
        return self._held(exc)

    def _held(self, exc: Exception) -> bool:
        """Hold `exc` and suppress it."""
        self.error = exc
        return True


class Guard(Capture):
    """Contain a failure of the block, the one boundary of the failure policy.

    It re-raises with a note under `DEBUG` or `STRICT_LOADING`, else degrades a render.
    """

    __slots__ = ("_args", "_detail", "_key", "_log", "_message", "_pass_through")

    def __init__(
        self,
        log: FailureLog,
        key: tuple[Hashable, ...],
        message: str,
        args: tuple[object, ...],
        catch: tuple[type[Exception], ...],
        pass_through: tuple[type[BaseException], ...],
        detail: Detail | None,
    ) -> None:
        """Contain `catch` but not `pass_through`, logging under `key` through `log`."""
        self._catch = catch
        self.error = None
        self._log = log
        self._key = key
        self._message = message
        self._args = args
        self._pass_through = pass_through
        self._detail = detail

    @override
    def _held(self, exc: Exception) -> bool:
        """Apply the failure policy to `exc`, answering whether it is suppressed."""
        if isinstance(exc, self._pass_through):
            return False
        args = self._args if self._detail is None else (*self._args, *self._detail(exc))
        if fail_loudly():
            exc.add_note(self._message % args if args else self._message)
            return False
        mark_degraded()
        self._log.emit(logging.ERROR, self._key, self._message, args, exc)
        return super()._held(exc)


_LOGS: WeakSet[FailureLog] = WeakSet()


class FailureLog:
    """Log a failure key once, then again after each quiet period it keeps failing in.

    A failing callable runs per request, so one key is logged at a bounded rate.
    """

    def __init__(self, logger: logging.Logger) -> None:
        """Log through `logger` and forget every reported key on `settings_reloaded`."""
        self._logger = logger
        self._reports: BoundedCache[Hashable, _Report] = BoundedCache()
        self._lock = threading.Lock()
        _LOGS.add(self)

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
        """Return whether a failure under `key` is logged now, recording it."""
        return self._due(key) is not None

    def emit(
        self,
        level: int,
        key: Hashable,
        message: str,
        args: tuple[object, ...],
        exc_info: BaseException | None = None,
        *,
        extra: Mapping[str, object] | None = None,
        quiet: int | None = None,
    ) -> None:
        """Log `message` when `key` is due, naming the occurrences left out.

        A `quiet` level logs an occurrence the quiet period leaves out at that level.
        """
        suppressed = self._due(key)
        if suppressed is None:
            if quiet is not None:
                self._logger.log(quiet, message, *args, exc_info=exc_info, extra=extra)
            return
        if suppressed:
            # The appended count turns on %-formatting, so a literal % is escaped.
            text = message if args else message.replace("%", "%%")
            message, args = text + _SUPPRESSED, (*args, suppressed)
        self._logger.log(
            level,
            message,
            *args,
            exc_info=exc_info,
            extra={**(extra or {}), "suppressed": suppressed},
        )

    def guard(
        self,
        key: Hashable,
        message: str,
        *args: object,
        catch: tuple[type[Exception], ...] = (Exception,),
        pass_through: tuple[type[BaseException], ...] = INTENDED_EXCEPTIONS,
        detail: Detail | None = None,
    ) -> Guard:
        """Return the boundary that contains a failure of its block under `key`.

        A call site outside any view passes `pass_through=()`.
        """
        return Guard(self, (key,), message, args, catch, pass_through, detail)

    def warn(self, key: Hashable, message: str, *args: object) -> None:
        """Log `message` for `key` at a bound rate, for a failure with no traceback."""
        self.emit(logging.WARNING, (key,), message, args)


def reset_failure_logs(**kwargs: object) -> None:
    """Forget the reported keys of every failure log, so each failure is logged again.

    A settings reload calls it, and so does a test that asserts on a log record.
    """
    for log in list(_LOGS):
        log.clear()


FRAMEWORK.attach(reset_failure_logs)


request_log: Final = FailureLog(logging.getLogger("django.request"))
"""The record of an exception a response survived, where Django logs an uncaught one."""

_SERVER_ERROR: Final = 500


def _rethrow(error: BaseException) -> None:
    raise error


def _announce_exception(error: Exception, request: HttpRequest | None) -> None:
    """Send `got_request_exception` while `error` is the exception being handled.

    A receiver reads `sys.exc_info()`, so a settled exception is raised here once more.
    """
    if not got_request_exception.has_listeners():
        return
    if sys.exception() is error:
        got_request_exception.send_robust(sender=None, request=request)
        return
    traceback, context = error.__traceback__, error.__context__
    try:
        _rethrow(error)
    except type(error):
        got_request_exception.send_robust(sender=None, request=request)
    finally:
        error.__traceback__, error.__context__ = traceback, context


def report_request_exception(
    error: Exception,
    request: HttpRequest | None,
    key: Hashable,
    message: str,
    args: tuple[object, ...],
    *,
    extra: Mapping[str, object] | None = None,
) -> None:
    """Report an exception a response survived as Django reports an uncaught one.

    Every occurrence sends `got_request_exception`, the record keeps the bounded rate.
    """
    _announce_exception(error, request)
    request_log.emit(
        logging.ERROR,
        key,
        message,
        args,
        error,
        extra={**(extra or {}), "status_code": _SERVER_ERROR, "request": request},
        quiet=logging.DEBUG,
    )


class BackendReadLog(FailureLog):
    """Read a value from a backend, returning a default when the read fails.

    It never re-raises, under `DEBUG` included, and never degrades the render.
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

        `valid` runs inside the guard, since inspecting the result can run backend code.
        """
        capture = Capture()
        with capture:
            answer = read()
            sound = valid(answer)
        if capture.error is not None or not sound:
            key = (type(backend).__name__, subject)
            message = _MALFORMED if capture.error is None else _FAILED
            self.emit(logging.ERROR, key, message, key, capture.error)
            return default
        return answer


__all__ = [
    "INTENDED_EXCEPTIONS",
    "QUIET_PERIOD",
    "BackendReadLog",
    "Capture",
    "Detail",
    "FailureLog",
    "Guard",
    "degraded",
    "degraded_status",
    "exception_text",
    "isolated_build",
    "mark_degraded",
    "report_request_exception",
    "request_log",
    "reset_failure_logs",
    "status_kept",
    "type_name",
    "watch_degraded",
]
