"""What a `page.py` declares about client router navigations through `page.router`."""

import sys
from dataclasses import dataclass
from functools import partial

from next.introspect import registering_site

from .declarations import NESTED, DeclarationStore


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
            return NESTED
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


class RouterStore(DeclarationStore[RouterDeclaration, RouterOptions]):
    """Hold the latest `page.router` call of each `page.py`, the last call winning."""

    __slots__ = ()

    def __init__(self) -> None:
        """Start empty, a page without a valid call taking `DEFAULT_OPTIONS`."""
        super().__init__(RouterDeclaration.options, DEFAULT_OPTIONS)


router_store = RouterStore()


class RouterDeclaring:
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
        router_store.record(
            registering_site(),
            sys._getframe(1).f_globals,
            partial(RouterDeclaration, navigate, prefetch, snapshot),
        )


page_router = router_store.resolved
"""Return the router options of the page at a `page.py` path, the defaults for none."""


__all__ = [
    "DEFAULT_OPTIONS",
    "RouterDeclaration",
    "RouterDeclaring",
    "RouterOptions",
    "RouterStore",
    "page_router",
    "router_store",
]
