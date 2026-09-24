"""System checks for the shape of a routed pages tree on disk.

The ids are `next.E008`, `next.E009` and `next.E087` for bracket syntax, `next.E010`
for a parameter directory with no page, `next.E030` and `next.E088` for a router that
cannot report or walk its trees, and `next.W002` for a tree nobody routes.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.checks import CheckMessage, Error, Warning as DjangoWarning, register
from django.urls.converters import get_converters

from next.checks import NEXT
from next.checks.common import (
    PageRootsError,
    first_visit,
    get_page_roots,
    get_router_manager,
    page_tree_skip_names,
    read_page_roots,
)
from next.conf import next_framework_settings
from next.utils import normalise_route_name, walk_page_tree


if TYPE_CHECKING:
    from collections.abc import Iterator


logger = logging.getLogger(__name__)

_PARAMETER_FORMAT_HINT = "Use [param] or [type:param] format."
_ARGS_FORMAT_HINT = "Use [[args]] format."


@register(NEXT)
def check_pages_structure(*args, **kwargs) -> list[CheckMessage]:
    """Check each router's pages tree for layouts, naming, and structure."""
    errors: list[CheckMessage] = []

    router_manager, init_errors = get_router_manager()
    if router_manager is None:
        return init_errors

    # Nested and doubly-mounted roots reach one directory through several trees.
    seen: set[Path] = set()
    for router in router_manager.backends:
        # The one check that names a failing `page_roots`, every other reader
        # takes the empty list, so a broken router costs one message and trace.
        try:
            roots = read_page_roots(router)
        except PageRootsError as e:
            # Only a raised failure carries a traceback, a wrong shape has no
            # cause and its whole diagnosis is the message below.
            hint = None
            if e.__cause__ is not None:
                logger.exception("a router failed to report its page trees")
                detail = f"{e}: {e.__cause__}"
            else:
                detail = str(e)
                hint = (
                    "page_roots() returns a list of next.urls.PageRoot entries, "
                    "each pairing a directory with its label."
                )
            errors.append(Error(detail, hint=hint, obj=settings, id="next.E030"))
            continue
        skip_dir_names = page_tree_skip_names(router)
        try:
            for root in roots:
                errors.extend(
                    _check_pages_directory(root.path, root.label, seen, skip_dir_names)
                )
        except (AttributeError, OSError) as e:
            errors.append(
                Error(f"Error checking router pages: {e}", obj=settings, id="next.E088")
            )

    return errors


def _configured_pages_dir_names() -> list[str]:
    """Return the `PAGES_DIR` name every `PAGE_BACKENDS` entry declares."""
    configured = next_framework_settings.PAGE_BACKENDS
    if not isinstance(configured, list):
        return []
    return [
        name
        for entry in configured
        if isinstance(entry, dict) and isinstance(name := entry.get("PAGES_DIR"), str)
        if name
    ]


def _working_directory_page_trees() -> list[Path]:
    """Return each distinct `PAGES_DIR` directory sitting beside the process."""
    cwd = Path.cwd()
    trees: dict[Path, None] = {}
    for name in _configured_pages_dir_names():
        candidate = (cwd / name).resolve()
        if candidate.is_dir():
            trees[candidate] = None
    return list(trees)


def _touches_a_routed_tree(directory: Path, routed: set[Path]) -> bool:
    """Whether a routed page tree is this directory, or sits above or below it.

    A routed tree nested inside the candidate makes it part of a served layout, the
    shape of an app package that happens to carry the `PAGES_DIR` name.
    """
    return any(
        root.is_relative_to(directory) or directory.is_relative_to(root)
        for root in routed
    )


def _holds_a_page(directory: Path) -> bool:
    """Whether the walk finds anything under `directory` the router would route.

    The walk refuses no directory name, because an unrouted tree has no skip set.
    """
    return next(walk_page_tree(directory), None) is not None


@register(NEXT)
def check_unrouted_working_directory_pages(*args, **kwargs) -> list[CheckMessage]:
    """Warn when a pages tree beside the process is routed by nobody (`next.W002`).

    Nothing else reports this, since the other checks only walk trees routers report.
    """
    router_manager, _init_errors = get_router_manager()
    if router_manager is None:
        return []
    routed = {
        root.path.resolve()
        for router in router_manager.backends
        for root in get_page_roots(router)
    }
    return [
        DjangoWarning(
            f"{directory} holds pages that no configured router routes, so "
            "nothing under it is served. Name the directory in "
            "NEXT_FRAMEWORK['PAGE_BACKENDS'] DIRS, or set that entry's "
            "APP_DIRS to False, which routes BASE_DIR / PAGES_DIR when DIRS "
            "names no root.",
            obj=str(directory),
            id="next.W002",
        )
        for directory in _working_directory_page_trees()
        if not _touches_a_routed_tree(directory, routed) and _holds_a_page(directory)
    ]


def _is_bracket_segment(name: str) -> bool:
    """Whether a directory name is bracketed, the shape of a routed parameter."""
    return name.startswith("[") and name.endswith("]")


def _is_args_segment(name: str) -> bool:
    """Whether a directory name is the doubly bracketed wildcard segment."""
    return name.startswith("[[") and name.endswith("]]")


def _check_directory_syntax(
    directories: list[Path], pages_path: Path, context: str
) -> list[CheckMessage]:
    """Check directory names under `pages_path` for valid bracket syntax."""
    errors: list[CheckMessage] = []

    for item in directories:
        dir_name_str = item.name
        relative_path = item.relative_to(pages_path)

        # The wildcard form is read first, because `[[args]]` also opens with `[`.
        if _is_args_segment(dir_name_str):
            reason = _args_syntax_error(dir_name_str)
            if reason is not None:
                errors.append(
                    Error(
                        f"{context} pages: Invalid args syntax "
                        f'"{dir_name_str}" in {relative_path}. {reason}',
                        obj=settings,
                        id="next.E009",
                    )
                )

        elif _is_bracket_segment(dir_name_str):
            reason = _parameter_syntax_error(dir_name_str)
            if reason is not None:
                errors.append(
                    Error(
                        f"{context} pages: Invalid parameter syntax "
                        f'"{dir_name_str}" in {relative_path}. {reason}',
                        obj=settings,
                        id="next.E008",
                    )
                )

        elif dir_name_str.startswith("["):
            errors.append(
                Error(
                    f"{context} pages: Incomplete args syntax "
                    f'"{dir_name_str}" in {relative_path}. '
                    f"Use [[args]] format.",
                    obj=settings,
                    id="next.E087",
                )
            )

    return errors


def _check_missing_page_files(
    directories: list[Path],
    pages_path: Path,
    context: str,
    skip_dir_names: frozenset[str],
) -> list[CheckMessage]:
    """Check for missing `page.py` files inside parameter directories."""
    errors: list[CheckMessage] = []

    for item in directories:
        dir_name_str = item.name
        if _is_bracket_segment(dir_name_str):
            page_file = item / "page.py"
            layout_file = item / "layout.djx"
            template_file = item / "template.djx"

            if page_file.exists() or layout_file.exists() or template_file.exists():
                continue

            has_child_routes = False
            for child in item.iterdir():
                if child.name in skip_dir_names:
                    continue
                if child.is_dir() and (child / "page.py").exists():
                    has_child_routes = True
                    break

            if not has_child_routes:
                errors.append(
                    Error(
                        f"{context} pages: Parameter directory "
                        f'"{item.relative_to(pages_path)}" is missing page.py file.',
                        obj=settings,
                        id="next.E010",
                    )
                )

    return errors


def _iter_routed_directories(
    pages_path: Path, skip_dir_names: frozenset[str]
) -> Iterator[Path]:
    """Yield every directory under `pages_path` the router's own walk enters.

    A refused name takes its whole subtree with it, so a structural report
    names only directories a route can come out of.
    """
    try:
        items = sorted(pages_path.iterdir())
    except OSError as e:
        logger.debug("Cannot list directory %s: %s", pages_path, e)
        return
    for item in items:
        if not item.is_dir() or item.name in skip_dir_names:
            continue
        yield item
        yield from _iter_routed_directories(item, skip_dir_names)


def _check_pages_directory(
    pages_path: Path, context: str, seen: set[Path], skip_dir_names: frozenset[str]
) -> list[CheckMessage]:
    """Check a specific pages directory for issues, skipping directories in `seen`."""
    if not pages_path.exists():
        return []

    directories = [
        item
        for item in _iter_routed_directories(pages_path, skip_dir_names)
        if first_visit(item, seen)
    ]
    errors = _check_directory_syntax(directories, pages_path, context)
    errors.extend(
        _check_missing_page_files(directories, pages_path, context, skip_dir_names)
    )
    return errors


def _route_name_error(name: str) -> str | None:
    """Return why Django refuses `name` between its angle brackets, or `None`.

    Django compiles a route as the pattern is built, so a name it refuses is a
    traceback out of the first URL resolution rather than a report here.
    """
    if normalise_route_name(name).isidentifier():
        return None
    return (
        f"Parameter name {name!r} is no valid Python identifier once '-' is "
        "read as '_', and Django refuses such a name when it compiles the route."
    )


def _converter_error(converter: str) -> str | None:
    """Return why Django knows no such converter, or `None` when it knows one.

    The registry is read per check, so a converter a project registers counts too.
    """
    registered = get_converters()
    if converter in registered:
        return None
    known = ", ".join(sorted(registered))
    return (
        f"No Django URL converter is registered under the name {converter!r}. "
        f"Registered converters: {known}."
    )


def _parameter_syntax_error(param_str: str) -> str | None:
    """Return why a `[param]` directory names no route, or `None` when it does."""
    if not _is_bracket_segment(param_str):
        return _PARAMETER_FORMAT_HINT

    content = param_str[1:-1]
    if ":" not in content:
        name = content.strip()
        return _PARAMETER_FORMAT_HINT if not name else _route_name_error(name)
    type_name, param_name = content.split(":", 1)
    if ":" in param_name:
        return _PARAMETER_FORMAT_HINT
    converter = type_name.strip()
    name = param_name.strip()
    if not converter or not name:
        return _PARAMETER_FORMAT_HINT
    return _converter_error(converter) or _route_name_error(name)


def _args_syntax_error(args_str: str) -> str | None:
    """Return why a `[[args]]` directory names no route, or `None` when it does.

    The name is read unstripped, because the router captures whatever sits
    between the brackets and Django allows no whitespace in a route parameter.
    """
    if not _is_args_segment(args_str):
        return _ARGS_FORMAT_HINT

    content = args_str[2:-2]
    if not content.strip():
        return _ARGS_FORMAT_HINT
    return _route_name_error(content)


__all__ = ["check_pages_structure", "check_unrouted_working_directory_pages"]
