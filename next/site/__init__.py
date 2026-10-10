"""The site identity, its origin, name and indexability, read by every head and route.

Checks register through `next.checks`, since `next.pages` imports this package early.
"""

from .config import site_indexable, site_origin, site_url
from .errors import SiteOriginError


__all__ = ["SiteOriginError", "site_indexable", "site_origin", "site_url"]
