"""The store of what a `page.py` declares for its own page through one `page` call."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Final, Protocol

from next.caches import PageCache
from next.utils import template_edits_watched

from .loaders import (
    PAGE_MODULE,
    load_page_module,
    load_python_module_memo,
    module_generation,
    module_version,
)


if TYPE_CHECKING:
    import types
    from collections.abc import Callable, Iterator
    from pathlib import Path


NESTED: Final = "it runs inside a function rather than at the top of the module"
"""Why a call made outside the top level of its `page.py` declares nothing."""

type _Stamp = tuple[int, int, int]


class Declared(Protocol):
    """One call as written, with the namespace of the module run that made it."""

    @property
    def namespace(self) -> dict[str, object]:
        """Return the globals of the module run that made the call."""


class DeclarationStore[D: Declared, T]:
    """Hold the last call of each `page.py` and what it declares, a later call winning.

    A reload runs the module in a new namespace, so a removed call stops applying.
    """

    __slots__ = (
        "_default",
        "_entries",
        "_held",
        "_lock",
        "_outside",
        "_read",
        "version",
    )

    def __init__(self, read: Callable[[D], T | None], default: T) -> None:
        """Start empty, `read` turning a call into what it declares, `None` for none."""
        self._read = read
        self._default = default
        self._entries: dict[Path, D] = {}
        self._outside: dict[Path, None] = {}
        self._held: PageCache[Path, tuple[_Stamp, T]] = PageCache()
        self._lock = threading.Lock()
        self.version = 0
        """Monotonic counter bumped on every change, a token for derived memos."""

    def __bool__(self) -> bool:
        """Whether any `page.py` has made the call since the last reset."""
        return bool(self._entries)

    def record(
        self,
        site: tuple[Path, bool],
        namespace: dict[str, object],
        make: Callable[[dict[str, object], bool], D],
    ) -> None:
        """Bind the call made at `site` from the module run owning `namespace`.

        A call from a module other than a `page.py` is noted for a check instead.
        """
        file_path, top_level = site
        if file_path.name != PAGE_MODULE:
            with self._lock:
                self._outside[file_path] = None
            return
        self.declare(file_path, make(namespace, top_level))

    def declare(self, file_path: Path, declaration: D) -> None:
        """Bind `declaration` to `file_path`, replacing what it declared before."""
        with self._lock:
            self._entries[file_path] = declaration
            self.version += 1

    def outside(self) -> tuple[Path, ...]:
        """Return each module other than a `page.py` that made the call."""
        with self._lock:
            return tuple(self._outside)

    def current(self, file_path: Path, module: types.ModuleType | None) -> D | None:
        """Return the call `module`, the latest load of `file_path`, made."""
        held = self._entries.get(file_path)
        if held is None or module is None or held.namespace is not vars(module):
            return None
        return held

    def loaded(self) -> Iterator[tuple[Path, D]]:
        """Yield each file with the call its latest load made, in declaration order."""
        with self._lock:
            paths = tuple(self._entries)
        for file_path in paths:
            held = self.current(file_path, load_page_module(file_path)[0])
            if held is not None:
                yield file_path, held

    def resolved(self, file_path: Path) -> T:
        """Return what the page at `file_path` declares, the default for nothing.

        A project without a call answers at once, which keeps a plain request free.
        """
        if not self._entries:
            return self._default
        if template_edits_watched():
            return self._value(file_path)
        stamp = (module_version(), module_generation(), self.version)
        held = self._held.get(file_path)
        if held is None or held[0] != stamp:
            held = self._held[file_path] = (stamp, self._value(file_path))
        return held[1]

    def _value(self, file_path: Path) -> T:
        """Return what the latest load of `file_path` declares, the default for none."""
        held = self.current(file_path, load_python_module_memo(file_path))
        value = None if held is None else self._read(held)
        return self._default if value is None else value

    def reset(self) -> None:
        """Drop every declaration."""
        with self._lock:
            self._entries.clear()
            self._outside.clear()
            self.version += 1


__all__ = ["NESTED", "DeclarationStore", "Declared"]
