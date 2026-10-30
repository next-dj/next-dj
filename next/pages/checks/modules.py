"""System checks for what a routed `page.py` module declares."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

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
from next.components.loading import last_load_error as component_load_error
from next.components.sources import import_component_modules
from next.errors import AsyncCallableError
from next.pages.checks.access import declares_for_itself, passes_down
from next.pages.loaders import (
    build_registered_loaders,
    last_load_error,
    load_page_module,
    load_python_module_memo,
    serves,
)
from next.pages.scan import iter_existing_scanned_pages
from next.permissions.access import declared
from next.ports import action_owners_slot
from next.utils import walk_page_tree


if TYPE_CHECKING:
    import types
    from collections.abc import Collection
    from pathlib import Path


# A page declares a body-source conflict only when two or more sources claim it.
_MIN_CONFLICTING_BODY_SOURCES = 2


class LoadedPage(NamedTuple):
    """One load of a `page.py` and whether it routes, read once for every body check."""

    module: types.ModuleType | None
    routed: bool

    @property
    def bodyless(self) -> bool:
        """Whether the module imported and gives the page no body."""
        return self.module is not None and not self.routed


def load_page(file_path: Path) -> LoadedPage:
    """Load a `page.py` once and tell whether it routes a URL of its own."""
    module, error = load_page_module(file_path)
    return LoadedPage(module, serves(file_path, module, error))


def serves_nothing(file_path: Path, loaded: LoadedPage) -> bool:
    """Return whether an imported `page.py` has no body and declares and passes nothing.

    An action of its directory still answers to it, so `next.E012` weighs that apart.
    """
    return (
        loaded.bodyless
        and getattr(loaded.module, "render", None) is None
        and declared(file_path).undeclared
        and not passes_down(file_path)
    )


def render_broken(loaded: LoadedPage) -> bool:
    """Return whether a `page.py` sets a `render` that is not callable (`next.E013`)."""
    render = getattr(loaded.module, "render", None)
    return render is not None and not callable(render)


def declares_without_page(file_path: Path, loaded: LoadedPage) -> bool:
    """Return whether a `page.py` with no body declares something without inherit."""
    return loaded.bodyless and declares_for_itself(file_path)


def faulty(file_path: Path, loaded: LoadedPage) -> bool:
    """Return whether `next.E012`, `next.E013` or `next.E166` names a `page.py`.

    It leaves actions out, which clear `next.E012` and `next.E166` for their directory.
    """
    return (
        serves_nothing(file_path, loaded)
        or render_broken(loaded)
        or declares_without_page(file_path, loaded)
    )


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
    owners = action_owners_slot.get().page_files()
    for router in router_manager.backends:
        skip_dir_names = page_tree_skip_names(router)
        try:
            for root in get_page_roots(router):
                root_errors, root_warnings = _check_page_functions_in_directory(
                    root.path, root.label, seen, skip_dir_names, owners
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
    pages_path: Path,
    context: str,
    seen: set[Path],
    skip_dir_names: frozenset[str],
    owners: Collection[Path],
) -> tuple[list[CheckMessage], list[CheckMessage]]:
    """Check `page.py` files for render/template rules, skipping files in `seen`.

    A `page.py` under a directory the router skips serves no URL, so the walk skips it.
    """
    errors: list[CheckMessage] = []
    warnings: list[CheckMessage] = []

    if not pages_path.exists():
        return errors, warnings

    for _url_path, page_file in walk_page_tree(pages_path, skip_dir_names):
        # A virtual page has a path that never existed, its template.djx is its body.
        if not page_file.exists() or not first_visit(page_file, seen):
            continue
        loaded = load_page(page_file)
        if loaded.module is None:
            # A broken import surfaces once through next.E017, so these checks skip it.
            continue
        if serves_nothing(page_file, loaded) and page_file not in owners:
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
        elif render_broken(loaded):
            errors.append(
                Error(
                    f"{context} pages: {page_file.relative_to(pages_path)} "
                    f"render attribute is not callable.",
                    obj=settings,
                    id="next.E013",
                )
            )
        elif shadow := _check_body_source_conflicts(page_file, loaded.module):
            warnings.append(shadow)

    return errors, warnings


def _active_body_sources(page_file: Path, module: types.ModuleType | None) -> list[str]:
    """Return the body sources declared on `page_file` in priority order."""
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


def _check_body_source_conflicts(
    page_file: Path, module: types.ModuleType | None
) -> CheckMessage | None:
    """Warn (`next.W043`) when more than one body source is declared for `page_file`."""
    sources = _active_body_sources(page_file, module)
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


def _async_refusal(path: Path) -> AsyncCallableError | None:
    """Return the async refusal that stopped the `page.py` at `path` from loading."""
    error = last_load_error(path)
    cause = None if error is None else error.__cause__
    return cause if isinstance(cause, AsyncCallableError) else None


@register(Tags.templates, NEXT)
def check_page_module_imports(*args, **kwargs) -> list[CheckMessage]:
    """Report the `page.py` files that raise while importing (`next.E017`).

    An async callable refused at import is left to `next.E218`, which names it.
    """
    router_manager, _init_errors = get_router_manager()
    if router_manager is None:
        return []
    return [
        Error(_page_import_error_message(path), obj=str(path), id="next.E017")
        for path in iter_existing_scanned_pages(router_manager, set())
        if load_python_module_memo(path) is None and _async_refusal(path) is None
    ]


def _async_error(path: Path, refusal: AsyncCallableError) -> CheckMessage:
    """Build the `next.E218` error for the module at `path` and what it refused."""
    return Error(
        f"{path} did not load. {refusal}",
        hint=(
            "Async support is not part of pages, context and actions. Keep the "
            "callable sync and bridge async code through async_to_sync."
        ),
        obj=str(path),
        id="next.E218",
    )


@register(Tags.templates, NEXT)
def check_async_callables(*args, **kwargs) -> list[CheckMessage]:
    """Report a `page.py` or `component.py` whose async callable was refused.

    The refusal stops the module from loading, so each one surfaces here (`next.E218`).
    """
    errors: list[CheckMessage] = []
    router_manager, _init_errors = get_router_manager()
    if router_manager is not None:
        for path in iter_existing_scanned_pages(router_manager, set()):
            refusal = _async_refusal(path)
            if refusal is not None:
                errors.append(_async_error(path, refusal))
    for module_path in import_component_modules():
        error = component_load_error(module_path)
        if isinstance(error, AsyncCallableError):
            errors.append(_async_error(module_path, error))
    return errors


__all__ = [
    "LoadedPage",
    "check_async_callables",
    "check_page_functions",
    "check_page_module_imports",
    "declares_without_page",
    "faulty",
    "load_page",
]
