"""Discovery helpers that list page roots and component folder pairs.

`runserver`, `collectstatic` and the staticfiles finder all reach these helpers, so
every read of third-party router code catches what that code raises and drops the
backend from the answer rather than passing on a value of the wrong shape.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, NamedTuple

from next.backends import backend_entries
from next.conf.signals import settings_reloaded
from next.utils import (
    forget_resolved_trees,
    page_roots_shape_error,
    resolve_base_dir,
    resolved_tree,
    template_edits_watched,
)


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

    from next.urls import RouterBackend


logger = logging.getLogger(__name__)


_FAILED = (
    "%s failed to report its %s, so it contributes nothing to the watcher. "
    "The same failure is not logged again until the framework is reconfigured."
)

_NOT_BUILT = (
    "PAGE_BACKENDS entry number %s (%s) could not be built, so it contributes "
    "nothing to the watcher. The same failure is not logged again until the "
    "framework is reconfigured."
)

_MALFORMED = (
    "%s reported a %s of the wrong type, so it contributes nothing to the "
    "watcher. The same failure is not logged again until the framework is "
    "reconfigured."
)

# A router that keeps raising is read once a second, so a report of the same
# failure is kept to one per configuration, keyed by source and subject.
_reported_failures: set[tuple[str, str]] = set()


class _BackendsMemo(NamedTuple):
    """The held routers, and the base directory they were built against.

    The base directory rides along because a router reads it while it is built
    and a change to it alone emits no reload. An incomplete build holds no
    routers, which is what makes the next read try the failing entry again.
    """

    base_dir: Path | None
    backends: list[RouterBackend] | None


_BACKENDS_MEMO: dict[str, _BackendsMemo | None] = {"value": None}


def _first_failure(source: str, subject: str) -> bool:
    """Whether this failure is unreported, recording it when it is."""
    key = (source, subject)
    if key in _reported_failures:
        return False
    _reported_failures.add(key)
    return True


def _forget_backends() -> None:
    """Drop the held routers and re-arm the diagnostics of the ones that failed."""
    _reported_failures.clear()
    _BACKENDS_MEMO["value"] = None


def forget_watch_state(**kwargs) -> None:
    """Drop everything the watch layer holds, so a reconfigure is read afresh."""
    _forget_backends()
    forget_resolved_trees()


settings_reloaded.connect(forget_watch_state)


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

    A complete build is held until the configuration behind it changes, which
    is what keeps a static lookup and a reloader tick from building one per
    read. An incomplete one is built again, because an entry can fail for a
    reason the next read no longer has, such as an app that had yet to load.

    A process watching the disk holds nothing at all, so every read builds the
    routers again. A router answers about the trees it probed while it was
    built, and that is what makes a page tree created, moved, or removed under
    the development server reach the very next read.
    """
    if template_edits_watched():
        return _build_page_backends_for_watch()[0]
    base_dir = resolve_base_dir()
    memo = _BACKENDS_MEMO["value"]
    if memo is not None:
        if memo.base_dir != base_dir:
            # A change of BASE_DIR alone emits no reload, so the routers built
            # against the previous one go, and their diagnostics with them.
            _forget_backends()
        elif memo.backends is not None:
            return memo.backends
    backends, complete = _build_page_backends_for_watch()
    _BACKENDS_MEMO["value"] = _BackendsMemo(base_dir, backends if complete else None)
    return backends


def iter_page_backends_for_watch() -> Iterator[RouterBackend]:
    """Return one router per `PAGE_BACKENDS` entry, skipping the ones that fail.

    A backend that cannot be built costs its own trees and nothing else, so the
    watcher keeps observing every tree the other entries report. Every router
    is built before the iterator is handed back, so abandoning it half way
    leaves nothing half built behind.
    """
    return iter(_page_backends_for_watch())


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
    return [resolved_tree(root.path) for root in roots]


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
