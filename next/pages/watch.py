"""Discovery helpers that list page roots and component folder pairs.

`runserver`, `collectstatic`, and static discovery reach this, dropping a bad router.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, NamedTuple

from next.backends import backend_entries
from next.conf.signals import settings_reloaded
from next.diagnostics import BackendReadLog
from next.ports import router_access_slot
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
    from next.utils import PageRoot


logger = logging.getLogger(__name__)


_NOT_BUILT = (
    "PAGE_BACKENDS entry number %s (%s) could not be built, so it contributes "
    "nothing to the watcher. The same failure is not logged again until the "
    "framework is reconfigured."
)

# Every read of a router goes through one log, so a backend that keeps raising
# reports once per configuration wherever the watch layer reads it.
_reads = BackendReadLog(logger)


class _BackendsMemo(NamedTuple):
    """The held routers, and the base directory they were built against.

    The base directory rides along since a change to it alone emits no reload, and
    `backends=None` marks an incomplete build so the next read retries it.
    """

    base_dir: Path | None
    backends: list[RouterBackend] | None


class _WatchState:
    """What the watch layer holds between reads, mutated in place on a reconfigure."""

    def __init__(self) -> None:
        """Start with no held routers."""
        self.memo: _BackendsMemo | None = None


_state = _WatchState()


def _forget_backends() -> None:
    """Drop the held routers and re-arm the diagnostics of the ones that failed."""
    _reads.clear()
    _state.memo = None


def forget_watch_state(**kwargs) -> None:
    """Drop everything the watch layer holds, so a reconfigure is read afresh."""
    _forget_backends()
    forget_resolved_trees()


settings_reloaded.connect(forget_watch_state)


def _build_page_backends_for_watch() -> tuple[list[RouterBackend], bool]:
    """Build one router per `PAGE_BACKENDS` entry, telling whether all were built."""
    routers = router_access_slot.get()
    backends: list[RouterBackend] = []
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


def _page_backends_for_watch() -> list[RouterBackend]:
    """Return the routers the watcher reads, building them when it has to.

    Held until the configuration changes, but rebuilt when incomplete since an
    entry can fail for a reason gone by the next read, and dev-server watch mode
    caches nothing.
    """
    if template_edits_watched():
        return _build_page_backends_for_watch()[0]
    base_dir = resolve_base_dir()
    memo = _state.memo
    if memo is not None:
        if memo.base_dir != base_dir:
            # A change of BASE_DIR alone emits no reload, so the routers built
            # against the previous one go, and their diagnostics with them.
            _forget_backends()
        elif memo.backends is not None:
            return memo.backends
    backends, complete = _build_page_backends_for_watch()
    _state.memo = _BackendsMemo(base_dir, backends if complete else None)
    return backends


def iter_page_backends_for_watch() -> Iterator[RouterBackend]:
    """Return one router per `PAGE_BACKENDS` entry, skipping the ones that fail.

    A backend that cannot be built costs its own trees alone, so the watcher still sees
    every tree the other entries report, and every router is built eagerly.
    """
    return iter(_page_backends_for_watch())


def page_root_paths_for_watch(backend: RouterBackend) -> list[Path]:
    """Return the resolved page trees `backend` reports.

    A backend that raises or misshapes its answer contributes no tree, not a bad value.
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
    return [resolved_tree(root.path) for root in roots]


def components_folder_name_for_watch(backend: RouterBackend) -> str | None:
    """Return the components folder `backend` names, dropping anything but a name."""
    return _reads.read(
        backend,
        "components folder name",
        backend.components_folder_name,
        valid=lambda name: name is None or isinstance(name, str),
        default=None,
    )


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
    return _djx_paths_for_watch("layout.djx")


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
