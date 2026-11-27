"""The boundary state one top-level render shares and the wrapper ids it mints."""

from __future__ import annotations

import threading
from contextlib import AbstractContextManager, contextmanager, nullcontext
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Final, override

from next.conf.boundaries import BoundarySettings, boundary_settings
from next.transactions.policy import probe_atomic_aliases
from next.transactions.savepoints import LazySavepoint

from .ids import current_deferral, new_response_id, wire_id


if TYPE_CHECKING:
    from collections.abc import Generator, Hashable
    from types import TracebackType


class Render:
    """What every boundary of one top-level render on one thread shares.

    The first boundary probes the aliases and keeps the wrappers on for its siblings.
    """

    __slots__ = (
        "aliases",
        "count",
        "errors",
        "failing",
        "holder",
        "response_id",
        "thread",
    )

    def __init__(self) -> None:
        """Start before any boundary of the render."""
        self.thread = threading.get_ident()
        self.errors: BoundarySettings | None = None
        self.aliases: tuple[str, ...] = ()
        self.holder: LazySavepoint | None = None
        self.failing: set[Hashable] = set()
        self.response_id = ""
        self.count = 0

    def settings(self) -> BoundarySettings:
        """Return the `ERRORS` values of the render, read once with the alias probe."""
        errors = self.errors
        if errors is None:
            errors = self.errors = boundary_settings()
            self.aliases = probe_atomic_aliases()
            holder = LazySavepoint(atomic=False)
            holder.__enter__()
            self.holder = holder
        return errors

    def next_id(self) -> str:
        """Return an id unique within the response, the render session minting first."""
        held = current_deferral()
        minted = None if held is None else held.minted()
        if minted is not None:
            return minted
        if not self.response_id:
            self.response_id = new_response_id()
        self.count += 1
        return wire_id(self.response_id, self.count)

    def close(self) -> None:
        """Release the handle the first boundary opened, removing the wrappers."""
        holder = self.holder
        if holder is not None:
            self.holder = None
            holder.__exit__(None, None, None)


_render: ContextVar[Render | None] = ContextVar("next_boundary_render", default=None)
_wrapper: ContextVar[str | None] = ContextVar("next_boundary_wrapper", default=None)


@contextmanager
def wrapping(ident: str) -> Generator[None]:
    """Hand the id of the wrapper drawn around the block to the boundary claiming it."""
    token = _wrapper.set(ident)
    try:
        yield
    finally:
        _wrapper.reset(token)


def claim_wrapper() -> str | None:
    """Return the id `wrapping` bound and clear it, so a nested boundary mints anew."""
    ident = _wrapper.get()
    if ident is not None:
        _wrapper.set(None)
    return ident


class Scope(AbstractContextManager[None]):
    """The block one top-level render shares its boundary state in."""

    __slots__ = ("_token", "state")

    _token: Token[Render | None]

    def __init__(self) -> None:
        """Start the state of a render no boundary met yet."""
        self.state = Render()

    @override
    def __enter__(self) -> None:
        """Make the state the one every boundary of the block shares."""
        self._token = _render.set(self.state)

    @override
    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Restore the state outside the block and release what it opened."""
        _render.reset(self._token)
        self.state.close()


_NESTED: Final[AbstractContextManager[None]] = nullcontext()


def current() -> Render | None:
    """Return the state of the render under way on this thread, if any."""
    state = _render.get()
    if state is not None and state.thread == threading.get_ident():
        return state
    return None


def render_boundaries() -> AbstractContextManager[None]:
    """Return the block every boundary of one top-level render shares its state in.

    An entry point opens it around its render, and a boundary outside one opens its own.
    """
    return _NESTED if current() is not None else Scope()


__all__ = [
    "Render",
    "Scope",
    "claim_wrapper",
    "current",
    "render_boundaries",
    "wrapping",
]
