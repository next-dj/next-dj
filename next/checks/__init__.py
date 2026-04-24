"""Aggregate system-check registration for all `next-dj` subpackages.

Importing a helper from this module triggers registration of all
`@register` side effects by loading each subpackage's `checks` module.
Re-exports are resolved lazily so that subpackage checks modules can
freely import from `next.checks.common` without cycling back through
this package.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from next.components.checks import (
        check_component_py_no_pages_context,
        check_cross_root_component_name_conflicts,
        check_duplicate_component_names,
        check_next_components_configuration,
    )
    from next.conf.checks import check_next_framework_unknown_top_level_keys
    from next.forms.checks import check_form_action_collisions
    from next.pages.checks import (
        _has_template_or_djx,
        check_context_functions,
        check_context_processor_signature,
        check_layout_templates,
        check_page_functions,
        check_pages_structure,
        check_request_in_context,
    )
    from next.pages.loaders import _load_python_module
    from next.static.checks import check_js_context_serializer
    from next.urls.checks import (
        check_duplicate_url_parameters,
        check_next_pages_configuration,
        check_url_patterns,
    )


_LAZY_SOURCES_BY_MODULE: dict[str, tuple[str, ...]] = {
    "next.components.checks": (
        "check_component_py_no_pages_context",
        "check_cross_root_component_name_conflicts",
        "check_duplicate_component_names",
        "check_next_components_configuration",
    ),
    "next.conf.checks": ("check_next_framework_unknown_top_level_keys",),
    "next.forms.checks": ("check_form_action_collisions",),
    "next.pages.checks": (
        "_has_template_or_djx",
        "check_context_functions",
        "check_context_processor_signature",
        "check_layout_templates",
        "check_page_functions",
        "check_pages_structure",
        "check_request_in_context",
        "check_template_loaders",
    ),
    "next.pages.loaders": ("_load_python_module",),
    "next.static.checks": ("check_js_context_serializer",),
    "next.urls.checks": (
        "check_duplicate_url_parameters",
        "check_next_pages_configuration",
        "check_url_patterns",
    ),
}


_LAZY_ATTRIBUTES: dict[str, str] = {
    name: module for module, names in _LAZY_SOURCES_BY_MODULE.items() for name in names
}


def register_all() -> None:
    """Import each subpackage's `checks` module to register its hooks."""
    for module_name in (
        "next.conf.checks",
        "next.pages.checks",
        "next.urls.checks",
        "next.components.checks",
        "next.forms.checks",
        "next.server.checks",
        "next.static.checks",
    ):
        importlib.import_module(module_name)


def __getattr__(name: str) -> object:
    """Lazily resolve re-exports from the per-subpackage `checks` modules."""
    module_name = _LAZY_ATTRIBUTES.get(name)
    if module_name is None:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    return getattr(importlib.import_module(module_name), name)


__all__ = ["register_all", *sorted(_LAZY_ATTRIBUTES)]  # noqa: PLE0604
