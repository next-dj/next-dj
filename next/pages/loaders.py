"""Template-text loaders and the layout composition engine.

`module.template` is consulted before the `TEMPLATE_LOADERS` chain runs.
"""

from __future__ import annotations

import contextlib
import functools
import importlib.util
import logging
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, override

from django.core.signals import setting_changed

from next.caches import BoundedCache
from next.conf import next_framework_settings
from next.conf.imports import import_class_cached
from next.conf.signals import settings_reloaded
from next.pages.errors import PageModuleImportError
from next.utils import (
    MAX_ANCESTOR_WALK_DEPTH,
    classify_dirs_entries,
    resolve_base_dir,
    stat_mtime_ns,
)

from .placeholder import PLACEHOLDER_CLOSE, PLACEHOLDER_OPEN, placeholder_spans
from .watch import get_pages_directories_for_watch


if TYPE_CHECKING:
    import types
    from collections.abc import Iterable, Sequence
    from pathlib import Path


logger = logging.getLogger(__name__)


# A token no real template source carries, so refilling the slot is unambiguous.
_BODY_SLOT = "\x00next-page-body\x00"


@dataclass(frozen=True, slots=True)
class _PageLoad:
    """One execution of a `page.py`, pinned to the nanosecond mtime it ran against.

    The module and the failure share one entry, so no racing load can pair them apart.
    """

    mtime_ns: int
    module: types.ModuleType | None
    error: Exception | None


_MODULE_MEMO: BoundedCache[Path, _PageLoad] = BoundedCache()
_FAILED_PATHS: set[Path] = set()
_MEMO_WRITE_LOCK = threading.Lock()


@dataclass(slots=True)
class _Generation:
    """A counter every module memo write and every page tree reload moves."""

    value: int = 0


_GENERATION = _Generation()


def module_generation() -> int:
    """Return the generation of the module memo, moved by every write and tree reload.

    A chain memo keys off it, so it never has to stat an ancestor to learn of a reload.
    """
    return _GENERATION.value


def has_load_errors() -> bool:
    """Whether the latest load of any `page.py` failed.

    Asked first by the per-request fail-loud probe, so a healthy site pays no `stat`.
    """
    return bool(_FAILED_PATHS)


def last_load_error(file_path: Path) -> PageModuleImportError | None:
    """Return the import failure of the `page.py` at `file_path`, loading it when stale.

    Wrapped afresh per call, since a re-raised instance grows its traceback per request.
    """
    return load_page_module(file_path)[1]


def load_page_module(
    file_path: Path,
) -> tuple[types.ModuleType | None, PageModuleImportError | None]:
    """Return the module now at `file_path` and its import failure from one load.

    A guard reading both in two calls could pair a failure with a later fixed module.
    """
    load = _page_load(file_path)
    if load is None or load.error is None:
        return (None if load is None else load.module), None
    error = PageModuleImportError(file_path)
    error.__cause__ = load.error
    return None, error


def _load_python_module(file_path: Path) -> types.ModuleType | None:
    """Execute `file_path` as a fresh module, raising whatever its body raises.

    `None` means importlib knows no loader for the suffix of `file_path`.
    """
    spec = importlib.util.spec_from_file_location("page_module", file_path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _remember(file_path: Path, load: _PageLoad) -> None:
    """Store `load` and keep the failure index in step with it.

    Ordered so a lock-free read never finds a memoised failure missing from the index.
    """
    with _MEMO_WRITE_LOCK:
        if load.error is None:
            _MODULE_MEMO[file_path] = load
            _FAILED_PATHS.discard(file_path)
        else:
            _FAILED_PATHS.add(file_path)
            _MODULE_MEMO[file_path] = load
        _GENERATION.value += 1


def _forget(file_path: Path) -> None:
    """Drop the entry of `file_path` and its place in the failure index together."""
    with _MEMO_WRITE_LOCK:
        _MODULE_MEMO.pop(file_path)
        _FAILED_PATHS.discard(file_path)
        _GENERATION.value += 1


def _page_load(file_path: Path) -> _PageLoad | None:
    """Return the load of `file_path` for its current mtime, executing it on a miss.

    `None` stands for a file that does not stat, like the `page.py` of a template page.
    """
    mtime_ns = stat_mtime_ns(file_path)
    if mtime_ns is None:
        if file_path in _MODULE_MEMO or file_path in _FAILED_PATHS:
            _forget(file_path)
        return None

    cached = _MODULE_MEMO.get(file_path)
    if cached is not None and cached.mtime_ns == mtime_ns:
        # Left where it sits, because this answers up to three times per URL
        # dispatch and the bound is there to cap memory, not to rank pages.
        return cached

    try:
        module = _load_python_module(file_path)
    except Exception as exc:
        if stat_mtime_ns(file_path) is None:
            # Removed between the stat and the exec, so absent rather than broken.
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

    A rewrite landing on the same mtime tick would otherwise return the stale module.
    """
    with _MEMO_WRITE_LOCK:
        _MODULE_MEMO.clear()
        _FAILED_PATHS.clear()
        _GENERATION.value += 1


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

    Reading them probes the trees of every router, too much work per walk.
    """
    return tuple(get_pages_directories_for_watch())


def forget_page_roots(**kwargs) -> None:
    """Drop the memoised page trees so the next walk asks the routers again."""
    _page_roots.cache_clear()
    with _MEMO_WRITE_LOCK:
        _GENERATION.value += 1


def _on_setting_changed(*, setting: str, **kwargs) -> None:
    """Drop the memoised page trees when the app list behind them moves.

    `settings_reloaded` covers only the `NEXT_FRAMEWORK` half, and the trees
    of a router with `APP_DIRS` move with `INSTALLED_APPS`.
    """
    if setting == "INSTALLED_APPS":
        forget_page_roots()


settings_reloaded.connect(forget_page_roots)
setting_changed.connect(_on_setting_changed)


_TREE_DEPTHS: BoundedCache[Path, tuple[tuple[Path, ...], int]] = BoundedCache()


def page_tree_depth(start_dir: Path) -> int:
    """Return the number of directories from `start_dir` up to its page tree root.

    A directory outside every tree answers the walk cap, memoised per set of trees.
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

    No `TemplateLoader`, because the chain supplies a body and this wraps one.
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

        The watched directories cost a `resolve` of the page trees, and no
        caller down this path reads them, so the walk skips them.
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
        """Return layouts wrapped outermost last, with the page in the first slot.

        A layout carrying more than one placeholder is what `next.W078` reports.
        """
        result = template_content

        for layout_file in layout_files:
            with contextlib.suppress(OSError, UnicodeDecodeError):
                layout_content = layout_file.read_text(encoding="utf-8")
                spans = placeholder_spans(layout_content)
                if spans:
                    start, end = spans[0]
                    # Sliced in, because a substitution would read escapes in the body.
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
