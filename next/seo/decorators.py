"""The `sitemap` object a `sitemap.py` declares the URLs of its dynamic trails with."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from next.introspect import registering_file

from .registry import SitemapItemsEntry, sitemap_items_registry


if TYPE_CHECKING:
    from collections.abc import Callable

    from .registry import KwargsOf


class SitemapDeclaration:
    """The `sitemap` object a `sitemap.py` decorates its items callables with."""

    def items[F: Callable[..., Any]](
        self,
        trail: str,
        *,
        kwargs: KwargsOf | None = None,
        lastmod: str | None = None,
        section: str | None = None,
    ) -> Callable[[F], F]:
        """Register the decorated callable as the URLs of `trail` in this tree.

        The tree is the one of the running `sitemap.py`, wherever the callable lives.
        """
        registered_from = registering_file()

        def register(func: F) -> F:
            sitemap_items_registry.register(
                SitemapItemsEntry(
                    file=registered_from,
                    trail=trail,
                    func=func,
                    section=section,
                    kwargs=kwargs,
                    lastmod=lastmod,
                )
            )
            return func

        return register


sitemap = SitemapDeclaration()


__all__ = ["SitemapDeclaration", "sitemap"]
