"""Template-text loaders and the layout composition engine.

`module.template` is consulted before the `TEMPLATE_LOADERS` chain runs.
"""

from __future__ import annotations

import logging
import os
import stat
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, ClassVar, Final, override

from next.boundaries.markup import wrapper_tag
from next.boundaries.options import DEFAULT_TAG
from next.caches import FRAMEWORK, ROUTES, SETTINGS, PageCache, memo
from next.conf import next_framework_settings
from next.conf.imports import import_class_cached
from next.conf.router import router_enabled
from next.discovery import get_pages_directories_for_watch
from next.errors import SettingError
from next.introspect import is_coroutine_callable, refuse_async
from next.pages.errors import PageModuleImportError
from next.permissions.access import declarations
from next.stamps import all_fresh, stamp_of, stat_mtime_ns, trusted
from next.utils import (
    LAYOUT_TEMPLATE,
    MAX_ANCESTOR_WALK_DEPTH,
    classify_dirs_entries,
    exec_module_file,
    is_within,
    resolve_base_dir,
    template_edits_watched,
)

from .layouts import (
    LayoutChain,
    LayoutId,
    forget_chains,
    held_chain,
    hold_chain,
    layout_id,
)
from .paths import page_path_info
from .placeholder import (
    BOUNDARY_END_TAG,
    BOUNDARY_TAG,
    FAILED_TAG,
    NO_WRAPPER,
    PLACEHOLDER_CLOSE,
    PLACEHOLDER_OPEN,
    SLOT_END_TAG,
    SLOT_TAG,
    RootScan,
    placeholder_spans,
    quote_source,
    scan_root,
)


if TYPE_CHECKING:
    import types
    from collections.abc import Iterable, Sequence
    from pathlib import Path


logger = logging.getLogger(__name__)


# A token no template source contains, so filling the slot is unambiguous.
_BODY_SLOT = "\x00next-page-body\x00"

PAGE_MODULE: Final = "page.py"
ERROR_TEMPLATE: Final = "error.djx"
LOADING_TEMPLATE: Final = "loading.djx"

_FAILED_BODY: Final = f"{{% {FAILED_TAG} %}}"
_BOUNDARY_CLOSE: Final = f"{{% {BOUNDARY_END_TAG} %}}"


@dataclass(frozen=True, slots=True)
class _PageLoad:
    """One execution of a `page.py`, recorded with the stamp taken before it ran.

    The module and the failure share one entry, so a concurrent load cannot split them.
    """

    stamp: int
    module: types.ModuleType | None
    error: Exception | None


_MODULE_MEMO: PageCache[Path, _PageLoad] = PageCache(depends=())
_FAILED_PATHS: set[Path] = set()
_MEMO_WRITE_LOCK = threading.Lock()

# Load stamp per path, kept on eviction since a re-exec at that mtime is a no-op.
_stamps: dict[Path, int] = {}


@dataclass(slots=True)
class _Version:
    """A monotonic counter of the module memo."""

    value: int = 0


_version = _Version()
"""Incremented only by a whole-memo reset and a page tree reload."""

_generation = _Version()
"""Incremented by every load, forget and reset, after the stamps it covers change."""


module_counters: Final = (_version, _generation)
"""The version and the generation counter, for a memo that reads both without a call."""


def module_version() -> int:
    """Return the version of the module memo, incremented by a reset and a reload."""
    return _version.value


def module_generation() -> int:
    """Return a counter that every load, forget and reset increments.

    A memo holding stamps compares it first and reads the stamps only after it changed.
    """
    return _generation.value


def module_stamps(paths: tuple[Path, ...]) -> tuple[int | None, ...]:
    """Return the stamp each path was last loaded at, `None` for one never loaded.

    A chain memo compares them, so a load elsewhere in the tree leaves it valid.
    """
    return tuple(map(_stamps.get, paths))


def _load_stamps(loads: Iterable[_PageLoad | None]) -> tuple[int | None, ...]:
    """Return the stamp each load ran against, `None` for a file that did not stat."""
    return tuple(None if load is None else load.stamp for load in loads)


@dataclass(frozen=True, slots=True)
class AncestorStamps:
    """The modules one memo reads, root first, with the load stamps it read them at.

    A memo over them rebuilds when one of them reloads, not on a load of another file.
    """

    paths: tuple[Path, ...]
    version: int
    watched: bool
    generation: int = -1
    stamps: tuple[int | None, ...] = ()
    settled: bool = True

    @classmethod
    def begin(cls, file_path: Path) -> AncestorStamps:
        """Walk the ancestors of `file_path` inside its page tree, before any load."""
        depth = page_tree_depth(file_path.parent)
        return cls(
            paths=tuple(reversed(page_path_info(file_path).ancestors[:depth])),
            version=_version.value,
            watched=template_edits_watched(),
        )

    def loaded(self) -> tuple[AncestorStamps, tuple[types.ModuleType | None, ...]]:
        """Load each ancestor once, returning the stamps of those loads and the modules.

        A build reads only these modules, so a later load invalidates the stamps.
        """
        loads = tuple(map(_page_load, self.paths))
        stamps = _load_stamps(loads)
        generation = _generation.value
        if module_stamps(self.paths) != stamps:
            generation = -1
        modules = tuple(None if load is None else load.module for load in loads)
        settled = all(map(trusted, stamps))
        return replace(
            self, generation=generation, stamps=stamps, settled=settled
        ), modules

    def revalidated(self) -> AncestorStamps | None:
        """Return the stamps while no ancestor has reloaded, otherwise `None`.

        A chain holding a token of an unsettled mtime is never served, edits or not.
        """
        watched = template_edits_watched()
        if (
            not self.settled
            or self.version != _version.value
            or self.watched is not watched
        ):
            return None
        if watched:
            loads = tuple(map(_page_load, self.paths))
            return self if _load_stamps(loads) == self.stamps else None
        generation = _generation.value
        if self.generation == generation:
            return self
        if module_stamps(self.paths) != self.stamps:
            return None
        return replace(self, generation=generation)


def has_load_errors() -> bool:
    """Whether the latest load of any `page.py` failed.

    The page access chain calls it first, so a site without one runs no stat.
    """
    return bool(_FAILED_PATHS)


def last_load_error(file_path: Path) -> PageModuleImportError | None:
    """Return the import failure of the `page.py` at `file_path`, loading it when stale.

    A new wrapper is built per call, since a re-raised instance grows its traceback.
    """
    return load_page_module(file_path)[1]


def load_page_module(
    file_path: Path,
) -> tuple[types.ModuleType | None, PageModuleImportError | None]:
    """Return the module now at `file_path` and its import failure from one load.

    A caller reading both in two calls could combine a failure with a later fix.
    """
    load = _page_load(file_path)
    if load is None or load.error is None:
        return (None if load is None else load.module), None
    error = PageModuleImportError(file_path)
    error.__cause__ = load.error
    return None, error


def _load_python_module(file_path: Path) -> types.ModuleType | None:
    """Execute `file_path` as a fresh page module, raising whatever its body raises.

    An async generator `render` raises here too, so the refusal runs once per load.
    """
    module = exec_module_file(file_path, "page_module")
    render = getattr(module, "render", None)
    if callable(render) and not is_coroutine_callable(render):
        refuse_async(render, "page.py render")
    return module


def _remember(file_path: Path, mtime_ns: int, load: _PageLoad) -> None:
    """Store `load` and the failure index, unless the file changed since `mtime_ns`.

    A slower load of an older mtime would otherwise overwrite the load that replaced it.
    """
    with _MEMO_WRITE_LOCK:
        if stat_mtime_ns(file_path) != mtime_ns:
            return
        # Ordered so a lock-free read never finds a failure missing from the index.
        if load.error is None:
            _MODULE_MEMO[file_path] = load
            _FAILED_PATHS.discard(file_path)
        else:
            _FAILED_PATHS.add(file_path)
            _MODULE_MEMO[file_path] = load
        _stamps[file_path] = load.stamp
        _generation.value += 1


def _forget(file_path: Path) -> None:
    """Drop the entry of `file_path` and its place in the failure index together."""
    with _MEMO_WRITE_LOCK:
        _MODULE_MEMO.pop(file_path)
        _FAILED_PATHS.discard(file_path)
        _stamps.pop(file_path, None)
        _generation.value += 1
    declarations.forget(file_path)


def _page_load(file_path: Path) -> _PageLoad | None:
    """Return the load of `file_path` for its current mtime, executing it on a miss.

    `None` stands for a file that does not stat, like the `page.py` of a template page.
    """
    mtime_ns = stat_mtime_ns(file_path)
    if mtime_ns is None:
        # The stamp survives an eviction, so it also records that the path was loaded.
        if (
            file_path in _stamps
            or file_path in _MODULE_MEMO
            or file_path in _FAILED_PATHS
        ):
            _forget(file_path)
        return None

    cached = _MODULE_MEMO.get(file_path)
    if cached is not None and cached.stamp == mtime_ns:
        # Not reordered, since it runs up to three times per dispatch.
        return cached

    stamp = stamp_of(mtime_ns)
    try:
        with declarations.collecting(file_path):
            module = _load_python_module(file_path)
    except SettingError:
        # A misshapen setting is no broken page, so its check names it, not this memo.
        raise
    except Exception as exc:
        if stat_mtime_ns(file_path) is None:
            # Removed between the stat and the execution, so absent rather than broken.
            _forget(file_path)
            return None
        logger.exception("Could not import page module %s", file_path)
        load = _PageLoad(stamp, None, exc)
    else:
        load = _PageLoad(stamp, module, None)
    _remember(file_path, mtime_ns, load)
    return load


def load_python_module_memo(file_path: Path) -> types.ModuleType | None:
    """Return the module now at `file_path`, or `None` when it is absent or broken."""
    load = _page_load(file_path)
    return None if load is None else load.module


def reset_module_memo() -> None:
    """Drop every memoised load so the next one re-executes from disk.

    A rewrite within the same mtime tick would otherwise return the stale module.
    """
    with _MEMO_WRITE_LOCK:
        loaded = (*_stamps, *_MODULE_MEMO, *_FAILED_PATHS)
        _MODULE_MEMO.clear()
        _FAILED_PATHS.clear()
        _stamps.clear()
        _ABOVE_TREE.clear()
        _version.value += 1
        _generation.value += 1
    for file_path in loaded:
        declarations.forget(file_path)


def _pages_dirs_for_config(config: dict) -> list[Path]:
    """Return candidate roots from one router `DIRS` entry (paths only)."""
    path_roots, _ = classify_dirs_entries(config.get("DIRS"), resolve_base_dir())
    return list(path_roots)


@memo(FRAMEWORK, ROUTES)
def shared_layouts() -> tuple[Path, ...]:
    """Return the resolved root `layout.djx` of each page backend `DIRS`.

    It wraps every page, and a tuple keeps a caller from reordering the shared result.
    """
    configs = next_framework_settings.PAGE_BACKENDS
    candidates = (
        layout.resolve()
        for config in configs
        if isinstance(config, dict)
        for directory in _pages_dirs_for_config(config)
        if directory.exists() and (layout := directory / LAYOUT_TEMPLATE).is_file()
    )
    return tuple(dict.fromkeys(candidates))


@memo(SETTINGS, ROUTES)
def _page_roots() -> tuple[Path, ...]:
    """Return the resolved page trees the routers report, memoised.

    Reading them probes the trees of every router, which is too costly per walk.
    """
    return tuple(get_pages_directories_for_watch())


def forget_page_roots(**kwargs: object) -> None:
    """Drop the memoised page trees so the next walk asks the routers again."""
    _page_roots.cache_clear()
    _ABOVE_TREE.clear()
    with _MEMO_WRITE_LOCK:
        _version.value += 1


SETTINGS.attach(forget_page_roots)
ROUTES.attach(forget_page_roots)


_TREE_DEPTHS: PageCache[Path, tuple[tuple[Path, ...], tuple[int, int] | None]] = (
    PageCache(depends=())
)


def _tree_depths(start_dir: Path) -> tuple[int, int] | None:
    """Return the directories from `start_dir` to its deepest and its outermost root."""
    roots = _page_roots()
    held = _TREE_DEPTHS.get(start_dir)
    if held is not None and held[0] is roots:
        return held[1]
    resolved = start_dir.resolve()
    depths = [
        len(resolved.relative_to(root).parts) + 1
        for root in roots
        if resolved.is_relative_to(root)
    ]
    bounds = (min(depths), max(depths)) if depths else None
    _TREE_DEPTHS[start_dir] = (roots, bounds)
    return bounds


def _tree_depth(start_dir: Path) -> int | None:
    """Return the directories from `start_dir` to its tree root, `None` outside one."""
    bounds = _tree_depths(start_dir)
    return None if bounds is None else bounds[0]


def _outer_tree_depth(start_dir: Path) -> int:
    """Return the directories from `start_dir` up to its outermost tree root, capped.

    A layout added between nested roots moves a directory only this deep.
    """
    bounds = _tree_depths(start_dir)
    if bounds is None:
        return MAX_ANCESTOR_WALK_DEPTH
    return min(bounds[1], MAX_ANCESTOR_WALK_DEPTH)


def page_tree_depth(start_dir: Path) -> int:
    """Return the number of directories from `start_dir` up to its page tree root.

    A directory outside every tree returns the walk cap.
    """
    depth = _tree_depth(start_dir)
    if depth is None:
        return MAX_ANCESTOR_WALK_DEPTH
    return min(depth, MAX_ANCESTOR_WALK_DEPTH)


def in_page_tree(start_dir: Path) -> bool:
    """Return whether `start_dir` lies inside a page tree some router routes."""
    return _tree_depth(start_dir) is not None


def _read_string_list(module: types.ModuleType, attr: str) -> list[str]:
    """Return a module-level string-sequence attribute or an empty list."""
    value = getattr(module, attr, None)
    if not isinstance(value, list | tuple):
        return []
    return [str(item) for item in value if isinstance(item, str) and item]


def read_module_string_lists(
    file_path: Path, attrs: Iterable[str]
) -> dict[str, list[str]] | None:
    """Return the named module-level string lists a page-tree module declares.

    `None` tells an absent or broken module from one declaring none.
    """
    module = load_python_module_memo(file_path)
    if module is None:
        return None
    return {attr: _read_string_list(module, attr) for attr in attrs}


class TemplateLoader(ABC):
    """Pluggable source of template text for a `page.py` path.

    Subclasses set `source_name` to their filename, surfaced by the `next.W043` check.
    """

    source_name: ClassVar[str] = ""

    @abstractmethod
    def can_load(self, file_path: Path) -> bool:
        """Return whether this loader applies without heavy work."""

    @abstractmethod
    def load_template(self, file_path: Path) -> str | None:
        """Return the template source. Return `None` when unavailable."""

    def source_path(self, file_path: Path) -> Path | None:
        """Return the filesystem path this loader reads for `file_path`.

        The page manager snapshots the mtime of the result for stale-cache detection.
        """
        del file_path
        return None


class PythonTemplateLoader(TemplateLoader):
    """Load from `page.py` when the module defines a `template` attribute."""

    source_name: ClassVar[str] = "template"

    @override
    def can_load(self, file_path: Path) -> bool:
        """Return whether the module loads and defines `template`."""
        module = load_python_module_memo(file_path)
        return module is not None and hasattr(module, "template")

    @override
    def load_template(self, file_path: Path) -> str | None:
        """Return `module.template` if the module exposes it."""
        module = load_python_module_memo(file_path)
        return getattr(module, "template", None) if module else None


class DjxTemplateLoader(TemplateLoader):
    """Load from a sibling `template.djx` next to `page.py`."""

    source_name: ClassVar[str] = "template.djx"

    @override
    def can_load(self, file_path: Path) -> bool:
        """Return whether sibling `template.djx` exists."""
        return (file_path.parent / "template.djx").exists()

    @override
    def load_template(self, file_path: Path) -> str | None:
        """Return the file contents of `template.djx`."""
        djx_file = file_path.parent / "template.djx"
        try:
            return djx_file.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            return None

    @override
    def source_path(self, file_path: Path) -> Path | None:
        """Return the sibling `template.djx` path for stale-cache detection."""
        djx_file = file_path.parent / "template.djx"
        return djx_file if djx_file.exists() else None


_ABOVE_TREE: PageCache[tuple[Path, int], tuple[tuple[Path, int], ...]] = PageCache(
    depends=(SETTINGS, ROUTES)
)
_RESOLVED: PageCache[Path, Path] = PageCache(depends=())


def _resolved(path: Path) -> Path:
    """Return `path` resolved, held so a repeated walk costs no `realpath`."""
    held = _RESOLVED.get(path)
    if held is None:
        held = _RESOLVED[path] = path.resolve()
    return held


def _layout_at(
    directory: Path, *, linked: bool, name: str = LAYOUT_TEMPLATE
) -> tuple[Path, int] | None:
    """Return the file `name` of `directory` by its resolved name, with its mtime.

    Only a link is resolved, since a plain file under a real directory names itself.
    A miss builds no `Path`, as a walk probes far more directories than hold a file.
    """
    try:
        status = os.lstat(f"{directory}{os.sep}{name}")
        if stat.S_ISLNK(status.st_mode):
            linked = True
            status = (directory / name).stat()
    except OSError:
        return None
    if not stat.S_ISREG(status.st_mode):
        return None
    layout_file = directory / name
    return (_resolved(layout_file) if linked else layout_file), status.st_mtime_ns


@dataclass(frozen=True, slots=True)
class PageBoundary:
    """The `error.djx` and the `loading.djx` one directory in the chain of a page holds.

    `level` counts the directories from the page up to it, `slot` the layouts inside it.
    """

    level: int
    slot: int
    error: Path | None
    loading: Path | None


@dataclass(frozen=True, slots=True)
class ErrorBoundary:
    """One `error.djx` in the chain of a page, placed as its `PageBoundary` is."""

    level: int
    slot: int
    source: Path


@dataclass(frozen=True, slots=True)
class BoundaryChain:
    """The boundary files a page composes through, nearest first, and its error ones.

    `deferred` holds the directories with a `loading.djx`, each a deferred boundary.
    """

    boundaries: tuple[PageBoundary, ...] = ()
    errors: tuple[ErrorBoundary, ...] = ()
    deferred: tuple[PageBoundary, ...] = ()

    @classmethod
    def of(cls, boundaries: tuple[PageBoundary, ...]) -> BoundaryChain:
        """Return the chain of `boundaries`, the shared empty one for none."""
        if not boundaries:
            return NO_BOUNDARIES
        errors = tuple(
            ErrorBoundary(found.level, found.slot, found.error)
            for found in boundaries
            if found.error is not None
        )
        deferred = tuple(found for found in boundaries if found.loading is not None)
        return cls(boundaries, errors, deferred)

    def guard_of(self, level: int) -> ErrorBoundary | None:
        """Return the nearest error boundary at or above `level`, `None` for none."""
        return next((guard for guard in self.errors if guard.level >= level), None)

    @property
    def loading(self) -> Path | None:
        """Return the nearest `loading.djx`, `None` when the chain holds none."""
        return next(
            (found.loading for found in self.boundaries if found.loading is not None),
            None,
        )

    @property
    def error(self) -> Path | None:
        """Return the nearest `error.djx`, `None` when the chain holds none."""
        return self.errors[0].source if self.errors else None


NO_BOUNDARIES: Final = BoundaryChain()
"""The chain of a page whose directories hold no boundary file."""


def _boundary_at(
    directory: Path, level: int, slot: int, *, linked: bool
) -> PageBoundary | None:
    """Return the boundary files of `directory`, `None` when it holds neither."""
    error = _layout_at(directory, linked=linked, name=ERROR_TEMPLATE)
    loading = _layout_at(directory, linked=linked, name=LOADING_TEMPLATE)
    if error is None and loading is None:
        return None
    return PageBoundary(
        level,
        slot,
        None if error is None else error[0],
        None if loading is None else loading[0],
    )


def _routed_root(start: Path) -> Path | None:
    """Return the deepest routed page tree root holding the resolved `start`."""
    depth = _tree_depth(start)
    if depth is None:
        return None
    return start if depth == 1 else start.parents[depth - 2]


def _above_tree(tree_root: Path, room: int) -> tuple[tuple[Path, int], ...]:
    """Return the layouts above `tree_root` with their mtimes, held between walks.

    A process watching template edits walks again, so a layout added later shows.
    """
    key = (tree_root, room)
    watched = template_edits_watched()
    held = _ABOVE_TREE.get(key)
    if held is not None and not watched and all_fresh(held):
        return held
    found: list[tuple[Path, int]] = []
    # The filesystem root is the last ancestor and holds no layout.
    for directory in tuple(tree_root.parents)[:-1][:room]:
        layout = _layout_at(directory, linked=False)
        if layout is not None:
            found.append(layout)
    if not watched:
        _ABOVE_TREE[key] = tuple((path, stamp_of(mtime)) for path, mtime in found)
    return tuple(found)


def _walk(
    file_path: Path,
    watched_depth: int,
    *,
    cut: bool = True,
    resolved: bool = False,
    tree_root: Path | None = None,
    boundaries: list[PageBoundary] | None = None,
) -> tuple[list[Path], list[Path], list[int | None]]:
    """Climb the ancestors of `file_path` for layouts, watched directories and mtimes.

    The climb follows the written path, so a symlinked folder keeps its tree's layouts.
    A `boundaries` list collects the boundary files of each probed directory or layout.
    """
    layout_files: list[Path] = []
    watched_dirs: list[Path] = []
    mtimes: list[int | None] = []
    start = file_path.parent if resolved else file_path.absolute().parent
    # Below a symlink the resolved depth does not count the written parents.
    linked = not resolved and _resolved(start) != start
    if linked:
        tree_root = None
    elif tree_root is None:
        tree_root = _routed_root(start)
    ancestors, above, room = _split_ancestry(start, watched_depth, tree_root)
    probed = ancestors[:MAX_ANCESTOR_WALK_DEPTH]

    for depth, current_dir in enumerate(probed):
        if depth < watched_depth:
            watched_dirs.append(current_dir)
        if boundaries is not None:
            found = _boundary_at(current_dir, depth, len(layout_files), linked=linked)
            if found is not None:
                boundaries.append(found)
        layout = _layout_at(current_dir, linked=linked)
        if layout is not None:
            layout_file, mtime_ns = layout
            layout_files.append(layout_file)
            mtimes.append(mtime_ns)
            if cut and _is_root_layout(layout_file, mtime_ns):
                return layout_files, watched_dirs, mtimes

    outer = _Outer(layout_files, mtimes, boundaries, len(probed) - len(layout_files))
    if above is not None and _append_above(above, room, outer, cut=cut):
        return layout_files, watched_dirs, mtimes

    _append_shared(outer, cut=cut)
    return layout_files, watched_dirs, mtimes


@dataclass(frozen=True, slots=True)
class _Outer:
    """The chain a walk extends with the layouts outside the directories it probed.

    The boundary files beside such a layout guard the page as the layout wraps it.
    """

    layout_files: list[Path]
    mtimes: list[int | None]
    boundaries: list[PageBoundary] | None
    base: int

    def append(self, layout_file: Path, mtime_ns: int | None) -> None:
        """Append `layout_file` after the boundary files of its directory."""
        boundaries = self.boundaries
        if boundaries is not None:
            slot = len(self.layout_files)
            found = _boundary_at(
                layout_file.parent, self.base + slot, slot, linked=False
            )
            if found is not None:
                boundaries.append(found)
        self.layout_files.append(layout_file)
        self.mtimes.append(mtime_ns)


def _append_above(above: Path, room: int, outer: _Outer, *, cut: bool) -> bool:
    """Append the layouts above the tree root `above`, telling whether a root ends."""
    for layout_file, mtime_ns in _above_tree(above, room):
        outer.append(layout_file, mtime_ns)
        if cut and _is_root_layout(layout_file, mtime_ns):
            return True
    return False


def _append_shared(outer: _Outer, *, cut: bool) -> None:
    """Append the `DIRS` root layouts not yet in the chain, ending at a root one."""
    for shared in shared_layouts():
        if shared in outer.layout_files:
            continue
        mtime_ns = stat_mtime_ns(shared)
        outer.append(shared, mtime_ns)
        if cut and mtime_ns is not None and _is_root_layout(shared, mtime_ns):
            break


def _split_ancestry(
    start: Path, watched_depth: int, tree_root: Path | None
) -> tuple[list[Path], Path | None, int]:
    """Split the ancestry of `start` into the directories to probe and a held tail.

    The tail is the part above `tree_root`, with the room the walk cap leaves it.
    """
    inside = _dirs_inside(start, tree_root)
    room = 0 if inside is None else MAX_ANCESTOR_WALK_DEPTH - inside
    if inside is None or watched_depth > inside or room <= 0:
        # The filesystem root is the last ancestor and holds no layout.
        return [start, *start.parents][:-1], None, 0
    return [start, *start.parents[: inside - 1]], tree_root, room


def _dirs_inside(start: Path, tree_root: Path | None) -> int | None:
    """Return the directories from `start` up to `tree_root`, or `None` outside it."""
    if tree_root is None or tree_root.parent == tree_root:
        return None
    if not is_within(start, tree_root):
        return None
    return len(start.parts) - len(tree_root.parts) + 1


def _climb(
    file_path: Path, watched_depth: int, *, cut: bool = True, resolved: bool = False
) -> tuple[list[Path], list[Path]]:
    """Climb the ancestors of `file_path` for layouts and watched directories.

    The climb ends at the first root layout unless `cut` is off.
    """
    layouts, watched, _ = _walk(file_path, watched_depth, cut=cut, resolved=resolved)
    return layouts, watched


def layout_chain(
    file_path: Path, *, cut: bool = True, resolved: bool = False
) -> tuple[Path, ...]:
    """Return the layouts that wrap `file_path` from near to far, cut after a root.

    The cut spans the `DIRS` tail too, and `cut=False` keeps the whole ancestry.
    A path that is `resolved` already skips the second resolution.
    """
    return tuple(_climb(file_path, 0, cut=cut, resolved=resolved)[0])


_BOUNDARIES: PageCache[Path, tuple[int, tuple[Path, ...], BoundaryChain]] = PageCache(
    depends=()
)


def _discover(file_path: Path) -> tuple[tuple[Path, ...], BoundaryChain]:
    """Walk the chain of `file_path` once for its layouts and its boundary files.

    Both are held until the module memo version moves, unless edits are watched.
    """
    watched = template_edits_watched()
    version = _version.value
    if not watched:
        held = _BOUNDARIES.get(file_path)
        if held is not None and held[0] == version:
            return held[1], held[2]
    found: list[PageBoundary] = []
    layouts = tuple(_walk(file_path, 0, boundaries=found)[0])
    chain = BoundaryChain.of(tuple(found))
    if not watched:
        _BOUNDARIES[file_path] = (version, layouts, chain)
    return layouts, chain


def page_boundaries(file_path: Path) -> BoundaryChain:
    """Return the boundary files the chain of `file_path` holds, nearest first.

    A process watching template edits walks again, so a file added later shows.
    """
    return _discover(file_path)[1]


def layout_stamps(
    file_path: Path, tree_root: Path | None = None
) -> tuple[tuple[Path, int | None], ...]:
    """Return the cut layouts of `file_path` with the mtimes a snapshot keeps.

    A `tree_root` only says where to hold the tail, so any ancestor yields one chain.
    """
    layouts, _, mtimes = _walk(file_path, 0, tree_root=tree_root)
    stamps = (None if mtime_ns is None else stamp_of(mtime_ns) for mtime_ns in mtimes)
    return tuple(zip(layouts, stamps, strict=True))


def _wrapper_of(layout_files: Sequence[Path], slot: int) -> str | None:
    """Return the wrapper element the layout around `slot` leaves its placeholder."""
    if slot >= len(layout_files):
        return DEFAULT_TAG
    text = template_source(layout_files[slot])
    spans = None if text is None else placeholder_spans(text)
    if text is None or not spans:
        return DEFAULT_TAG
    return wrapper_tag(text[: spans[0][0]])


def _guarded(content: str, found: PageBoundary, wrapper: str | None) -> str:
    """Return `content` inside the internal boundary tag of the files of `found`.

    A `loading.djx` adds its source after the wrapper, an empty error source for none.
    """
    shown = wrapper or NO_WRAPPER
    deferred = "" if found.loading is None else f" {quote_source(found.loading)}"
    opening = f"{{% {BOUNDARY_TAG} {quote_source(found.error)} {shown}{deferred} %}}"
    return f"{opening}{content}{_BOUNDARY_CLOSE}"


def _as_placeholder_fallback(body: str) -> str:
    """Return `body` as the fallback of a paired placeholder.

    Only a chain rendered without its ancestor layout ever shows the fallback.
    """
    return f"{PLACEHOLDER_OPEN}{body}{PLACEHOLDER_CLOSE}"


@dataclass(frozen=True, slots=True)
class _Held[T]:
    """A value read from a file under the stamp taken before the read.

    A token of an unsettled mtime never equals a stat, so such an entry is never shared.
    """

    stamp: int
    value: T


_LAYOUT_TEXTS: PageCache[Path, _Held[str]] = PageCache(depends=())
_LAYOUT_ROOTS: PageCache[Path, _Held[RootScan]] = PageCache(depends=())


def template_source(source: Path) -> str | None:
    """Return the text of a template `source`, read again only when its mtime moved.

    A build, its root check, the sources snapshot and the system checks share one read.
    """
    mtime_ns = stat_mtime_ns(source)
    if mtime_ns is None:
        return None
    held = _held_text(source, mtime_ns)
    return None if held is None else held.value


def _held_text(source: Path, mtime_ns: int) -> _Held[str] | None:
    """Return the text of `source` held for `mtime_ns`, read again on any other stamp.

    The BOM a Windows editor writes is dropped, so it never lands inside a page.
    """
    held = _LAYOUT_TEXTS.get(source)
    if held is not None and held.stamp == mtime_ns:
        return held
    stamp = stamp_of(mtime_ns)
    try:
        text = source.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None
    held = _LAYOUT_TEXTS[source] = _Held(stamp, text)
    return held


def forget_layout_texts() -> None:
    """Drop every held layout source, so a rewrite within one mtime tick is read."""
    _LAYOUT_TEXTS.clear()
    _LAYOUT_ROOTS.clear()
    _ABOVE_TREE.clear()
    _RESOLVED.clear()
    _BOUNDARIES.clear()
    forget_chains()


def _layout_roots() -> tuple[Path, ...]:
    """Return the page trees and the `DIRS` roots a layout id names its path below."""
    return (*_page_roots(), *(shared.parent for shared in shared_layouts()))


def _slotted(content: str, ident: LayoutId) -> str:
    """Return `content` inside the internal slot tag of the layout `ident`."""
    return f"{{% {SLOT_TAG} {ident} %}}{content}{{% {SLOT_END_TAG} %}}"


def layout_ids(file_path: Path) -> tuple[LayoutId, ...]:
    """Return the ids of the layouts that wrap `file_path`, outer first.

    The chain is held beside the skeleton until the module memo version moves.
    """
    version = _version.value
    held = held_chain(file_path, version)
    if held is not None:
        return held.ids
    layouts, _watched, mtimes = _walk(file_path, 0)
    roots = _layout_roots()
    ids: list[LayoutId] = []
    stamps: list[tuple[Path, int | None]] = []
    for layout_file, mtime_ns in zip(layouts, mtimes, strict=True):
        text = template_source(layout_file)
        if text is None or not placeholder_spans(text):
            continue
        ids.append(layout_id(layout_file, text, roots))
        stamps.append((layout_file, None if mtime_ns is None else stamp_of(mtime_ns)))
    chain = LayoutChain(tuple(reversed(ids)), tuple(stamps), version)
    hold_chain(file_path, chain)
    return chain.ids


def layout_root_scan(layout_file: Path) -> RootScan | None:
    """Return the root markers of `layout_file`, `None` when it does not read."""
    mtime_ns = stat_mtime_ns(layout_file)
    if mtime_ns is None:
        return None
    return _scan_at(layout_file, mtime_ns)


def _scan_at(layout_file: Path, mtime_ns: int) -> RootScan | None:
    """Return the root scan of `layout_file` read at `mtime_ns`, lexing it on a miss."""
    held = _LAYOUT_ROOTS.get(layout_file)
    if held is not None and held.stamp == mtime_ns:
        return held.value
    content = _held_text(layout_file, mtime_ns)
    if content is None:
        return None
    scan = scan_root(content.value)
    _LAYOUT_ROOTS[layout_file] = _Held(content.stamp, scan)
    return scan


def _is_root_layout(layout_file: Path, mtime_ns: int) -> bool:
    """Return whether `layout_file` read at `mtime_ns` is a root, unreadable is not."""
    scan = _scan_at(layout_file, mtime_ns)
    return scan is not None and scan.is_root


class LayoutTemplateLoader:
    """Compose nested `layout.djx` wrappers around the page template.

    It is not a `TemplateLoader`, since the loader chain supplies the body it wraps.
    """

    def can_load(self, file_path: Path) -> bool:
        """Return whether any `layout.djx` wraps the path as composition walks it.

        The walk is uncut, so it stats the layouts without reading one for its marker.
        """
        return bool(layout_chain(file_path, cut=False))

    def compose_skeleton(self, file_path: Path) -> str:
        """Return the layout chain for `file_path` with a slot where the body goes.

        The chain depends on the path alone, so a per-request body fills a cached slot.
        """
        return self.compose_body(_BODY_SLOT, file_path)

    def compose_slotted(self, file_path: Path) -> tuple[str, BoundaryChain]:
        """Return what `compose_skeleton` returns with the chain the walk found."""
        return self.compose_chained(_BODY_SLOT, file_path)

    def fill_skeleton(self, skeleton: str, body: str) -> str:
        """Return `skeleton` with `body` substituted into its body slot."""
        return skeleton.replace(_BODY_SLOT, body)

    def compose_body(self, body: str, file_path: Path) -> str:
        """Wrap `body` through the ancestor layout chain for `file_path`.

        A sibling `layout.djx` substitutes `body`, else it is a placeholder fallback.
        """
        return self.compose_chained(body, file_path)[0]

    def compose_chained(self, body: str, file_path: Path) -> tuple[str, BoundaryChain]:
        """Return what `compose_body` returns with the boundary chain the walk found."""
        layout_files, chain = _discover(file_path)
        sibling = file_path.parent / LAYOUT_TEMPLATE
        if chain.boundaries:
            composed = self._compose_guarded(
                body, layout_files, chain.boundaries, fallback=not sibling.is_file()
            )
            return composed, chain
        if not layout_files:
            return body, chain

        wrapped = body if sibling.is_file() else _as_placeholder_fallback(body)
        return self._compose_layout_hierarchy(wrapped, layout_files), chain

    def compose_failure(self, file_path: Path, caught: ErrorBoundary) -> str:
        """Return the chain of `file_path` from `caught` out, around a failed body.

        Layouts and boundaries below `caught` drop out, since the failure skipped them.
        """
        layout_files, chain = _discover(file_path)
        guards = tuple(
            replace(found, loading=None)
            for found in chain.boundaries
            if found.error is not None and found.level >= caught.level
        )
        sibling = (file_path.parent / LAYOUT_TEMPLATE).is_file()
        return self._compose_guarded(
            _FAILED_BODY, layout_files, guards, fallback=not sibling, start=caught.slot
        )

    def _compose_guarded(
        self,
        body: str,
        layout_files: Sequence[Path],
        guards: tuple[PageBoundary, ...],
        *,
        fallback: bool,
        start: int = 0,
    ) -> str:
        """Wrap `body` in the layouts from `start` out, each boundary at its slot.

        A boundary wraps the content before the layout of its own directory does.
        """
        content = body
        done = start
        for guard in guards:
            content = self._layered(
                content, layout_files, done, guard.slot, fallback=fallback
            )
            done = guard.slot
            content = _guarded(content, guard, _wrapper_of(layout_files, done))
        return self._layered(
            content, layout_files, done, len(layout_files), fallback=fallback
        )

    def _layered(
        self,
        content: str,
        layout_files: Sequence[Path],
        start: int,
        stop: int,
        *,
        fallback: bool,
    ) -> str:
        """Wrap `content` in the layouts from `start` to `stop`, nearest first."""
        if start >= stop:
            return content
        if start == 0 and fallback:
            content = _as_placeholder_fallback(content)
        return self._compose_layout_hierarchy(content, layout_files[start:stop])

    def layout_sources(self, file_path: Path) -> tuple[list[Path], list[Path]]:
        """Return the layout files behind `file_path` and the directories watched.

        A new `layout.djx` moves only its directory's mtime, so watch directories too.
        """
        return _climb(file_path, _outer_tree_depth(file_path.parent))

    def _find_layout_files(
        self, file_path: Path, *, resolved: bool = False
    ) -> list[Path]:
        """Return `layout.djx` paths from near to far plus global layouts, root cut."""
        return list(layout_chain(file_path, resolved=resolved))

    def _get_pages_dirs_for_config(self, config: dict) -> list[Path]:
        """Return candidate roots from one router `DIRS` entry (paths only)."""
        return _pages_dirs_for_config(config)

    def _compose_layout_hierarchy(
        self, template_content: str, layout_files: Sequence[Path]
    ) -> str:
        """Wrap the content in each layout from the nearest to the outermost.

        Each layout gets the content in its first placeholder, see `next.W078`.
        With the client router on, the content sits inside the slot tag of its layout.
        """
        result = template_content
        roots = _layout_roots() if router_enabled() else None

        for layout_file in layout_files:
            layout_content = template_source(layout_file)
            if layout_content is None:
                continue
            spans = placeholder_spans(layout_content)
            if spans:
                start, end = spans[0]
                if roots is not None:
                    ident = layout_id(layout_file, layout_content, roots)
                    result = _slotted(result, ident)
                # Sliced, since a substitution would interpret escapes in the body.
                result = layout_content[:start] + result + layout_content[end:]
        return result


@memo(FRAMEWORK)
def build_registered_loaders() -> Sequence[TemplateLoader]:
    """Instantiate `TEMPLATE_LOADERS` dotted paths into `TemplateLoader` instances.

    Bad entries are skipped with a debug log, as `check_template_loaders` reports them.
    """
    configured = next_framework_settings.TEMPLATE_LOADERS
    seen: set[type[TemplateLoader]] = set()
    instances: list[TemplateLoader] = []
    for entry in configured:
        if not isinstance(entry, str):
            logger.debug("Skipping non-string TEMPLATE_LOADERS entry: %r", entry)
            continue
        try:
            cls = import_class_cached(entry)
        except ImportError as e:
            logger.debug("Cannot import TEMPLATE_LOADERS entry %r: %s", entry, e)
            continue
        if not isinstance(cls, type) or not issubclass(cls, TemplateLoader):
            logger.debug(
                "TEMPLATE_LOADERS entry %r is not a TemplateLoader subclass", entry
            )
            continue
        if cls in seen:
            logger.debug("Skipping duplicate TEMPLATE_LOADERS entry: %r", entry)
            continue
        seen.add(cls)
        instances.append(cls())

    return tuple(instances)


def has_body(file_path: Path, module: types.ModuleType | None) -> bool:
    """Return whether `render`, a `template` string, or a loader gives the page a body.

    A `layout.djx` is no body, since it only wraps the body of the page beneath it.
    """
    if module is not None:
        if callable(getattr(module, "render", None)):
            return True
        if isinstance(getattr(module, "template", None), str):
            return True
    return any(loader.can_load(file_path) for loader in build_registered_loaders())


def renders_itself(file_path: Path) -> bool:
    """Return whether the page defines `render()`, importing it as a route does."""
    module, _error = load_page_module(file_path)
    return module is not None and callable(getattr(module, "render", None))


def serves(
    file_path: Path,
    module: types.ModuleType | None,
    error: PageModuleImportError | None,
) -> bool:
    """Return whether one load of a `page.py` routes a URL, a broken one for its error.

    A path that never existed is a virtual page, so a loader alone gives it a body.
    """
    if error is not None:
        return True
    if module is None and file_path.exists():
        return False
    return has_body(file_path, module)
