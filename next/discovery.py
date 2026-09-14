"""Discovery of the configured routers and of the page trees they route.

Sits outside every area since checks, sources, and the page scan share it, reached
through a port to dodge a `next.urls`/`next.pages` import cycle.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from django.conf import settings
from django.core.checks import CheckMessage, Error
from django.core.exceptions import ImproperlyConfigured

from next.conf.signals import settings_reloaded
from next.ports import router_access_slot
from next.utils import page_roots_shape_error, walk_page_tree


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from next.urls import RouterBackend, RouterManager
    from next.utils import PageRoot


logger = logging.getLogger(__name__)


class _RouterManagerCache:
    """The one manager a check run builds, instead of one per asking check."""

    def __init__(self) -> None:
        """Start with nothing built."""
        self.held: tuple[RouterManager | None, list[CheckMessage]] | None = None


_router_manager_cache = _RouterManagerCache()


@dataclass(frozen=True, slots=True)
class _ScannedTrees:
    """The pages and the component folders one walk of a router's trees found.

    Both come from the same walk, since a second walk might disagree with the first.
    """

    pairs: tuple[tuple[str, Path], ...]
    component_folders: tuple[tuple[Path, Path, str], ...]


@dataclass(frozen=True, slots=True)
class _RouterContract:
    """What one router answers about the walk of its own page trees."""

    components_folder: str | None
    skip_names: frozenset[str]


# Keyed by identity, because a third-party backend is free to compare equal to
# another one. The list pins each key so no `id` is reused while live.
_CACHED_ROUTERS: list[RouterBackend] = []
_SCANNED_TREES_CACHE: dict[int, _ScannedTrees] = {}
_ROUTER_CONTRACT_CACHE: dict[int, _RouterContract] = {}


def _keep_alive(router: RouterBackend) -> int:
    """Return the cache key of `router`, pinning it for the rest of the run."""
    _CACHED_ROUTERS.append(router)
    return id(router)


def get_router_manager() -> tuple[RouterManager | None, list[CheckMessage]]:
    """Return a per-run cached `RouterManager` or initialisation errors.

    Only `settings_reloaded` or an explicit `reset_check_caches` drops the cache, so
    a change to another router input like `INSTALLED_APPS` needs a manual reset.
    """
    cached = _router_manager_cache.held
    if cached is not None:
        return cached
    result: tuple[RouterManager | None, list[CheckMessage]]
    try:
        router_manager = router_access_slot.get().create_manager()
        # Throwaway manager for this check run, so the live URL caches and the
        # memos hanging off `router_reloaded` stay warm.
        router_manager.reload(notify=False)
    except (ImproperlyConfigured, ImportError, AttributeError) as e:
        error = Error(
            f"Error initializing router manager: {e}", obj=settings, id="next.E007"
        )
        result = (None, [error])
    else:
        result = (router_manager, [])
    _router_manager_cache.held = result
    return result


def discover_page_registrations(
    router_manager: RouterManager | None = None,
) -> list[tuple[str, Path]]:
    """Execute every routed `page.py`, answering the trail and path of each that ran.

    Django runs its checks in an order none may rely on, and the import pass is
    memoised per file by mtime, so every asking check runs it and a repeat costs a stat.
    """
    if router_manager is None:
        router_manager, _errors = get_router_manager()
    if router_manager is None:
        return []
    # Imported here because `next.pages.scan` reads the router manager from here,
    # so a top-level import either way would close the loop.
    scan = importlib.import_module("next.pages.scan")
    loaded: list[tuple[str, Path]] = scan.load_scanned_page_modules(router_manager)
    return loaded


def reset_router_manager_cache(**kwargs) -> None:
    """Drop the cached `RouterManager` and everything read off its routers.

    The scans and the contract answers go with it.
    """
    _router_manager_cache.held = None
    _SCANNED_TREES_CACHE.clear()
    _ROUTER_CONTRACT_CACHE.clear()
    _CACHED_ROUTERS.clear()


settings_reloaded.connect(reset_router_manager_cache)


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
    """A router failed to report usable page trees.

    A raised failure travels as `__cause__`, so the check that reports it
    names the cause while every other reader takes the empty list.
    """


def read_page_roots(router: RouterBackend) -> list[PageRoot]:
    """Return the page trees `router` reports, raising `PageRootsError` on failure.

    Third-party `page_roots` can raise or answer any shape, so both failure modes
    collapse into one exception a check run survives with a message, not a traceback.
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

    A failing router reports none here rather than raising, since one check already
    calls `read_page_roots` directly and turns the failure into a single message.
    """
    try:
        return read_page_roots(router)
    except PageRootsError:
        return []


def get_pages_directories(router: RouterBackend) -> list[Path]:
    """Return every pages root a scanning check walks once, in router order.

    Keyed on the resolved path since a symlinked tree has several spellings, but
    reported under the router's own spelling since the page registries key on that.
    """
    roots: dict[Path, Path] = {}
    for root in get_page_roots(router):
        roots.setdefault(root.path.resolve(), root.path)
    return list(roots.values())


def _read_components_folder_name(router: RouterBackend) -> str | None:
    """Return the components folder `router` names, dropping anything but a name.

    `components_folder_name` is third-party code that can raise or answer the wrong
    shape, and a check run survives both by skipping no folder at all.
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


def _router_contract(router: RouterBackend) -> _RouterContract:
    """Return the per-run reading of `router`'s walk contract, taking it once.

    A router that raises would otherwise write one traceback per asking check.
    """
    key = id(router)
    contract = _ROUTER_CONTRACT_CACHE.get(key)
    if contract is None:
        contract = _RouterContract(
            components_folder=_read_components_folder_name(router),
            skip_names=_read_skip_dir_names(router),
        )
        _ROUTER_CONTRACT_CACHE[_keep_alive(router)] = contract
    return contract


def page_tree_skip_names(router: RouterBackend) -> frozenset[str]:
    """Return the directory names a walk of `router`'s page trees does not enter.

    Both halves come from that router alone, so the walk never refuses a name another
    `PAGE_BACKENDS` entry declared for a tree this router does not serve.
    """
    contract = _router_contract(router)
    if contract.components_folder is None:
        return contract.skip_names
    return contract.skip_names | {contract.components_folder}


def _walk_page_trees(router: RouterBackend) -> _ScannedTrees:
    """Walk every tree `router` reports once, keeping both things checks read."""
    components_folder = _router_contract(router).components_folder
    skip_names = page_tree_skip_names(router)
    folders: list[tuple[Path, Path, str]] = []

    def collect_folder(folder: Path, tree_root: Path, route_trail: str) -> None:
        if folder.name == components_folder:
            folders.append((folder, tree_root, route_trail))

    pairs = [
        pair
        for pages_dir in get_pages_directories(router)
        for pair in walk_page_tree(pages_dir, skip_names, on_skipped_dir=collect_folder)
    ]
    return _ScannedTrees(pairs=tuple(pairs), component_folders=tuple(folders))


def _scanned_trees(router: RouterBackend) -> _ScannedTrees:
    """Return the per-run walk of `router`'s page trees, running it once."""
    scanned = _SCANNED_TREES_CACHE.get(id(router))
    if scanned is None:
        scanned = _walk_page_trees(router)
        _SCANNED_TREES_CACHE[_keep_alive(router)] = scanned
    return scanned


def iter_scanned_page_pairs(router: RouterBackend) -> Iterator[tuple[str, Path]]:
    """Yield `(url_path, page_file)` for every page under the trees `router` routes.

    The walk is the framework's own, not the backend's, so a backend that
    reports its trees through `page_roots` is checked whatever it routes from.
    """
    yield from _scanned_trees(router).pairs


def iter_page_tree_component_folders(
    router: RouterBackend,
) -> Iterator[tuple[Path, Path, str]]:
    """Yield `(folder, tree_root, route_trail)` per components folder in the trees.

    The walk, the skip set and the folder name are the router's own, so a
    check discovers the folders that walk registers and no others.
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
    "page_tree_skip_names",
    "read_page_roots",
    "reset_router_manager_cache",
]
