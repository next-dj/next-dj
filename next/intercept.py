"""The table of intercepting routes, which page serves a zone over a routed target."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final


if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class Intercept:
    """One intercept page bound to the routed page whose zone GETs it may serve.

    `marker` is the prefix of its marked directory, such as `(.)` or `(...)`.
    """

    target_file: Path
    file: Path
    marker: str


class InterceptTable:
    """Intercept rules indexed by their target page, published whole per page root.

    A publish swaps the index in one assignment, so a reader never sees half a root.
    """

    def __init__(self) -> None:
        """Start empty."""
        self._by_root: dict[Path, tuple[Intercept, ...]] = {}
        self._by_target: dict[Path, tuple[Intercept, ...]] = {}
        self._generation = 0
        self._lock = threading.Lock()

    @property
    def generation(self) -> int:
        """Counter bumped on every clear, which a walk reads before it starts."""
        return self._generation

    def publish(
        self, root: Path, rules: Iterable[Intercept], *, generation: int | None = None
    ) -> bool:
        """Replace every rule of the page root `root` with `rules`.

        A walk begun before the last clear passes its stale `generation` and is dropped.
        """
        published = tuple(set(rules))
        with self._lock:
            if generation is not None and generation != self._generation:
                return False
            self._swap(root, published)
        return True

    def _swap(self, root: Path, published: tuple[Intercept, ...]) -> None:
        """Replace one root and reindex only the targets it touches, the lock held."""
        dropped = frozenset(self._by_root.get(root, ()))
        by_target = dict(self._by_target)
        touched = {rule.target_file for rule in (*dropped, *published)}
        for target in touched:
            kept = (rule for rule in by_target.get(target, ()) if rule not in dropped)
            found = sorted(
                (*kept, *(r for r in published if r.target_file == target)),
                key=lambda rule: rule.file,
            )
            if found:
                by_target[target] = tuple(found)
            else:
                by_target.pop(target, None)
        self._by_root = {**self._by_root, root: published}
        self._by_target = by_target

    def of(self, target_file: Path) -> tuple[Intercept, ...]:
        """Return the rules over `target_file` in file order, one dict get on a miss."""
        return self._by_target.get(target_file, ())

    def all(self) -> tuple[Intercept, ...]:
        """Return every published rule, grouped by target and in file order."""
        return tuple(rule for rules in self._by_target.values() for rule in rules)

    def clear(self) -> None:
        """Drop every rule, so a rebuilt URLconf publishes its roots afresh."""
        with self._lock:
            self._by_root = {}
            self._by_target = {}
            self._generation += 1


intercepts: Final = InterceptTable()
"""The process-wide table the file router fills and zone GETs read."""


__all__ = ["Intercept", "InterceptTable", "intercepts"]
