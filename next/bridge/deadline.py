"""The deadline of the running job, which user code reads as the seconds left."""

from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Generator


_deadline: ContextVar[float | None] = ContextVar("next_defer_deadline", default=None)


def remaining() -> float | None:
    """Return the seconds left before the current job's deadline, `None` without one."""
    at = _deadline.get()
    if at is None:
        return None
    return max(0.0, at - time.monotonic())


@contextmanager
def deadline(at: float | None) -> Generator[None]:
    """Run the block under a deadline at the `time.monotonic()` instant `at`."""
    token = _deadline.set(at)
    try:
        yield
    finally:
        _deadline.reset(token)


__all__ = ["deadline", "remaining"]
