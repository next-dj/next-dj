"""System checks for the partial-rendering subsystem.

Importing the package registers every check, and each submodule names the ids it owns.
"""

from .backends import (
    check_asset_version_moves_between_deploys,
    check_manifest_version_has_manifest_storage,
    check_partial_backend_names_a_path,
    check_partial_backends_is_a_list,
    check_single_partial_backend,
)
from .codes import (
    E_BACKEND_WITHOUT_PATH,
    E_BACKENDS_NOT_A_LIST,
    E_COMPOSED_TEMPLATE_SYNTAX,
    E_CONTEXT_ZONE_UNKNOWN,
    E_DUPLICATE_ZONE,
    E_LAZY_WITHOUT_PLACEHOLDER,
    E_NON_ASCII_ZONE,
    E_OP_BAD_NAME,
    E_OP_SHADOWS_BUILTIN,
    E_ZONE_IN_COMPONENT,
    E_ZONE_IN_FOR,
    E_ZONE_IN_IF,
    W_ASSET_VERSION_FROZEN,
    W_FORM_BACKEND_NOT_AWARE,
    W_FORM_IN_FOR_NO_KEY,
    W_MANIFEST_VERSION_NO_STORAGE,
    W_TOO_MANY_BACKENDS,
    W_WITH_OVER_ZONE,
)
from .forms import check_form_backend_partial_aware, check_repeated_form_has_key
from .ops import check_custom_patch_ops_well_formed
from .pages import reset_composed_pages_memo
from .templates import check_composed_templates_compile
from .zones import (
    check_context_zone_names_exist,
    check_duplicate_zone_names,
    check_lazy_zone_has_placeholder,
    check_no_zone_in_component,
    check_with_directly_over_zone,
    check_zone_name_is_slug,
    check_zone_not_in_if,
    check_zone_not_in_loop,
)


__all__ = [
    "E_BACKENDS_NOT_A_LIST",
    "E_BACKEND_WITHOUT_PATH",
    "E_COMPOSED_TEMPLATE_SYNTAX",
    "E_CONTEXT_ZONE_UNKNOWN",
    "E_DUPLICATE_ZONE",
    "E_LAZY_WITHOUT_PLACEHOLDER",
    "E_NON_ASCII_ZONE",
    "E_OP_BAD_NAME",
    "E_OP_SHADOWS_BUILTIN",
    "E_ZONE_IN_COMPONENT",
    "E_ZONE_IN_FOR",
    "E_ZONE_IN_IF",
    "W_ASSET_VERSION_FROZEN",
    "W_FORM_BACKEND_NOT_AWARE",
    "W_FORM_IN_FOR_NO_KEY",
    "W_MANIFEST_VERSION_NO_STORAGE",
    "W_TOO_MANY_BACKENDS",
    "W_WITH_OVER_ZONE",
    "check_asset_version_moves_between_deploys",
    "check_composed_templates_compile",
    "check_context_zone_names_exist",
    "check_custom_patch_ops_well_formed",
    "check_duplicate_zone_names",
    "check_form_backend_partial_aware",
    "check_lazy_zone_has_placeholder",
    "check_manifest_version_has_manifest_storage",
    "check_no_zone_in_component",
    "check_partial_backend_names_a_path",
    "check_partial_backends_is_a_list",
    "check_repeated_form_has_key",
    "check_single_partial_backend",
    "check_with_directly_over_zone",
    "check_zone_name_is_slug",
    "check_zone_not_in_if",
    "check_zone_not_in_loop",
    "reset_composed_pages_memo",
]
