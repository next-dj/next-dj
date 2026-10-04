"""System checks for the `@context` callables a routed `page.py` registers."""

from __future__ import annotations

import inspect
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, NamedTuple, get_origin

from django.core.checks import CheckMessage, Error, Tags, register

from next.checks import NEXT
from next.checks.common import (
    RegistrationSubject,
    discover_page_registrations,
    get_router_manager,
    registration_file_errors,
)
from next.deps.introspect import HINT_ERRORS, cached_type_hints
from next.pages.manager import page


if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from next.pages.registry import ZoneBinding


# The file router routes only this name, so a context bound to any other file never
# runs, including one bound to a sibling page.py.
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

    The router manager is the one these checks resolved, so a patched router tree is
    used. `None` means the routers failed to load, which `next.E007` reports.
    """
    router_manager, _init_errors = get_router_manager()
    if router_manager is None:
        return None
    return discover_page_registrations(router_manager)


def loaded_page_contexts() -> list[PageContexts]:
    """Return the `@context` every routed `page.py` registered as it imported.

    The registry keys on the path importlib gave the module, the same spelling the
    walk reports, so a binding is looked up without resolving a symlink on either side.
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

    A hint the resolver itself cannot evaluate does not block a page, so an unreadable
    annotation counts as an absent one.
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
def check_context_functions(*args, **kwargs) -> list[CheckMessage]:
    """Require a keyless `@context` callable to be annotated dict-like (`next.E029`)."""
    errors: list[CheckMessage] = []
    for entry in loaded_page_contexts():
        errors.extend(_keyless_context_errors(entry.page_path, entry.bindings))
    return errors


@register(Tags.templates, NEXT)
def check_context_registration_files(*args, **kwargs) -> list[CheckMessage]:
    """Flag a `@context` no page render collects (`next.E074`).

    A registration keys on the file declaring the callable, so an imported helper
    binds to its own module, and a sibling page's callable binds to that other page.
    """
    if load_routed_pages() is None:
        return []

    return registration_file_errors(
        _PAGE_CONTEXT_SUBJECT,
        registrations=page._context_manager.registered_names(),
        misattributed=page._context_manager.misattributed(),
    )


@register(Tags.templates, NEXT)
def check_single_keyless_context(*args, **kwargs) -> list[CheckMessage]:
    """Flag a `page.py` with more than one keyless `@context` (`next.E018`).

    Keyless callables share one slot, so only the last survives and runs.
    """
    errors: list[CheckMessage] = []
    pages = loaded_page_contexts()
    conflicts = page._context_manager.keyless_conflicts()
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


__all__ = [
    "PageContexts",
    "annotation_mismatch",
    "check_context_functions",
    "check_context_registration_files",
    "check_single_keyless_context",
    "load_routed_pages",
    "loaded_page_contexts",
]
