"""The site identity, its origin, name and indexability, shared by every head and route.

Checks register through `next.checks`, since `next.pages` imports this package early.
"""

from .config import SiteConfig, site_config, site_indexable, site_origin, site_url
from .errors import SiteOriginError


__all__ = [
    "SiteConfig",
    "SiteOriginError",
    "site_config",
    "site_indexable",
    "site_origin",
    "site_url",
]
