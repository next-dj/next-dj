"""System checks for the SEO sources and the sitemap backends.

Importing the package registers every check.
"""

from .backends import check_seo_settings
from .robots import (
    check_robots_disallow,
    check_seo_single_sources,
    check_seo_text_files,
)
from .routes import check_seo_route_collisions, check_seo_routes_at_host_root
from .sitemaps import (
    check_sitemap_dynamic_routes,
    check_sitemap_excluded_items,
    check_sitemap_i18n_options,
    check_sitemap_items_trails,
    check_sitemap_noindex_items,
    check_sitemap_section_collisions,
    check_sitemap_section_labels,
    check_sitemap_templates,
)
from .sources import (
    check_seo_module_annotations,
    check_seo_module_attributes,
    check_seo_module_imports,
    check_seo_sources_below_root,
    check_seo_sources_on_closed_site,
    check_sitemap_items_files,
)


__all__ = [
    "check_robots_disallow",
    "check_seo_module_annotations",
    "check_seo_module_attributes",
    "check_seo_module_imports",
    "check_seo_route_collisions",
    "check_seo_routes_at_host_root",
    "check_seo_settings",
    "check_seo_single_sources",
    "check_seo_sources_below_root",
    "check_seo_sources_on_closed_site",
    "check_seo_text_files",
    "check_sitemap_dynamic_routes",
    "check_sitemap_excluded_items",
    "check_sitemap_i18n_options",
    "check_sitemap_items_files",
    "check_sitemap_items_trails",
    "check_sitemap_noindex_items",
    "check_sitemap_section_collisions",
    "check_sitemap_section_labels",
    "check_sitemap_templates",
]
