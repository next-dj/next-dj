"""The boundary a `page.py` declares for its own page body through `page.defer`."""

import sys
from dataclasses import dataclass
from functools import partial

from next.defer import BoundaryOptionError, BoundaryOptions, DeferMode
from next.introspect import registering_site

from .declarations import NESTED, DeclarationStore


@dataclass(frozen=True, slots=True, eq=False)
class PageDeferDeclaration:
    """One `page.defer` call as written, with the namespace of the run that made it."""

    timeout: object
    parallel: object
    namespace: dict[str, object]
    top_level: bool

    def problem(self) -> str | None:
        """Return why the call declares no options, `None` when it declares them."""
        if not self.top_level:
            return NESTED
        try:
            self._parsed()
        except BoundaryOptionError as error:
            return str(error)
        return None

    def options(self) -> BoundaryOptions | None:
        """Return the options the call declares, `None` for one E222 reports."""
        if not self.top_level:
            return None
        try:
            return self._parsed()
        except BoundaryOptionError:
            return None

    def _parsed(self) -> BoundaryOptions:
        return BoundaryOptions.parse(
            DeferMode.STREAM, timeout=self.timeout, parallel=self.parallel
        )


class PageDeferStore(DeclarationStore[PageDeferDeclaration, BoundaryOptions | None]):
    """Hold the latest `page.defer` call of each `page.py`, the last call winning."""

    __slots__ = ()

    def __init__(self) -> None:
        """Start empty, a page without a valid call declaring no options."""
        super().__init__(PageDeferDeclaration.options, None)


page_defer_store = PageDeferStore()


class PageDeferDeclaring:
    """The `page.defer` declaration of the `Page` manager."""

    def defer(
        self, *, timeout: float | str | None = None, parallel: bool = True
    ) -> None:
        """Bound the streamed body of this page by `timeout` and place its job.

        `False` keeps the job on the request lane, `True` lets it take the pool.
        """
        page_defer_store.record(
            registering_site(),
            sys._getframe(1).f_globals,
            partial(PageDeferDeclaration, timeout, parallel),
        )


page_defer = page_defer_store.resolved
"""Return the boundary options of the page at a `page.py` path, `None` for none."""


__all__ = [
    "PageDeferDeclaration",
    "PageDeferDeclaring",
    "PageDeferStore",
    "page_defer",
    "page_defer_store",
]
