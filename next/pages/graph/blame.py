"""The inherited group a failed context run is blamed on, with what it settled."""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import TYPE_CHECKING, cast

from .merge import spread


if TYPE_CHECKING:
    from collections.abc import Iterator


class Blame:
    """The inherited group a context run failed in, `-1` for the page's own ones.

    It also keeps each inherited result in run order, so a boundary can cut the shell.
    """

    __slots__ = ("group", "settled")

    def __init__(self) -> None:
        """Start on the page's own callables, which a failure elsewhere moves off."""
        self.group = -1
        self.settled: list[tuple[int, str | None, object]] = []

    def settle(self, group: int, key: str | None, value: object) -> None:
        """Keep what one callable of the inherited `group` returned."""
        self.settled.append((group, key, value))

    def level(self, inherited: tuple[int, ...]) -> int:
        """Return the directory of the failed group, `-1` for the page's own ones."""
        group = self.group
        return inherited[group] if 0 <= group < len(inherited) else -1

    def cut(self, groups: int) -> dict[str, object]:
        """Return the context the first `groups` inherited groups merged."""
        merged: dict[str, object] = {}
        for group, key, value in self.settled:
            if group >= groups:
                continue
            if key is not None:
                merged[key] = value
            else:
                spread(merged, cast("dict[str, object]", value))
        return merged


blame_watch: contextvars.ContextVar[Blame | None] = contextvars.ContextVar(
    "next_context_blame", default=None
)


def blame(group: int) -> None:
    """Record that a callable of `group` failed, for the block `blaming` watches."""
    held = blame_watch.get()
    if held is not None:
        held.group = group


@contextmanager
def blaming() -> Iterator[Blame]:
    """Watch the context runs of the block for the group a failure belongs to."""
    held = Blame()
    token = blame_watch.set(held)
    try:
        yield held
    finally:
        blame_watch.reset(token)


__all__ = ["Blame", "blame", "blame_watch", "blaming"]
