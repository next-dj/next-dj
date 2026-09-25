"""SEO routes implementation bound into the `next.ports` slot at app startup."""

from typing import TYPE_CHECKING, override

from next.ports import SeoRoutes

from .manager import seo_manager
from .urls import urlpatterns


if TYPE_CHECKING:
    from django.urls import URLPattern


class SeoRoutesImpl(SeoRoutes):
    """Answer the sitemap and robots routes of the sources the page trees declare.

    A route without its source stays out, so a project's own view there still answers.
    """

    @override
    def patterns(self) -> "list[URLPattern]":
        """Return the routes of `next.seo.urls` whose source a page tree declares."""
        served: set[str] = set()
        if seo_manager.has_sitemap():
            served.update(("sitemap", "sitemap_section"))
        if seo_manager.robots_source() is not None:
            served.add("robots")
        return [pattern for pattern in urlpatterns if pattern.name in served]


__all__ = ["SeoRoutesImpl"]
