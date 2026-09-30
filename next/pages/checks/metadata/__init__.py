"""System checks for page metadata and its settings tier."""

from .head import (
    check_metadata_head_literals,
    check_metadata_head_tags,
    check_metadata_social_folds,
)
from .ld import check_metadata_jsonld
from .scope import check_metadata_settings_scope
from .shape import (
    check_metadata_callable_returns_mapping,
    check_metadata_enum_values,
    check_metadata_hreflang_patterns,
    check_metadata_noindex_canonical,
    check_metadata_parent_parameter,
    check_metadata_registration_files,
    check_metadata_url_schemes,
    check_page_metadata_shape,
)
from .templates import check_metadata_tag_rendered
from .titles import check_metadata_title_templates


__all__ = [
    "check_metadata_callable_returns_mapping",
    "check_metadata_enum_values",
    "check_metadata_head_literals",
    "check_metadata_head_tags",
    "check_metadata_hreflang_patterns",
    "check_metadata_jsonld",
    "check_metadata_noindex_canonical",
    "check_metadata_parent_parameter",
    "check_metadata_registration_files",
    "check_metadata_settings_scope",
    "check_metadata_social_folds",
    "check_metadata_tag_rendered",
    "check_metadata_title_templates",
    "check_metadata_url_schemes",
    "check_page_metadata_shape",
]
