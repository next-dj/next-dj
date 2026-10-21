"""Discovery helpers that list page roots and component folder pairs.

`runserver`, `collectstatic` and static discovery use it, skipping a failing router.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, NamedTuple

from next.backends import backend_entries
from next.diagnostics import QUIET_PERIOD, BackendReadLog
from next.ports import router_access_slot
from next.utils import (
    LAYOUT_TEMPLATE,
    forget_resolved_trees,
    page_roots_shape_error,
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
    "nothing to the watcher. The same failure is not logged again for "
    f"{QUIET_PERIOD:.0f} seconds or until the framework is reconfigured."
)

# Every router read goes through one log, so a failing backend logs at a bounded rate.
_reads = BackendReadLog(logger)


class _BackendsMemo(NamedTuple):
    """The held routers, `None` for an incomplete build.

    A `BASE_DIR` change reloads the routers, which drops the memo with them.
    """

    backends: list[RouterBackend] | None


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


def forget_watch_state(**kwargs) -> None:
    """Drop everything the watch layer holds, so a reconfiguration is read again."""
    _forget_backends()
    forget_resolved_trees()


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


def iter_page_backends_for_watch() -> Iterator[RouterBackend]:
    """Return one router per `PAGE_BACKENDS` entry, skipping the ones that fail.

    A backend that cannot be built loses only its own trees, and routers build eagerly.
    """
    return iter(_page_backends_for_watch())


def page_root_keys_for_backend(backend: RouterBackend) -> list[tuple[Path, str | None]]:
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


def page_root_paths_for_watch(backend: RouterBackend) -> list[Path]:
    """Return the resolved page trees `backend` reports."""
    return [path for path, _key in page_root_keys_for_backend(backend)]


def components_folder_name_for_watch(backend: RouterBackend) -> str | None:
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
