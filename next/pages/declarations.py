"""The store of what a `page.py` declares for its own page through one `page` call."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from .loaders import load_page_module


if TYPE_CHECKING:
    import types
    from collections.abc import Iterator
    from pathlib import Path


class Declared(Protocol):
    """One call as written, with the namespace of the module run that made it."""

    @property
    def namespace(self) -> dict[str, object]:
        """Return the globals of the module run that made the call."""


class ModuleDeclarations[D: Declared]:
    """Hold the latest call of each `page.py`, the last call winning.

    A reload runs the module in a new namespace, so a removed call stops applying.
    """

    __slots__ = ("_entries", "_outside", "version")

    def __init__(self) -> None:
        """Start empty."""
        self._entries: dict[Path, D] = {}
        self._outside: dict[Path, None] = {}
        self.version = 0
        """Monotonic counter bumped on every change, a token for derived memos."""

    def __bool__(self) -> bool:
        """Whether any `page.py` has made the call since the last reset."""
        return bool(self._entries)

    def declare(self, file_path: Path, declaration: D) -> None:
        """Bind `declaration` to `file_path`, replacing what it declared before."""
        self._entries[file_path] = declaration
        self.version += 1

    def misplace(self, file_path: Path) -> None:
        """Note a call from `file_path`, a module other than a `page.py`."""
        self._outside[file_path] = None

    def outside(self) -> tuple[Path, ...]:
        """Return each module other than a `page.py` that made the call."""
        return tuple(self._outside)

    def current(self, file_path: Path, module: types.ModuleType | None) -> D | None:
        """Return the call `module`, the latest load of `file_path`, made."""
        held = self._entries.get(file_path)
        if held is None or module is None or held.namespace is not vars(module):
            return None
        return held

    def loaded(self) -> Iterator[tuple[Path, D]]:
        """Yield each file with the call its latest load made, in declaration order."""
        for file_path in tuple(self._entries):
            held = self.current(file_path, load_page_module(file_path)[0])
            if held is not None:
                yield file_path, held

    def reset(self) -> None:
        """Drop every declaration."""
        self._entries.clear()
        self._outside.clear()
        self.version += 1


__all__ = ["Declared", "ModuleDeclarations"]
