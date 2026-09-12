"""Aggregate system-check registration for all `next-dj` subpackages.

Importing a helper from this module triggers registration of all `@register` side
effects by loading each subpackage's `checks` module. Re-exports are resolved lazily so
that subpackage checks modules can freely import from `next.checks.common` without
cycling back through this package.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING


NEXT: str = "next"
"""Shared system-check tag that selects every `next-dj` check."""


if TYPE_CHECKING:
    from next.components.checks import (
        check_component_context_registration_files,
        check_component_py_no_pages_context,
        check_cross_root_component_name_conflicts,
        check_duplicate_component_names,
        check_next_components_configuration,
    )
    from next.conf.checks import (
        check_next_framework_unknown_top_level_keys,
        check_next_framework_value_types,
    )
    from next.forms.checks import check_form_action_collisions
    from next.pages.checks import (
        check_context_functions,
        check_context_processor_signature,
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
    from next.static.checks import (
        check_asset_kinds_are_loadable,
        check_inline_asset_bodies_are_loadable,
        check_js_context_serializer,
        check_reserved_js_context_keys,
    )
    from next.urls.checks import (
        check_next_pages_configuration,
        check_reverse_name_collisions,
        check_url_patterns,
    )


_LAZY_SOURCES_BY_MODULE: dict[str, tuple[str, ...]] = {
    "next.components.checks": (
        "check_component_context_registration_files",
        "check_component_py_no_pages_context",
        "check_cross_root_component_name_conflicts",
        "check_duplicate_component_names",
        "check_next_components_configuration",
    ),
    "next.conf.checks": (
        "check_next_framework_unknown_top_level_keys",
        "check_next_framework_value_types",
    ),
    "next.forms.checks": ("check_form_action_collisions",),
    "next.pages.checks": (
        "check_context_functions",
        "check_context_processor_signature",
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
    "next.static.checks": (
        "check_asset_kinds_are_loadable",
        "check_inline_asset_bodies_are_loadable",
        "check_js_context_serializer",
        "check_reserved_js_context_keys",
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
    "check_asset_kinds_are_loadable",
    "check_component_context_registration_files",
    "check_component_py_no_pages_context",
    "check_context_functions",
    "check_context_processor_signature",
    "check_context_registration_files",
    "check_cross_root_component_name_conflicts",
    "check_duplicate_component_names",
    "check_form_action_collisions",
    "check_inline_asset_bodies_are_loadable",
    "check_js_context_serializer",
    "check_layout_templates",
    "check_next_components_configuration",
    "check_next_framework_unknown_top_level_keys",
    "check_next_framework_value_types",
    "check_next_pages_configuration",
    "check_page_functions",
    "check_page_module_imports",
    "check_pages_structure",
    "check_request_in_context",
    "check_reserved_js_context_keys",
    "check_reverse_name_collisions",
    "check_single_keyless_context",
    "check_template_loaders",
    "check_unrouted_working_directory_pages",
    "check_url_patterns",
    "register_all",
    "reset_check_caches",
]


def register_all() -> None:
    """Import each subpackage's `checks` module to register its hooks."""
    for module_name in (
        "next.conf.checks",
        "next.pages.checks",
        "next.urls.checks",
        "next.components.checks",
        "next.forms.checks",
        "next.static.checks",
        "next.partial.checks",
        "next.apps.checks",
    ):
        importlib.import_module(module_name)


def reset_check_caches() -> None:
    """Drop every per-run check cache so the next run rebuilds from disk.

    Tests and scripts that invoke checks directly and mutate the page or
    component tree in place need this, since the caches otherwise freeze the
    scanned state for the lifetime of the process. The module memo and context
    registry are cleared together so a re-executed `page.py` repopulates the
    registry from its current source instead of keeping a stale `@context`.
    """
    importlib.import_module("next.checks.common").reset_router_manager_cache()
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
