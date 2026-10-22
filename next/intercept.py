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

    `source` is the marked directory, `marker` its prefix such as `(.)` or `(...)`.
    """

    target_file: Path
    file: Path
    source: Path
    marker: str


class InterceptTable:
    """Intercept rules indexed by their target page, published whole per page root.

    A publish swaps the index in one assignment, so a reader never sees half a root.
    """

    def __init__(self) -> None:
        """Start empty at version zero."""
        self._by_root: dict[Path, tuple[Intercept, ...]] = {}
        self._by_target: dict[Path, tuple[Intercept, ...]] = {}
        self._version = 0
        self._generation = 0
        self._lock = threading.Lock()

    @property
    def version(self) -> int:
        """Monotonic counter bumped on every publish."""
        return self._version

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
        published = tuple(sorted(set(rules), key=lambda rule: rule.file))
        with self._lock:
            if generation is not None and generation != self._generation:
                return False
            self._swap(root, published)
        return True

    def _swap(self, root: Path, published: tuple[Intercept, ...]) -> None:
        """Replace one root and rebuild the target index, the lock already held."""
        by_root = dict(self._by_root)
        by_root[root] = published
        by_target: dict[Path, list[Intercept]] = {}
        for held in by_root.values():
            for rule in held:
                by_target.setdefault(rule.target_file, []).append(rule)
        self._by_root = by_root
        self._by_target = {
            target: tuple(sorted(found, key=lambda rule: rule.file))
            for target, found in by_target.items()
        }
        self._version += 1

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
            self._version += 1
            self._generation += 1


intercepts: Final = InterceptTable()
"""The process-wide table the file router fills and zone GETs read."""


def forget_intercepts(**kwargs: object) -> None:
    """Drop every rule on a router reload, so a tree no longer routed keeps none."""
    intercepts.clear()


__all__ = ["Intercept", "InterceptTable", "forget_intercepts", "intercepts"]
