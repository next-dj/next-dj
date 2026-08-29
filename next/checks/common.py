"""Shared helpers used by per-subpackage system-check modules."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from django.conf import settings
from django.core.checks import CheckMessage, Error

from next.conf.imports import import_class_cached
from next.conf.signals import settings_reloaded
from next.utils import page_roots_shape_error, walk_page_tree


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from next.components.manager import ComponentsManager
    from next.urls import RouterBackend, RouterManager
    from next.utils import PageRoot


logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RegistrationSubject:
    """The wording one context decorator uses in its registration-file check."""

    decorator: str
    anchor_name: str
    render: str
    code: str


def registration_file_errors(
    subject: RegistrationSubject,
    *,
    registrations: dict[Path, tuple[str, ...]],
    misattributed: Iterable[tuple[Path, Path, str]],
) -> list[CheckMessage]:
    """Report registrations that no render of the intended file ever collects.

    A registration keys on the file declaring the callable, so decorating an
    imported helper binds it either to a file that is no anchor at all, or to
    another anchor file whose render answers a different URL.
    """
    records = sorted(misattributed, key=_by_paths)
    errors = _cross_file_errors(subject, records)
    errors.extend(_dead_file_errors(subject, registrations, _names_by_file(records)))
    return errors


def import_backend_class(dotted_path: str) -> type[Any]:
    """Import a dotted backend path, folding any import-time failure into ImportError.

    A backend module runs arbitrary code at import, and a check that lets it
    raise takes the whole run down instead of reporting one error.
    """
    try:
        return import_class_cached(dotted_path)
    except Exception as exc:
        msg = f"{dotted_path} raised {type(exc).__name__}: {exc}"
        raise ImportError(msg) from exc


def _names_by_file(records: list[tuple[Path, Path, str]]) -> dict[Path, set[str]]:
    """Group the misattributed names by the file they landed on."""
    grouped: dict[Path, set[str]] = {}
    for _registered_from, declared_in, name in records:
        grouped.setdefault(declared_in, set()).add(name)
    return grouped


def _cross_file_errors(
    subject: RegistrationSubject, records: list[tuple[Path, Path, str]]
) -> list[CheckMessage]:
    """Report each registration that landed on a file other than the one running it."""
    errors: list[CheckMessage] = []
    for registered_from, declared_in, name in records:
        errors.append(
            Error(
                f"{registered_from} runs {subject.decorator} on {name}, declared in "
                f"{declared_in}, so the registration binds to {declared_in} and no "
                f"{subject.render} of {registered_from} collects it. Declare the "
                f"callable in {registered_from}, wrapping the shared helper.",
                obj=str(registered_from),
                id=subject.code,
            )
        )
    return errors


def _dead_file_errors(
    subject: RegistrationSubject,
    registrations: dict[Path, tuple[str, ...]],
    already_reported: dict[Path, set[str]],
) -> list[CheckMessage]:
    """Report registrations sitting on a file the renderer never looks at.

    A name in `already_reported` is left out, because the cross-file report
    has named that callable and its fix already.
    """
    errors: list[CheckMessage] = []
    for file_path in sorted(registrations, key=str):
        if file_path.name == subject.anchor_name:
            continue
        unreported = set(registrations[file_path]) - already_reported.get(
            file_path, set()
        )
        if not unreported:
            continue
        names = ", ".join(sorted(unreported))
        errors.append(
            Error(
                f"{file_path} registers {subject.decorator} callables ({names}) but "
                f"is not a {subject.anchor_name}, so no {subject.render} collects "
                f"them. Declare the callable in the {subject.anchor_name} that "
                "needs it, wrapping this helper.",
                obj=str(file_path),
                id=subject.code,
            )
        )
    return errors


def _by_paths(record: tuple[Path, Path, str]) -> tuple[str, str, str]:
    """Order misattribution records so the report is stable across runs."""
    registered_from, declared_in, name = record
    return (str(registered_from), str(declared_in), name)


def errors_for_unknown_keys(
    config: dict[str, Any], *, allowed: frozenset[str], prefix: str
) -> list[CheckMessage]:
    """Return an `Error` list when `config` contains keys outside `allowed`."""
    unknown = sorted(k for k in config if k not in allowed)
    if not unknown:
        return []
    unknown_fmt = ", ".join(repr(k) for k in unknown)
    allowed_fmt = ", ".join(sorted(allowed))
    return [
        Error(
            f"{prefix} has unknown keys {unknown_fmt}. Allowed keys are {allowed_fmt}.",
            obj=settings,
            id="next.E035",
        )
    ]


# One manager per `manage.py check` run instead of rescanning the page and
# component trees for every registered check.
_ROUTER_MANAGER_CACHE: dict[
    str, tuple[RouterManager | None, list[CheckMessage]] | None
] = {"value": None}
_COMPONENTS_MANAGER_CACHE: dict[str, ComponentsManager | None] = {"value": None}


@dataclass(frozen=True, slots=True)
class _ScannedTrees:
    """The pages and the component folders one walk of a router's trees found.

    Both come out of the same walk, because a second walk could disagree
    with the first about either.
    """

    pairs: tuple[tuple[str, Path], ...]
    component_folders: tuple[tuple[Path, Path, str], ...]


@dataclass(frozen=True, slots=True)
class _RouterContract:
    """What one router answers about the walk of its own page trees."""

    components_folder: str | None
    skip_names: frozenset[str]


# Keyed by identity, because configuration-equal backends (`FileRouterBackend`)
# would share an entry. The list pins each key so no `id` is reused while live.
_CACHED_ROUTERS: list[RouterBackend] = []
_SCANNED_TREES_CACHE: dict[int, _ScannedTrees] = {}
_ROUTER_CONTRACT_CACHE: dict[int, _RouterContract] = {}


def _keep_alive(router: RouterBackend) -> int:
    """Return the cache key of `router`, pinning it for the rest of the run."""
    _CACHED_ROUTERS.append(router)
    return id(router)


def get_router_manager() -> tuple[RouterManager | None, list[CheckMessage]]:
    """Return a per-run cached `RouterManager` or initialisation errors.

    The cache is dropped only on `settings_reloaded` (a `NEXT_FRAMEWORK` change,
    which `override_settings` triggers) or an explicit `reset_check_caches`, so
    changes to other router inputs (for example `INSTALLED_APPS`) need a reset.
    """
    cached = _ROUTER_MANAGER_CACHE["value"]
    if cached is not None:
        return cached
    # next.urls.checks imports next.checks.common, so the manager import is
    # deferred here to break the next.checks.common <-> next.urls cycle.
    from next.urls import RouterManager  # noqa: PLC0415

    result: tuple[RouterManager | None, list[CheckMessage]]
    try:
        router_manager = RouterManager()
        router_manager.reload()
    except (ImportError, AttributeError) as e:
        error = Error(
            f"Error initializing router manager: {e}", obj=settings, id="next.E007"
        )
        result = (None, [error])
    else:
        result = (router_manager, [])
    _ROUTER_MANAGER_CACHE["value"] = result
    return result


def reset_router_manager_cache(**kwargs) -> None:
    """Drop the cached `RouterManager` and everything read off its routers.

    The scans and contract answers belong to this manager's routers, so they go too.
    """
    _ROUTER_MANAGER_CACHE["value"] = None
    _SCANNED_TREES_CACHE.clear()
    _ROUTER_CONTRACT_CACHE.clear()
    _CACHED_ROUTERS.clear()


def get_components_manager() -> ComponentsManager:
    """Return a per-run cached `ComponentsManager` holding every component source.

    The manager is the checks' own, because the live registry holds only what
    requests have already made the router walk. Invalidated like
    `get_router_manager`.
    """
    cached = _COMPONENTS_MANAGER_CACHE["value"]
    if cached is not None:
        return cached
    # next.components imports next.conf, which imports next.checks.common during
    # app setup, so the manager import is deferred here to break that cycle.
    from next.components.manager import ComponentsManager  # noqa: PLC0415

    manager = ComponentsManager()
    manager.reload()
    _COMPONENTS_MANAGER_CACHE["value"] = manager
    _register_page_tree_component_folders(manager)
    return manager


def _register_page_tree_component_folders(manager: ComponentsManager) -> None:
    """Register every components folder the configured page trees carry.

    The folders and the registration are the router's own, so a check reads
    the store a render would resolve against.
    """
    router_manager, _errors = get_router_manager()
    if router_manager is None:
        return
    for router in router_manager.backends:
        for folder, tree_root, route_trail in iter_page_tree_component_folders(router):
            manager.register_router_walk_folder(folder, tree_root, route_trail)


def reset_components_manager_cache(**kwargs) -> None:
    """Drop the cached `ComponentsManager` so the next check run rebuilds it."""
    _COMPONENTS_MANAGER_CACHE["value"] = None


settings_reloaded.connect(reset_router_manager_cache)
settings_reloaded.connect(reset_components_manager_cache)


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

    `page_roots` is third-party code that can raise anything and answer any
    shape, and a check run has to survive both with a message rather than a
    traceback, so either outcome becomes one framework exception the callers
    handle narrowly.
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

    A router that raises or answers the wrong shape reports none here. One
    check calls `read_page_roots` directly and turns that failure into a
    message, so the run reports it once instead of once per reader.
    """
    try:
        return read_page_roots(router)
    except PageRootsError:
        return []


def get_pages_directories(router: RouterBackend) -> list[Path]:
    """Return every pages root a scanning check walks once, in router order.

    A tree mounted twice is scanned once, keyed on the resolved path because
    a symlinked tree has several spellings, and reported under the spelling
    the router used because the page registries key on that path.
    """
    roots: dict[Path, Path] = {}
    for root in get_page_roots(router):
        roots.setdefault(root.path.resolve(), root.path)
    return list(roots.values())


def _read_components_folder_name(router: RouterBackend) -> str | None:
    """Return the components folder `router` names, dropping anything but a name.

    `components_folder_name` is third-party code that can raise or answer the
    wrong shape, and a check run survives both by skipping no folder rather
    than by ending in a traceback.
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

    `skip_dir_names` is third-party code that can raise or answer the wrong
    shape, and a check run survives both by refusing no directory rather than
    by ending in a traceback.
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

    Several checks ask the same two questions, and a router that raises would
    otherwise write one traceback per asking check.
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

    Both halves are that router's own answers, so the check walk refuses
    exactly what the router refuses, never a name another `PAGE_BACKENDS`
    entry declared for a tree this router does not serve.
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
    "errors_for_unknown_keys",
    "first_visit",
    "get_components_manager",
    "get_page_roots",
    "get_pages_directories",
    "get_router_manager",
    "import_backend_class",
    "iter_page_tree_component_folders",
    "iter_scanned_page_pairs",
    "page_tree_skip_names",
    "read_page_roots",
    "reset_components_manager_cache",
    "reset_router_manager_cache",
]
