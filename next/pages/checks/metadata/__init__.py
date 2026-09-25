"""System checks for page metadata, its settings tier and the opt-in SEO audits."""

from .audits import (
    check_seo_alternates,
    check_seo_canonical,
    check_seo_description,
    check_seo_titles,
)
from .scope import check_metadata_settings_scope
from .shape import (
    check_metadata_absolute_urls,
    check_metadata_callable_returns_mapping,
    check_metadata_hreflang_patterns,
    check_metadata_noindex_canonical,
    check_metadata_registration_files,
    check_metadata_url_schemes,
    check_page_metadata_shape,
    check_single_metadata_callable,
)
from .templates import check_metadata_tag_rendered
from .titles import check_metadata_title_templates


__all__ = [
    "check_metadata_absolute_urls",
    "check_metadata_callable_returns_mapping",
    "check_metadata_hreflang_patterns",
    "check_metadata_noindex_canonical",
    "check_metadata_registration_files",
    "check_metadata_settings_scope",
    "check_metadata_tag_rendered",
    "check_metadata_title_templates",
    "check_metadata_url_schemes",
    "check_page_metadata_shape",
    "check_seo_alternates",
    "check_seo_canonical",
    "check_seo_description",
    "check_seo_titles",
    "check_single_metadata_callable",
]
