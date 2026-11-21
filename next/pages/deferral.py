"""The boundary a `page.py` declares for its own page body through `page.defer`."""

import sys
from dataclasses import dataclass
from pathlib import Path

from next.caches import PageCache
from next.defer import BoundaryOptionError, BoundaryOptions, DeferMode
from next.introspect import registering_site
from next.utils import template_edits_watched

from .declarations import ModuleDeclarations
from .loaders import (
    PAGE_MODULE,
    load_python_module_memo,
    module_generation,
    module_version,
)


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
            return "it runs inside a function rather than at the top of the module"
        try:
            self._parsed()
        except BoundaryOptionError as error:
            return str(error)
        return None

    def options(self) -> BoundaryOptions | None:
        """Return the options the call declares, `None` for one E222 reports."""
        if self.problem() is not None:
            return None
        return self._parsed()

    def _parsed(self) -> BoundaryOptions:
        return BoundaryOptions.parse(
            DeferMode.STREAM, timeout=self.timeout, parallel=self.parallel
        )


class PageDeferDeclarations(ModuleDeclarations[PageDeferDeclaration]):
    """Hold the latest `page.defer` call of each `page.py`, the last call winning."""

    __slots__ = ()


page_defer_declarations = PageDeferDeclarations()


class BoundaryDeclarations:
    """The `page.defer` declaration of the `Page` manager."""

    def defer(
        self, *, timeout: float | str | None = None, parallel: bool | None = None
    ) -> None:
        """Bound the streamed body of this page by `timeout` and place its job.

        `None` lets placement decide, `True` pins the pool, `False` the request lane.
        """
        file_path, top_level = registering_site()
        if file_path.name != PAGE_MODULE:
            page_defer_declarations.misplace(file_path)
            return
        namespace = sys._getframe(1).f_globals
        page_defer_declarations.declare(
            file_path, PageDeferDeclaration(timeout, parallel, namespace, top_level)
        )


type _Stamp = tuple[int, int, int]

_HELD: PageCache[Path, tuple[_Stamp, BoundaryOptions | None]] = PageCache()


def _read(file_path: Path) -> BoundaryOptions | None:
    """Return the options the latest load of `file_path` declares."""
    module = load_python_module_memo(file_path)
    held = page_defer_declarations.current(file_path, module)
    return None if held is None else held.options()


def page_defer(file_path: Path) -> BoundaryOptions | None:
    """Return the boundary options of the page at `file_path`, `None` for none.

    A project without a declaration answers at once, which keeps a plain `GET` free.
    """
    if not page_defer_declarations:
        return None
    if template_edits_watched():
        return _read(file_path)
    stamp = (module_version(), module_generation(), page_defer_declarations.version)
    held = _HELD.get(file_path)
    if held is None or held[0] != stamp:
        held = _HELD[file_path] = (stamp, _read(file_path))
    return held[1]


__all__ = [
    "BoundaryDeclarations",
    "PageDeferDeclaration",
    "PageDeferDeclarations",
    "page_defer",
    "page_defer_declarations",
]
