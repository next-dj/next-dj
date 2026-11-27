"""Mint boundary tokens in a test, fresh, at a chosen instant, or already expired."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, override

from django.core import signing

from next.defer.manager import defer_manager
from next.defer.tokens import (
    StackedTokenBackend,
    TokenClock,
    TokenSeal,
    swap_token_clock,
    token_clock,
)
from next.defer.tokens.seal import decoded


if TYPE_CHECKING:
    from collections.abc import Generator

    from django.http import HttpRequest

    from next.defer.tokens import BoundaryClaims
    from next.defer.tokens.claims import Pairs


class NoSeal(TokenSeal):
    """Seal nothing, so a test reads its tokens, which `next.E221` refuses off DEBUG."""

    protects = False

    @override
    def seal(self, payload: bytes, salt: str) -> str:
        return signing.b64_encode(payload).decode()

    @override
    def open(self, sealed: str, salt: str) -> bytes:
        return decoded(sealed)


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


@contextmanager
def clocked[C: TokenClock](clock: C) -> Generator[C]:
    """Make tokens read `clock` inside the block, the previous clock after it."""
    previous = swap_token_clock(clock)
    try:
        yield clock
    finally:
        swap_token_clock(previous)


@contextmanager
def freeze_token_clock(at: int) -> Generator[FrozenClock]:
    """Hold the clock tokens read at the Unix second `at` inside the block."""
    with clocked(FrozenClock(at)) as clock:
        yield clock


def issue_boundary_token(
    request: HttpRequest,
    claims: BoundaryClaims,
    *,
    tokens: Pairs[object] = (),
    at: int | None = None,
) -> str:
    """Return the token the configured backend mints for `claims`, at `at` if given."""
    backend = defer_manager.tokens()
    if at is None:
        return backend.issue(request, claims, tokens=tokens)
    with freeze_token_clock(at):
        return backend.issue(request, claims, tokens=tokens)


def expire_boundary_token(
    request: HttpRequest, claims: BoundaryClaims, *, tokens: Pairs[object] = ()
) -> str:
    """Return a token for `claims` whose lifetime ended a second before now.

    A backend other than the shipped stack mints it at the epoch instead.
    """
    backend = defer_manager.tokens()
    at = 0
    if isinstance(backend, StackedTokenBackend):
        lifetime = backend.settings_for(tokens).lifetime(request, claims)
        at = max(0, token_clock().now() - lifetime - 1)
    return issue_boundary_token(request, claims, tokens=tokens, at=at)


__all__ = [
    "FrozenClock",
    "NoSeal",
    "clocked",
    "expire_boundary_token",
    "freeze_token_clock",
    "issue_boundary_token",
]
