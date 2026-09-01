"""Discovery helpers that list page roots and component folder pairs.

`runserver`, `collectstatic` and the staticfiles finder all reach these helpers, so
every read of third-party router code catches what that code raises and drops the
backend from the answer rather than passing on a value of the wrong shape.
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from typing import TYPE_CHECKING

from next.backends import backend_entries
from next.conf.signals import settings_reloaded
from next.utils import page_roots_shape_error, resolve_base_dir, store_bounded


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from next.urls import RouterBackend


logger = logging.getLogger(__name__)


_FAILED = (
    "%s failed to report its %s, so it contributes nothing to the watcher. "
    "The same failure is not logged again until the settings reload."
)

_NOT_BUILT = (
    "PAGE_BACKENDS entry number %s (%s) could not be built, so it contributes "
    "nothing to the watcher. The same failure is not logged again until the "
    "settings reload."
)

_MALFORMED = (
    "%s reported a %s of the wrong type, so it contributes nothing to the "
    "watcher. The same failure is not logged again until the settings reload."
)

# A router that keeps raising is read once a second, so a report of the same
# failure is kept to one per settings generation. One key per source and
# subject bounds the set.
_reported_failures: set[tuple[str, str]] = set()

# The base directory rides along, because a router reads it while it is built
# and a change to it alone emits no reload.
_backends_memo: dict[str, tuple[Path | None, list[RouterBackend] | None] | None] = {
    "value": None
}

# Held with the routers, so a re-pointed root symlink reaches the watcher only
# after a restart.
_resolved_roots: OrderedDict[Path, Path] = OrderedDict()
_RESOLVED_ROOTS_MAX_SIZE = 2048


def _first_failure(source: str, subject: str) -> bool:
    """Whether this failure is unreported, recording it when it is."""
    key = (source, subject)
    if key in _reported_failures:
        return False
    _reported_failures.add(key)
    return True


def _forget_watch_state(**kwargs) -> None:
    """Rebuild the routers and diagnose a failing one again after a reconfigure."""
    _reported_failures.clear()
    _backends_memo["value"] = None
    _resolved_roots.clear()


settings_reloaded.connect(_forget_watch_state)


def _build_page_backends_for_watch() -> tuple[list[RouterBackend], bool]:
    """Build one router per `PAGE_BACKENDS` entry, telling whether all were built."""
    # next.urls imports next.pages, so the router import is deferred here to
    # break the next.pages <-> next.urls cycle.
    from next.urls import RouterFactory  # noqa: PLC0415

    backends: list[RouterBackend] = []
    complete = True
    for position, config in enumerate(backend_entries("PAGE_BACKENDS"), start=1):
        try:
            backend = RouterFactory.create_backend(config)
        except Exception:
            complete = False
            # Keyed by position, because entries naming no BACKEND share a key.
            if _first_failure(str(position), "construction"):
                logger.exception(_NOT_BUILT, position, config.get("BACKEND"))
            continue
        backends.append(backend)
    return backends, complete


def _page_backends_for_watch() -> list[RouterBackend]:
    """Return the routers the watcher reads, building them when it has to.

    A complete build is held until the configuration behind it changes, while
    one that dropped an entry is tried again, because the entry can fail for a
    reason the next read no longer has, such as an app that had yet to load.
    """
    base_dir = resolve_base_dir()
    memo = _backends_memo["value"]
    if memo is not None:
        if memo[0] != base_dir:
            # A change of BASE_DIR alone emits no reload, so the routers built
            # against the previous one go, and their diagnostics with them.
            _forget_watch_state()
        elif memo[1] is not None:
            return memo[1]
    backends, complete = _build_page_backends_for_watch()
    _backends_memo["value"] = (base_dir, backends if complete else None)
    return backends


def iter_page_backends_for_watch() -> Iterator[RouterBackend]:
    """Return one router per `PAGE_BACKENDS` entry, skipping the ones that fail.

    A backend that cannot be built costs its own trees and nothing else, so
    the watcher keeps observing every tree the other entries report. The
    routers are built before the iterator is handed back, because an abandoned
    iterator would otherwise leave the held ones half a configuration behind.
    """
    return iter(_page_backends_for_watch())


def _resolved_root_for_watch(path: Path) -> Path:
    """Return the resolved form of a reported tree, memoised until a reload.

    A router reports the same handful of trees every tick, and resolving one
    costs more than everything else the tick pays for it.
    """
    resolved = _resolved_roots.get(path)
    if resolved is None:
        resolved = path.resolve()
        # A router free to report a new tree per tick would otherwise grow the
        # memo without bound, and dropping the stalest keeps the steady set.
        store_bounded(_resolved_roots, path, resolved, _RESOLVED_ROOTS_MAX_SIZE)
    return resolved


def page_root_paths_for_watch(backend: RouterBackend) -> list[Path]:
    """Return the resolved page trees `backend` reports.

    A backend that raises or answers something other than `PageRoot` entries
    contributes no tree instead of reaching a caller that dereferences it, and
    the two are told apart because only one of them is a failing source.
    """
    source = type(backend).__name__
    subject = "page roots"
    try:
        roots = list(backend.page_roots())
        malformed = page_roots_shape_error(source, roots)
    except Exception:
        if _first_failure(source, subject):
            logger.exception(_FAILED, source, subject)
        return []
    if malformed is not None:
        if _first_failure(source, subject):
            logger.error(_MALFORMED, source, subject)
        return []
    return [_resolved_root_for_watch(root.path) for root in roots]


def components_folder_name_for_watch(backend: RouterBackend) -> str | None:
    """Return the components folder `backend` names, dropping anything but a name."""
    subject = "components folder name"
    try:
        # Widened from the declared `str | None`, because a third-party backend
        # can return anything and the check below has to stay reachable.
        name: object = backend.components_folder_name()
    except Exception:
        if _first_failure(type(backend).__name__, subject):
            logger.exception(_FAILED, type(backend).__name__, subject)
        return None
    if name is None or isinstance(name, str):
        return name
    if _first_failure(type(backend).__name__, subject):
        logger.error(_MALFORMED, type(backend).__name__, subject)
    return None


def get_pages_directories_for_watch() -> list[Path]:
    """Return the resolved page roots the autoreloader should observe.

    Every tree a router routes and nothing more, the set the page checks walk.
    """
    seen: set[Path] = set()
    result: list[Path] = []
    for backend in iter_page_backends_for_watch():
        for root in page_root_paths_for_watch(backend):
            if root not in seen:
                seen.add(root)
                result.append(root)
    return result


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
