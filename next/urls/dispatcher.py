"""Walk filesystem page trees once to emit routes and register components.

`FilesystemTreeDispatcher` performs a single depth-first walk per
page-tree root. It yields `(url_path, page_file)` pairs for every
discovered `page.py` (plus virtual `template.djx`-only pages), and
registers `_components` folders it encounters along the way.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import Generator, Iterable
    from pathlib import Path


logger = logging.getLogger(__name__)


class FilesystemTreeDispatcher:
    """Run one depth-first walk: routes per node or skip component folders."""

    def __init__(
        self,
        skip_dir_names: Iterable[str],
        *,
        components_folder_name: str,
        register_components: bool,
    ) -> None:
        """Remember which dirs to skip and whether to register component roots."""
        self._skip_set = frozenset(skip_dir_names)
        self._components_folder_name = components_folder_name
        self._register_components = register_components

    def walk(self, pages_path: Path) -> Generator[tuple[str, Path], None, None]:
        """Yield `(url_path, page_file)`, where `url_path` is the route trail."""
        yield from self._visit(pages_path, pages_path, "")

    def _visit(
        self,
        current_path: Path,
        tree_root: Path,
        url_path: str,
    ) -> Generator[tuple[str, Path], None, None]:
        try:
            items = list(current_path.iterdir())
        except OSError as e:
            logger.debug("Cannot list directory %s: %s", current_path, e)
            return
        for item in items:
            if item.is_dir():
                if item.name in self._skip_set:
                    if (
                        self._register_components
                        and item.name == self._components_folder_name
                    ):
                        _register_components_folder(item, tree_root, url_path)
                    continue
                dir_name = item.name
                new_url_path = f"{url_path}/{dir_name}" if url_path else dir_name
                yield from self._visit(item, tree_root, new_url_path)
            elif item.name == "page.py":
                yield url_path, item

        if current_path.is_dir():
            page_file = current_path / "page.py"
            template_file = current_path / "template.djx"
            if not page_file.exists() and template_file.exists():
                yield url_path, current_path / "page.py"


def scan_pages_tree(
    pages_path: Path,
    skip_dir_names: Iterable[str] = (),
    *,
    components_folder_name: str = "_components",
    register_components: bool = False,
) -> Generator[tuple[str, Path], None, None]:
    """Walk a tree for `page.py` (and virtual pages) without a router instance."""
    dispatcher = FilesystemTreeDispatcher(
        skip_dir_names,
        components_folder_name=components_folder_name,
        register_components=register_components,
    )
    yield from dispatcher.walk(pages_path)


def _scan_pages_directory(
    pages_path: Path,
    skip_dir_names: Iterable[str] = (),
    *,
    components_folder_name: str = "_components",
    register_components: bool = False,
) -> Generator[tuple[str, Path], None, None]:
    """Yield the same pairs as `scan_pages_tree`."""
    yield from scan_pages_tree(
        pages_path,
        skip_dir_names,
        components_folder_name=components_folder_name,
        register_components=register_components,
    )


def _register_components_folder(
    folder: Path,
    pages_root: Path,
    scope_relative: str,
) -> None:
    """Register one `_components` folder found during the file-router page walk."""
    import next.components as components_mod  # noqa: PLC0415

    components_mod.register_components_folder_from_router_walk(
        folder,
        pages_root,
        scope_relative,
    )


__all__ = ["FilesystemTreeDispatcher", "scan_pages_tree"]
