"""System checks for the access a `proxy.py` and a `page.py` declare.

Every page of every tree is read once per run, which loads each `proxy.py` over it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

from django.core.checks import CheckMessage, Error, Warning as DjangoWarning, register

from next.checks import NEXT
from next.checks.common import RunMemo
from next.components.sources import get_components_manager
from next.conf import next_framework_settings
from next.discovery import (
    discover_page_registrations,
    first_visit,
    get_router_manager,
    iter_scanned_page_pairs,
)
from next.pages.access import page_access
from next.pages.loaders import (
    PAGE_MODULE,
    PROXY_MODULE,
    in_page_tree,
    load_python_module_memo,
)
from next.pages.scan import iter_proxy_modules
from next.permissions.access import declared, declared_files
from next.permissions.manager import permission_manager


if TYPE_CHECKING:
    from pathlib import Path

    from next.pages.access import PageAccess


class TreePage(NamedTuple):
    """One page of a routed tree with the access its chain gives it."""

    page_path: Path
    access: PageAccess


class _TreeAccess(NamedTuple):
    """Every page of the routed trees and every `proxy.py` they hold."""

    pages: tuple[TreePage, ...]
    proxies: tuple[Path, ...]


def _read_trees() -> _TreeAccess:
    """Load every page and `proxy.py` of the routed trees and read each page rule."""
    router_manager, _errors = get_router_manager()
    if router_manager is None:
        return _TreeAccess((), ())
    discover_page_registrations(router_manager)
    proxies = tuple(iter_proxy_modules(router_manager))
    for proxy in proxies:
        load_python_module_memo(proxy)
    seen: set[Path] = set()
    pages = tuple(
        TreePage(page_path, page_access(page_path))
        for router in router_manager.backends
        for _url_path, page_path in iter_scanned_page_pairs(router)
        if first_visit(page_path, seen)
    )
    return _TreeAccess(pages, proxies)


_trees: RunMemo[_TreeAccess] = RunMemo()


def _tree_access() -> _TreeAccess:
    """Return the pages and proxies of the routed trees, read once per run."""
    router_manager, _errors = get_router_manager()
    return _trees.get(router_manager, _read_trees)


def tree_pages() -> tuple[TreePage, ...]:
    """Return every page of the routed trees with its rule, its declarations loaded."""
    return _tree_access().pages


def load_declarations() -> None:
    """Run every page, `proxy.py` and `component.py`, so each declaration is in."""
    tree_pages()
    if not isinstance(next_framework_settings.COMPONENT_BACKENDS, list):
        return
    for backend in get_components_manager().backends:
        backend.import_component_modules()


def access_in_use() -> bool:
    """Return whether the project declares access, by a proxy, a declaration or a perm.

    The access checks of a page and its actions stay silent for a project without one.
    """
    load_declarations()
    return bool(
        _tree_access().proxies or declared_files() or permission_manager.registry
    )


def _tree_files() -> tuple[Path, ...]:
    """Return every `page.py` and `proxy.py` of the routed trees, sorted."""
    trees = _tree_access()
    files = {entry.page_path for entry in trees.pages}
    files.update(trees.proxies)
    return tuple(sorted(files))


@register(NEXT)
def check_public_beside_requirements(*args, **kwargs) -> list[CheckMessage]:
    """Flag a `public()` beside a requirement of the same file (`next.E163`).

    The requirement still binds the file, so the `public()` opens nothing.
    """
    return [
        Error(
            f"public() in {file_path} sits beside its own login_required() or "
            "permission_required(), which still bind, so nothing below it is "
            "public.",
            hint="Keep public() or the requirements, not both.",
            obj=str(file_path),
            id="next.E163",
        )
        for file_path in _tree_files()
        if (rule := declared(file_path)).public and rule.requirements
    ]


def _covers(proxy: Path, page_path: Path) -> bool:
    """Return whether a page sits in the directory of `proxy` or below it."""
    return page_path.parent.is_relative_to(proxy.parent)


@register(NEXT)
def check_proxies_over_pages(*args, **kwargs) -> list[CheckMessage]:
    """Warn about a `proxy.py` with no page in its directory or below (`next.W128`)."""
    trees = _tree_access()
    pages = [entry.page_path for entry in trees.pages]
    return [
        DjangoWarning(
            f"{proxy} has no page in its directory or below it, so its access "
            "declarations bind no page.",
            hint="Move it to a directory over the pages it is meant for, or delete it.",
            obj=str(proxy),
            id="next.W128",
        )
        for proxy in trees.proxies
        if not any(_covers(proxy, page_path) for page_path in pages)
    ]


_COMPONENT_MODULE = "component.py"


def _segment_module(file_path: Path) -> bool:
    """Return whether an endpoint reads the declarations of `file_path`."""
    if file_path.name == _COMPONENT_MODULE:
        return True
    return file_path.name in {PAGE_MODULE, PROXY_MODULE} and in_page_tree(
        file_path.parent
    )


@register(NEXT)
def check_declaring_modules(*args, **kwargs) -> list[CheckMessage]:
    """Flag an access declaration bound to a file no endpoint reads (`next.E164`).

    A declaration binds to the file calling it, so one made in a helper guards nothing.
    """
    tree_pages()
    return [
        Error(
            f"An access declaration binds to {file_path}, which is no page.py or "
            "proxy.py of a page tree and no component.py, so it guards nothing.",
            hint=(
                "Call login_required(), permission_required() or public() at the top "
                "level of the page.py, proxy.py or component.py it is meant for, "
                "not from a helper in another module."
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
    "check_proxies_over_pages",
    "check_public_beside_requirements",
    "load_declarations",
    "tree_pages",
]
