"""The clock boundary tokens read their mint and expiry time from."""

import time
from collections.abc import Generator
from contextlib import contextmanager
from typing import override


class TokenClock:
    """Read whole Unix seconds from the system clock."""

    def now(self) -> int:
        """Return the current Unix time in whole seconds."""
        return int(time.time())


class FrozenClock(TokenClock):
    """Hold one instant until a test moves it, so expiry needs no patched `time`."""

    def __init__(self, at: int) -> None:
        """Stop the clock at the Unix second `at`."""
        self.at = at

    @override
    def now(self) -> int:
        """Return the held instant."""
        return self.at

    def advance(self, seconds: int) -> None:
        """Move the held instant by `seconds`."""
        self.at += seconds


class _Held:
    """The clock tokens read now."""

    __slots__ = ("clock",)

    def __init__(self) -> None:
        """Start on the system clock."""
        self.clock: TokenClock = TokenClock()


_held = _Held()


def token_clock() -> TokenClock:
    """Return the clock tokens read now."""
    return _held.clock


@contextmanager
def clocked[C: TokenClock](clock: C) -> Generator[C]:
    """Make tokens read `clock` inside the block, the previous clock after it."""
    previous, _held.clock = _held.clock, clock
    try:
        yield clock
    finally:
        _held.clock = previous


__all__ = ["FrozenClock", "TokenClock", "clocked", "token_clock"]
