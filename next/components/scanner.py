"""Filesystem scanner that produces `ComponentInfo` from a directory.

`ComponentScanner` walks one directory and yields a `ComponentInfo`
for each `.djx` file (simple component) or sub-directory containing
`component.djx` / `component.py` (composite component).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from next.utils import classify_dirs_entries, resolve_base_dir

from .info import ComponentInfo
from .loading import ModuleLoader


if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path


logger = logging.getLogger(__name__)


class ComponentScanner:
    """Scan one folder for `.djx` files and composite component directories."""

    DEFAULT_COMPONENTS_DIR_NAME: str = "_components"

    def __init__(
        self,
        components_dir: str | None = None,
        *,
        module_loader: ModuleLoader | None = None,
    ) -> None:
        """Store the configured dir name and wire a module loader."""
        self._components_dir = (
            components_dir
            if components_dir is not None
            else self.DEFAULT_COMPONENTS_DIR_NAME
        )
        self._module_loader = module_loader or ModuleLoader()

    def scan_directory(
        self,
        directory: Path,
        scope_root: Path,
        scope_relative: str,
    ) -> Sequence[ComponentInfo]:
        """Return a list of `ComponentInfo` found immediately inside `directory`."""
        components: list[ComponentInfo] = []
        try:
            for item in directory.iterdir():
                if item.is_file() and item.suffix == ".djx":
                    comp = self._create_simple_component(
                        item, scope_root, scope_relative
                    )
                    components.append(comp)
                elif item.is_dir():
                    composite = self._try_create_composite_component(
                        item, scope_root, scope_relative
                    )
                    if composite is not None:
                        components.append(composite)
        except OSError as e:
            logger.debug("Cannot scan directory %s: %s", directory, e)
        return components

    def _create_simple_component(
        self,
        djx_file: Path,
        scope_root: Path,
        scope_relative: str,
    ) -> ComponentInfo:
        return ComponentInfo(
            name=djx_file.stem,
            scope_root=scope_root,
            scope_relative=scope_relative,
            template_path=djx_file,
            module_path=None,
            is_simple=True,
        )

    def _try_create_composite_component(
        self,
        directory: Path,
        scope_root: Path,
        scope_relative: str,
    ) -> ComponentInfo | None:
        comp_djx = directory / "component.djx"
        comp_py = directory / "component.py"
        if not comp_djx.exists() and not comp_py.exists():
            return None
        template_path: Path | None = comp_djx if comp_djx.exists() else None
        if comp_py.exists() and template_path is None:
            mod = self._module_loader.load(comp_py)
            if mod is not None and hasattr(mod, "component"):
                template_path = comp_py
        return ComponentInfo(
            name=directory.name,
            scope_root=scope_root,
            scope_relative=scope_relative,
            template_path=template_path,
            module_path=comp_py if comp_py.exists() else None,
            is_simple=False,
        )


def component_extra_roots_from_config(config: Mapping[str, Any]) -> list[Path]:
    """Return existing directory paths from the config `DIRS` entry."""
    base_dir = resolve_base_dir()
    dirs_list = list(config.get("DIRS") or [])
    path_roots, _ = classify_dirs_entries(dirs_list, base_dir)
    return [p for p in path_roots if p.exists()]


__all__ = ["ComponentScanner", "component_extra_roots_from_config"]
