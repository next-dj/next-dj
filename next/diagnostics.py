"""Containment of failures in user and third-party code, each cause logged once."""

from collections.abc import Callable, Hashable
from contextvars import ContextVar
from logging import Logger

from django.core.exceptions import BadRequest, PermissionDenied, SuspiciousOperation
from django.http import Http404

from next.conf.settings import fail_loudly
from next.conf.signals import settings_reloaded


# A view raises these to answer 400, 403 or 404, and Django converts each into its own
# response and log entry, so a containment must let them propagate.
INTENDED_EXCEPTIONS: tuple[type[BaseException], ...] = (
    Http404,
    PermissionDenied,
    SuspiciousOperation,
    BadRequest,
)


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


_FAILED = (
    "%s failed to report its %s, so it contributes nothing to the watcher. "
    "The same failure is not logged again until the framework is reconfigured."
)

_MALFORMED = (
    "%s reported %s of the wrong type, so it contributes nothing to the watcher. "
    "The same failure is not logged again until the framework is reconfigured."
)


class FailureLog:
    """Log each failure key once until the framework settings are reloaded.

    A failing user callable runs on every request, so logging every occurrence would
    repeat one traceback per request. The set of reported keys has no bound, so a key
    is drawn from a finite set such as a source path or a setting name, never from
    request data.
    """

    def __init__(self, logger: Logger) -> None:
        """Log through `logger` and forget every reported key on `settings_reloaded`."""
        self._logger = logger
        self._reported: set[Hashable] = set()
        settings_reloaded.connect(self.clear)

    def clear(self, **kwargs: object) -> None:
        """Forget every reported key, so each failure is logged again."""
        self._reported.clear()

    def first_failure(self, *key: Hashable) -> bool:
        """Return whether `key` is not reported yet, recording it as reported."""
        if key in self._reported:
            return False
        self._reported.add(key)
        return True

    def contain(
        self, exc: BaseException, key: Hashable, message: str, *args: object
    ) -> None:
        """Re-raise `exc` under `DEBUG` or `STRICT_LOADING`, else log it once.

        Call it from the `except` block that caught `exc`, then return the caller's
        fallback. A re-raised exception carries `message` as a note, so the technical
        error page names the source and the fix. Otherwise the render is marked
        degraded, and `message` is logged with the traceback the first time `key` fails.
        """
        if fail_loudly():
            exc.add_note(message % args if args else message)
            raise  # noqa: PLE0704 - re-raises the exception the caller is handling
        _DEGRADED.set(True)
        if self.first_failure(key):
            self._logger.error(message, *args, exc_info=exc)

    def warn(self, key: Hashable, message: str, *args: object) -> None:
        """Log `message` once for `key`, for a failure that has no traceback."""
        if self.first_failure(key):
            self._logger.warning(message, *args)


class BackendReadLog(FailureLog):
    """Read a value from a backend, returning a default when the read fails.

    A backend is read on every reloader tick and static lookup, so a failing read is
    logged once per backend class and subject. Unlike `contain`, a failing read is
    never re-raised, under `DEBUG` included.
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
        except Exception:
            if self.first_failure(source, subject):
                self._logger.exception(_FAILED, source, subject)
            return default
        if not sound:
            if self.first_failure(source, subject):
                self._logger.error(_MALFORMED, source, subject)
            return default
        return answer


__all__ = [
    "INTENDED_EXCEPTIONS",
    "BackendReadLog",
    "FailureLog",
    "degraded",
    "watch_degraded",
]
