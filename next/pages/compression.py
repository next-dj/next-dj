"""Whether next.dj compresses a page, as its `page.py` declares by `page.compress`."""

import sys
from dataclasses import dataclass
from functools import partial

from next.introspect import registering_site

from .declarations import NESTED, DeclarationStore


@dataclass(frozen=True, slots=True, eq=False)
class CompressDeclaration:
    """One `page.compress` call as written, with the namespace of the run that made it.

    A reload runs the module in a new namespace, so a removed call stops applying.
    """

    enabled: object
    namespace: dict[str, object]
    top_level: bool

    def problem(self) -> str | None:
        """Return why the call declares nothing, `None` when it declares a choice."""
        if not self.top_level:
            return NESTED
        if not isinstance(self.enabled, bool):
            return f"enabled is {self.enabled!r}, which is no bool"
        return None

    def choice(self) -> bool | None:
        """Return the choice the call declares, `None` for one E222 reports."""
        if self.problem() is not None:
            return None
        return bool(self.enabled)


class CompressStore(DeclarationStore[CompressDeclaration, bool | None]):
    """Hold the latest `page.compress` call of each `page.py`, the last call winning."""

    __slots__ = ()

    def __init__(self) -> None:
        """Start empty, a page without a valid call declaring no choice."""
        super().__init__(CompressDeclaration.choice, None)


compress_store = CompressStore()


class CompressDeclaring:
    """The `page.compress` declaration of the `Page` manager."""

    def compress(self, enabled: object, /) -> None:
        """Let next.dj compress this page with `True`, or keep it plain with `False`.

        A page that renders a secret beside reflected input opts out against BREACH.
        """
        compress_store.record(
            registering_site(),
            sys._getframe(1).f_globals,
            partial(CompressDeclaration, enabled),
        )


page_compress = compress_store.resolved
"""Return the compression choice of the page at a `page.py` path, `None` for none."""


__all__ = [
    "CompressDeclaration",
    "CompressDeclaring",
    "CompressStore",
    "compress_store",
    "page_compress",
]
