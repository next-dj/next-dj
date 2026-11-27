"""The wire ids of boundaries and the render session that mints them."""

from __future__ import annotations

import re
import secrets
from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING, Final, Protocol


if TYPE_CHECKING:
    from collections.abc import Generator


ID_BYTES: Final = 4
"""The random bytes of a response id, one per response among the ids a page holds."""

ID_HEAD: Final = "b-"
"""What every wire id starts with, the whole id reading `b-<response id>-<n>`."""

IDENT: Final = re.compile(
    rf"{ID_HEAD}[0-9a-f]{{{2 * ID_BYTES}}}-[0-9]{{1,9}}", re.ASCII
)
"""The pattern of one wire id, which a reload and the stale wrapper check match."""


def new_response_id() -> str:
    """Return a fresh response id, unique to one response among the ids a page holds."""
    return secrets.token_hex(ID_BYTES)


def wire_id(response_id: str, number: int) -> str:
    """Return the wire id `number` of the response `response_id` names."""
    return f"{ID_HEAD}{response_id}-{number}"


def response_id_of(ident: str) -> str:
    """Return the id of the response a wire id was minted in."""
    return ident[len(ID_HEAD) : ident.rindex("-")]


class Deferral(Protocol):
    """The render session the deferred calls of one render gather in."""

    def minted(self) -> str | None:
        """Return the next wire id of its open session, `None` while none is open."""


_deferral: ContextVar[Deferral | None] = ContextVar("next_deferral", default=None)


def current_deferral() -> Deferral | None:
    """Return the render session of this context, `None` outside every one."""
    return _deferral.get()


@contextmanager
def deferral(held: Deferral | None) -> Generator[None]:
    """Run the block in the render session `held`, outside every one for `None`."""
    token = _deferral.set(held)
    try:
        yield
    finally:
        _deferral.reset(token)


__all__ = [
    "IDENT",
    "ID_BYTES",
    "ID_HEAD",
    "Deferral",
    "current_deferral",
    "deferral",
    "new_response_id",
    "response_id_of",
    "wire_id",
]
