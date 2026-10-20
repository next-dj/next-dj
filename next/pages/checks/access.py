"""System checks for the access a `page.py` declares.

Every page of every tree is read once per run, which loads each `page.py` over it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from django.core.checks import CheckMessage, Error, register

from next.checks import NEXT
from next.checks.common import RunMemo
from next.components.sources import import_component_modules
from next.discovery import (
    discover_page_registrations,
    first_visit,
    get_router_manager,
    iter_scanned_page_pairs,
)
from next.pages.access import page_access
from next.pages.loaders import PAGE_MODULE, in_page_tree
from next.pages.manager import page
from next.permissions.access import declarations, declared, declared_files
from next.permissions.manager import permission_manager
from next.permissions.markers import OPEN


if TYPE_CHECKING:
    from pathlib import Path

    from next.pages.access import PageAccess


class TreePage(NamedTuple):
    """One page of a routed tree with the access its chain gives it."""

    page_path: Path
    access: PageAccess


def _read_trees() -> tuple[TreePage, ...]:
    """Load every `page.py` of the routed trees and read the rule of each."""
    router_manager, _errors = get_router_manager()
    if router_manager is None:
        return ()
    discover_page_registrations(router_manager)
    seen: set[Path] = set()
    return tuple(
        TreePage(page_path, page_access(page_path))
        for router in router_manager.backends
        for _url_path, page_path in iter_scanned_page_pairs(router)
        if first_visit(page_path, seen)
    )


_trees: RunMemo[tuple[TreePage, ...]] = RunMemo()


def tree_pages() -> tuple[TreePage, ...]:
    """Return every `page.py` of the routed trees with its rule, read once per run."""
    router_manager, _errors = get_router_manager()
    return _trees.get(router_manager, _read_trees)


def load_declarations() -> None:
    """Run every `page.py` and `component.py`, so each declaration is in."""
    tree_pages()
    import_component_modules()


def access_in_use() -> bool:
    """Return whether the project declares access, by a declaration or a permission.

    The access checks of a page and its actions stay silent for a project without one.
    """
    load_declarations()
    return bool(declared_files() or permission_manager.registry)


@register(NEXT)
def check_public_beside_requirements(*args, **kwargs) -> list[CheckMessage]:
    """Flag a `public()` beside a requirement of the same `page.py` (`next.E163`).

    The requirement still binds the page, so the `public()` opens nothing.
    """
    return [
        Error(
            f"public() in {file_path} sits beside its own login_required() or "
            "permission_required(), with or without inherit=True. The requirement "
            "still binds, so the public() opens nothing.",
            hint=(
                "Keep public() or the requirements, not both. To open one page "
                "under a guarded directory, declare page.public() in that page alone."
            ),
            obj=str(file_path),
            id="next.E163",
        )
        for file_path in sorted(entry.page_path for entry in tree_pages())
        if (rule := declared(file_path)).public and rule.requirements
    ]


def passes_down(file_path: Path) -> bool:
    """Return whether a `page.py` passes access or context to the pages below it."""
    if declarations.inherited(file_path) is not OPEN:
        return True
    return page.contexts.passes_context(file_path)


def declares_for_itself(file_path: Path) -> bool:
    """Return whether a `page.py` declares access only a page of its own would take.

    A `public()` beside a requirement of the same file is left to `next.E163`.
    """
    own, passed = declared(file_path), declarations.inherited(file_path)
    if own.public and own.requirements:
        return False
    if own.public and not passed.public:
        return True
    return not set(own.requirements).issubset(passed.requirements)


_COMPONENT_MODULE = "component.py"


def _segment_module(file_path: Path) -> bool:
    """Return whether an endpoint reads the declarations of `file_path`."""
    if file_path.name == _COMPONENT_MODULE:
        return True
    return file_path.name == PAGE_MODULE and in_page_tree(file_path.parent)


@register(NEXT)
def check_declaring_modules(*args, **kwargs) -> list[CheckMessage]:
    """Flag an access declaration bound to a file no endpoint reads (`next.E164`).

    A loader may run a `page.py` outside every tree, whose declarations guard nothing.
    """
    tree_pages()
    return [
        Error(
            f"An access declaration binds to {file_path}, which is no page.py of "
            "a page tree and no component.py, so it guards nothing.",
            hint=(
                "Move the module into a page tree, or drop its access declarations. "
                "A declaration from a helper module raises when the page loads."
            ),
            obj=str(file_path),
            id="next.E164",
        )
        for file_path in declared_files()
        if not _segment_module(file_path)
    ]


__all__ = [
    "access_in_use",
    "check_declaring_modules",
    "check_public_beside_requirements",
    "declares_for_itself",
    "load_declarations",
    "passes_down",
    "tree_pages",
]
