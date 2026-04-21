"""System checks for the components subsystem."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.checks import CheckMessage, Error, Tags, register

from next.checks.common import errors_for_unknown_keys
from next.conf import next_framework_settings

from .backends import FileComponentsBackend
from .manager import ComponentsManager


if TYPE_CHECKING:
    from pathlib import Path


_COMPONENT_BACKEND_SETTINGS_KEY = "DEFAULT_COMPONENT_BACKENDS"

_FILE_COMPONENT_BACKEND_CONFIG_KEYS = frozenset(
    {
        "BACKEND",
        "COMPONENTS_DIR",
        "DIRS",
    },
)


def _validate_single_component_backend(
    config: dict[str, object],
    index: int,
) -> list[CheckMessage]:
    """Validate required keys and types for one merged component backend dict."""
    prefix = f"NEXT_FRAMEWORK['{_COMPONENT_BACKEND_SETTINGS_KEY}'][{index}]"
    errors: list[CheckMessage] = [
        Error(
            f"{prefix} must specify {key}.",
            obj=settings,
            id="next.E031",
        )
        for key in ("BACKEND", "DIRS", "COMPONENTS_DIR")
        if key not in config
    ]
    if errors:
        return errors
    if not isinstance(config["BACKEND"], str):
        errors.append(
            Error(
                f"{prefix}.BACKEND must be a string.",
                obj=settings,
                id="next.E032",
            ),
        )
    if not isinstance(config["DIRS"], list):
        errors.append(
            Error(
                f"{prefix}.DIRS must be a list.",
                obj=settings,
                id="next.E032",
            ),
        )
    if not isinstance(config["COMPONENTS_DIR"], str):
        errors.append(
            Error(
                f"{prefix}.COMPONENTS_DIR must be a string.",
                obj=settings,
                id="next.E027",
            ),
        )
    errors.extend(
        errors_for_unknown_keys(
            config,
            allowed=_FILE_COMPONENT_BACKEND_CONFIG_KEYS,
            prefix=prefix,
        ),
    )
    return errors


@register(Tags.compatibility)
def check_next_components_configuration(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Validate `DEFAULT_COMPONENT_BACKENDS` shape in merged `NEXT_FRAMEWORK`."""
    raw = getattr(settings, "NEXT_FRAMEWORK", None)
    if raw is not None and not isinstance(raw, dict):
        return []

    backends = next_framework_settings.DEFAULT_COMPONENT_BACKENDS
    if not isinstance(backends, list):
        return [
            Error(
                "NEXT_FRAMEWORK['DEFAULT_COMPONENT_BACKENDS'] must be a list of "
                "backend configuration dictionaries.",
                obj=settings,
                id="next.E023",
            ),
        ]

    if len(backends) == 0:
        return [
            Error(
                "NEXT_FRAMEWORK['DEFAULT_COMPONENT_BACKENDS'] must contain at least "
                "one component backend entry.",
                obj=settings,
                id="next.E033",
            ),
        ]

    errors: list[CheckMessage] = []
    for i, config in enumerate(backends):
        if not isinstance(config, dict):
            errors.append(
                Error(
                    f"NEXT_FRAMEWORK['{_COMPONENT_BACKEND_SETTINGS_KEY}'][{i}] "
                    "must be a dictionary.",
                    obj=settings,
                    id="next.E002",
                ),
            )
            continue
        errors.extend(_validate_single_component_backend(config, i))

    return errors


@register(Tags.compatibility)
def check_duplicate_component_names(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Check that no two components share the same name within the same scope."""
    errors: list[CheckMessage] = []
    configs = next_framework_settings.DEFAULT_COMPONENT_BACKENDS
    if not isinstance(configs, list) or not configs:
        return errors
    manager = ComponentsManager()
    manager._reload_config()
    for backend in manager._backends:
        if not isinstance(backend, FileComponentsBackend):
            continue
        backend._ensure_loaded()
        seen: dict[tuple[Path, str], list[tuple[str, str]]] = {}

        for info in backend._registry:
            key = (info.scope_root, info.name)
            if key not in seen:
                seen[key] = []
            path_str = str(info.template_path or info.module_path or "")
            seen[key].append((info.scope_relative, path_str))

        for (_scope_root, name), entries in seen.items():
            if len(entries) > 1:
                paths_str = ", ".join(p for _sr, p in entries if p)
                errors.append(
                    Error(
                        f'Component name "{name}" is registered more than once '
                        f"within the same scope: {paths_str}",
                        obj=settings,
                        id="next.E020",
                    ),
                )
    return errors


@register(Tags.compatibility)
def check_cross_root_component_name_conflicts(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Reject one component name in the root route scope on more than one page tree."""
    errors: list[CheckMessage] = []
    configs = next_framework_settings.DEFAULT_COMPONENT_BACKENDS
    if not isinstance(configs, list) or not configs:
        return errors
    manager = ComponentsManager()
    manager._reload_config()
    for backend in manager._backends:
        if not isinstance(backend, FileComponentsBackend):
            continue
        backend._ensure_loaded()
        by_name: dict[str, dict[Path, str]] = {}
        for info in backend._registry:
            if (info.scope_relative or "").strip():
                continue
            root = info.resolved_scope_root
            path_str = str(info.template_path or info.module_path or "")
            roots_for_name = by_name.setdefault(info.name, {})
            roots_for_name.setdefault(root, path_str)
        for name, roots_map in sorted(by_name.items()):
            if len(roots_map) <= 1:
                continue
            details = ". ".join(
                f"{root}: {path_str or '?'}"
                for root, path_str in sorted(
                    roots_map.items(),
                    key=lambda item: str(item[0]),
                )
            )
            errors.append(
                Error(
                    f'Component name "{name}" uses the shared root namespace on more '
                    f"than one page tree. Each distinct directory root in "
                    f"NEXT_FRAMEWORK DEFAULT_PAGE_BACKENDS DIRS must expose unique "
                    f"names at the root route scope. Locations: {details}.",
                    obj=settings,
                    id="next.E034",
                ),
            )
    return errors


def _component_py_uses_pages_context(file_path: Path) -> bool:
    """Return True if `component.py` imports `context` from `next.pages`."""
    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.ImportFrom)
            and getattr(node, "module", None) == "next.pages"
        ):
            for alias in node.names:
                if getattr(alias, "name", None) == "context":
                    return True
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "page"
            and node.attr == "context"
        ):
            return True
    return False


@register(Tags.compatibility)
def check_component_py_no_pages_context(
    *_args: object,
    **_kwargs: object,
) -> list[CheckMessage]:
    """Check that `component.py` files do not use `context` from `next.pages`."""
    errors: list[CheckMessage] = []
    configs = next_framework_settings.DEFAULT_COMPONENT_BACKENDS
    if not isinstance(configs, list) or not configs:
        return errors
    manager = ComponentsManager()
    manager._reload_config()
    for backend in manager._backends:
        if not isinstance(backend, FileComponentsBackend):
            continue
        backend._ensure_loaded()

        for info in backend._registry:
            if info.module_path is None:
                continue
            if not info.module_path.exists():
                continue
            if _component_py_uses_pages_context(info.module_path):
                errors.append(
                    Error(
                        "component.py must not use context from next.pages. "
                        "Use component context from next.components instead.",
                        obj=str(info.module_path),
                        id="next.E021",
                    ),
                )
    return errors


__all__ = [
    "check_component_py_no_pages_context",
    "check_cross_root_component_name_conflicts",
    "check_duplicate_component_names",
    "check_next_components_configuration",
]
