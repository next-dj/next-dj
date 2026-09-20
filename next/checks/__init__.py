"""Aggregate system-check registration for all `next-dj` subpackages.

Importing a helper loads every subpackage's `checks` module and so triggers its
`@register` side effects. Re-exports resolve lazily to break the cycle back here.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING


NEXT: str = "next"
"""Shared system-check tag that selects every `next-dj` check."""


if TYPE_CHECKING:
    from next.apps.checks import (
        check_builtin_tag_libraries_complete,
        check_django_templates_backend_present,
    )
    from next.components.checks import (
        check_component_context_registration_files,
        check_component_module_imports,
        check_component_py_no_pages_context,
        check_cross_root_component_name_conflicts,
        check_duplicate_component_names,
        check_next_components_configuration,
    )
    from next.conf.checks import (
        check_next_framework_unknown_top_level_keys,
        check_next_framework_value_types,
    )
    from next.forms.checks import (
        check_action_applied_to_class,
        check_action_guard_permissions,
        check_component_widget_components,
        check_component_widget_field_types,
        check_form_action_backends_configuration,
        check_form_action_collisions,
        check_form_anchor_files,
        check_form_wizard_backend,
        check_form_wizard_sessions,
        check_form_wizard_steps,
        check_forms_outside_base_dir,
        check_instance_from_url_on_non_model_form,
        check_instance_from_url_unknown_field,
        check_invalid_form_meta_scope,
        check_shared_action_name_collisions,
        check_success_message_framework,
        check_wizard_step_actions,
        check_wizard_step_field_collisions,
        check_wizard_step_file_fields,
        check_wizard_url_param_route,
    )
    from next.pages.checks import (
        check_context_functions,
        check_context_processor_signature,
        check_context_reads_foreign_zone,
        check_context_registration_files,
        check_layout_templates,
        check_page_functions,
        check_page_module_imports,
        check_pages_structure,
        check_request_in_context,
        check_single_keyless_context,
        check_template_loaders,
        check_unrouted_working_directory_pages,
    )
    from next.partial.checks import (
        check_composed_templates_compile,
        check_context_zone_names_exist,
        check_custom_patch_ops_well_formed,
        check_duplicate_zone_names,
        check_form_backend_partial_aware,
        check_lazy_zone_has_placeholder,
        check_manifest_version_has_manifest_storage,
        check_no_zone_in_component,
        check_partial_backend_names_a_path,
        check_partial_backends_is_a_list,
        check_repeated_form_has_key,
        check_single_partial_backend,
        check_with_directly_over_zone,
        check_zone_name_is_slug,
        check_zone_not_in_if,
        check_zone_not_in_loop,
    )
    from next.static.checks import (
        check_app_directories_finder,
        check_asset_kinds_are_loadable,
        check_inline_asset_bodies_are_loadable,
        check_js_context_serializer,
        check_reserved_js_context_keys,
        check_static_backends,
    )
    from next.urls.checks import (
        check_next_pages_configuration,
        check_reverse_name_collisions,
        check_url_patterns,
    )


_LAZY_SOURCES_BY_MODULE: dict[str, tuple[str, ...]] = {
    "next.apps.checks": (
        "check_builtin_tag_libraries_complete",
        "check_django_templates_backend_present",
    ),
    "next.components.checks": (
        "check_component_context_registration_files",
        "check_component_module_imports",
        "check_component_py_no_pages_context",
        "check_cross_root_component_name_conflicts",
        "check_duplicate_component_names",
        "check_next_components_configuration",
    ),
    "next.conf.checks": (
        "check_next_framework_unknown_top_level_keys",
        "check_next_framework_value_types",
    ),
    "next.forms.checks": (
        "check_action_applied_to_class",
        "check_action_guard_permissions",
        "check_component_widget_components",
        "check_component_widget_field_types",
        "check_form_action_backends_configuration",
        "check_form_action_collisions",
        "check_form_anchor_files",
        "check_form_wizard_backend",
        "check_form_wizard_sessions",
        "check_form_wizard_steps",
        "check_forms_outside_base_dir",
        "check_instance_from_url_on_non_model_form",
        "check_instance_from_url_unknown_field",
        "check_invalid_form_meta_scope",
        "check_shared_action_name_collisions",
        "check_success_message_framework",
        "check_wizard_step_actions",
        "check_wizard_step_field_collisions",
        "check_wizard_step_file_fields",
        "check_wizard_url_param_route",
    ),
    "next.pages.checks": (
        "check_context_functions",
        "check_context_processor_signature",
        "check_context_reads_foreign_zone",
        "check_context_registration_files",
        "check_layout_templates",
        "check_page_functions",
        "check_page_module_imports",
        "check_pages_structure",
        "check_request_in_context",
        "check_single_keyless_context",
        "check_template_loaders",
        "check_unrouted_working_directory_pages",
    ),
    "next.partial.checks": (
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
    ),
    "next.static.checks": (
        "check_app_directories_finder",
        "check_asset_kinds_are_loadable",
        "check_inline_asset_bodies_are_loadable",
        "check_js_context_serializer",
        "check_reserved_js_context_keys",
        "check_static_backends",
    ),
    "next.urls.checks": (
        "check_next_pages_configuration",
        "check_reverse_name_collisions",
        "check_url_patterns",
    ),
}


_LAZY_ATTRIBUTES: dict[str, str] = {
    name: module for module, names in _LAZY_SOURCES_BY_MODULE.items() for name in names
}

__all__ = [
    "NEXT",
    "check_action_applied_to_class",
    "check_action_guard_permissions",
    "check_app_directories_finder",
    "check_asset_kinds_are_loadable",
    "check_builtin_tag_libraries_complete",
    "check_component_context_registration_files",
    "check_component_module_imports",
    "check_component_py_no_pages_context",
    "check_component_widget_components",
    "check_component_widget_field_types",
    "check_composed_templates_compile",
    "check_context_functions",
    "check_context_processor_signature",
    "check_context_reads_foreign_zone",
    "check_context_registration_files",
    "check_context_zone_names_exist",
    "check_cross_root_component_name_conflicts",
    "check_custom_patch_ops_well_formed",
    "check_django_templates_backend_present",
    "check_duplicate_component_names",
    "check_duplicate_zone_names",
    "check_form_action_backends_configuration",
    "check_form_action_collisions",
    "check_form_anchor_files",
    "check_form_backend_partial_aware",
    "check_form_wizard_backend",
    "check_form_wizard_sessions",
    "check_form_wizard_steps",
    "check_forms_outside_base_dir",
    "check_inline_asset_bodies_are_loadable",
    "check_instance_from_url_on_non_model_form",
    "check_instance_from_url_unknown_field",
    "check_invalid_form_meta_scope",
    "check_js_context_serializer",
    "check_layout_templates",
    "check_lazy_zone_has_placeholder",
    "check_manifest_version_has_manifest_storage",
    "check_next_components_configuration",
    "check_next_framework_unknown_top_level_keys",
    "check_next_framework_value_types",
    "check_next_pages_configuration",
    "check_no_zone_in_component",
    "check_page_functions",
    "check_page_module_imports",
    "check_pages_structure",
    "check_partial_backend_names_a_path",
    "check_partial_backends_is_a_list",
    "check_repeated_form_has_key",
    "check_request_in_context",
    "check_reserved_js_context_keys",
    "check_reverse_name_collisions",
    "check_shared_action_name_collisions",
    "check_single_keyless_context",
    "check_single_partial_backend",
    "check_static_backends",
    "check_success_message_framework",
    "check_template_loaders",
    "check_unrouted_working_directory_pages",
    "check_url_patterns",
    "check_with_directly_over_zone",
    "check_wizard_step_actions",
    "check_wizard_step_field_collisions",
    "check_wizard_step_file_fields",
    "check_wizard_url_param_route",
    "check_zone_name_is_slug",
    "check_zone_not_in_if",
    "check_zone_not_in_loop",
    "register_all",
    "reset_check_caches",
]


def register_all() -> None:
    """Import each subpackage's `checks` module to register its hooks.

    The map already lists every area, so a new entry registers its checks simply
    by appearing there, not by a second listing here.
    """
    for module_name in _LAZY_SOURCES_BY_MODULE:
        importlib.import_module(module_name)


def reset_check_caches() -> None:
    """Drop every per-run check cache so the next run rebuilds from disk.

    Tests that mutate the page or component tree in place need this, since the caches
    otherwise freeze the scanned state for the process lifetime.
    """
    # Reached by name because each of these pulls in an area package that imports
    # its own `checks` module, and that module imports `NEXT` back from here.
    importlib.import_module("next.discovery").reset_router_manager_cache()
    sources = importlib.import_module("next.components.sources")
    sources.reset_components_manager_cache()
    importlib.import_module("next.partial.checks").reset_composed_pages_memo()
    importlib.import_module("next.urls.checks").reset_collected_patterns_cache()
    importlib.import_module("next.pages.loaders").reset_module_memo()
    importlib.import_module("next.pages.manager").reset_context_registry()


def __getattr__(name: str) -> object:
    """Lazily resolve re-exports from the per-subpackage `checks` modules."""
    module_name = _LAZY_ATTRIBUTES.get(name)
    if module_name is None:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    return getattr(importlib.import_module(module_name), name)


def __dir__() -> list[str]:
    """List exactly the names `__all__` carries, so both views of the facade agree."""
    return sorted(__all__)
