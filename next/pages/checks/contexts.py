"""System checks for the `@context` callables a routed `page.py` registers.

The ids are `next.E029` for a keyless callable returning no dict, `next.E074` for a
dead registration, and `next.E018` for two keyless callables on one page.
"""

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


# The file router routes only this name, so a context bound anywhere else is
# dead, including one bound to the page.py next door.
_PAGE_CONTEXT_SUBJECT = RegistrationSubject(
    decorator="@context", anchor_name="page.py", render="page render", code="next.E074"
)


class PageContexts(NamedTuple):
    """A routed `page.py`, its URL trail, and the `@context` bindings it registered."""

    url_path: str
    page_path: Path
    bindings: tuple[ZoneBinding, ...]


def load_routed_pages() -> tuple[list[CheckMessage], list[tuple[str, Path]]]:
    """Import every routed `page.py`, answering with the ones that loaded.

    The pass is the one the form checks run, and it takes the manager these checks
    resolved so a caller that pointed them at a router tree reaches it here too.
    """
    router_manager, init_errors = get_router_manager()
    if router_manager is None:
        return init_errors, []
    return init_errors, discover_page_registrations(router_manager)


def loaded_page_contexts() -> tuple[list[CheckMessage], list[PageContexts]]:
    """Return the `@context` every routed `page.py` registered as it imported.

    The registry keys on the path importlib gave the module, the same spelling the
    walk reports, so a binding is looked up without resolving a symlink on either side.
    """
    init_errors, loaded = load_routed_pages()
    bindings = page.zone_bindings()
    return init_errors, [
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

    A hint the resolver itself could not evaluate is no ground to block a page, so an
    unreadable annotation answers the same as an absent one.
    """
    try:
        return cached_type_hints(func).get("return", inspect.Signature.empty)
    except HINT_ERRORS:
        return inspect.Signature.empty


def _check_context_function(
    func_name: str, func: Callable[..., Any], page_path: Path
) -> CheckMessage | None:
    """Emit an error when keyless context callables are not annotated dict-like.

    The check is static, because executing user code at ``manage.py check`` time is
    expensive and can hit databases that have yet to be migrated.
    """
    annotation = _return_annotation(func)
    if _annotation_is_dict_like(annotation):
        return None
    annotation_name = getattr(annotation, "__name__", None) or repr(annotation)
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
    """Require keyless `@context` callables to return a dict when invoked."""
    init_errors, pages = loaded_page_contexts()
    errors = list(init_errors)
    for entry in pages:
        errors.extend(_keyless_context_errors(entry.page_path, entry.bindings))
    return errors


@register(Tags.templates, NEXT)
def check_context_registration_files(*args, **kwargs) -> list[CheckMessage]:
    """Flag a `@context` no page render collects (`next.E074`).

    A registration keys on the file declaring the callable, so an imported helper
    binds to its own module, and a sibling page's callable binds to that other page.
    """
    init_errors, _loaded = load_routed_pages()
    if init_errors:
        return init_errors

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
    init_errors, pages = loaded_page_contexts()
    errors = list(init_errors)
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
    "check_context_functions",
    "check_context_registration_files",
    "check_single_keyless_context",
    "load_routed_pages",
    "loaded_page_contexts",
]
