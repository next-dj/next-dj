"""System checks for the `@context` callables a routed `page.py` registers."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, NamedTuple, get_origin

from django.core.checks import (
    CheckMessage,
    Error,
    Tags,
    Warning as DjangoWarning,
    register,
)

from next.checks import NEXT
from next.checks.common import (
    RegistrationSubject,
    discover_page_registrations,
    get_router_manager,
    registration_file_errors,
)
from next.deps.errors import DependencyCycleError
from next.deps.introspect import HINT_ERRORS, cached_type_hints
from next.deps.resolver import current_resolver
from next.introspect import callable_name
from next.pages.manager import page

from .access import tree_pages


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from next.pages.graph import ContextGraph
    from next.pages.registry import ZoneBinding


# The file router routes only this name, so a context in any other file never runs.
_PAGE_CONTEXT_SUBJECT = RegistrationSubject(
    decorator="@context", anchor_name="page.py", render="page render", code="next.E074"
)


class PageContexts(NamedTuple):
    """A routed `page.py`, its URL trail, and the `@context` bindings it registered."""

    url_path: str
    page_path: Path
    bindings: tuple[ZoneBinding, ...]


def load_routed_pages() -> list[tuple[str, Path]] | None:
    """Import every routed `page.py` once per run and return the ones that loaded.

    `None` means the routers failed to load, which `next.E007` reports.
    """
    router_manager, _init_errors = get_router_manager()
    if router_manager is None:
        return None
    return discover_page_registrations(router_manager)


def loaded_page_contexts() -> list[PageContexts]:
    """Return the `@context` every routed `page.py` registered as it imported.

    The registry keys on the path importlib gave the module, so no symlink is resolved.
    """
    loaded = load_routed_pages() or []
    bindings = page.zone_bindings()
    return [
        PageContexts(url_path, page_path, bindings.get(page_path, ()))
        for url_path, page_path in loaded
    ]


def _annotation_is_dict_like(annotation: object) -> bool:
    """Return True when the return annotation maps to a dict-like result."""
    if annotation is inspect.Signature.empty:
        return True
    origin = get_origin(annotation)
    candidate = annotation if origin is None else origin
    if not isinstance(candidate, type):
        return False
    try:
        return issubclass(candidate, Mapping)
    except TypeError:
        return False


def _return_annotation(func: Callable[..., Any]) -> object:
    """Return the resolved return annotation, read the way the DI resolver reads it.

    An unreadable hint counts as absent, since the resolver does not block a page on it.
    """
    try:
        return cached_type_hints(func).get("return", inspect.Signature.empty)
    except HINT_ERRORS:
        return inspect.Signature.empty


def annotation_mismatch(func: Callable[..., Any]) -> str | None:
    """Return the name of a return annotation that is not dict-like, else `None`.

    Static on purpose, since running user code at check time can hit an unmigrated DB.
    """
    annotation = _return_annotation(func)
    if _annotation_is_dict_like(annotation):
        return None
    return getattr(annotation, "__name__", None) or repr(annotation)


def _check_context_function(
    func_name: str, func: Callable[..., Any], page_path: Path
) -> CheckMessage | None:
    """Emit an error when keyless context callables are not annotated dict-like."""
    annotation_name = annotation_mismatch(func)
    if annotation_name is None:
        return None
    return Error(
        f"Context function '{func_name}' in {page_path} "
        "must return a dictionary when registered as a keyless context "
        f"(got return annotation {annotation_name}). "
        "Annotate it '-> dict' (or a TypedDict), or register it with a key "
        "like @context('name').",
        obj=str(page_path),
        id="next.E029",
    )


def _keyless_context_errors(
    page_path: Path, bindings: tuple[ZoneBinding, ...]
) -> list[CheckMessage]:
    """Return the return-shape errors of the keyless `@context` of one page."""
    errors: list[CheckMessage] = []
    for binding in bindings:
        if binding.key is not None:
            continue
        error = _check_context_function(binding.name, binding.func, page_path)
        if error is not None:
            errors.append(error)
    return errors


@register(Tags.templates, NEXT)
def check_context_functions(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Require a keyless `@context` callable to be annotated dict-like (`next.E029`)."""
    errors: list[CheckMessage] = []
    for entry in loaded_page_contexts():
        errors.extend(_keyless_context_errors(entry.page_path, entry.bindings))
    return errors


@register(Tags.templates, NEXT)
def check_context_registration_files(
    *args: object, **kwargs: object
) -> list[CheckMessage]:
    """Flag a `@context` no page render collects (`next.E074`).

    A registration keys on the declaring file, so an imported helper binds elsewhere.
    """
    if load_routed_pages() is None:
        return []
    tree_pages()
    return registration_file_errors(
        _PAGE_CONTEXT_SUBJECT,
        registrations=page.contexts.registered_names(),
        misattributed=page.contexts.misattributed(),
    )


@register(Tags.templates, NEXT)
def check_single_keyless_context(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Flag a `page.py` with more than one keyless `@context` (`next.E018`).

    Keyless callables share one slot, so only the last survives and runs.
    """
    errors: list[CheckMessage] = []
    pages = loaded_page_contexts()
    conflicts = page.contexts.keyless_conflicts()
    for entry in pages:
        names = conflicts.get(entry.page_path)
        if not names:
            continue
        joined = ", ".join(names)
        errors.append(
            Error(
                f"page.py at {entry.page_path} registers multiple keyless @context "
                f"callables ({joined}). Only the last one runs, so the "
                "earlier ones are ignored. Give each a key like "
                "@context('name'), or merge them into a single callable.",
                obj=str(entry.page_path),
                id="next.E018",
            )
        )
    return errors


def _async_pages() -> list[Path]:
    """Return every routed `page.py` whose render runs an async context callable."""
    return [
        entry.page_path
        for entry in loaded_page_contexts()
        if page.contexts.awaits(entry.page_path)
    ]


def _serialising_reads(page_path: Path, graph: ContextGraph) -> list[CheckMessage]:
    """Return a warning for each sync callable naming the key of an async one.

    A serial graph overlaps nothing, so only a parallel one has an overlap to lose.
    """
    if not graph.parallel:
        return []
    warnings: list[CheckMessage] = []
    steps = graph.steps
    for index, step in enumerate(steps):
        if step.is_async:
            continue
        awaited = sorted(
            callable_name(steps[earlier].func)
            for earlier in graph.reads[index]
            if steps[earlier].is_async
        )
        if not awaited:
            continue
        warnings.append(
            DjangoWarning(
                f"The sync @context callable '{callable_name(step.func)}' of "
                f"{page_path} reads the async {', '.join(awaited)} by name, so it "
                "waits for them on the request thread and the callables after it "
                "cannot overlap them.",
                hint="Declare the reading callable 'async def', or pass the value "
                "it needs another way.",
                obj=str(page_path),
                id="next.W163",
            )
        )
    return warnings


@register(Tags.templates, NEXT)
def check_async_context_reads(*args: object, **kwargs: object) -> list[CheckMessage]:
    """Warn about a sync `@context` reading an async one by name (`next.W163`)."""
    warnings: list[CheckMessage] = []
    for page_path in _async_pages():
        warnings.extend(_serialising_reads(page_path, page.contexts.graph(page_path)))
    return warnings


@register(Tags.templates, NEXT)
def check_async_dependency_cycles(
    *args: object, **kwargs: object
) -> list[CheckMessage]:
    """Warn about a named-dependency cycle an async `@context` reaches (`next.W168`).

    The async plan compiler finds it without running anything, the render raises it.
    """
    warnings: list[CheckMessage] = []
    resolver = current_resolver()
    for page_path in _async_pages():
        for step in page.contexts.graph(page_path).steps:
            if not step.is_async:
                continue
            name = callable_name(step.func)
            try:
                resolver.awaits(step.func, key=f"{page_path}:{name}")
            except DependencyCycleError as error:
                warnings.append(
                    DjangoWarning(
                        f"The async @context callable '{name}' of {page_path} "
                        f"reaches a cycle of named dependencies: {error}",
                        hint="Break the cycle, one of the dependencies must stop "
                        "naming the other.",
                        obj=str(page_path),
                        id="next.W168",
                    )
                )
    return warnings


__all__ = [
    "PageContexts",
    "annotation_mismatch",
    "check_async_context_reads",
    "check_async_dependency_cycles",
    "check_context_functions",
    "check_context_registration_files",
    "check_single_keyless_context",
    "load_routed_pages",
    "loaded_page_contexts",
]
