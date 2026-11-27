"""Discovery of the page backends and the trees they route, for checks and watchers."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, NamedTuple

from django.conf import settings
from django.core.checks import CheckMessage, Error
from django.core.exceptions import ImproperlyConfigured

from next.backends import backend_entries
from next.caches import FRAMEWORK, ROUTES
from next.diagnostics import QUIET_PERIOD, BackendReadLog, FailureLog, exception_text
from next.errors import SettingError
from next.ports import page_scan_slot, route_access_slot
from next.utils import (
    LAYOUT_TEMPLATE,
    PageRoot,
    is_within,
    page_roots_shape_error,
    resolved_tree,
    template_edits_watched,
    walk_page_tree,
)


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from next.urls import PageBackend, RouteManager
    from next.utils import InterceptPage


logger = logging.getLogger(__name__)
_failures = FailureLog(logger)


class _RouteManagerCache:
    """The one manager a check run builds and the routed pages it imported."""

    def __init__(self) -> None:
        """Start with nothing built."""
        self.held: tuple[RouteManager | None, list[CheckMessage]] | None = None
        self.pages: tuple[RouteManager, list[tuple[str, Path]]] | None = None


_route_manager_cache = _RouteManagerCache()


@dataclass(frozen=True, slots=True)
class _ScannedTrees:
    """The pages, intercept pages and component folders one walk of a router found.

    All three come from one walk, since a second walk might disagree with the first.
    """

    pages: tuple[tuple[PageRoot, str, Path], ...]
    intercepts: tuple[tuple[PageRoot, str, Path], ...]
    component_folders: tuple[tuple[Path, Path, str], ...]


@dataclass(frozen=True, slots=True)
class _RouterContract:
    """What one router answers about the walk of its own page trees."""

    components_folder: str | None
    skip_names: frozenset[str]


# Keyed by identity since backends may compare equal, and the list pins each `id`.
_CACHED_ROUTERS: list[PageBackend] = []
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


def _keep_alive(router: PageBackend) -> int:
    """Return the cache key of `router`, pinning it for the rest of the run."""
    _CACHED_ROUTERS.append(router)
    return id(router)


def get_route_manager() -> tuple[RouteManager | None, list[CheckMessage]]:
    """Return a per-run cached `RouteManager` or initialisation errors.

    Only `settings_reloaded` or `reset_check_caches` drops the cache.
    """
    cached = _route_manager_cache.held
    if cached is not None:
        return cached
    result: tuple[RouteManager | None, list[CheckMessage]]
    try:
        route_manager = route_access_slot.get().create_manager()
        # A throwaway manager keeps the live URL caches warm.
        route_manager.reload(notify=False)
    except SettingError:
        # The check of that setting names it, so no run caches a manager for it.
        raise
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
        result = (route_manager, [])
    _route_manager_cache.held = result
    return result


def discover_page_registrations(
    route_manager: RouteManager | None = None,
) -> list[tuple[str, Path]]:
    """Execute every routed `page.py` once per manager, returning each that ran.

    The result is cached beside the per-run walk of the trees and dropped with it.
    """
    if route_manager is None:
        route_manager, _errors = get_route_manager()
    if route_manager is None:
        return []
    held = _route_manager_cache.pages
    if held is not None and held[0] is route_manager:
        return held[1]
    loaded = page_scan_slot.get().load_scanned_page_modules(route_manager)
    _route_manager_cache.pages = (route_manager, loaded)
    return loaded


def reset_route_manager_cache(**kwargs: object) -> None:
    """Drop the cached `RouteManager` and everything read off its routers.

    The scans, the imported pages and the contract answers go with it.
    """
    _route_manager_cache.held = None
    _route_manager_cache.pages = None
    _SCANNED_TREES_CACHE.clear()
    _ROUTER_CONTRACT_CACHE.clear()
    _CACHED_ROUTERS.clear()
    _routed_skip_names.held = None


FRAMEWORK.attach(reset_route_manager_cache)


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


def read_page_roots(router: PageBackend) -> list[PageRoot]:
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


def get_page_roots(router: PageBackend) -> list[PageRoot]:
    """Return every page tree `router` reports, duplicates and all, in router order.

    A failing router reports none, since one check reports it via `read_page_roots`.
    """
    try:
        return read_page_roots(router)
    except PageRootsError:
        return []


def get_pages_directories(router: PageBackend) -> list[Path]:
    """Return every pages root a scanning check walks once, in router order.

    Deduplicated on the resolved path, reported under the router's own spelling.
    """
    roots: dict[Path, Path] = {}
    for root in get_page_roots(router):
        roots.setdefault(root.path.resolve(), root.path)
    return list(roots.values())


def _read_components_folder_name(router: PageBackend) -> str | None:
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


def _read_skip_dir_names(router: PageBackend) -> frozenset[str]:
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


def _read_contract(router: PageBackend) -> _RouterContract:
    """Ask `router` for its walk contract."""
    return _RouterContract(
        components_folder=_read_components_folder_name(router),
        skip_names=_read_skip_dir_names(router),
    )


def _router_contract(router: PageBackend) -> _RouterContract:
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


def page_tree_skip_names(router: PageBackend) -> frozenset[str]:
    """Return the directory names a walk of `router`'s page trees does not enter.

    Both halves come from that router alone, not from another `PAGE_BACKENDS` entry.
    """
    return _skip_names(_router_contract(router))


def _skip_names_by_router(
    manager: RouteManager, routers: tuple[PageBackend, ...]
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


def _routed_roots(router: PageBackend) -> list[PageRoot]:
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


def routed_page_trees(manager: RouteManager) -> list[tuple[PageRoot, frozenset[str]]]:
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


def _walk_page_trees(router: PageBackend) -> _ScannedTrees:
    """Walk every tree `router` reports once, keeping what checks read."""
    components_folder = _router_contract(router).components_folder
    skip_names = page_tree_skip_names(router)
    folders: list[tuple[Path, Path, str]] = []

    def collect_folder(folder: Path, tree_root: Path, scope_trail: str) -> None:
        if folder.name == components_folder:
            folders.append((folder, tree_root, scope_trail))

    reported: dict[Path, PageRoot] = {}
    for root in get_page_roots(router):
        reported.setdefault(root.path, root)
    pages: list[tuple[PageRoot, str, Path]] = []
    intercepts: list[tuple[PageRoot, str, Path]] = []
    for pages_dir in get_pages_directories(router):
        tree = _tree_root(reported, pages_dir)
        nested = [
            other
            for other in reported
            if other != pages_dir and is_within(other, pages_dir)
        ]
        found: list[InterceptPage] = []
        pages.extend(
            (tree, url_path, page_path)
            for url_path, page_path in walk_page_tree(
                pages_dir,
                skip_names,
                on_skipped_dir=collect_folder,
                on_intercept=found.append,
            )
        )
        intercepts.extend(
            (tree, hit.target_trail, hit.file)
            for hit in found
            if not any(is_within(hit.file, root) for root in nested)
        )
    return _ScannedTrees(
        pages=tuple(pages),
        intercepts=tuple(intercepts),
        component_folders=tuple(folders),
    )


def _scanned_trees(router: PageBackend) -> _ScannedTrees:
    """Return the per-run walk of `router`'s page trees, running it once."""
    scanned = _SCANNED_TREES_CACHE.get(id(router))
    if scanned is None:
        scanned = _walk_page_trees(router)
        _SCANNED_TREES_CACHE[_keep_alive(router)] = scanned
    return scanned


def iter_scanned_page_pairs(router: PageBackend) -> Iterator[tuple[str, Path]]:
    """Yield `(url_path, page_file)` for every page under the trees `router` routes.

    An intercept page comes with the trail of its target, after the routed ones.
    """
    scanned = _scanned_trees(router)
    for _root, url_path, page_path in (*scanned.pages, *scanned.intercepts):
        yield url_path, page_path


def iter_scanned_pages(router: PageBackend) -> Iterator[tuple[PageRoot, str, Path]]:
    """Yield `(root, url_path, page_file)` per page, `url_path` relative to `root`.

    The tree is the one the walk entered, so a reader can route the trail through it.
    """
    yield from _scanned_trees(router).pages


def iter_scanned_intercepts(
    router: PageBackend,
) -> Iterator[tuple[PageRoot, str, Path]]:
    """Yield `(root, target_trail, page_file)` per intercept page of the trees.

    The trail is that of the page the intercept serves over, relative to `root`.
    """
    yield from _scanned_trees(router).intercepts


def iter_page_tree_component_folders(
    router: PageBackend,
) -> Iterator[tuple[Path, Path, str]]:
    """Yield `(folder, tree_root, scope_trail)` per components folder in the trees.

    The walk is the router's own, so a check finds the folders that walk registers.
    """
    yield from _scanned_trees(router).component_folders


_NOT_BUILT = (
    "PAGE_BACKENDS entry number %s (%s) could not be built, so it contributes "
    "nothing to the watcher. The same failure is not logged again for "
    f"{QUIET_PERIOD:.0f} seconds or until the framework is reconfigured."
)

# Every router read goes through one log, so a failing backend logs at a bounded rate.
_reads = BackendReadLog(logger)


class _BackendsMemo(NamedTuple):
    """The held routers, `None` for an incomplete build.

    A `BASE_DIR` change reloads the routers, which drops the memo with them.
    """

    backends: list[PageBackend] | None


class _WatchState:
    """What the watch layer holds between reads, mutated in place on a reconfigure."""

    def __init__(self) -> None:
        """Start with no held routers."""
        self.memo: _BackendsMemo | None = None


_state = _WatchState()


def _forget_backends() -> None:
    """Drop the held routers and reset the logged failures of the ones that failed."""
    _reads.clear()
    _state.memo = None


def forget_watch_state(**kwargs: object) -> None:
    """Drop everything the watch layer holds, so a reconfiguration is read again."""
    _forget_backends()


FRAMEWORK.attach(forget_watch_state)
ROUTES.attach(forget_watch_state)


def _build_page_backends_for_watch() -> tuple[list[PageBackend], bool]:
    """Build one router per `PAGE_BACKENDS` entry, telling whether all were built."""
    routers = route_access_slot.get()
    backends: list[PageBackend] = []
    complete = True
    for position, config in enumerate(backend_entries("PAGE_BACKENDS"), start=1):
        try:
            backend = routers.create_backend(config)
        except Exception:
            complete = False
            # Keyed by position, because entries naming no BACKEND share a key.
            if _reads.first_failure(str(position), "construction"):
                logger.exception(_NOT_BUILT, position, config.get("BACKEND"))
            continue
        backends.append(backend)
    return backends, complete


def _page_backends_for_watch() -> list[PageBackend]:
    """Return the routers the watcher reads, building them when it has to.

    Held until the configuration changes, but an incomplete build is rebuilt per read.
    """
    if template_edits_watched():
        return _build_page_backends_for_watch()[0]
    memo = _state.memo
    if memo is not None and memo.backends is not None:
        return memo.backends
    backends, complete = _build_page_backends_for_watch()
    _state.memo = _BackendsMemo(backends if complete else None)
    return backends


def iter_page_backends_for_watch() -> Iterator[PageBackend]:
    """Return one router per `PAGE_BACKENDS` entry, skipping the ones that fail.

    A backend that cannot be built loses only its own trees, and routers build eagerly.
    """
    return iter(_page_backends_for_watch())


def page_root_keys_for_backend(backend: PageBackend) -> list[tuple[Path, str | None]]:
    """Return the resolved page trees `backend` reports with their static keys.

    A backend that raises or returns the wrong shape contributes no tree.
    """
    roots: list[PageRoot] = _reads.read(
        backend,
        "page roots",
        lambda: list(backend.page_roots()),
        valid=lambda reported: (
            page_roots_shape_error(type(backend).__name__, reported) is None
        ),
        default=[],
    )
    return [(resolved_tree(root.path), root.key) for root in roots]


def page_root_paths_for_watch(backend: PageBackend) -> list[Path]:
    """Return the resolved page trees `backend` reports."""
    return [path for path, _key in page_root_keys_for_backend(backend)]


def components_folder_name_for_watch(backend: PageBackend) -> str | None:
    """Return the components folder `backend` names, dropping anything but a name."""
    return _reads.read(
        backend,
        "components folder name",
        backend.components_folder_name,
        valid=lambda name: name is None or isinstance(name, str),
        default=None,
    )


def get_page_root_keys_for_watch() -> list[tuple[Path, str | None]]:
    """Return every resolved page root with its static key, a repeated root once.

    The first router to report a root names its key.
    """
    seen: set[Path] = set()
    result: list[tuple[Path, str | None]] = []
    for backend in iter_page_backends_for_watch():
        for root, key in page_root_keys_for_backend(backend):
            if root not in seen:
                seen.add(root)
                result.append((root, key))
    return result


def get_pages_directories_for_watch() -> list[Path]:
    """Return the resolved page roots the autoreloader should observe.

    Every tree a router routes and nothing more, the set the page checks walk.
    """
    return [root for root, _key in get_page_root_keys_for_watch()]


def _djx_paths_for_watch(file_name: str) -> set[Path]:
    """Return every `file_name` under the page trees, each resolved once."""
    found: set[Path] = set()
    for pages_path in get_pages_directories_for_watch():
        try:
            for path in pages_path.rglob(file_name):
                found.add(path.resolve())
        except OSError as e:
            logger.debug("Cannot rglob %s under %s: %s", file_name, pages_path, e)
    return found


def get_layout_djx_paths_for_watch() -> set[Path]:
    """Return every `layout.djx` path under page trees."""
    return _djx_paths_for_watch(LAYOUT_TEMPLATE)


def get_template_djx_paths_for_watch() -> set[Path]:
    """Return every `template.djx` path under page trees."""
    return _djx_paths_for_watch("template.djx")


def iter_pages_roots_with_components_folder_names() -> list[tuple[Path, str]]:
    """Return distinct resolved page-root and components-folder-name pairs."""
    seen: set[tuple[Path, str]] = set()
    result: list[tuple[Path, str]] = []
    for backend in iter_page_backends_for_watch():
        comp_name = components_folder_name_for_watch(backend)
        if comp_name is None:
            continue
        for root in page_root_paths_for_watch(backend):
            key = (root, comp_name)
            if key not in seen:
                seen.add(key)
                result.append(key)
    return result


__all__ = [
    "PageRootsError",
    "components_folder_name_for_watch",
    "discover_page_registrations",
    "first_visit",
    "forget_watch_state",
    "get_layout_djx_paths_for_watch",
    "get_page_root_keys_for_watch",
    "get_page_roots",
    "get_pages_directories",
    "get_pages_directories_for_watch",
    "get_route_manager",
    "get_template_djx_paths_for_watch",
    "iter_page_backends_for_watch",
    "iter_page_tree_component_folders",
    "iter_pages_roots_with_components_folder_names",
    "iter_scanned_intercepts",
    "iter_scanned_page_pairs",
    "iter_scanned_pages",
    "page_root_keys_for_backend",
    "page_root_paths_for_watch",
    "page_tree_skip_names",
    "read_page_roots",
    "reset_route_manager_cache",
    "routed_page_trees",
]
