"""The transaction a `page.py` declares for its own page through `page.atomic`."""

import sys
from dataclasses import dataclass
from pathlib import Path

from next.caches import PageCache
from next.introspect import registering_site
from next.transactions import AliasChoice, AtomicOverride, override_of, override_problem
from next.utils import template_edits_watched

from .declarations import ModuleDeclarations
from .loaders import (
    PAGE_MODULE,
    load_python_module_memo,
    module_generation,
    module_version,
)


@dataclass(frozen=True, slots=True, eq=False)
class AtomicDeclaration:
    """One `page.atomic` call as written, with the namespace of the run that made it.

    A reload runs the module in a new namespace, so a removed call stops applying.
    """

    atomic: object
    aliases: object
    namespace: dict[str, object]
    top_level: bool

    def problem(self) -> str | None:
        """Return why the call declares no override, `None` when it declares one."""
        if not self.top_level:
            return "it runs inside a function rather than at the top of the module"
        return override_problem(self.atomic, self.aliases)

    def override(self) -> AtomicOverride | None:
        """Return the override the call declares, `None` for one E240 reports."""
        if not self.top_level:
            return None
        return override_of(self.atomic, self.aliases)


class AtomicDeclarations(ModuleDeclarations[AtomicDeclaration]):
    """Hold the latest `page.atomic` call of each `page.py`, the last call winning."""

    __slots__ = ()


atomic_declarations = AtomicDeclarations()


class TransactionDeclarations:
    """The `page.atomic` declaration of the `Page` manager."""

    def atomic(
        self, atomic: object, /, *, aliases: AliasChoice | list[str] = None
    ) -> None:
        """Run this page in a transaction, or out of one, whatever the request method.

        `aliases` of `None` takes `TRANSACTIONS["ALIASES"]`, and `"all"` every alias.
        """
        file_path, top_level = registering_site()
        if file_path.name != PAGE_MODULE:
            atomic_declarations.misplace(file_path)
            return
        namespace = sys._getframe(1).f_globals
        atomic_declarations.declare(
            file_path, AtomicDeclaration(atomic, aliases, namespace, top_level)
        )


type _Stamp = tuple[int, int, int]

_HELD: PageCache[Path, tuple[_Stamp, AtomicOverride | None]] = PageCache()


def _read(file_path: Path) -> AtomicOverride | None:
    """Return the override the latest load of `file_path` declares."""
    module = load_python_module_memo(file_path)
    held = atomic_declarations.current(file_path, module)
    return None if held is None else held.override()


def page_atomic(file_path: Path) -> AtomicOverride | None:
    """Return the transaction override of the page at `file_path`, `None` for none.

    A project without a declaration answers at once, which keeps a plain `GET` free.
    """
    if not atomic_declarations:
        return None
    if template_edits_watched():
        return _read(file_path)
    stamp = (module_version(), module_generation(), atomic_declarations.version)
    held = _HELD.get(file_path)
    if held is None or held[0] != stamp:
        held = _HELD[file_path] = (stamp, _read(file_path))
    return held[1]


__all__ = [
    "AtomicDeclaration",
    "AtomicDeclarations",
    "TransactionDeclarations",
    "atomic_declarations",
    "page_atomic",
]
