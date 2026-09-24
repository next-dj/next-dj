from __future__ import annotations

from typing import TYPE_CHECKING, Self


if TYPE_CHECKING:
    from types import TracebackType


class LockWonByAnotherThread:
    """Stand-in lock that flips a flag on `holder` the moment it is entered.

    Double-checked locking guards against a second thread arriving while the first
    one loads. Swapping the lock for this one puts the waiting thread in the test,
    so the inner re-check sees the flag the winner set.
    """

    __slots__ = ("_flag", "_holder", "entered")

    def __init__(self, holder: object, flag: str) -> None:
        """Bind the stand-in to the attribute the winning thread would have set."""
        self._holder = holder
        self._flag = flag
        self.entered = 0

    def __enter__(self) -> Self:
        """Record the wait and hand over a holder another thread already loaded."""
        self.entered += 1
        object.__setattr__(self._holder, self._flag, True)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release nothing. The stand-in never held anything."""
        return
