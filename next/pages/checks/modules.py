"""System checks for what a routed `page.py` module declares."""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.conf import settings
from django.core.checks import (
    CheckMessage,
    Error,
    Tags,
    Warning as DjangoWarning,
    register,
)

from next.checks import NEXT
from next.checks.common import (
    WALK_HINT,
    first_visit,
    get_page_roots,
    get_router_manager,
    page_tree_skip_names,
)
from next.pages.checks.access import declares_without_page, subtree_only
from next.pages.loaders import (
    build_registered_loaders,
    has_body,
    last_load_error,
    load_page_module,
    load_python_module_memo,
)
from next.pages.scan import iter_existing_scanned_pages
from next.permissions.access import declared
from next.ports import action_owners_slot
from next.utils import walk_page_tree


if TYPE_CHECKING:
    from collections.abc import Collection
    from pathlib import Path


# A page declares a body-source conflict only when two or more sources claim it.
_MIN_CONFLICTING_BODY_SOURCES = 2


def serves_nothing(file_path: Path) -> bool:
    """Return whether an imported `page.py` has no body and declares and passes nothing.

    An action of its directory still answers to it, so `next.E012` weighs that apart.
    """
    module, _error = load_page_module(file_path)
    return (
        getattr(module, "render", None) is None
        and not has_body(file_path, module)
        and declared(file_path).undeclared
        and not subtree_only(file_path)
    )


def render_broken(file_path: Path) -> bool:
    """Return whether a `page.py` sets a `render` that is not callable (`next.E013`)."""
    module, _error = load_page_module(file_path)
    render = getattr(module, "render", None)
    return render is not None and not callable(render)


def faulty(file_path: Path) -> bool:
    """Return whether `next.E012`, `next.E013` or `next.E166` names a `page.py`.

    It leaves actions out, which clear `next.E012` and `next.E166` for their directory.
    """
    return (
        serves_nothing(file_path)
        or render_broken(file_path)
        or declares_without_page(file_path)
    )


def _action_owners() -> Collection[Path]:
    """Return the `page.py` files an action of their directory answers to."""
    return action_owners_slot.get().page_files()


@register(NEXT)
def check_page_functions(*args, **kwargs) -> list[CheckMessage]:
    """Validate each page module for `render` or `template`. Warn when empty."""
    errors: list[CheckMessage] = []
    warnings: list[CheckMessage] = []

    router_manager, _init_errors = get_router_manager()
    if router_manager is None:
        return []

    # One `page.py` reached through several page trees is one page.
    seen: set[Path] = set()
    for router in router_manager.backends:
        skip_dir_names = page_tree_skip_names(router)
        try:
            for root in get_page_roots(router):
                root_errors, root_warnings = _check_page_functions_in_directory(
                    root.path, root.label, seen, skip_dir_names
                )
                errors.extend(root_errors)
                warnings.extend(root_warnings)
        except (AttributeError, OSError) as e:
            errors.append(
                Error(
                    f"{type(router).__name__} page trees could not be walked to "
                    f"check page.py functions: {e}",
                    hint=WALK_HINT,
                    obj=settings,
                    id="next.E011",
                )
            )

    return errors + warnings


def _check_page_functions_in_directory(
    pages_path: Path, context: str, seen: set[Path], skip_dir_names: frozenset[str]
) -> tuple[list[CheckMessage], list[CheckMessage]]:
    """Check `page.py` files for render/template rules, skipping files in `seen`.

    The walk skips what the router skips, so a `page.py` under a skipped directory
    serves no URL and is exempt from the body-source rules.
    """
    errors: list[CheckMessage] = []
    warnings: list[CheckMessage] = []

    if not pages_path.exists():
        return errors, warnings

    for _url_path, page_file in walk_page_tree(pages_path, skip_dir_names):
        # A virtual page carries a path that never existed, and the
        # template.djx that made it is body source enough.
        if not page_file.exists() or not first_visit(page_file, seen):
            continue
        _module, error = load_page_module(page_file)
        if error is not None:
            # A broken import surfaces once through next.E017, so the
            # body-source checks stay silent for this file.
            continue
        if serves_nothing(page_file) and page_file not in _action_owners():
            errors.append(
                Error(
                    f"{context} pages: {page_file.relative_to(pages_path)} "
                    "has no body source, passes nothing to the pages below, and no "
                    "action of its directory answers to it. Add a render function, a "
                    "template attribute, or a template a loader finds, like "
                    "template.djx.",
                    hint=(
                        "A layout.djx wraps the body of a page and is none itself. "
                        "A page.py needs no body source when it passes its access "
                        "with inherit=True or its context with inherit_context=True "
                        "to the pages below, or when its directory declares an action."
                    ),
                    obj=settings,
                    id="next.E012",
                )
            )
        elif render_broken(page_file):
            errors.append(
                Error(
                    f"{context} pages: {page_file.relative_to(pages_path)} "
                    f"render attribute is not callable.",
                    obj=settings,
                    id="next.E013",
                )
            )
        elif (shadow_warning := _check_body_source_conflicts(page_file)) is not None:
            warnings.append(shadow_warning)

    return errors, warnings


def _active_body_sources(page_file: Path) -> list[str]:
    """Return the body sources declared on `page_file` in priority order.

    Priority: `render()`, the `template` attribute, then
    `NEXT_FRAMEWORK["TEMPLATE_LOADERS"]` entries.
    """
    module = load_python_module_memo(page_file)
    sources: list[str] = []
    if module is not None:
        if callable(getattr(module, "render", None)):
            sources.append("render()")
        template_attr = getattr(module, "template", None)
        if isinstance(template_attr, str):
            sources.append("template")
    sources.extend(
        loader.source_name
        for loader in build_registered_loaders()
        if loader.can_load(page_file) and loader.source_name
    )
    return sources


def _check_body_source_conflicts(page_file: Path) -> CheckMessage | None:
    """Warn (`next.W043`) when more than one body source is declared for `page_file`."""
    sources = _active_body_sources(page_file)
    if len(sources) < _MIN_CONFLICTING_BODY_SOURCES:
        return None
    winner = sources[0]
    shadowed = ", ".join(sources[1:])
    return DjangoWarning(
        f"{page_file} declares multiple body sources: {', '.join(sources)}. "
        f"{winner} takes priority and {shadowed} will not be used. "
        "Priority order: render() > template > registered TEMPLATE_LOADERS.",
        obj=str(page_file),
        id="next.W043",
    )


def _page_import_error_message(page_path: Path) -> str:
    """Compose the `next.E017` text, naming the recorded failure when known."""
    error = last_load_error(page_path)
    if error is None:
        return (
            f"{page_path.name} at {page_path} could not be imported. Fix the syntax "
            "or import error so the framework stops skipping the module silently."
        )
    cause = error.__cause__
    return (
        f"{page_path.name} at {page_path} failed to import "
        f"({type(cause).__name__}: {cause}). A raising import in the module "
        "body counts the same as a syntax error. Fix it so the framework "
        "stops skipping the module silently."
    )


@register(Tags.templates, NEXT, deploy=True)
def check_page_module_imports(*args, **kwargs) -> list[CheckMessage]:
    """Report the `page.py` files that raise while importing (`next.E017`).

    Importing every user module walks the whole tree, so it runs under `check --deploy`.
    """
    router_manager, _init_errors = get_router_manager()
    if router_manager is None:
        return []
    return [
        Error(_page_import_error_message(path), obj=str(path), id="next.E017")
        for path in iter_existing_scanned_pages(router_manager, set())
        if load_python_module_memo(path) is None
    ]


__all__ = [
    "check_page_functions",
    "check_page_module_imports",
    "faulty",
    "render_broken",
    "serves_nothing",
]
