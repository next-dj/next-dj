"""System checks for the router scope, the routes it fetches and the pages it cannot."""

from __future__ import annotations

from typing import Final

from django.conf import settings
from django.core.checks import CheckMessage, Tags, Warning as DjangoWarning
from django.urls import get_script_prefix

from next.checks import NEXT, register
from next.conf.router import RouterScope, router_enabled, router_settings
from next.partial.keys import ROUTER_ATTR
from next.router.manager import exclude_prefixes
from next.urls.scope import Stranded, route_rows, stranded_pages

from .settings import PREFIX


_SHOWN: Final = 5


def _top(head: str) -> str:
    """Return the first path segment of a literal route start, with its slash."""
    cut = head.find("/")
    return head if cut < 0 else head[: cut + 1]


@register(Tags.urls, NEXT)
def check_router_scope(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn about the `all` scope over routes that serve no page (`next.W191`).

    The router fetches each such link before it loads it as a document.
    """
    current = router_settings()
    if not router_enabled() or current.scope is not RouterScope.ALL:
        return []
    script = get_script_prefix()
    kept = exclude_prefixes()
    prefixes = sorted(
        {
            script + top
            for row in route_rows()
            if not (row.page or row.framework)
            and (top := _top(row.heads[0]))
            and not any((script + top).startswith(prefix) for prefix in kept)
        }
    )
    if not prefixes:
        return []
    listed = ", ".join(map(repr, prefixes[:_SHOWN]))
    more = f" and {len(prefixes) - _SHOWN} more" if len(prefixes) > _SHOWN else ""
    exclude = [*current.exclude, *prefixes]
    return [
        DjangoWarning(
            f"{PREFIX}['SCOPE'] is 'all' and the URLconf serves routes that are no "
            f"page under {listed}{more}. The client router fetches each such link "
            "before it loads the link as a document, and prefetches it on hover.",
            hint=f"Drop SCOPE for its default 'pages', or set {PREFIX}['EXCLUDE'] "
            f"to {exclude!r}.",
            obj=settings,
            id="next.W191",
        )
    ]


def _named(page: Stranded) -> str:
    """Return a page loaded as a document, and what keeps the table off it."""
    if page.by is None:
        return f"{page.route!r} (a converter or a regex the table cannot carry)"
    return f"{page.route!r} (behind {page.by!r}, which the table reads wider)"


@register(Tags.urls, NEXT)
def check_stranded_pages(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn about the pages the `pages` scope loads as a document (`next.W192`)."""
    if not router_enabled() or router_settings().scope is not RouterScope.PAGES:
        return []
    found = stranded_pages()
    if not found:
        return []
    listed = ", ".join(map(_named, found[:_SHOWN]))
    more = f" and {len(found) - _SHOWN} more" if len(found) > _SHOWN else ""
    return [
        DjangoWarning(
            f"{PREFIX}['SCOPE'] is 'pages' and the route table cannot own the pages "
            f"{listed}{more}. The client router loads each link to them as a document.",
            hint="Use a built-in converter or a regex with ASCII classes such as [0-9] "
            "for \\d, move the page ahead of the route in its way, or mark its links "
            f"with {ROUTER_ATTR}.",
            obj=settings,
            id="next.W192",
        )
    ]


__all__ = ["check_router_scope", "check_stranded_pages"]
