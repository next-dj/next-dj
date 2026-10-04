"""The SEO routes to mount at the host root when `next.urls` is under a prefix.

Only routes whose source exists are mounted, so a later project view still answers.
"""

from typing import Final

from django.urls import URLPattern, path

from .routes import (
    HOST_ROOT_NAMESPACE,
    ROBOTS_NAME,
    ROBOTS_ROUTE,
    SECTION_NAME,
    SECTION_ROUTE,
    SITEMAP_NAME,
    SITEMAP_ROUTE,
    SeoPatterns,
)
from .views import robots_view, sitemap_view


PATTERNS: Final[tuple[URLPattern, ...]] = (
    path(SITEMAP_ROUTE, sitemap_view, name=SITEMAP_NAME),
    path(SECTION_ROUTE, sitemap_view, name=SECTION_NAME),
    path(ROBOTS_ROUTE, robots_view, name=ROBOTS_NAME),
)
"""Every SEO route, served or not."""

app_name = HOST_ROOT_NAMESPACE
urlpatterns = SeoPatterns(PATTERNS)
