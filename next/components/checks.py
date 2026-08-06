"""System checks for the components subsystem."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from itertools import combinations
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.checks import CheckMessage, Error, register

from next.checks import NEXT
from next.checks.common import (
    RegistrationSubject,
    errors_for_unknown_keys,
    get_components_manager,
    import_backend_class,
    registration_file_errors,
)
from next.conf import next_framework_settings

from .backends import ComponentsBackend
from .context import component


if TYPE_CHECKING:
    from pathlib import Path


_COMPONENT_BACKEND_SETTINGS_KEY = "COMPONENT_BACKENDS"

# The scanner renders only this name, so a context bound anywhere else is dead,
# including one bound to the component.py next door.
_COMPONENT_CONTEXT_SUBJECT = RegistrationSubject(
    decorator="@component.context",
    anchor_name="component.py",
    render="component render",
    code="next.E075",
)

_FILE_COMPONENT_BACKEND_CONFIG_KEYS = frozenset({"BACKEND", "COMPONENTS_DIR", "DIRS"})

# The curated root re-exports the page decorator, so every spelling below names
# the same wrong `context` inside a component.py.
_PAGE_CONTEXT_MODULES = frozenset({"next", "next.pages"})
_PAGE_CONTEXT_OWNERS = frozenset({"next", "page", "next.page", "next.pages"})


def _backend_class_errors(dotted: str, prefix: str) -> list[CheckMessage]:
    """Import the dotted backend path and verify the family subclass."""
    try:
        klass = import_backend_class(dotted)
    except ImportError as exc:
        return [
            Error(
                f"{prefix}.BACKEND cannot be imported: {exc}.",
                obj=settings,
                id="next.E032",
            )
        ]
    if not (isinstance(klass, type) and issubclass(klass, ComponentsBackend)):
        return [
            Error(
                f"{prefix}.BACKEND is not a ComponentsBackend subclass.",
                obj=settings,
                id="next.E032",
            )
        ]
    return []


def _validate_single_component_backend(
    config: dict[str, object], index: int
) -> list[CheckMessage]:
    """Validate required keys and types for one merged component backend dict."""
    prefix = f"NEXT_FRAMEWORK['{_COMPONENT_BACKEND_SETTINGS_KEY}'][{index}]"
    errors: list[CheckMessage] = [
        Error(f"{prefix} must specify {key}.", obj=settings, id="next.E031")
        for key in ("BACKEND", "DIRS", "COMPONENTS_DIR")
        if key not in config
    ]
    if errors:
        return errors
    backend_path = config["BACKEND"]
    if not isinstance(backend_path, str):
        errors.append(
            Error(f"{prefix}.BACKEND must be a string.", obj=settings, id="next.E032")
        )
    else:
        errors.extend(_backend_class_errors(backend_path, prefix))
    if not isinstance(config["DIRS"], list):
        errors.append(
            Error(f"{prefix}.DIRS must be a list.", obj=settings, id="next.E032")
        )
    if not isinstance(config["COMPONENTS_DIR"], str):
        errors.append(
            Error(
                f"{prefix}.COMPONENTS_DIR must be a string.",
                obj=settings,
                id="next.E027",
            )
        )
    errors.extend(
        errors_for_unknown_keys(
            config, allowed=_FILE_COMPONENT_BACKEND_CONFIG_KEYS, prefix=prefix
        )
    )
    return errors


@register(NEXT)
def check_next_components_configuration(*args, **kwargs) -> list[CheckMessage]:
    """Validate `COMPONENT_BACKENDS` shape in merged `NEXT_FRAMEWORK`."""
    raw = getattr(settings, "NEXT_FRAMEWORK", None)
    if raw is not None and not isinstance(raw, dict):
        return []

    backends = next_framework_settings.COMPONENT_BACKENDS
    if not isinstance(backends, list):
        return [
            Error(
                "NEXT_FRAMEWORK['COMPONENT_BACKENDS'] must be a list of "
                "backend configuration dictionaries.",
                obj=settings,
                id="next.E023",
            )
        ]

    if len(backends) == 0:
        return [
            Error(
                "NEXT_FRAMEWORK['COMPONENT_BACKENDS'] must contain at least "
                "one component backend entry.",
                obj=settings,
                id="next.E033",
            )
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
                )
            )
            continue
        errors.extend(_validate_single_component_backend(config, i))

    return errors


def _checked_backends() -> tuple[ComponentsBackend, ...]:
    """Return the loaded backends of the components store the checks read."""
    return get_components_manager().backends


@register(NEXT)
def check_duplicate_component_names(*args, **kwargs) -> list[CheckMessage]:
    """Check that no two components share a name within one route scope.

    The scope is the pair the resolver scores on, so the same name under two
    route trails of one tree is the documented override rather than a clash.
    """
    errors: list[CheckMessage] = []
    configs = next_framework_settings.COMPONENT_BACKENDS
    if not isinstance(configs, list) or not configs:
        return errors
    for backend in _checked_backends():
        seen: dict[tuple[Path, str, str], list[str]] = {}

        for info in backend.iter_components():
            key = (info.scope_root, info.scope_relative or "", info.name)
            path_str = str(info.template_path or info.module_path or "")
            seen.setdefault(key, []).append(path_str)

        for (_scope_root, _scope_relative, name), paths in seen.items():
            if len(paths) > 1:
                paths_str = ", ".join(p for p in paths if p)
                errors.append(
                    Error(
                        f'Component name "{name}" is registered more than once '
                        f"within the same scope: {paths_str}",
                        obj=settings,
                        id="next.E020",
                    )
                )
    return errors


@dataclass(frozen=True, slots=True)
class _RootScopeEntry:
    """One component registered at the root scope of a single component root."""

    root: Path
    path: str
    everywhere: bool
    """True for a `COMPONENT_BACKENDS` root, whose components resolve from
    every template rather than only from below the root."""


def _root_scope_entries(
    backend: ComponentsBackend,
) -> dict[str, dict[Path, _RootScopeEntry]]:
    """Group the root-scope components of one backend by name, then by root."""
    by_name: dict[str, dict[Path, _RootScopeEntry]] = {}
    global_roots = frozenset(backend.global_component_roots())
    for info in backend.iter_components():
        if (info.scope_relative or "").strip():
            continue
        entry = _RootScopeEntry(
            root=info.resolved_scope_root,
            path=str(info.template_path or info.module_path or ""),
            everywhere=info.scope_root in global_roots,
        )
        by_name.setdefault(info.name, {}).setdefault(entry.root, entry)
    return by_name


def _resolution_is_ordering(first: _RootScopeEntry, second: _RootScopeEntry) -> bool:
    """Whether only registration order decides between two same-named components.

    A `COMPONENT_BACKENDS` root and a page tree score alike, and the resolver
    hands the page tree the win as a project-local override, so that pair is
    decided by a rule rather than by order. Two roots of the same kind score
    alike with nothing left to break the tie, but only where one template can
    reach both, which for page trees means one tree sitting inside the other.
    """
    if first.everywhere != second.everywhere:
        return False
    if first.everywhere:
        return True
    return first.root.is_relative_to(second.root) or second.root.is_relative_to(
        first.root
    )


def _order_decided_entries(
    entries: dict[Path, _RootScopeEntry],
) -> list[_RootScopeEntry]:
    """Return the roots that share one name with no rule to pick between them."""
    involved: dict[Path, _RootScopeEntry] = {}
    for first, second in combinations(entries.values(), 2):
        if _resolution_is_ordering(first, second):
            involved[first.root] = first
            involved[second.root] = second
    return sorted(involved.values(), key=lambda entry: str(entry.root))


@register(NEXT)
def check_cross_root_component_name_conflicts(*args, **kwargs) -> list[CheckMessage]:
    """Reject a root-scope name that only registration order resolves."""
    errors: list[CheckMessage] = []
    configs = next_framework_settings.COMPONENT_BACKENDS
    if not isinstance(configs, list) or not configs:
        return errors
    for backend in _checked_backends():
        for name, entries in sorted(_root_scope_entries(backend).items()):
            ambiguous = _order_decided_entries(entries)
            if not ambiguous:
                continue
            details = ". ".join(
                f"{entry.root}: {entry.path or '?'}" for entry in ambiguous
            )
            errors.append(
                Error(
                    f'Component name "{name}" is registered at the root scope of '
                    f"component roots the same template resolves against, and "
                    f"neither takes precedence over the other, so only "
                    f"registration order decides which one renders. Rename one "
                    f"of them or move it under a route scope. Locations: "
                    f"{details}.",
                    obj=settings,
                    id="next.E034",
                )
            )
    return errors


def _dotted_owner(node: ast.expr) -> str | None:
    """Spell an attribute owner back as a dotted name, or None when it is computed."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _dotted_owner(node.value)
        return None if parent is None else f"{parent}.{node.attr}"
    return None


def _component_py_uses_pages_context(file_path: Path) -> bool:
    """Return True if `component.py` reaches for the page `context` decorator."""
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
            and node.module in _PAGE_CONTEXT_MODULES
            and any(alias.name == "context" for alias in node.names)
        ):
            return True
        if (
            isinstance(node, ast.Attribute)
            and node.attr == "context"
            and _dotted_owner(node.value) in _PAGE_CONTEXT_OWNERS
        ):
            return True
    return False


@register(NEXT)
def check_component_py_no_pages_context(*args, **kwargs) -> list[CheckMessage]:
    """Check that `component.py` files do not use `context` from `next.pages`."""
    errors: list[CheckMessage] = []
    configs = next_framework_settings.COMPONENT_BACKENDS
    if not isinstance(configs, list) or not configs:
        return errors
    for backend in _checked_backends():
        for info in backend.iter_components():
            if info.module_path is None:
                continue
            if not info.module_path.exists():
                continue
            if _component_py_uses_pages_context(info.module_path):
                errors.append(
                    Error(
                        "component.py must not use context from next.pages or "
                        "the next package root. Use component context from "
                        "next.components instead.",
                        obj=str(info.module_path),
                        id="next.E021",
                    )
                )
    return errors


@register(NEXT)
def check_component_context_registration_files(*args, **kwargs) -> list[CheckMessage]:
    """Flag a `@component.context` no component render collects (`next.E075`).

    A registration keys on the file declaring the callable, so decorating an
    imported helper binds it to that module, and decorating a callable from a
    sibling `component.py` binds it to that other component.
    """
    configs = next_framework_settings.COMPONENT_BACKENDS
    if not isinstance(configs, list) or not configs:
        return []

    for backend in _checked_backends():
        # Called for the import it performs, which is what runs the
        # decorators this check then reads out of the registry.
        backend.import_component_modules()

    return registration_file_errors(
        _COMPONENT_CONTEXT_SUBJECT,
        registrations=component._registry.registered_names(),
        misattributed=component._registry.misattributed(),
    )


__all__ = [
    "check_component_context_registration_files",
    "check_component_py_no_pages_context",
    "check_cross_root_component_name_conflicts",
    "check_duplicate_component_names",
    "check_next_components_configuration",
]
