"""Template-text loaders and the layout composition engine.

`module.template` is consulted before the `TEMPLATE_LOADERS` chain runs.
"""

from __future__ import annotations

import contextlib
import functools
import logging
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, ClassVar, override

from django.core.signals import setting_changed

from next.caches import PageCache
from next.conf import next_framework_settings
from next.conf.imports import import_class_cached
from next.conf.signals import settings_reloaded
from next.pages.errors import PageModuleImportError
from next.utils import (
    MAX_ANCESTOR_WALK_DEPTH,
    classify_dirs_entries,
    exec_module_file,
    resolve_base_dir,
    stat_mtime_ns,
    template_edits_watched,
)

from .paths import page_path_info
from .placeholder import PLACEHOLDER_CLOSE, PLACEHOLDER_OPEN, placeholder_spans
from .watch import get_pages_directories_for_watch


if TYPE_CHECKING:
    import types
    from collections.abc import Iterable, Sequence
    from pathlib import Path


logger = logging.getLogger(__name__)


# A token no template source contains, so filling the slot is unambiguous.
_BODY_SLOT = "\x00next-page-body\x00"


@dataclass(frozen=True, slots=True)
class _PageLoad:
    """One execution of a `page.py`, recorded with the nanosecond mtime it read.

    The module and the failure share one entry, so a concurrent load cannot split them.
    """

    mtime_ns: int
    module: types.ModuleType | None
    error: Exception | None


_MODULE_MEMO: PageCache[Path, _PageLoad] = PageCache()
_FAILED_PATHS: set[Path] = set()
_MEMO_WRITE_LOCK = threading.Lock()

# The mtime of the current load per path, successful or failed. An eviction from the
# memo keeps it, since re-executing a file at the same mtime changes nothing.
_stamps: dict[Path, int] = {}


@dataclass(slots=True)
class _Version:
    """A monotonic counter of the module memo."""

    value: int = 0


_version = _Version()
"""Incremented only by a whole-memo reset and a page tree reload."""

_generation = _Version()
"""Incremented by every load, forget and reset, after the stamps it covers change."""


def module_version() -> int:
    """Return the version of the module memo, incremented by a reset and a reload."""
    return _version.value


def module_generation() -> int:
    """Return a counter that every load, forget and reset increments.

    A memo holding stamps compares it first and reads the stamps only after it changed.
    """
    return _generation.value


def module_stamps(paths: tuple[Path, ...]) -> tuple[int | None, ...]:
    """Return the mtime each path was last loaded at, `None` for one never loaded.

    A chain memo compares them, so a load elsewhere in the tree leaves it valid.
    """
    return tuple(map(_stamps.get, paths))


def _load_stamps(loads: Iterable[_PageLoad | None]) -> tuple[int | None, ...]:
    """Return the mtime each load ran against, `None` for a file that did not stat."""
    return tuple(None if load is None else load.mtime_ns for load in loads)


@dataclass(frozen=True, slots=True)
class AncestorStamps:
    """The `page.py` files above one page, root first, and the load stamps a memo read.

    A memo over them rebuilds once one of them reloads, and a load of another file
    leaves it valid.
    """

    paths: tuple[Path, ...]
    version: int
    watched: bool
    generation: int = -1
    stamps: tuple[int | None, ...] = ()

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
        return replace(self, generation=generation, stamps=stamps), modules

    def revalidated(self) -> AncestorStamps | None:
        """Return the stamps while no ancestor has reloaded, otherwise `None`.

        While template edits are watched each ancestor loads again, so an edit on disk
        takes effect at once.
        """
        watched = template_edits_watched()
        if self.version != _version.value or self.watched is not watched:
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

    The per-request fail-loud check calls it first, so a site without one runs no stat.
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
    """Execute `file_path` as a fresh page module, raising whatever its body raises."""
    return exec_module_file(file_path, "page_module")


def _remember(file_path: Path, load: _PageLoad) -> None:
    """Store `load` and the failure index, unless the file changed since its stat.

    A slower load of an older mtime would otherwise overwrite the load that replaced it.
    """
    with _MEMO_WRITE_LOCK:
        if stat_mtime_ns(file_path) != load.mtime_ns:
            return
        # Ordered so a lock-free read never finds a failure missing from the index.
        if load.error is None:
            _MODULE_MEMO[file_path] = load
            _FAILED_PATHS.discard(file_path)
        else:
            _FAILED_PATHS.add(file_path)
            _MODULE_MEMO[file_path] = load
        _stamps[file_path] = load.mtime_ns
        _generation.value += 1


def _forget(file_path: Path) -> None:
    """Drop the entry of `file_path` and its place in the failure index together."""
    with _MEMO_WRITE_LOCK:
        _MODULE_MEMO.pop(file_path)
        _FAILED_PATHS.discard(file_path)
        _stamps.pop(file_path, None)
        _generation.value += 1


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
    if cached is not None and cached.mtime_ns == mtime_ns:
        # Not reordered, because this runs up to three times per URL dispatch and the
        # bound limits memory rather than ranking pages.
        return cached

    try:
        module = _load_python_module(file_path)
    except Exception as exc:
        if stat_mtime_ns(file_path) is None:
            # Removed between the stat and the execution, so absent rather than broken.
            _forget(file_path)
            return None
        logger.exception("Could not import page module %s", file_path)
        load = _PageLoad(mtime_ns, None, exc)
    else:
        load = _PageLoad(mtime_ns, module, None)
    _remember(file_path, load)
    return load


def _load_python_module_memo(file_path: Path) -> types.ModuleType | None:
    """Return the module now at `file_path`, or `None` when it is absent or broken."""
    load = _page_load(file_path)
    return None if load is None else load.module


def reset_module_memo() -> None:
    """Drop every memoised load so the next one re-executes from disk.

    A rewrite within the same mtime tick would otherwise return the stale module.
    """
    with _MEMO_WRITE_LOCK:
        _MODULE_MEMO.clear()
        _FAILED_PATHS.clear()
        _stamps.clear()
        _version.value += 1
        _generation.value += 1


def _pages_dirs_for_config(config: dict) -> list[Path]:
    """Return candidate roots from one router `DIRS` entry (paths only)."""
    path_roots, _ = classify_dirs_entries(config.get("DIRS"), resolve_base_dir())
    return list(path_roots)


@functools.cache
def _additional_layout_files() -> tuple[Path, ...]:
    """Return root-level `layout.djx` files from each page backend `DIRS`.

    A tuple, so a caller cannot reorder the shared result.
    """
    configs = next_framework_settings.PAGE_BACKENDS or []
    if not isinstance(configs, list):
        configs = []
    candidates = (
        layout
        for config in configs
        if isinstance(config, dict)
        for directory in _pages_dirs_for_config(config)
        if directory.exists() and (layout := directory / "layout.djx").exists()
    )
    return tuple(dict.fromkeys(candidates))


def _reset_additional_layouts_cache(**kwargs) -> None:
    """Drop cached root-level `layout.djx` paths on settings reload."""
    _additional_layout_files.cache_clear()


settings_reloaded.connect(_reset_additional_layouts_cache)


@functools.cache
def _page_roots() -> tuple[Path, ...]:
    """Return the resolved page trees the routers report, memoised.

    Reading them probes the trees of every router, which is too costly per walk.
    """
    return tuple(get_pages_directories_for_watch())


def forget_page_roots(**kwargs) -> None:
    """Drop the memoised page trees so the next walk asks the routers again."""
    _page_roots.cache_clear()
    with _MEMO_WRITE_LOCK:
        _version.value += 1


def _on_setting_changed(*, setting: str, **kwargs) -> None:
    """Drop the memoised page trees when the app list behind them moves.

    `settings_reloaded` covers only the `NEXT_FRAMEWORK` half, and the trees
    of a router with `APP_DIRS` move with `INSTALLED_APPS`.
    """
    if setting == "INSTALLED_APPS":
        forget_page_roots()


settings_reloaded.connect(forget_page_roots)
setting_changed.connect(_on_setting_changed)


_TREE_DEPTHS: PageCache[Path, tuple[tuple[Path, ...], int]] = PageCache()


def page_tree_depth(start_dir: Path) -> int:
    """Return the number of directories from `start_dir` up to its page tree root.

    A directory outside every tree returns the walk cap. The result is memoised per set
    of trees.
    """
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
    depths.append(MAX_ANCESTOR_WALK_DEPTH)
    depth = min(depths)
    _TREE_DEPTHS[start_dir] = (roots, depth)
    return depth


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

    `None` tells an absent or broken module apart from one that declares none of the
    names, and anything but a list of non-empty strings reads as empty.
    """
    module = _load_python_module_memo(file_path)
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
        module = _load_python_module_memo(file_path)
        return module is not None and hasattr(module, "template")

    @override
    def load_template(self, file_path: Path) -> str | None:
        """Return `module.template` if the module exposes it."""
        module = _load_python_module_memo(file_path)
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
            return djx_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    @override
    def source_path(self, file_path: Path) -> Path | None:
        """Return the sibling `template.djx` path for stale-cache detection."""
        djx_file = file_path.parent / "template.djx"
        return djx_file if djx_file.exists() else None


def _as_placeholder_fallback(body: str) -> str:
    """Return `body` as the fallback of a paired placeholder.

    Only a chain rendered without its ancestor layout ever shows the fallback.
    """
    return f"{PLACEHOLDER_OPEN}{body}{PLACEHOLDER_CLOSE}"


class LayoutTemplateLoader:
    """Compose nested `layout.djx` wrappers around the page template.

    It is not a `TemplateLoader`, because the loader chain supplies a body and this
    class wraps it.
    """

    def can_load(self, file_path: Path) -> bool:
        """Return whether at least one `layout.djx` exists on the path."""
        return bool(self._find_layout_files(file_path))

    def compose_skeleton(self, file_path: Path) -> str:
        """Return the layout chain for `file_path` with a slot where the body goes.

        The chain depends on the path alone, so a caller with a body that
        changes per request caches this and fills the slot per request.
        """
        return self.compose_body(_BODY_SLOT, file_path)

    def fill_skeleton(self, skeleton: str, body: str) -> str:
        """Return `skeleton` with `body` substituted into its body slot."""
        return skeleton.replace(_BODY_SLOT, body)

    def compose_body(self, body: str, file_path: Path) -> str:
        """Wrap `body` through the ancestor layout chain for `file_path`.

        A sibling `layout.djx` substitutes `body` directly, otherwise `body` becomes
        the fallback of a paired placeholder the ancestor layout renders in its place.
        """
        layout_files = self._find_layout_files(file_path)
        if not layout_files:
            return body

        sibling_layout = (file_path.parent / "layout.djx").exists()
        wrapped = body if sibling_layout else _as_placeholder_fallback(body)
        return self._compose_layout_hierarchy(wrapped, layout_files)

    def layout_sources(self, file_path: Path) -> tuple[list[Path], list[Path]]:
        """Return the layout files behind `file_path` and the directories watched.

        A `layout.djx` appearing or disappearing moves the mtime of its directory and of
        no tracked file, so a caller detecting change needs the directories too.
        """
        return self._walk_ancestors(file_path, page_tree_depth(file_path.parent))

    def _walk_ancestors(
        self, file_path: Path, watched_depth: int
    ) -> tuple[list[Path], list[Path]]:
        """Climb the ancestors of `file_path` for layouts and watched directories."""
        layout_files: list[Path] = []
        watched_dirs: list[Path] = []
        current_dir = file_path.parent

        for depth in range(MAX_ANCESTOR_WALK_DEPTH):
            if current_dir == current_dir.parent:
                break
            if depth < watched_depth:
                watched_dirs.append(current_dir)
            layout_file = current_dir / "layout.djx"
            if layout_file.exists():
                layout_files.append(layout_file)
            current_dir = current_dir.parent

        for additional_layout in self._get_additional_layout_files():
            if additional_layout not in layout_files:
                layout_files.append(additional_layout)

        return layout_files, watched_dirs

    def _find_layout_files(self, file_path: Path) -> list[Path]:
        """Return `layout.djx` paths from near to far plus global layouts.

        The walk skips the watched directories, which cost a `resolve` of the page trees
        and which no caller of this method reads.
        """
        layout_files, _ = self._walk_ancestors(file_path, 0)
        return layout_files

    def _get_additional_layout_files(self) -> Sequence[Path]:
        """Return the memoised root-level `layout.djx` files of every page backend."""
        return _additional_layout_files()

    def _get_pages_dirs_for_config(self, config: dict) -> list[Path]:
        """Return candidate roots from one router `DIRS` entry (paths only)."""
        return _pages_dirs_for_config(config)

    def _compose_layout_hierarchy(
        self, template_content: str, layout_files: list[Path]
    ) -> str:
        """Wrap the content in each layout from the nearest to the outermost.

        Each layout receives the content in its first placeholder, and `next.W078`
        reports a layout that carries more than one.
        """
        result = template_content

        for layout_file in layout_files:
            with contextlib.suppress(OSError, UnicodeDecodeError):
                layout_content = layout_file.read_text(encoding="utf-8")
                spans = placeholder_spans(layout_content)
                if spans:
                    start, end = spans[0]
                    # Sliced, since a substitution would interpret escapes in the body.
                    result = layout_content[:start] + result + layout_content[end:]
        return result


@functools.cache
def build_registered_loaders() -> Sequence[TemplateLoader]:
    """Instantiate `TEMPLATE_LOADERS` dotted paths into `TemplateLoader` instances.

    Bad entries are skipped with a debug log rather than raised, since
    `check_template_loaders` already reports the same misconfiguration to the user.
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


def _reset_registered_loaders_cache(**kwargs) -> None:
    """Drop cached loader instances on settings reload."""
    build_registered_loaders.cache_clear()


settings_reloaded.connect(_reset_registered_loaders_cache)
