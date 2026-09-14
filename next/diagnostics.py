"""Guarded reads of what a third-party backend reports, logged once per source."""

from collections.abc import Callable
from logging import Logger

from next.conf.signals import settings_reloaded


_FAILED = (
    "%s failed to report its %s, so it contributes nothing to the watcher. "
    "The same failure is not logged again until the framework is reconfigured."
)

_MALFORMED = (
    "%s reported %s of the wrong type, so it contributes nothing to the watcher. "
    "The same failure is not logged again until the framework is reconfigured."
)


class BackendReadLog:
    """Read what a backend reports, answering a default when it cannot.

    A backend that keeps raising is read on every reloader tick and static lookup, so
    a failing source is reported once and re-armed on the reconfigure it subscribes to.
    """

    def __init__(self, logger: Logger) -> None:
        """Report through `logger`, with every diagnostic armed."""
        self._logger = logger
        self._reported: set[tuple[str, str]] = set()
        settings_reloaded.connect(self.clear)

    def clear(self, **kwargs: object) -> None:
        """Re-arm every diagnostic, so a reconfigure is reported afresh."""
        self._reported.clear()

    def first_failure(self, source: str, subject: str) -> bool:
        """Whether this failure is unreported, recording it when it is."""
        key = (source, subject)
        if key in self._reported:
            return False
        self._reported.add(key)
        return True

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


__all__ = ["BackendReadLog"]
