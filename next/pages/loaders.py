"""Template-text loaders and the layout composition engine.

`TemplateLoader` is the abstract contract. The page manager consults
`module.template` directly and then iterates the loader chain built
from `NEXT_FRAMEWORK["TEMPLATE_LOADERS"]`. The default chain contains
only `DjxTemplateLoader`. `PythonTemplateLoader` is available for
projects that register it explicitly. Registering it changes nothing
at render time and only affects how the `next.W043` conflict check
reports the body source. The manager does not call it by default.

`DjxTemplateLoader` reads a sibling `template.djx`.
`LayoutTemplateLoader` composes outer `layout.djx` wrappers up the
directory chain. It is not registered through `TEMPLATE_LOADERS`.
Layouts have their own dedicated path.
"""

from __future__ import annotations

import contextlib
import importlib.util
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, ClassVar, override

from django.core.signals import setting_changed

from next.conf import next_framework_settings
from next.conf.imports import import_class_cached
from next.conf.signals import settings_reloaded
from next.utils import classify_dirs_entries, resolve_base_dir

from .paths import _MAX_ANCESTOR_WALK_DEPTH
from .watch import get_pages_directories_for_watch


if TYPE_CHECKING:
    import types
    from collections.abc import Iterable
    from pathlib import Path


logger = logging.getLogger(__name__)


# A token no real template source carries, so refilling the slot is unambiguous.
_BODY_SLOT = "\x00next-page-body\x00"


class PageModuleImportError(Exception):
    """A `page.py` body raised while importing.

    Covers any exception raised by the module body. ImportError,
    SyntaxError, and AttributeError are common examples, not a closed
    list. The original exception travels as `__cause__` and the
    offending path as `file_path`.
    """

    def __init__(self, file_path: Path) -> None:
        """Compose the message from the failing path."""
        super().__init__(f"{file_path} failed to import")
        self.file_path = file_path


_LAST_LOAD_ERROR: dict[Path, tuple[float, Exception]] = {}


def has_load_errors() -> bool:
    """Whether any `page.py` import failure is on record.

    The per-request fail-loud probe asks this first, so a healthy deployment
    pays one dict read instead of a `stat` per request.
    """
    return bool(_LAST_LOAD_ERROR)


def _record_load_error(file_path: Path, exc: Exception, mtime: float | None) -> None:
    """Remember the import failure keyed by the mtime taken before exec.

    A `None` mtime means the file vanished before executing, so any
    stale record is dropped instead of binding the failure to a file
    that no longer exists.
    """
    if mtime is None:
        _LAST_LOAD_ERROR.pop(file_path, None)
        return
    _LAST_LOAD_ERROR[file_path] = (mtime, exc)


def last_load_error(file_path: Path) -> PageModuleImportError | None:
    """Return the recorded import failure while `file_path` is unchanged on disk.

    A record the file has outlived is dropped here rather than left to arm
    `has_load_errors` forever. Every call wraps the stored cause in a fresh
    `PageModuleImportError`, because re-raising one shared instance would
    grow its traceback per request and pin each request's frame locals.
    """
    entry = _LAST_LOAD_ERROR.get(file_path)
    if entry is None:
        return None
    try:
        mtime = file_path.stat().st_mtime
    except OSError:
        # The file is gone, so nothing can import it again to clear this.
        _LAST_LOAD_ERROR.pop(file_path, None)
        return None
    if mtime != entry[0]:
        # A rewrite the memo has not executed yet, and the record belongs to
        # the source that failed, not to what sits there now.
        _LAST_LOAD_ERROR.pop(file_path, None)
        return None
    error = PageModuleImportError(file_path)
    error.__cause__ = entry[1]
    return error


def _load_python_module(file_path: Path) -> types.ModuleType | None:
    """Load `file_path` as a module or return `None` on failure.

    Whatever the module body raises is recorded through `_record_load_error`,
    so callers tell a broken `page.py` from an absent one via
    `last_load_error`.
    """
    try:
        spec = importlib.util.spec_from_file_location("page_module", file_path)
        if not spec or not spec.loader:
            return None
        module = importlib.util.module_from_spec(spec)
    except OSError as e:
        logger.debug("Could not load module %s: %s", file_path, e)
        return None
    # The mtime is taken before exec so a failure is recorded against the
    # file that actually executed, not a rewrite landing mid-import.
    try:
        mtime: float | None = file_path.stat().st_mtime
    except OSError:
        mtime = None
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        if mtime is None:
            # An unstatable file raises from exec too, and that is the
            # legitimately absent case, not a broken module body.
            logger.debug("Could not load module %s: %s", file_path, exc)
        else:
            logger.exception("Could not import page module %s", file_path)
        _record_load_error(file_path, exc, mtime)
        return None
    else:
        _LAST_LOAD_ERROR.pop(file_path, None)
        return module


_MODULE_MEMO: dict[Path, tuple[float, types.ModuleType | None]] = {}


def _load_python_module_memo(file_path: Path) -> types.ModuleType | None:
    """Return `_load_python_module(file_path)` memoised by mtime.

    Different call sites (`PythonTemplateLoader.can_load`, `load_template`,
    and `Page._create_regular_page_pattern`) previously executed the
    module up to three times per URL dispatch. The memo keys by mtime so
    that autoreload and template-stale detection still pick up edits.
    """
    try:
        mtime = file_path.stat().st_mtime
    except OSError:
        _MODULE_MEMO.pop(file_path, None)
        return _load_python_module(file_path)

    cached = _MODULE_MEMO.get(file_path)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    module = _load_python_module(file_path)
    _MODULE_MEMO[file_path] = (mtime, module)
    return module


def reset_module_memo() -> None:
    """Drop every memoised module so the next load re-executes from disk.

    The memo keys by mtime, so a rewrite landing on the same tick would
    otherwise return a stale module. Recorded import failures share that
    lifecycle and go with it.
    """
    # Errors go first, so a load racing this reset can at worst leave a fresh
    # memo entry behind, never a memoised failure without its recorded error.
    _LAST_LOAD_ERROR.clear()
    _MODULE_MEMO.clear()


# A single-slot holder mutated in place, so a reset needs no `global`.
_ADDITIONAL_LAYOUTS_CACHE: dict[str, list[Path] | None] = {"value": None}


def _reset_additional_layouts_cache(**kwargs) -> None:
    """Drop cached root-level `layout.djx` paths on settings reload."""
    _ADDITIONAL_LAYOUTS_CACHE["value"] = None


settings_reloaded.connect(_reset_additional_layouts_cache)


_PAGE_ROOTS_CACHE: dict[str, tuple[Path, ...] | None] = {"value": None}


def _page_roots() -> tuple[Path, ...]:
    """Return the resolved page trees the routers report, memoised.

    Reading them builds every router backend, too much work to repeat per walk.
    """
    cached = _PAGE_ROOTS_CACHE["value"]
    if cached is None:
        cached = tuple(get_pages_directories_for_watch())
        _PAGE_ROOTS_CACHE["value"] = cached
    return cached


def _reset_page_roots_cache(**kwargs) -> None:
    """Drop the memoised page trees so the next walk asks the routers again."""
    _PAGE_ROOTS_CACHE["value"] = None


def _on_setting_changed(*, setting: str, **kwargs) -> None:
    """Drop the memoised page trees when the app list behind them moves.

    `settings_reloaded` covers only the `NEXT_FRAMEWORK` half, and the trees
    of a router with `APP_DIRS` move with `INSTALLED_APPS`.
    """
    if setting == "INSTALLED_APPS":
        _reset_page_roots_cache()


settings_reloaded.connect(_reset_page_roots_cache)
setting_changed.connect(_on_setting_changed)


def _read_string_list(module: types.ModuleType, attr: str) -> list[str]:
    """Return a module-level string-sequence attribute or an empty list."""
    value = getattr(module, attr, None)
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if isinstance(item, str) and item]


def read_module_string_lists(
    file_path: Path, attrs: Iterable[str]
) -> dict[str, list[str]] | None:
    """Return the named module-level string lists a page-tree module declares.

    Answers `None` when the file does not execute as a module, which tells an
    absent or broken module apart from one that declares none of the names.
    Anything but a list or tuple of non-empty strings reads as an empty list,
    so a caller never has to type-check what a user module bound to the name.
    """
    module = _load_python_module(file_path)
    if module is None:
        return None
    return {attr: _read_string_list(module, attr) for attr in attrs}


class TemplateLoader(ABC):
    """Pluggable source of template text for a `page.py` path.

    Subclasses set `source_name` to the filename they back. Typical
    values are `"template.djx"` or `"template.md"`. The name is
    surfaced in the `next.W043` body-source conflict check.
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

        The page manager uses the result to snapshot file mtimes for
        stale-cache detection. The default returns `None` for
        non-file-based loaders. Subclasses override when they back a
        sibling file.
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


class LayoutTemplateLoader(TemplateLoader):
    """Compose nested `layout.djx` wrappers around the page template."""

    @override
    def can_load(self, file_path: Path) -> bool:
        """Return whether at least one `layout.djx` exists on the path."""
        return bool(self._find_layout_files(file_path))

    @override
    def load_template(self, file_path: Path) -> str | None:
        """Return the composed template with the page inside the innermost slot."""
        layout_files = self._find_layout_files(file_path)
        if not layout_files:
            return None

        template_content = self._wrap_in_template_block(file_path)
        return self._compose_layout_hierarchy(template_content, layout_files)

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

        Returns `body` verbatim when no layouts apply. When a sibling
        `layout.djx` exists the innermost layout owns the `{% block template %}`
        slot, so `body` is substituted as-is. Otherwise `body` is wrapped in
        a `{% block template %}` block before substitution so the ancestor
        layout's placeholder remains a valid block.
        """
        layout_files = self._find_layout_files(file_path)
        if not layout_files:
            return body

        sibling_layout = (file_path.parent / "layout.djx").exists()
        wrapped = (
            body
            if sibling_layout
            else f"{{% block template %}}{body}{{% endblock template %}}"
        )
        return self._compose_layout_hierarchy(wrapped, layout_files)

    def layout_sources(self, file_path: Path) -> tuple[list[Path], list[Path]]:
        """Return the layout files behind `file_path` and the directories watched.

        A caller detecting change needs the directories too, because a
        `layout.djx` that appears or disappears moves the mtime of its
        directory and of no tracked file.
        """
        return self._walk_ancestors(
            file_path, self._watched_ancestor_depth(file_path.parent)
        )

    def _walk_ancestors(
        self, file_path: Path, watched_depth: int
    ) -> tuple[list[Path], list[Path]]:
        """Climb the ancestors of `file_path` for layouts and watched directories."""
        layout_files: list[Path] = []
        watched_dirs: list[Path] = []
        current_dir = file_path.parent

        for depth in range(_MAX_ANCESTOR_WALK_DEPTH):
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

    def _watched_ancestor_depth(self, start_dir: Path) -> int:
        """Return how many ancestors of `start_dir` are worth watching for change.

        The walk itself climbs past the page tree, because a layout above it
        still joins the chain, but a directory up there is a shared one like
        the home directory, whose mtime moves for reasons no page shares.
        """
        resolved = start_dir.resolve()
        depths = [
            len(resolved.relative_to(root).parts) + 1
            for root in _page_roots()
            if resolved.is_relative_to(root)
        ]
        if not depths:
            return _MAX_ANCESTOR_WALK_DEPTH
        return min(*depths, _MAX_ANCESTOR_WALK_DEPTH)

    def _find_layout_files(self, file_path: Path) -> list[Path]:
        """Return `layout.djx` paths from near to far plus global layouts.

        The watched directories cost a `resolve` of the page trees, and no
        caller down this path reads them, so the walk skips them.
        """
        layout_files, _ = self._walk_ancestors(file_path, 0)
        return layout_files

    def _get_additional_layout_files(self) -> list[Path]:
        """Return root-level `layout.djx` files from each page backend `DIRS`."""
        cached = _ADDITIONAL_LAYOUTS_CACHE["value"]
        if cached is not None:
            return cached
        configs = next_framework_settings.PAGE_BACKENDS or []
        if not isinstance(configs, list):
            configs = []
        candidates = (
            layout
            for c in configs
            if isinstance(c, dict)
            for d in self._get_pages_dirs_for_config(c)
            if d.exists() and (layout := d / "layout.djx").exists()
        )
        result = list(dict.fromkeys(candidates))
        _ADDITIONAL_LAYOUTS_CACHE["value"] = result
        return result

    def _get_pages_dirs_for_config(self, config: dict) -> list[Path]:
        """Return candidate roots from one router `DIRS` entry (paths only)."""
        path_roots, _ = classify_dirs_entries(
            list(config.get("DIRS") or []), resolve_base_dir()
        )
        return list(path_roots)

    def _wrap_in_template_block(self, file_path: Path) -> str:
        """Return the page body wrapped in `{% block template %}` when needed."""
        template_file = file_path.parent / "template.djx"
        if template_file.exists():
            with contextlib.suppress(OSError, UnicodeDecodeError):
                content = template_file.read_text(encoding="utf-8")
                layout_file = file_path.parent / "layout.djx"
                if layout_file.exists():
                    return content
                return f"{{% block template %}}{content}{{% endblock template %}}"
        return "{% block template %}{% endblock template %}"

    def _compose_layout_hierarchy(
        self, template_content: str, layout_files: list[Path]
    ) -> str:
        """Return layouts wrapped outermost last, with the page in the first slot."""
        result = template_content

        for layout_file in layout_files:
            with contextlib.suppress(OSError, UnicodeDecodeError):
                layout_content = layout_file.read_text(encoding="utf-8")
                for placeholder in (
                    "{% block template %}{% endblock template %}",
                    "{% block template %}{% endblock %}",
                ):
                    if placeholder in layout_content:
                        result = layout_content.replace(placeholder, result, 1)
                        break
        return result


# A single-slot holder mutated in place, so a reset needs no `global`.
_REGISTERED_LOADERS_CACHE: dict[str, list[TemplateLoader] | None] = {"value": None}


def build_registered_loaders() -> list[TemplateLoader]:
    """Instantiate `TEMPLATE_LOADERS` dotted paths into `TemplateLoader` instances.

    Entries that cannot be imported or are not `TemplateLoader` subclasses
    are skipped with a debug-level log. `check_template_loaders` is the
    user-visible report for the same misconfigurations. The result is
    memoised and reset on `settings_reloaded`.
    """
    cached = _REGISTERED_LOADERS_CACHE["value"]
    if cached is not None:
        return cached

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

    _REGISTERED_LOADERS_CACHE["value"] = instances
    return instances


def _reset_registered_loaders_cache(**kwargs) -> None:
    """Drop cached loader instances on settings reload."""
    _REGISTERED_LOADERS_CACHE["value"] = None


settings_reloaded.connect(_reset_registered_loaders_cache)
