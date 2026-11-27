"""What a `page.py` declares about client router navigations through `page.router`."""

from dataclasses import dataclass

from next.conf.shapes import flag
from next.pages.declarations import PageDeclaration


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


def _options(
    *, navigate: bool = True, prefetch: bool = True, snapshot: bool = True
) -> RouterOptions:
    """Return the options the flags make, raising `ValueError` for a non-bool flag."""
    given = {"navigate": navigate, "prefetch": prefetch, "snapshot": snapshot}
    wrong = [f"{name}={value!r}" for name, value in given.items() if not flag(value)]
    if wrong:
        msg = f"{', '.join(wrong)} is no bool, each flag taking True or False"
        raise ValueError(msg)
    return RouterOptions(navigate=navigate, prefetch=prefetch, snapshot=snapshot)


router_declaration = PageDeclaration("router", _options, DEFAULT_OPTIONS)
"""The `page.router` call, which declares how the client router treats its page."""

page_router = router_declaration.of
"""Return the router options of the page at a `page.py` path, the defaults for none."""


__all__ = ["DEFAULT_OPTIONS", "RouterOptions", "page_router", "router_declaration"]
