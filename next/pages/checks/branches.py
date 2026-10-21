"""The directories of the routed page trees that hold no page and no action."""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from next.checks.common import (
    first_visit,
    get_page_roots,
    get_router_manager,
    page_tree_skip_names,
)
from next.pages.loaders import PAGE_MODULE, shared_layouts
from next.pages.manager import page
from next.permissions.access import declarations
from next.permissions.markers import OPEN
from next.utils import LAYOUT_TEMPLATE, PAGE_FILES, list_page_dir, routes_into

from .modules import faulty, load_page


if TYPE_CHECKING:
    from collections.abc import Collection, Iterator
    from pathlib import Path

    from next.permissions.markers import AccessRule


class EmptyBranch(NamedTuple):
    """The outermost directory of a branch with no page and no action in it.

    `source` names the bodyless `page.py` passing to it, else its `layout.djx`.
    """

    directory: Path
    source: Path | None
    access: AccessRule
    context: bool


class _Survey(NamedTuple):
    """What one directory and everything below it hold, as the branch report reads it.

    `reported` means an error names something in it, so `branches` goes up in its place.
    """

    directory: Path
    filled: bool
    reported: bool
    passing: Path | None
    layout: Path | None
    branches: tuple[EmptyBranch, ...]


def _branch(survey: _Survey) -> EmptyBranch:
    """Return the report of an empty directory, naming what it holds in vain."""
    source = survey.passing
    if source is None:
        return EmptyBranch(survey.directory, survey.layout, access=OPEN, context=False)
    return EmptyBranch(
        survey.directory,
        source,
        access=declarations.inherited(source),
        context=page.contexts.passes_context(source),
    )


def _survey(
    directory: Path, skip_dir_names: frozenset[str], action_dirs: Collection[Path]
) -> _Survey:
    """Survey one directory and its subtree for pages, actions, and what fills neither.

    A pageless `[param]` is `next.E010` and a `faulty` page.py has its own error.
    """
    listing = list_page_dir(directory)
    page_file = directory / PAGE_MODULE
    filled = directory in action_dirs
    own_error = directory.name.startswith("[") and directory.name.endswith("]")
    passing: Path | None = None
    if not filled and not listing.files.isdisjoint(PAGE_FILES):
        loaded = load_page(page_file)
        filled = loaded.routed
        if loaded.bodyless:
            if faulty(page_file, loaded):
                own_error = True
            else:
                passing = page_file
    layout = directory / LAYOUT_TEMPLATE if LAYOUT_TEMPLATE in listing.files else None
    reported = own_error
    branches: list[EmptyBranch] = []
    for child_dir in listing.children:
        if not routes_into(child_dir.name, skip_dir_names):
            continue
        child = _survey(child_dir, skip_dir_names, action_dirs)
        filled = filled or child.filled
        if child.filled or child.reported:
            branches.extend(child.branches)
        else:
            branches.append(_branch(child))
        reported = reported or child.reported
        passing = passing or child.passing
        layout = layout or child.layout
    if own_error and not filled:
        # The error on this directory already names every empty branch below it.
        branches.clear()
    return _Survey(directory, filled, reported, passing, layout, tuple(branches))


def _tree_branches(
    root: Path, skip_dir_names: frozenset[str], action_dirs: Collection[Path]
) -> tuple[EmptyBranch, ...]:
    """Return the empty branches of one tree, the root itself when all of it is one.

    A root `layout.djx` of `DIRS` wraps the pages of every tree, so it alone is none.
    """
    survey = _survey(root, skip_dir_names, action_dirs)
    if survey.filled or survey.reported:
        return survey.branches
    shared = set(shared_layouts())
    own_guard = survey.passing == root / PAGE_MODULE
    if not own_guard and (root / LAYOUT_TEMPLATE).resolve() in shared:
        return survey.branches
    return (_branch(survey),)


def empty_branches(action_dirs: Collection[Path]) -> Iterator[EmptyBranch]:
    """Yield the outermost directory of every branch with no page and no action.

    `action_dirs` names the directories whose files declare an action.
    """
    router_manager, _errors = get_router_manager()
    if router_manager is None:
        return
    seen: set[Path] = set()
    for router in router_manager.backends:
        skip_dir_names = page_tree_skip_names(router)
        for root in get_page_roots(router):
            if not root.path.is_dir():
                continue
            for branch in _tree_branches(root.path, skip_dir_names, action_dirs):
                if first_visit(branch.directory, seen):
                    yield branch


__all__ = ["EmptyBranch", "empty_branches"]
