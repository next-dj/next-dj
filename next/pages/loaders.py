"""Template-text loaders and the layout composition engine.

`TemplateLoader` is the abstract contract. The page manager consults
`module.template` directly and then iterates the loader chain built
from `NEXT_FRAMEWORK["TEMPLATE_LOADERS"]`. The default chain contains
only `DjxTemplateLoader`. `PythonTemplateLoader` is kept for projects
that register it explicitly or rely on the legacy capability-detection
code path. The manager does not call it by default.

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
from typing import TYPE_CHECKING, ClassVar

from next.conf import next_framework_settings
from next.conf.imports import import_class_cached
from next.conf.signals import settings_reloaded
from next.utils import classify_dirs_entries, resolve_base_dir


if TYPE_CHECKING:
    import types
    from pathlib import Path


logger = logging.getLogger(__name__)


_MAX_ANCESTOR_WALK_DEPTH = 64


def _load_python_module(file_path: Path) -> types.ModuleType | None:
    """Load `file_path` as a module or return `None` on failure."""
    try:
        spec = importlib.util.spec_from_file_location("page_module", file_path)
        if not spec or not spec.loader:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    except (ImportError, AttributeError, OSError, SyntaxError) as e:
        logger.debug("Could not load module %s: %s", file_path, e)
        return None
    else:
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


_ADDITIONAL_LAYOUTS_CACHE: list[Path] | None = None


def _reset_additional_layouts_cache(**_kwargs: object) -> None:
    """Drop cached root-level `layout.djx` paths on settings reload."""
    global _ADDITIONAL_LAYOUTS_CACHE  # noqa: PLW0603
    _ADDITIONAL_LAYOUTS_CACHE = None


settings_reloaded.connect(_reset_additional_layouts_cache)


def _read_string_list(module: types.ModuleType, attr: str) -> list[str]:
    """Return a module-level string-sequence attribute or an empty list."""
    value = getattr(module, attr, None)
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if isinstance(item, str) and item]


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
        _ = file_path
        return None


class PythonTemplateLoader(TemplateLoader):
    """Load from `page.py` when the module defines a `template` attribute."""

    source_name: ClassVar[str] = "template"

    def can_load(self, file_path: Path) -> bool:
        """Return whether the module loads and defines `template`."""
        module = _load_python_module_memo(file_path)
        return module is not None and hasattr(module, "template")

    def load_template(self, file_path: Path) -> str | None:
        """Return `module.template` if the module exposes it."""
        module = _load_python_module_memo(file_path)
        return getattr(module, "template", None) if module else None


class DjxTemplateLoader(TemplateLoader):
    """Load from a sibling `template.djx` next to `page.py`."""

    source_name: ClassVar[str] = "template.djx"

    def can_load(self, file_path: Path) -> bool:
        """Return whether sibling `template.djx` exists."""
        return (file_path.parent / "template.djx").exists()

    def load_template(self, file_path: Path) -> str | None:
        """Return the file contents of `template.djx`."""
        djx_file = file_path.parent / "template.djx"
        try:
            return djx_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def source_path(self, file_path: Path) -> Path | None:
        """Return the sibling `template.djx` path for stale-cache detection."""
        djx_file = file_path.parent / "template.djx"
        return djx_file if djx_file.exists() else None


class LayoutTemplateLoader(TemplateLoader):
    """Compose nested `layout.djx` wrappers around the page template."""

    def can_load(self, file_path: Path) -> bool:
        """Return whether at least one `layout.djx` exists on the path."""
        return self._find_layout_files(file_path) is not None

    def load_template(self, file_path: Path) -> str | None:
        """Return the composed template with the page inside the innermost slot."""
        layout_files = self._find_layout_files(file_path)
        if not layout_files:
            return None

        template_content = self._wrap_in_template_block(file_path)
        return self._compose_layout_hierarchy(template_content, layout_files)

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

    def _find_layout_files(self, file_path: Path) -> list[Path] | None:
        """Return `layout.djx` paths from near to far plus global layouts."""
        layout_files = []
        current_dir = file_path.parent

        for _ in range(_MAX_ANCESTOR_WALK_DEPTH):
            if current_dir == current_dir.parent:
                break
            layout_file = current_dir / "layout.djx"
            if layout_file.exists():
                layout_files.append(layout_file)
            current_dir = current_dir.parent

        if additional_layouts := self._get_additional_layout_files():
            for additional_layout in additional_layouts:
                if additional_layout not in layout_files:
                    layout_files.append(additional_layout)

        return layout_files or None

    def _get_additional_layout_files(self) -> list[Path]:
        """Return root-level `layout.djx` files from each page backend `DIRS`."""
        global _ADDITIONAL_LAYOUTS_CACHE  # noqa: PLW0603
        if _ADDITIONAL_LAYOUTS_CACHE is not None:
            return _ADDITIONAL_LAYOUTS_CACHE
        configs = next_framework_settings.DEFAULT_PAGE_BACKENDS or []
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
        _ADDITIONAL_LAYOUTS_CACHE = result
        return result

    def _get_pages_dirs_for_config(self, config: dict) -> list[Path]:
        """Return candidate roots from one router `DIRS` entry (paths only)."""
        path_roots, _ = classify_dirs_entries(
            list(config.get("DIRS") or []),
            resolve_base_dir(),
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
        self,
        template_content: str,
        layout_files: list[Path],
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


class LayoutManager:
    """Cache composed layout strings per page path."""

    def __init__(self) -> None:
        """Initialise an empty layout cache."""
        self._layout_registry: dict[Path, str] = {}
        self._layout_loader = LayoutTemplateLoader()

    def discover_layouts_for_template(self, template_path: Path) -> str | None:
        """Compose and store layout text when `LayoutTemplateLoader` applies."""
        if not self._layout_loader.can_load(template_path):
            return None

        composed_template = self._layout_loader.load_template(template_path)
        if composed_template:
            self._layout_registry[template_path] = composed_template

        return composed_template

    def get_layout_template(self, template_path: Path) -> str | None:
        """Return the cached composed template for `template_path`."""
        return self._layout_registry.get(template_path)

    def clear_registry(self) -> None:
        """Drop all cached layout strings."""
        self._layout_registry.clear()


_REGISTERED_LOADERS_CACHE: list[TemplateLoader] | None = None


def build_registered_loaders() -> list[TemplateLoader]:
    """Instantiate `TEMPLATE_LOADERS` dotted paths into `TemplateLoader` instances.

    Entries that cannot be imported or are not `TemplateLoader` subclasses
    are skipped with a debug-level log. `check_template_loaders` is the
    user-visible report for the same misconfigurations. The result is
    memoised and reset on `settings_reloaded`.
    """
    global _REGISTERED_LOADERS_CACHE  # noqa: PLW0603
    if _REGISTERED_LOADERS_CACHE is not None:
        return _REGISTERED_LOADERS_CACHE

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
                "TEMPLATE_LOADERS entry %r is not a TemplateLoader subclass",
                entry,
            )
            continue
        if cls in seen:
            logger.debug("Skipping duplicate TEMPLATE_LOADERS entry: %r", entry)
            continue
        seen.add(cls)
        instances.append(cls())

    _REGISTERED_LOADERS_CACHE = instances
    return _REGISTERED_LOADERS_CACHE


def _reset_registered_loaders_cache(**_kwargs: object) -> None:
    """Drop cached loader instances on settings reload."""
    global _REGISTERED_LOADERS_CACHE  # noqa: PLW0603
    _REGISTERED_LOADERS_CACHE = None


settings_reloaded.connect(_reset_registered_loaders_cache)
