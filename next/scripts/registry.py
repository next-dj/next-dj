"""Registry of the scripts each page tree declares, keyed by the root of the tree."""

from pathlib import Path

from .discovery import ScriptsSource
from .signals import scripts_registered


class ScriptsRegistry:
    """Hold the `scripts.py` of every page tree in tree order, one per root."""

    def __init__(self) -> None:
        """Start empty."""
        self._roots: list[Path] = []
        self._index: dict[Path, ScriptsSource | None] = {}

    def register(self, root: Path, source: ScriptsSource | None) -> None:
        """Bind the source of the tree at `root`, `None` for a tree without one.

        `scripts_registered` announces a tree holding a `scripts.py`, the rest pass.
        """
        if root not in self._index:
            self._roots.append(root)
        self._index[root] = source
        if source is not None:
            scripts_registered.send(
                sender=ScriptsRegistry, root=root, source=source, scripts=source.scripts
            )

    def source(self, root: Path) -> ScriptsSource | None:
        """Return the source of the tree at `root`, if it has one."""
        return self._index.get(root)

    def roots(self) -> tuple[Path, ...]:
        """Return every registered tree in the order it was registered."""
        return tuple(self._roots)

    def sources(self) -> tuple[ScriptsSource, ...]:
        """Return every source a tree holds, in tree order."""
        return tuple(
            source for root in self._roots if (source := self._index[root]) is not None
        )

    def reset(self) -> None:
        """Drop every tree, so the next read discovers them again."""
        self._roots.clear()
        self._index.clear()


__all__ = ["ScriptsRegistry"]
