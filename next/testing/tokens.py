"""Mint boundary tokens in a test, fresh, at a chosen instant, or already expired."""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

from next.suspense.manager import suspense_manager
from next.suspense.tokens import FrozenClock, StackedTokenBackend, clocked, token_clock


if TYPE_CHECKING:
    from collections.abc import Generator

    from django.http import HttpRequest

    from next.suspense.tokens import BoundaryClaims
    from next.suspense.tokens.claims import Pairs


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
    backend = suspense_manager.tokens()
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
    backend = suspense_manager.tokens()
    at = 0
    if isinstance(backend, StackedTokenBackend):
        lifetime = backend.settings_for(tokens).lifetime(request, claims)
        at = max(0, token_clock().now() - lifetime - 1)
    return issue_boundary_token(request, claims, tokens=tokens, at=at)


__all__ = ["expire_boundary_token", "freeze_token_clock", "issue_boundary_token"]
