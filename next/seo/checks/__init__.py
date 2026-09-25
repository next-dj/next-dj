"""System checks for the `sitemap.py`, `robots.py` and `robots.txt` of every page tree.

Importing the package registers every check, and each submodule names the ids it owns.
"""

from .robots import check_robots_disallow, check_robots_file, check_robots_single_source
from .routes import check_seo_route_collisions, check_seo_routes_at_host_root
from .sitemaps import (
    check_sitemap_dynamic_routes,
    check_sitemap_items_trails,
    check_sitemap_noindex_items,
    check_sitemap_section_labels,
    check_sitemap_templates,
)
from .sources import (
    check_seo_module_attributes,
    check_seo_module_imports,
    check_seo_sources_below_root,
    check_sitemap_items_files,
)


__all__ = [
    "check_robots_disallow",
    "check_robots_file",
    "check_robots_single_source",
    "check_seo_module_attributes",
    "check_seo_module_imports",
    "check_seo_route_collisions",
    "check_seo_routes_at_host_root",
    "check_seo_sources_below_root",
    "check_sitemap_dynamic_routes",
    "check_sitemap_items_files",
    "check_sitemap_items_trails",
    "check_sitemap_noindex_items",
    "check_sitemap_section_labels",
    "check_sitemap_templates",
]
