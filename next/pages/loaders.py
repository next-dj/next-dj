"""Template-text loaders and the layout composition engine.

`TemplateLoader` is the abstract contract. `PythonTemplateLoader` reads
from a `template` module attribute, `DjxTemplateLoader` reads from a
sibling `template.djx`, and `LayoutTemplateLoader` composes outer
`layout.djx` wrappers up the directory chain. `LayoutManager` caches
composed layout strings per page path.
"""

from __future__ import annotations

import contextlib
import importlib.util
import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from next.conf import next_framework_settings
from next.utils import classify_dirs_entries, resolve_base_dir


if TYPE_CHECKING:
    import types
    from pathlib import Path


logger = logging.getLogger(__name__)


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


def _read_string_list(module: types.ModuleType, attr: str) -> list[str]:
    """Return a module-level string-sequence attribute or an empty list."""
    value = getattr(module, attr, None)
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item) for item in value if isinstance(item, str) and item]


class TemplateLoader(ABC):
    """Pluggable source of template text for a `page.py` path."""

    @abstractmethod
    def can_load(self, file_path: Path) -> bool:
        """Return whether this loader applies without heavy work."""

    @abstractmethod
    def load_template(self, file_path: Path) -> str | None:
        """Return the template source, or `None` if unavailable."""


class PythonTemplateLoader(TemplateLoader):
    """Load from `page.py` when the module defines a `template` attribute."""

    def can_load(self, file_path: Path) -> bool:
        """Return whether the module loads and defines `template`."""
        module = _load_python_module(file_path)
        return module is not None and hasattr(module, "template")

    def load_template(self, file_path: Path) -> str | None:
        """Return `module.template` if the module exposes it."""
        module = _load_python_module(file_path)
        return getattr(module, "template", None) if module else None


class DjxTemplateLoader(TemplateLoader):
    """Load from a sibling `template.djx` next to `page.py`."""

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

    def _find_layout_files(self, file_path: Path) -> list[Path] | None:
        """Return `layout.djx` paths from near to far plus global layouts."""
        layout_files = []
        current_dir = file_path.parent

        while current_dir != current_dir.parent:
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
        return list(dict.fromkeys(candidates))

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
