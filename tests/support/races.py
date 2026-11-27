from __future__ import annotations

from typing import TYPE_CHECKING, Self


if TYPE_CHECKING:
    from types import TracebackType


class LockWonByAnotherThread:
    """Stand-in lock that sets an attribute of `holder` the moment it is entered.

    Entering it puts the waiting thread in the test, so the re-check sees the value.
    """

    __slots__ = ("_flag", "_holder", "_value", "entered")

    def __init__(self, holder: object, flag: str, *, value: object = True) -> None:
        """Bind the stand-in to the attribute the winning thread would have set."""
        self._holder = holder
        self._flag = flag
        self._value = value
        self.entered = 0

    def __enter__(self) -> Self:
        """Record the wait and hand over a holder another thread already loaded."""
        self.entered += 1
        object.__setattr__(self._holder, self._flag, self._value)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release nothing. The stand-in never held anything."""
        return
