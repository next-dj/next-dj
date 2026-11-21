"""What a `page.py` declares about client router navigations through `page.router`."""

import sys
from dataclasses import dataclass
from pathlib import Path

from next.caches import PageCache
from next.introspect import registering_site
from next.utils import template_edits_watched

from .declarations import ModuleDeclarations
from .loaders import (
    PAGE_MODULE,
    load_python_module_memo,
    module_generation,
    module_version,
)


@dataclass(frozen=True, slots=True)
class RouterOptions:
    """How the client router treats one page.

    `navigate` false answers every navigation with a hard load, `prefetch` false
    refuses prefetches before any render, `snapshot` false keeps it out of history.
    """

    navigate: bool = True
    prefetch: bool = True
    snapshot: bool = True


DEFAULT_OPTIONS = RouterOptions()
"""The options of a page that declares none."""

_FLAGS = ("navigate", "prefetch", "snapshot")


@dataclass(frozen=True, slots=True, eq=False)
class RouterDeclaration:
    """One `page.router` call as written, with the namespace of the run that made it.

    A reload runs the module in a new namespace, so a removed call stops applying.
    """

    navigate: object
    prefetch: object
    snapshot: object
    namespace: dict[str, object]
    top_level: bool

    def problem(self) -> str | None:
        """Return why the call declares nothing, `None` when it declares options."""
        if not self.top_level:
            return "it runs inside a function rather than at the top of the module"
        wrong = [name for name in _FLAGS if not isinstance(getattr(self, name), bool)]
        if wrong:
            shown = ", ".join(f"{name}={getattr(self, name)!r}" for name in wrong)
            return f"{shown} is no bool"
        return None

    def options(self) -> RouterOptions | None:
        """Return the options the call declares, `None` for one E238 reports."""
        if self.problem() is not None:
            return None
        return RouterOptions(
            navigate=bool(self.navigate),
            prefetch=bool(self.prefetch),
            snapshot=bool(self.snapshot),
        )


class RouterDeclarations(ModuleDeclarations[RouterDeclaration]):
    """Hold the latest `page.router` call of each `page.py`, the last call winning."""

    __slots__ = ()


router_declarations = RouterDeclarations()


class NavigationDeclarations:
    """The `page.router` declaration of the `Page` manager."""

    def router(
        self,
        *,
        navigate: object = True,
        prefetch: object = True,
        snapshot: object = True,
    ) -> None:
        """Declare how the client router treats this page.

        A page whose context callables act on a visit opts out of prefetch.
        """
        file_path, top_level = registering_site()
        if file_path.name != PAGE_MODULE:
            router_declarations.misplace(file_path)
            return
        namespace = sys._getframe(1).f_globals
        router_declarations.declare(
            file_path,
            RouterDeclaration(navigate, prefetch, snapshot, namespace, top_level),
        )


type _Stamp = tuple[int, int, int]

_HELD: PageCache[Path, tuple[_Stamp, RouterOptions]] = PageCache()


def _read(file_path: Path) -> RouterOptions:
    """Return the options the latest load of `file_path` declares."""
    module = load_python_module_memo(file_path)
    held = router_declarations.current(file_path, module)
    options = None if held is None else held.options()
    return DEFAULT_OPTIONS if options is None else options


def page_router(file_path: Path) -> RouterOptions:
    """Return the router options of the page at `file_path`, the defaults for none.

    A project without a declaration answers at once, which keeps a navigation free.
    """
    if not router_declarations:
        return DEFAULT_OPTIONS
    if template_edits_watched():
        return _read(file_path)
    stamp = (module_version(), module_generation(), router_declarations.version)
    held = _HELD.get(file_path)
    if held is None or held[0] != stamp:
        held = _HELD[file_path] = (stamp, _read(file_path))
    return held[1]


__all__ = [
    "DEFAULT_OPTIONS",
    "NavigationDeclarations",
    "RouterDeclaration",
    "RouterDeclarations",
    "RouterOptions",
    "page_router",
    "router_declarations",
]
