"""Discovery of the configured routers and of the page trees they route."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.checks import CheckMessage, Error
from django.core.exceptions import ImproperlyConfigured

from next.diagnostics import FailureLog, exception_text
from next.ports import page_scan_slot, router_access_slot
from next.utils import PageRoot, page_roots_shape_error, walk_page_tree


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from next.urls import RouterBackend, RouterManager


logger = logging.getLogger(__name__)
_failures = FailureLog(logger)


class _RouterManagerCache:
    """The one manager a check run builds and the routed pages it imported."""

    def __init__(self) -> None:
        """Start with nothing built."""
        self.held: tuple[RouterManager | None, list[CheckMessage]] | None = None
        self.pages: tuple[RouterManager, list[tuple[str, Path]]] | None = None


_router_manager_cache = _RouterManagerCache()


@dataclass(frozen=True, slots=True)
class _ScannedTrees:
    """The pages, their trees and the component folders one walk of a router found.

    All three come from one walk, since a second walk might disagree with the first.
    """

    pages: tuple[tuple[PageRoot, str, Path], ...]
    component_folders: tuple[tuple[Path, Path, str], ...]


@dataclass(frozen=True, slots=True)
class _RouterContract:
    """What one router answers about the walk of its own page trees."""

    components_folder: str | None
    skip_names: frozenset[str]


# Keyed by identity since backends may compare equal, and the list pins each `id`.
_CACHED_ROUTERS: list[RouterBackend] = []
_SCANNED_TREES_CACHE: dict[int, _ScannedTrees] = {}
_ROUTER_CONTRACT_CACHE: dict[int, _RouterContract] = {}


class _RoutedSkipNames:
    """The skip names of the routers of one manager version, read once per version.

    Backends stay pinned per manager version, so no `id` is reused while memoised.
    """

    __slots__ = ("held",)

    def __init__(self) -> None:
        """Start with no version read."""
        self.held: tuple[object, dict[int, frozenset[str]]] | None = None


_routed_skip_names = _RoutedSkipNames()


def _keep_alive(router: RouterBackend) -> int:
    """Return the cache key of `router`, pinning it for the rest of the run."""
    _CACHED_ROUTERS.append(router)
    return id(router)


def get_router_manager() -> tuple[RouterManager | None, list[CheckMessage]]:
    """Return a per-run cached `RouterManager` or initialisation errors.

    Only `settings_reloaded` or `reset_check_caches` drops the cache.
    """
    cached = _router_manager_cache.held
    if cached is not None:
        return cached
    result: tuple[RouterManager | None, list[CheckMessage]]
    try:
        router_manager = router_access_slot.get().create_manager()
        # A throwaway manager keeps the live URL caches warm.
        router_manager.reload(notify=False)
    except (ImproperlyConfigured, ImportError, AttributeError) as e:
        error = Error(
            f"The routers NEXT_FRAMEWORK['PAGE_BACKENDS'] lists failed to "
            f"initialise: {e}",
            hint=(
                "Fix the BACKEND path or OPTIONS of the failing entry. Every other "
                "page, URL and SEO check is skipped until the routers load."
            ),
            obj=settings,
            id="next.E007",
        )
        result = (None, [error])
    else:
        result = (router_manager, [])
    _router_manager_cache.held = result
    return result


def discover_page_registrations(
    router_manager: RouterManager | None = None,
) -> list[tuple[str, Path]]:
    """Execute every routed `page.py` once per manager, returning each that ran.

    The result is cached beside the per-run walk of the trees and dropped with it.
    """
    if router_manager is None:
        router_manager, _errors = get_router_manager()
    if router_manager is None:
        return []
    held = _router_manager_cache.pages
    if held is not None and held[0] is router_manager:
        return held[1]
    loaded = page_scan_slot.get().load_scanned_page_modules(router_manager)
    _router_manager_cache.pages = (router_manager, loaded)
    return loaded


def reset_router_manager_cache(**kwargs) -> None:
    """Drop the cached `RouterManager` and everything read off its routers.

    The scans, the imported pages and the contract answers go with it.
    """
    _router_manager_cache.held = None
    _router_manager_cache.pages = None
    _SCANNED_TREES_CACHE.clear()
    _ROUTER_CONTRACT_CACHE.clear()
    _CACHED_ROUTERS.clear()
    _routed_skip_names.held = None


def first_visit(path: Path, seen: set[Path]) -> bool:
    """Whether `path` is reached for the first time, recording it when it is.

    The identity is the resolved path, so two spellings of one file count once.
    """
    resolved = path.resolve()
    if resolved in seen:
        return False
    seen.add(resolved)
    return True


class PageRootsError(Exception):
    """A router failed to report usable page trees, a raised failure is its cause."""


def read_page_roots(router: RouterBackend) -> list[PageRoot]:
    """Return the page trees `router` reports, raising `PageRootsError` on failure.

    A third-party `page_roots` can raise or answer any shape.
    """
    try:
        roots = list(router.page_roots())
        malformed = page_roots_shape_error(type(router).__name__, roots)
    except Exception as exc:
        msg = f"{type(router).__name__} failed to list its page trees"
        raise PageRootsError(msg) from exc
    if malformed is not None:
        raise PageRootsError(malformed)
    return roots


def get_page_roots(router: RouterBackend) -> list[PageRoot]:
    """Return every page tree `router` reports, duplicates and all, in router order.

    A failing router reports none, since one check reports it via `read_page_roots`.
    """
    try:
        return read_page_roots(router)
    except PageRootsError:
        return []


def get_pages_directories(router: RouterBackend) -> list[Path]:
    """Return every pages root a scanning check walks once, in router order.

    Deduplicated on the resolved path, reported under the router's own spelling.
    """
    roots: dict[Path, Path] = {}
    for root in get_page_roots(router):
        roots.setdefault(root.path.resolve(), root.path)
    return list(roots.values())


def _read_components_folder_name(router: RouterBackend) -> str | None:
    """Return the components folder `router` names, dropping anything but a name.

    A raise or a wrong shape from third-party code skips no folder at all.
    """
    try:
        name: object = router.components_folder_name()
    except Exception:
        logger.exception(
            "%s failed to name its components folder, so the check walk enters "
            "every folder under its page trees",
            type(router).__name__,
        )
        return None
    return name if isinstance(name, str) else None


def _read_skip_dir_names(router: RouterBackend) -> frozenset[str]:
    """Return the directory names `router` refuses, dropping anything but names.

    Third-party `skip_dir_names` may raise or misbehave, so nothing here gets refused.
    """
    try:
        names: object = router.skip_dir_names()
        if isinstance(names, str) or not isinstance(names, Iterable):
            return frozenset()
        return frozenset(name for name in names if isinstance(name, str))
    except Exception:
        logger.exception(
            "%s failed to name the directories its walk refuses, so the check "
            "walk enters every directory under its page trees",
            type(router).__name__,
        )
        return frozenset()


def _read_contract(router: RouterBackend) -> _RouterContract:
    """Ask `router` for its walk contract."""
    return _RouterContract(
        components_folder=_read_components_folder_name(router),
        skip_names=_read_skip_dir_names(router),
    )


def _router_contract(router: RouterBackend) -> _RouterContract:
    """Return the per-run reading of `router`'s walk contract, taking it once.

    A router that raises would otherwise write one traceback per asking check.
    """
    key = id(router)
    contract = _ROUTER_CONTRACT_CACHE.get(key)
    if contract is None:
        contract = _read_contract(router)
        _ROUTER_CONTRACT_CACHE[_keep_alive(router)] = contract
    return contract


def _skip_names(contract: _RouterContract) -> frozenset[str]:
    """Return the names a contract refuses, its components folder included."""
    if contract.components_folder is None:
        return contract.skip_names
    return contract.skip_names | {contract.components_folder}


def page_tree_skip_names(router: RouterBackend) -> frozenset[str]:
    """Return the directory names a walk of `router`'s page trees does not enter.

    Both halves come from that router alone, not from another `PAGE_BACKENDS` entry.
    """
    return _skip_names(_router_contract(router))


def _skip_names_by_router(
    manager: RouterManager, routers: tuple[RouterBackend, ...]
) -> dict[int, frozenset[str]]:
    """Return the skip names of every router of `manager`, read once per version."""
    # Read after `backends`, since their first read loads them and moves the version.
    version = manager.version
    held = _routed_skip_names.held
    if held is not None and held[0] == version:
        return held[1]
    names = {id(router): _skip_names(_read_contract(router)) for router in routers}
    _routed_skip_names.held = (version, names)
    return names


def _routed_roots(router: RouterBackend) -> list[PageRoot]:
    """Return the page trees `router` reports, none when it fails to report them.

    A request reads the trees, so the failure goes through a `FailureLog` guard.
    """
    with _failures.guard(
        ("page_roots", type(router)),
        "%s, so the scripts.py and SEO sources of its page trees are not loaded. Make "
        "page_roots() return a list of next.urls.PageRoot.",
        catch=(PageRootsError,),
        detail=exception_text,
    ):
        return read_page_roots(router)
    return []


def routed_page_trees(manager: RouterManager) -> list[tuple[PageRoot, frozenset[str]]]:
    """Return each tree `manager` routes once, in router order, with the names it skips.

    The `scripts.py` and SEO sources at the top of each tree are discovered from it.
    """
    routers = manager.backends
    skip_names_of = _skip_names_by_router(manager, routers)
    seen: set[Path] = set()
    found: list[tuple[PageRoot, frozenset[str]]] = []
    for router in routers:
        skip_names = skip_names_of[id(router)]
        found.extend(
            (root, skip_names)
            for root in _routed_roots(router)
            if first_visit(root.path, seen)
        )
    return found


def _tree_root(reported: dict[Path, PageRoot], pages_dir: Path) -> PageRoot:
    """Return the tree the router reports at `pages_dir`, an unmounted one by default.

    The walk reads `get_pages_directories`, which may name a directory no root reports.
    """
    root = reported.get(pages_dir)
    return PageRoot(path=pages_dir, label="Root") if root is None else root


def _walk_page_trees(router: RouterBackend) -> _ScannedTrees:
    """Walk every tree `router` reports once, keeping both things checks read."""
    components_folder = _router_contract(router).components_folder
    skip_names = page_tree_skip_names(router)
    folders: list[tuple[Path, Path, str]] = []

    def collect_folder(folder: Path, tree_root: Path, scope_trail: str) -> None:
        if folder.name == components_folder:
            folders.append((folder, tree_root, scope_trail))

    reported: dict[Path, PageRoot] = {}
    for root in get_page_roots(router):
        reported.setdefault(root.path, root)
    pages = tuple(
        (_tree_root(reported, pages_dir), url_path, page_path)
        for pages_dir in get_pages_directories(router)
        for url_path, page_path in walk_page_tree(
            pages_dir, skip_names, on_skipped_dir=collect_folder
        )
    )
    return _ScannedTrees(pages=pages, component_folders=tuple(folders))


def _scanned_trees(router: RouterBackend) -> _ScannedTrees:
    """Return the per-run walk of `router`'s page trees, running it once."""
    scanned = _SCANNED_TREES_CACHE.get(id(router))
    if scanned is None:
        scanned = _walk_page_trees(router)
        _SCANNED_TREES_CACHE[_keep_alive(router)] = scanned
    return scanned


def iter_scanned_page_pairs(router: RouterBackend) -> Iterator[tuple[str, Path]]:
    """Yield `(url_path, page_file)` for every page under the trees `router` routes.

    The walk is the framework's own, so a backend reporting `page_roots` is checked.
    """
    for _root, url_path, page_path in _scanned_trees(router).pages:
        yield url_path, page_path


def iter_scanned_pages(router: RouterBackend) -> Iterator[tuple[PageRoot, str, Path]]:
    """Yield `(root, url_path, page_file)` per page, `url_path` relative to `root`.

    The tree is the one the walk entered, so a reader can route the trail through it.
    """
    yield from _scanned_trees(router).pages


def iter_page_tree_component_folders(
    router: RouterBackend,
) -> Iterator[tuple[Path, Path, str]]:
    """Yield `(folder, tree_root, scope_trail)` per components folder in the trees.

    The walk is the router's own, so a check finds the folders that walk registers.
    """
    yield from _scanned_trees(router).component_folders


__all__ = [
    "PageRootsError",
    "discover_page_registrations",
    "first_visit",
    "get_page_roots",
    "get_pages_directories",
    "get_router_manager",
    "iter_page_tree_component_folders",
    "iter_scanned_page_pairs",
    "iter_scanned_pages",
    "page_tree_skip_names",
    "read_page_roots",
    "reset_router_manager_cache",
    "routed_page_trees",
]
