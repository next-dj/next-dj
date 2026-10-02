"""Failures of user and third-party code: raised under DEBUG, else logged once."""

from collections.abc import Callable, Hashable
from logging import Logger

from django.core.exceptions import PermissionDenied
from django.http import Http404

from next.conf.settings import fail_loudly
from next.conf.signals import settings_reloaded


# A view raises these on purpose to answer 404 or 403, so they are never contained.
INTENDED_EXCEPTIONS: tuple[type[BaseException], ...] = (Http404, PermissionDenied)


_FAILED = (
    "%s failed to report its %s, so it contributes nothing to the watcher. "
    "The same failure is not logged again until the framework is reconfigured."
)

_MALFORMED = (
    "%s reported %s of the wrong type, so it contributes nothing to the watcher. "
    "The same failure is not logged again until the framework is reconfigured."
)


class FailureLog:
    """Report each failing source once, re-armed when the framework is reconfigured.

    User code that keeps raising runs on every request, so a production log would
    otherwise carry the same traceback per hit and bury the first, useful one.
    """

    def __init__(self, logger: Logger) -> None:
        """Report through `logger`, with every diagnostic armed."""
        self._logger = logger
        self._reported: set[Hashable] = set()
        settings_reloaded.connect(self.clear)

    def clear(self, **kwargs: object) -> None:
        """Re-arm every diagnostic, so a reconfigure is reported afresh."""
        self._reported.clear()

    def first_failure(self, *key: Hashable) -> bool:
        """Whether this failure is unreported, recording it when it is."""
        if key in self._reported:
            return False
        self._reported.add(key)
        return True

    def contain(
        self, exc: BaseException, key: Hashable, message: str, *args: object
    ) -> None:
        """Handle `exc`, the exception in flight: re-raise it when loud, else log once.

        Call it from the `except` block that caught `exc`, then return the caller's
        fallback. Under `DEBUG` or `STRICT_LOADING` the exception propagates with
        `message` attached as a note, so the technical page names the source and the
        fix. Otherwise `message` is logged with the traceback the first time `key`
        fails.
        """
        if fail_loudly():
            exc.add_note(message % args if args else message)
            raise  # noqa: PLE0704 - re-raises the exception the caller is handling
        if self.first_failure(key):
            self._logger.error(message, *args, exc_info=exc)

    def warn(self, key: Hashable, message: str, *args: object) -> None:
        """Log `message` once for `key`, for a failure that has no traceback."""
        if self.first_failure(key):
            self._logger.warning(message, *args)


class BackendReadLog(FailureLog):
    """Read what a backend reports, answering a default when it cannot.

    A backend that keeps raising is read on every reloader tick and static lookup, so
    a failing source is reported once and re-armed on the reconfigure it subscribes to.
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
        """Return what `read` answers, or `default` when it raises or is malformed.

        The shape is checked inside the guard, because inspecting what came back is
        itself a read of backend code and a raising one is the same kind of failure.
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


__all__ = ["INTENDED_EXCEPTIONS", "BackendReadLog", "FailureLog"]
