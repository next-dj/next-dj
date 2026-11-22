"""The transaction a `page.py` declares for its own page through `page.atomic`."""

import sys
from dataclasses import dataclass
from functools import partial

from next.introspect import registering_site
from next.transactions import AliasChoice, AtomicOverride, override_of, override_problem

from .declarations import NESTED, DeclarationStore


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
            return NESTED
        return override_problem(self.atomic, self.aliases)

    def override(self) -> AtomicOverride | None:
        """Return the override the call declares, `None` for one E240 reports."""
        if not self.top_level:
            return None
        return override_of(self.atomic, self.aliases)


class AtomicStore(DeclarationStore[AtomicDeclaration, AtomicOverride | None]):
    """Hold the latest `page.atomic` call of each `page.py`, the last call winning."""

    __slots__ = ()

    def __init__(self) -> None:
        """Start empty, a page without a valid call declaring no override."""
        super().__init__(AtomicDeclaration.override, None)


atomic_store = AtomicStore()


class AtomicDeclaring:
    """The `page.atomic` declaration of the `Page` manager."""

    def atomic(
        self, atomic: object, /, *, aliases: AliasChoice | list[str] = None
    ) -> None:
        """Run this page in a transaction, or out of one, whatever the request method.

        `"writes"` opens it before the first write, `aliases` of `None` on `ALIASES`.
        """
        atomic_store.record(
            registering_site(),
            sys._getframe(1).f_globals,
            partial(AtomicDeclaration, atomic, aliases),
        )


page_atomic = atomic_store.resolved
"""Return the transaction override of the page at a `page.py` path, `None` for none."""


__all__ = [
    "AtomicDeclaration",
    "AtomicDeclaring",
    "AtomicStore",
    "atomic_store",
    "page_atomic",
]
