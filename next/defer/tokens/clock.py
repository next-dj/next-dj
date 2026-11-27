"""The clock boundary tokens read their mint and expiry time from."""

import time


class TokenClock:
    """Read whole Unix seconds from the system clock."""

    def now(self) -> int:
        """Return the current Unix time in whole seconds."""
        return int(time.time())


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


def swap_token_clock(clock: TokenClock) -> TokenClock:
    """Make tokens read `clock` from now on and return the clock they read before."""
    previous, _held.clock = _held.clock, clock
    return previous


__all__ = ["TokenClock", "swap_token_clock", "token_clock"]
