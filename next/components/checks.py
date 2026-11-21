"""System checks for the components subsystem."""

from __future__ import annotations

import ast
import inspect
import textwrap
from dataclasses import dataclass
from itertools import combinations
from typing import TYPE_CHECKING, cast

from django.conf import settings
from django.core.checks import CheckMessage, Error, Warning as DjangoWarning, register
from django.template import Template, TemplateSyntaxError

from next.checks import NEXT
from next.checks.common import (
    RegistrationSubject,
    async_callable_error,
    async_refusal,
    errors_for_unknown_keys,
    import_backend_class,
    registration_file_errors,
)
from next.conf import next_framework_settings
from next.conf.defer import defer_template
from next.defer import MAX_DURATION, RESERVED_OPTIONS, DeferMode
from next.deps.introspect import cached_signature
from next.introspect import callable_name

from .backends import ComponentsBackend
from .context import component
from .deferral import defer_declarations
from .loading import last_load_error
from .nodes import ComponentTagNode
from .sources import get_components_manager
from .trip import token_overrides


if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path
    from typing import Any

    from .info import ComponentInfo


_COMPONENT_BACKEND_SETTINGS_KEY = "COMPONENT_BACKENDS"

# The scanner renders only this name, so a context bound anywhere else is dead.
_COMPONENT_CONTEXT_SUBJECT = RegistrationSubject(
    decorator="@component.context",
    anchor_name="component.py",
    render="component render",
    code="next.E075",
)

_FILE_COMPONENT_BACKEND_CONFIG_KEYS = frozenset({"BACKEND", "COMPONENTS_DIR", "DIRS"})

# The curated root re-exports the page decorator, so every spelling below is the same.
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
                id="next.E055",
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
            Error(f"{prefix}.BACKEND must be a string.", obj=settings, id="next.E056")
        )
    else:
        errors.extend(_backend_class_errors(backend_path, prefix))
    if not isinstance(config["DIRS"], list):
        errors.append(
            Error(f"{prefix}.DIRS must be a list.", obj=settings, id="next.E057")
        )
    if not isinstance(config["COMPONENTS_DIR"], str):
        errors.append(
            Error(
                f"{prefix}.COMPONENTS_DIR must be a string.",
                obj=settings,
                id="next.E080",
            )
        )
    errors.extend(
        errors_for_unknown_keys(
            config, allowed=_FILE_COMPONENT_BACKEND_CONFIG_KEYS, prefix=prefix
        )
    )
    return errors


@register(NEXT)
def check_next_components_configuration(
    *args: object, **kwargs: object
) -> list[CheckMessage]:
    """Validate `COMPONENT_BACKENDS` shape in merged `NEXT_FRAMEWORK`."""
    raw = getattr(settings, "NEXT_FRAMEWORK", None)
    if raw is not None and not isinstance(raw, dict):
        return []

    backends = next_framework_settings.COMPONENT_BACKENDS
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
                    id="next.E079",
                )
            )
            continue
        errors.extend(_validate_single_component_backend(config, i))

    return errors


def _checked_backends() -> tuple[ComponentsBackend, ...]:
    """Return the loaded backends of the components store the checks read."""
    return get_components_manager().backends


@register(NEXT)
def check_duplicate_component_names(
    *args: object, **kwargs: object
) -> list[CheckMessage]:
    """Check that no two components share a name within one route scope."""
    errors: list[CheckMessage] = []
    if not next_framework_settings.COMPONENT_BACKENDS:
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

    A page tree beats a `COMPONENT_BACKENDS` root, so only same-kind roots tie.
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
def check_cross_root_component_name_conflicts(
    *args: object, **kwargs: object
) -> list[CheckMessage]:
    """Reject a root-scope name that only registration order resolves."""
    errors: list[CheckMessage] = []
    if not next_framework_settings.COMPONENT_BACKENDS:
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
def check_component_py_no_pages_context(
    *args: object, **kwargs: object
) -> list[CheckMessage]:
    """Check that `component.py` files do not use `context` from `next.pages`."""
    errors: list[CheckMessage] = []
    if not next_framework_settings.COMPONENT_BACKENDS:
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
def check_component_module_imports(
    *args: object, **kwargs: object
) -> list[CheckMessage]:
    """Report a `component.py` that raises while importing (`next.E084`).

    An async callable refused at import reports as `next.E218`, which names it.
    """
    if not next_framework_settings.COMPONENT_BACKENDS:
        return []

    errors: list[CheckMessage] = []
    for backend in _checked_backends():
        for module_path in backend.import_component_modules():
            error = last_load_error(module_path)
            if error is None:
                continue
            refusal = async_refusal(error)
            if refusal is not None:
                errors.append(async_callable_error(module_path, refusal))
                continue
            errors.append(
                Error(
                    f"component.py at {module_path} failed to import "
                    f"({type(error).__name__}: {error}). Its access declarations "
                    "are unknown, so the component renders as nothing, or raises "
                    "the error under DEBUG or STRICT_LOADING.",
                    obj=str(module_path),
                    id="next.E084",
                )
            )
    return errors


@register(NEXT)
def check_component_context_registration_files(
    *args: object, **kwargs: object
) -> list[CheckMessage]:
    """Flag a `@component.context` no component render collects (`next.E075`).

    A registration keys on the declaring file, so an imported helper binds elsewhere.
    """
    if not next_framework_settings.COMPONENT_BACKENDS:
        return []

    for backend in _checked_backends():
        # Called for the import, which runs the decorators the registry read needs.
        backend.import_component_modules()

    return registration_file_errors(
        _COMPONENT_CONTEXT_SUBJECT,
        registrations=component.registry.registered_names(),
        misattributed=component.registry.misattributed(),
    )


def _import_component_modules() -> None:
    """Run every `component.py` the listed backends hold, registering its calls."""
    if not next_framework_settings.COMPONENT_BACKENDS:
        return
    for backend in _checked_backends():
        backend.import_component_modules()


def _reserved_parameters(func: Callable[..., Any]) -> list[str]:
    """Return the parameters of `func` named like a boundary option of the tag."""
    try:
        signature = cached_signature(func)
    except (ValueError, TypeError):
        return []
    return [name for name in signature.parameters if name in RESERVED_OPTIONS]


@register(NEXT)
def check_component_context_reserved_parameters(
    *args: object, **kwargs: object
) -> list[CheckMessage]:
    """Warn about a `@component.context` parameter named like a boundary option."""
    _import_component_modules()
    reserved = ", ".join(sorted(RESERVED_OPTIONS))
    return [
        DjangoWarning(
            f"@component.context {callable_name(entry.func)} in {file_path} takes "
            f"the parameter {name!r}, which the component tag reserves for its "
            "boundary options, so a call site cannot pass it as a prop.",
            hint=f"Rename the parameter, the tag reserves {reserved}.",
            obj=str(file_path),
            id="next.W158",
        )
        for file_path, entries in component.registry.registered().items()
        for entry in entries
        for name in _reserved_parameters(entry.func)
    ]


def _self_calling_error(info: ComponentInfo) -> Path | None:
    """Return the `error.djx` of `info` when one of its component tags names `info`."""
    files = info.files
    path = None if files is None else files.error
    if path is None:
        return None
    try:
        compiled = Template(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, TemplateSyntaxError):
        return None
    tags = cast(
        "list[ComponentTagNode]", compiled.nodelist.get_nodes_by_type(ComponentTagNode)
    )
    return path if any(tag.name == info.name for tag in tags) else None


@register(NEXT)
def check_component_error_self_reference(
    *args: object, **kwargs: object
) -> list[CheckMessage]:
    """Refuse an `error.djx` that renders the component it is the error UI of."""
    if not next_framework_settings.COMPONENT_BACKENDS:
        return []
    return [
        Error(
            f"The error.djx of component {info.name!r} renders {info.name!r} itself, "
            "so a failure there is never caught by that component and reaches the "
            "next error boundary up.",
            hint="Render the error UI without the component, or move the shared "
            "markup into a component of its own.",
            obj=str(path),
            id="next.E229",
        )
        for backend in _checked_backends()
        for info in backend.iter_components()
        if (path := _self_calling_error(info)) is not None
    ]


def _declared_mode(info: ComponentInfo) -> DeferMode | None:
    """Return the mode the `component.defer` call of `info` declares, else `None`."""
    options = defer_declarations.options(info.module_path)
    return None if options is None else options.mode


def _deferred(info: ComponentInfo) -> bool:
    """Whether `info` defers, through a `component.defer` mode or a `loading.djx`."""
    files = info.files
    if files is not None and files.loading is not None:
        return True
    return _declared_mode(info) is not None


def _deferred_components() -> list[ComponentInfo]:
    """Return every component of the listed backends that defers its render."""
    if not next_framework_settings.COMPONENT_BACKENDS:
        return []
    _import_component_modules()
    return [
        info
        for backend in _checked_backends()
        for info in backend.iter_components()
        if _deferred(info)
    ]


def _site(info: ComponentInfo) -> str:
    """Return the file a deferral warning points at, the module before the template."""
    return str(info.module_path or info.template_path or "")


@register(NEXT)
def check_deferred_component_loading(
    *args: object, **kwargs: object
) -> list[CheckMessage]:
    """Warn about a deferred component with no loading UI of its own or by default.

    An `inline` component renders in place on first paint, so it needs no loading UI.
    """
    if defer_template("LOADING") is not None:
        return []
    return [
        DjangoWarning(
            f"Component {info.name!r} defers its render but has no loading.djx, and "
            'DEFER["LOADING"] names no default, so its placeholder stays empty until '
            "the content arrives.",
            hint='Add a loading.djx beside the component or set DEFER["LOADING"] to a '
            "template path.",
            obj=_site(info),
            id="next.W157",
        )
        for info in _deferred_components()
        if (info.files is None or info.files.loading is None)
        and _declared_mode(info) is not DeferMode.INLINE
    ]


@register(NEXT)
def check_deferred_component_error(
    *args: object, **kwargs: object
) -> list[CheckMessage]:
    """Warn about a deferred component with no error UI of its own or by default."""
    if defer_template("ERROR") is not None:
        return []
    return [
        DjangoWarning(
            f"Component {info.name!r} defers its render but has no error.djx, and "
            'DEFER["ERROR"] names no default, so a failed render leaves an empty '
            "error wrapper.",
            hint='Add an error.djx beside the component or set DEFER["ERROR"] to a '
            "template path.",
            obj=_site(info),
            id="next.W170",
        )
        for info in _deferred_components()
        if info.files is None or info.files.error is None
    ]


def _long_token_age(info: ComponentInfo) -> int | None:
    """Return the `tokens` `max_age` of `info` when it exceeds one day, else `None`."""
    age = dict(token_overrides(info)).get("max_age")
    return age if isinstance(age, int) and age > MAX_DURATION else None


@register(NEXT)
def check_component_token_age(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn about a deferred component whose token outlives one day (`next.W182`)."""
    return [
        DjangoWarning(
            f"Component {info.name!r} declares a token max_age of {age} seconds, above "
            "one day. A leaked reload token stays usable that long.",
            hint="Keep tokens['max_age'] at 86400 or below.",
            obj=_site(info),
            id="next.W182",
        )
        for info in _deferred_components()
        if (age := _long_token_age(info)) is not None
    ]


def _returned(node: ast.AST) -> Iterator[ast.expr]:
    """Yield what the `return` statements of a body return, nested functions aside."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            continue
        if isinstance(child, ast.Return) and child.value is not None:
            yield child.value
        yield from _returned(child)


def _literal_keys(value: ast.expr) -> Iterator[str]:
    """Yield the text keys a dict display or a `dict(...)` call spells out."""
    if isinstance(value, ast.Dict):
        for key in value.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                yield key.value
    elif (
        isinstance(value, ast.Call)
        and isinstance(value.func, ast.Name)
        and value.func.id == "dict"
    ):
        yield from (keyword.arg for keyword in value.keywords if keyword.arg)


def _returned_keys(func: Callable[..., Any]) -> frozenset[str]:
    """Return the keys a function visibly returns, none when its source is unread."""
    try:
        tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    except (OSError, TypeError, SyntaxError):
        return frozenset()
    return frozenset(
        key for value in _returned(tree.body[0]) for key in _literal_keys(value)
    )


@register(NEXT)
def check_keyless_context_props(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn about a keyless context that returns a declared prop (`next.W185`).

    A call passing that prop always wins, so the render raises at every such call.
    """
    _import_component_modules()
    messages: list[CheckMessage] = []
    for file_path, entries in component.registry.registered().items():
        options = defer_declarations.options(file_path)
        props = frozenset(() if options is None else options.props)
        for entry in entries:
            if entry.key is not None or not props:
                continue
            clashing = sorted(props & _returned_keys(entry.func))
            if not clashing:
                continue
            names = ", ".join(repr(name) for name in clashing)
            messages.append(
                DjangoWarning(
                    f"The keyless @component.context {callable_name(entry.func)} in "
                    f"{file_path} returns {names}, which the component declares as a "
                    "prop, so every call that passes it raises "
                    "ComponentContextConflictError.",
                    hint="Register the value under @component.context(key), or drop "
                    "it from the returned dict and let the call pass it.",
                    obj=str(file_path),
                    id="next.W185",
                )
            )
    return messages


__all__ = [
    "check_component_context_registration_files",
    "check_component_context_reserved_parameters",
    "check_component_error_self_reference",
    "check_component_module_imports",
    "check_component_py_no_pages_context",
    "check_component_token_age",
    "check_cross_root_component_name_conflicts",
    "check_deferred_component_error",
    "check_deferred_component_loading",
    "check_duplicate_component_names",
    "check_keyless_context_props",
    "check_next_components_configuration",
]
