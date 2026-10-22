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
    is_intercept_name,
    is_route_group,
    is_within,
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
    """The intercept directory a directory lies in and the trail it targets."""

    source: Path
    target_trail: str


@dataclass(frozen=True, slots=True)
class TreeDirectory:
    """One routed directory with the URL trail of its level and its own.

    `url_trail` is `None` where the router walks no further, `within` the scope above.
    """

    path: Path
    scope_trail: str
    url_trail: str | None
    within: InterceptScope | None
    scope: InterceptScope | None
    skip_names: frozenset[str]


@dataclass(frozen=True, slots=True)
class TreeIntercept:
    """One page under an intercept directory and the trail it targets."""

    root: PageRoot
    file: Path
    raw_trail: str
    target_trail: str


@dataclass(frozen=True, slots=True)
class TreeWalk:
    """Every routed directory and every intercept page the walk of a run found."""

    directories: tuple[TreeDirectory, ...] = ()
    intercepts: tuple[TreeIntercept, ...] = ()


def _entered(
    directory: Path, url_trail: str, within: InterceptScope | None
) -> InterceptScope | None:
    """Return the scope of an intercept directory the router enters, `None` for none.

    A malformed name, a `(..)` past the root and a nested intercept are never entered.
    """
    parsed = parse_intercept(directory.name)
    if parsed is None or within is not None:
        return None
    target_trail = resolve_intercept(url_trail, *parsed)
    if target_trail is None:
        return None
    return InterceptScope(directory, target_trail)


class _Walker:
    """Collect the directories of one tree as the router walks it.

    Only a tree its own router reports inside it is left to that tree's walk.
    """

    def __init__(
        self,
        root: PageRoot,
        skip_names: frozenset[str],
        nested: frozenset[Path],
        directories: list[TreeDirectory],
        intercepts: list[TreeIntercept],
    ) -> None:
        """Start over a tree, the resolved trees inside it and the lists to fill."""
        self.root = root
        self.skip_names = skip_names
        self.nested = nested
        self.directories = directories
        self.intercepts = intercepts

    def level(
        self,
        directory: Path,
        raw_trail: str,
        url_trail: str,
        within: InterceptScope | None,
    ) -> None:
        """Record each routed child of `directory`, entering those the router enters."""
        for child in list_page_dir(directory).children:
            name = child.name
            if not routes_into(name, self.skip_names):
                continue
            if self.nested and child.resolve() in self.nested:
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
                    child, url_trail, child_trail, within, scope, self.skip_names
                )
            )
            if child_trail is not None:
                self.enter(child, raw, child_trail, scope)

    def enter(
        self,
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
                TreeIntercept(self.root, directory / "page.py", raw_trail, url_trail)
            )
        self.level(directory, raw_trail, url_trail, scope)


def _walk(router_manager: RouterManager) -> TreeWalk:
    """Walk every tree each router reports, a tree two routers share walked once."""
    reached: set[Path] = set()
    directories: list[TreeDirectory] = []
    intercepts: list[TreeIntercept] = []
    for router in router_manager.backends:
        skip_names = page_tree_skip_names(router)
        roots = get_page_roots(router)
        for root in roots:
            if not first_visit(root.path, reached):
                continue
            here = root.path.resolve()
            nested = frozenset(
                other
                for other in (found.path.resolve() for found in roots)
                if other != here and is_within(other, here)
            )
            _Walker(root, skip_names, nested, directories, intercepts).level(
                root.path, "", "", None
            )
    return TreeWalk(tuple(directories), tuple(intercepts))


_walks: RunMemo[TreeWalk] = RunMemo()


def tree_walk() -> TreeWalk:
    """Return the walk of every routed tree, taken once per run."""
    router_manager, _errors = get_router_manager()
    if router_manager is None:
        return TreeWalk()
    return _walks.get(router_manager, lambda: _walk(router_manager))


__all__ = ["InterceptScope", "TreeDirectory", "TreeIntercept", "TreeWalk", "tree_walk"]
