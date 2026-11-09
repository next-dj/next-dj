"""The site identity, its origin, name and indexability, read by every head and route.

Checks register through `next.checks.catalog`, as `next.pages` imports this early.
"""

from .config import site_indexable, site_origin, site_url
from .errors import SiteOriginError


__all__ = ["SiteOriginError", "site_indexable", "site_origin", "site_url"]
