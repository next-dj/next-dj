"""One walk of every routed directory of the page trees, shared by the checks of a run.

The router's walk drops a malformed or unresolved intercept, so the checks walk here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from next.checks.common import (
    RunMemo,
    first_visit,
    get_page_roots,
    get_router_manager,
    page_tree_skip_names,
)
from next.utils import (
    PAGE_FILES,
    is_route_group,
    join_trail,
    list_page_dir,
    parse_intercept,
    resolve_intercept,
    routes_into,
)


if TYPE_CHECKING:
    from pathlib import Path

    from next.urls import RouterManager
    from next.utils import PageRoot


@dataclass(frozen=True, slots=True)
class InterceptScope:
    """The intercept directory a directory lies in and the trail its marker targets."""

    source: Path
    marker: str
    target_trail: str


@dataclass(frozen=True, slots=True)
class TreeDirectory:
    """One routed directory with the URL trail of its level and its own.

    `url_trail` is `None` where the router walks no further, `within` the scope above.
    """

    root: PageRoot
    path: Path
    raw_trail: str
    scope_trail: str
    url_trail: str | None
    within: InterceptScope | None
    scope: InterceptScope | None
    skip_names: frozenset[str]


@dataclass(frozen=True, slots=True)
class TreeIntercept:
    """One page under an intercept directory, the trail it targets and its tree."""

    root: PageRoot
    file: Path
    raw_trail: str
    target_trail: str
    scope: InterceptScope


@dataclass(frozen=True, slots=True)
class TreeWalk:
    """Every routed directory and every intercept page the walk of a run found."""

    directories: tuple[TreeDirectory, ...] = ()
    intercepts: tuple[TreeIntercept, ...] = ()


def is_intercept_name(name: str) -> bool:
    """Whether the router reads `name` as an intercept, valid or malformed."""
    return name[:2] == "(."


def _entered(
    directory: Path, url_trail: str, within: InterceptScope | None
) -> InterceptScope | None:
    """Return the scope of an intercept directory the router enters, `None` for none.

    A malformed name, a `(..)` past the root and a nested intercept are never entered.
    """
    parsed = parse_intercept(directory.name)
    if parsed is None or within is not None:
        return None
    marker, rest = parsed
    target_trail = resolve_intercept(url_trail, marker, rest)
    if target_trail is None:
        return None
    return InterceptScope(directory, marker, target_trail)


class _Walker:
    """Collect the directories of every tree, each once, under the deepest root."""

    def __init__(self, roots: frozenset[Path]) -> None:
        """Start empty over the resolved paths of every reported tree."""
        self.roots = roots
        self.seen: set[Path] = set()
        self.directories: list[TreeDirectory] = []
        self.intercepts: list[TreeIntercept] = []

    def level(
        self,
        root: PageRoot,
        skip_names: frozenset[str],
        directory: Path,
        raw_trail: str,
        url_trail: str,
        within: InterceptScope | None,
    ) -> None:
        """Record each routed child of `directory`, entering the ones the router enters.

        A tree reported inside this one is left to its own walk, as the router does.
        """
        for child in list_page_dir(directory).children:
            name = child.name
            if not routes_into(name, skip_names) or child.resolve() in self.roots:
                continue
            if not first_visit(child, self.seen):
                continue
            raw = join_trail(raw_trail, name)
            scope = within
            child_trail: str | None
            if is_intercept_name(name):
                entered = _entered(child, url_trail, within)
                child_trail = None if entered is None else entered.target_trail
                scope = within if entered is None else entered
            elif is_route_group(name):
                child_trail = url_trail
            else:
                child_trail = join_trail(url_trail, name)
            self.directories.append(
                TreeDirectory(
                    root, child, raw, url_trail, child_trail, within, scope, skip_names
                )
            )
            if child_trail is not None:
                self.enter(root, skip_names, child, raw, child_trail, scope)

    def enter(
        self,
        root: PageRoot,
        skip_names: frozenset[str],
        directory: Path,
        raw_trail: str,
        url_trail: str,
        scope: InterceptScope | None,
    ) -> None:
        """Record the intercept page of `directory`, then walk the level below it."""
        if scope is not None and not list_page_dir(directory).files.isdisjoint(
            PAGE_FILES
        ):
            self.intercepts.append(
                TreeIntercept(root, directory / "page.py", raw_trail, url_trail, scope)
            )
        self.level(root, skip_names, directory, raw_trail, url_trail, scope)


def _walk(router_manager: RouterManager) -> TreeWalk:
    """Walk every tree each router reports, a tree reached twice walked once."""
    reached: set[Path] = set()
    trees: list[tuple[PageRoot, frozenset[str]]] = []
    for router in router_manager.backends:
        skip_names = page_tree_skip_names(router)
        trees.extend(
            (root, skip_names)
            for root in get_page_roots(router)
            if first_visit(root.path, reached)
        )
    walker = _Walker(frozenset(reached))
    for root, skip_names in trees:
        walker.level(root, skip_names, root.path, "", "", None)
    return TreeWalk(tuple(walker.directories), tuple(walker.intercepts))


_walks: RunMemo[TreeWalk] = RunMemo()


def tree_walk() -> TreeWalk:
    """Return the walk of every routed tree, taken once per run."""
    router_manager, _errors = get_router_manager()
    if router_manager is None:
        return TreeWalk()
    return _walks.get(router_manager, lambda: _walk(router_manager))


__all__ = [
    "InterceptScope",
    "TreeDirectory",
    "TreeIntercept",
    "TreeWalk",
    "is_intercept_name",
    "tree_walk",
]
