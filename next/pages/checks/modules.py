"""System checks for what a routed `page.py` module declares.

The ids are `next.E011` for a walk that fails, `next.E012` and `next.E013` for a
missing or uncallable body source, `next.W043` for several body sources, and
`next.E017` for a module that raises while importing.
"""

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
    first_visit,
    get_page_roots,
    get_router_manager,
    page_tree_skip_names,
)
from next.pages.loaders import (
    _load_python_module_memo,
    build_registered_loaders,
    last_load_error,
)
from next.pages.scan import iter_existing_scanned_pages
from next.utils import walk_page_tree


if TYPE_CHECKING:
    from pathlib import Path


# A page declares a body-source conflict only when two or more sources claim it.
_MIN_CONFLICTING_BODY_SOURCES = 2


@register(NEXT)
def check_page_functions(*args, **kwargs) -> list[CheckMessage]:
    """Validate each page module for `render` or `template`. Warn when empty."""
    errors: list[CheckMessage] = []
    warnings: list[CheckMessage] = []

    router_manager, init_errors = get_router_manager()
    if router_manager is None:
        return init_errors

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
                    f"Error checking page functions: {e}", obj=settings, id="next.E011"
                )
            )

    return errors + warnings


def _check_page_functions_in_directory(
    pages_path: Path, context: str, seen: set[Path], skip_dir_names: frozenset[str]
) -> tuple[list[CheckMessage], list[CheckMessage]]:
    """Check `page.py` files for render/template rules, skipping files in `seen`.

    The walk refuses what the router refuses, so a `page.py` under a skipped
    directory answers no URL and is held to no body-source rule.
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
        render_func = _load_render_function(page_file)
        if last_load_error(page_file) is not None:
            # A broken import surfaces once through next.E017, so the
            # body-source checks stay silent for this file.
            continue
        has_template = _has_template_or_djx(page_file)
        hard_error = False

        if render_func is None and not has_template:
            errors.append(
                Error(
                    f"{context} pages: {page_file.relative_to(pages_path)} "
                    "has no body source. Add a render function, a template "
                    "attribute, a sibling template.djx, or a sibling layout.djx.",
                    obj=settings,
                    id="next.E012",
                )
            )
            hard_error = True
        elif render_func is not None and not callable(render_func):
            errors.append(
                Error(
                    f"{context} pages: {page_file.relative_to(pages_path)} "
                    f"render attribute is not callable.",
                    obj=settings,
                    id="next.E013",
                )
            )
            hard_error = True

        if not hard_error:
            shadow_warning = _check_body_source_conflicts(page_file)
            if shadow_warning is not None:
                warnings.append(shadow_warning)

    return errors, warnings


def _active_body_sources(page_file: Path) -> list[str]:
    """Return the body sources declared on `page_file` in priority order.

    Priority: `render()`, the `template` attribute, then
    `NEXT_FRAMEWORK["TEMPLATE_LOADERS"]` entries.
    """
    module = _load_python_module_memo(page_file)
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


def _load_render_function(file_path: Path) -> object:
    """Return the `render` callable declared in a `page.py`, or `None`.

    A broken `page.py` loads as `None` and yields `None` here. The caller
    reads `last_load_error` and leaves the failure to `next.E017`.
    """
    module = _load_python_module_memo(file_path)
    if module is None:
        return None
    return getattr(module, "render", None)


def _has_template_or_djx(file_path: Path) -> bool:
    """Return True when the page has a body source or a sibling ``layout.djx``."""
    if (file_path.parent / "layout.djx").exists():
        return True

    module = _load_python_module_memo(file_path)
    if module is not None and hasattr(module, "template"):
        return True

    return any(loader.can_load(file_path) for loader in build_registered_loaders())


def _page_import_error_message(page_path: Path) -> str:
    """Compose the `next.E017` text, naming the recorded failure when known."""
    error = last_load_error(page_path)
    if error is None:
        return (
            f"page.py at {page_path} could not be imported. Fix the syntax or "
            "import error so the framework stops skipping the module silently."
        )
    cause = error.__cause__
    return (
        f"page.py at {page_path} failed to import "
        f"({type(cause).__name__}: {cause}). A raising import in the module "
        "body counts the same as a syntax error. Fix it so the framework "
        "stops skipping the module silently."
    )


@register(Tags.templates, NEXT, deploy=True)
def check_page_module_imports(*args, **kwargs) -> list[CheckMessage]:
    """Report `page.py` files that raise while importing (`next.E017`).

    Importing every user module costs a full tree walk, so the check is a deployment
    one and runs under `manage.py check --deploy` instead of on every command.
    """
    router_manager, init_errors = get_router_manager()
    if router_manager is None:
        return init_errors
    return [
        Error(_page_import_error_message(page_path), obj=str(page_path), id="next.E017")
        for page_path in iter_existing_scanned_pages(router_manager, set())
        if _load_python_module_memo(page_path) is None
    ]


__all__ = ["check_page_functions", "check_page_module_imports"]
