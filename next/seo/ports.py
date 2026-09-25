"""SEO routes implementation bound into the `next.ports` slot at app startup."""

from typing import TYPE_CHECKING, override

from django.urls import path

from next.ports import SeoRoutes

from .manager import seo_manager
from .views import robots, sitemap


if TYPE_CHECKING:
    from django.urls import URLPattern

    from next.ports import VersionSource


class SeoRoutesImpl(SeoRoutes):
    """Answer the routes the discovered sources call for, none without a source."""

    @override
    def patterns(self) -> "list[URLPattern]":
        """Return the sitemap routes and the robots route, each behind its source."""
        patterns: list[URLPattern] = []
        if seo_manager.has_sitemap():
            patterns.append(path("sitemap.xml", sitemap, name="sitemap"))
            patterns.append(
                path("sitemap-<slug:section>.xml", sitemap, name="sitemap_section")
            )
        if seo_manager.robots_source() is not None:
            patterns.append(path("robots.txt", robots, name="robots"))
        return patterns

    @override
    def version_source(self) -> "VersionSource":
        """Return the manager, whose version the lazy urlpatterns key on."""
        return seo_manager


__all__ = ["SeoRoutesImpl"]
