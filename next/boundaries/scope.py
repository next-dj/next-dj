"""The boundary state one top-level render shares and the wrapper ids it mints."""

from __future__ import annotations

import secrets
import threading
from contextlib import AbstractContextManager, contextmanager, nullcontext
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Final, override

from next.transactions import LazySavepoint, probe_atomic_aliases

from .options import Errors, error_settings


if TYPE_CHECKING:
    from collections.abc import Callable, Hashable, Iterator
    from types import TracebackType


_SEAL_BYTES: Final = 4


class Render:
    """What every boundary of one top-level render on one thread shares.

    The first boundary probes the aliases and keeps the wrappers on for its siblings.
    """

    __slots__ = ("aliases", "count", "errors", "failing", "holder", "seal", "thread")

    def __init__(self) -> None:
        """Start before any boundary of the render."""
        self.thread = threading.get_ident()
        self.errors: Errors | None = None
        self.aliases: tuple[str, ...] = ()
        self.holder: LazySavepoint | None = None
        self.failing: set[Hashable] = set()
        self.seal = ""
        self.count = 0

    def settings(self) -> Errors:
        """Return the `ERRORS` values of the render, read once with the alias probe."""
        errors = self.errors
        if errors is None:
            errors = self.errors = error_settings()
            self.aliases = probe_atomic_aliases()
            holder = LazySavepoint(atomic=False)
            holder.__enter__()
            self.holder = holder
        return errors

    def next_id(self) -> str:
        """Return a boundary id unique within the response, from a bound minter."""
        minter = _minter.get()
        minted = None if minter is None else minter()
        if minted is not None:
            return minted
        if not self.seal:
            self.seal = secrets.token_hex(_SEAL_BYTES)
        self.count += 1
        return f"b-{self.seal}-{self.count}"

    def close(self) -> None:
        """Release the handle the first boundary opened, removing the wrappers."""
        holder = self.holder
        if holder is not None:
            self.holder = None
            holder.__exit__(None, None, None)


_render: ContextVar[Render | None] = ContextVar("next_boundary_render", default=None)
_minter: ContextVar[Callable[[], str | None] | None] = ContextVar(
    "next_boundary_minter", default=None
)
_wrapper: ContextVar[str | None] = ContextVar("next_boundary_wrapper", default=None)


@contextmanager
def wrapping(ident: str) -> Iterator[None]:
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


@contextmanager
def minting(minter: Callable[[], str | None]) -> Iterator[None]:
    """Take the id of every error wrapper inside the block from `minter`, if it has one.

    A deferred render binds its session, so every wrapper shares one id scheme.
    """
    token = _minter.set(minter)
    try:
        yield
    finally:
        _minter.reset(token)


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
    "minting",
    "render_boundaries",
    "wrapping",
]
