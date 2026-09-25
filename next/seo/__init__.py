"""Sitemap and robots built from the page trees, on top of `django.contrib.sitemaps`."""

from . import checks, signals
from .errors import SeoBaseError, SitemapTrailError
from .manager import seo_manager, sitemap
from .markers import Entry, Rule
from .sitemaps import RouteSitemap


__all__ = [
    "Entry",
    "RouteSitemap",
    "Rule",
    "SeoBaseError",
    "SitemapTrailError",
    "checks",
    "seo_manager",
    "signals",
    "sitemap",
]
