"""The directories of the routed page trees that hold no page and no action."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from next.checks.common import (
    first_visit,
    get_page_roots,
    get_router_manager,
    page_tree_skip_names,
)
from next.pages.access import routed
from next.pages.loaders import PAGE_MODULE, shared_layouts
from next.pages.manager import page
from next.permissions.access import declarations
from next.permissions.markers import OPEN

from .modules import faulty


if TYPE_CHECKING:
    from collections.abc import Collection, Iterator

    from next.permissions.markers import AccessRule


logger = logging.getLogger(__name__)

_LAYOUT = "layout.djx"
_TEMPLATE = "template.djx"


class EmptyBranch(NamedTuple):
    """The outermost directory of a branch with no page and no action in it.

    `source` names the first bodyless `page.py` passing to the branch, else its first
    `layout.djx`, and `access` and `context` hold what that `page.py` passes.
    """

    directory: Path
    source: Path | None
    access: AccessRule
    context: bool


class _Survey(NamedTuple):
    """What one directory and everything below it hold, as the branch report reads it.

    `reported` means an error already names a file or a directory of an empty branch,
    and `branches` lists the empty branches of the children, outermost only.
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


def _listing(
    directory: Path, skip_dir_names: frozenset[str]
) -> tuple[frozenset[str], list[Path]]:
    """Return the file names of `directory` and the directories the router enters.

    One listing answers both, and the directories come sorted by name.
    """
    try:
        with os.scandir(directory) as scan:
            entries = list(scan)
    except OSError as e:
        logger.debug("Cannot list directory %s: %s", directory, e)
        return frozenset(), []
    files: set[str] = set()
    children: list[Path] = []
    for entry in entries:
        if not entry.is_dir():
            files.add(entry.name)
        elif not entry.name.startswith("_") and entry.name not in skip_dir_names:
            children.append(Path(entry.path))
    return frozenset(files), sorted(children)


def _survey(
    directory: Path, skip_dir_names: frozenset[str], action_dirs: Collection[Path]
) -> _Survey:
    """Survey one directory and its subtree for pages, actions, and what fills neither.

    A bracketed directory without a page is `next.E010`, and a bodyless `page.py` that
    `faulty` names is `next.E012`, `next.E013` or `next.E166`, so either is reported.
    """
    files, child_dirs = _listing(directory, skip_dir_names)
    page_file = directory / PAGE_MODULE
    has_page_file = PAGE_MODULE in files
    filled = directory in action_dirs or (
        (has_page_file or _TEMPLATE in files) and routed(page_file)
    )
    name = directory.name
    reported = name.startswith("[") and name.endswith("]")
    passing: Path | None = None
    if has_page_file and not filled:
        if faulty(page_file):
            reported = True
        else:
            passing = page_file
    layout = directory / _LAYOUT if _LAYOUT in files else None
    children = [_survey(child, skip_dir_names, action_dirs) for child in child_dirs]
    branches: list[EmptyBranch] = []
    for child in children:
        filled = filled or child.filled
        if child.filled:
            branches.extend(child.branches)
        elif not child.reported:
            branches.append(_branch(child))
        reported = reported or child.reported
        passing = passing or child.passing
        layout = layout or child.layout
    return _Survey(directory, filled, reported, passing, layout, tuple(branches))


def _tree_branches(
    root: Path, skip_dir_names: frozenset[str], action_dirs: Collection[Path]
) -> tuple[EmptyBranch, ...]:
    """Return the empty branches of one tree, the root itself when all of it is one.

    A root `layout.djx` of `DIRS` wraps every page of every tree, so that root is none.
    """
    survey = _survey(root, skip_dir_names, action_dirs)
    if survey.filled:
        return survey.branches
    shared = {layout.resolve() for layout in shared_layouts()}
    if (root / _LAYOUT).resolve() in shared:
        return survey.branches
    return () if survey.reported else (_branch(survey),)


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
