"""The sitemap and robots.txt routes built from the page trees and sitemap backends."""

from . import checks, signals
from .backends import PageTreeSitemapBackend, SitemapBackend
from .decorators import sitemap
from .errors import (
    RobotsRuleError,
    SeoSourceImportError,
    SitemapEntryError,
    SitemapTrailError,
)
from .manager import seo_manager
from .markers import RobotsRule, SitemapEntry


__all__ = [
    "PageTreeSitemapBackend",
    "RobotsRule",
    "RobotsRuleError",
    "SeoSourceImportError",
    "SitemapBackend",
    "SitemapEntry",
    "SitemapEntryError",
    "SitemapTrailError",
    "checks",
    "seo_manager",
    "signals",
    "sitemap",
]
