"""Aggregate system-check registration for all `next-dj` subpackages.

Importing a helper from this module triggers registration of all
`@register` side effects by loading each subpackage's `checks` module.
Re-exports are resolved lazily so that subpackage checks modules can
freely import from `next.checks.common` without cycling back through
this package.
"""

from __future__ import annotations

from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from next.components.checks import (
        check_component_py_no_pages_context,
        check_cross_root_component_name_conflicts,
        check_duplicate_component_names,
        check_next_components_configuration,
    )
    from next.conf.checks import check_next_framework_unknown_top_level_keys
    from next.pages.checks import (
        _has_template_or_djx,
        check_context_functions,
        check_layout_templates,
        check_page_functions,
        check_pages_structure,
        check_request_in_context,
    )
    from next.pages.loaders import _load_python_module
    from next.urls.checks import (
        check_duplicate_url_parameters,
        check_next_pages_configuration,
        check_url_patterns,
    )


_LAZY_ATTRIBUTES: dict[str, tuple[str, str]] = {
    "check_component_py_no_pages_context": (
        "next.components.checks",
        "check_component_py_no_pages_context",
    ),
    "check_cross_root_component_name_conflicts": (
        "next.components.checks",
        "check_cross_root_component_name_conflicts",
    ),
    "check_duplicate_component_names": (
        "next.components.checks",
        "check_duplicate_component_names",
    ),
    "check_next_components_configuration": (
        "next.components.checks",
        "check_next_components_configuration",
    ),
    "check_next_framework_unknown_top_level_keys": (
        "next.conf.checks",
        "check_next_framework_unknown_top_level_keys",
    ),
    "check_context_functions": ("next.pages.checks", "check_context_functions"),
    "check_layout_templates": ("next.pages.checks", "check_layout_templates"),
    "check_page_functions": ("next.pages.checks", "check_page_functions"),
    "check_pages_structure": ("next.pages.checks", "check_pages_structure"),
    "check_request_in_context": ("next.pages.checks", "check_request_in_context"),
    "_has_template_or_djx": ("next.pages.checks", "_has_template_or_djx"),
    "_load_python_module": ("next.pages.loaders", "_load_python_module"),
    "check_duplicate_url_parameters": (
        "next.urls.checks",
        "check_duplicate_url_parameters",
    ),
    "check_next_pages_configuration": (
        "next.urls.checks",
        "check_next_pages_configuration",
    ),
    "check_url_patterns": ("next.urls.checks", "check_url_patterns"),
}


def register_all() -> None:
    """Import each subpackage's `checks` module to register its hooks."""
    import importlib  # noqa: PLC0415

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
    target = _LAZY_ATTRIBUTES.get(name)
    if target is None:
        msg = f"module {__name__!r} has no attribute {name!r}"
        raise AttributeError(msg)
    module_name, attr_name = target
    import importlib  # noqa: PLC0415

    module = importlib.import_module(module_name)
    return getattr(module, attr_name)


__all__ = [
    "_has_template_or_djx",
    "_load_python_module",
    "check_component_py_no_pages_context",
    "check_context_functions",
    "check_cross_root_component_name_conflicts",
    "check_duplicate_component_names",
    "check_duplicate_url_parameters",
    "check_layout_templates",
    "check_next_components_configuration",
    "check_next_framework_unknown_top_level_keys",
    "check_next_pages_configuration",
    "check_page_functions",
    "check_pages_structure",
    "check_request_in_context",
    "check_url_patterns",
    "register_all",
]
