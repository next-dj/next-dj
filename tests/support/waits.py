import asyncio
import time
from collections.abc import Callable

import pytest


WAIT = 5.0
"""The longest a test waits on a thread, an event or a condition before it fails."""

QUIET = 0.05
"""The window a negative check leaves a wrong outcome to show itself in."""

_POLL = 0.001


def until(holds: Callable[[], bool]) -> None:
    """Poll `holds` from this thread until it is true, failing once `WAIT` passed."""
    deadline = time.monotonic() + WAIT
    while not holds():
        if time.monotonic() >= deadline:
            pytest.fail("the condition never held")
        time.sleep(_POLL)


async def until_loop(holds: Callable[[], bool]) -> None:
    """Yield to the running loop until `holds` is true, failing once `WAIT` passed."""
    deadline = time.monotonic() + WAIT
    while not holds():
        if time.monotonic() >= deadline:
            pytest.fail("the condition never held")
        await asyncio.sleep(_POLL)
