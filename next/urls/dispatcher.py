"""Walk filesystem page trees once to emit routes and register components.

`FilesystemTreeDispatcher` runs `walk_page_tree` once per root.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from next.components import register_components_folder_from_router_walk
from next.utils import walk_page_tree


if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable
    from pathlib import Path

    from next.utils import InterceptPage


class FilesystemTreeDispatcher:
    """Run one depth-first walk that yields routes and skips component folders."""

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

    def walk(
        self,
        pages_path: Path,
        *,
        on_intercept: Callable[[InterceptPage], None] | None = None,
    ) -> Generator[tuple[str, Path], None, None]:
        """Yield `(url_path, page_file)`, `url_path` the group-free route trail."""
        on_skipped = self._register_folder if self._register_components else None
        yield from walk_page_tree(
            pages_path,
            self._skip_set,
            on_skipped_dir=on_skipped,
            on_intercept=on_intercept,
        )

    def _register_folder(self, folder: Path, tree_root: Path, scope_trail: str) -> None:
        """Register a skipped components folder under its file-system trail."""
        if folder.name == self._components_folder_name:
            register_components_folder_from_router_walk(folder, tree_root, scope_trail)


def scan_pages_tree(
    pages_path: Path,
    skip_dir_names: Iterable[str] = (),
    *,
    components_folder_name: str = "_components",
    register_components: bool = False,
    on_intercept: Callable[[InterceptPage], None] | None = None,
) -> Generator[tuple[str, Path], None, None]:
    """Walk a tree for `page.py` (and virtual pages) without a router instance."""
    dispatcher = FilesystemTreeDispatcher(
        skip_dir_names,
        components_folder_name=components_folder_name,
        register_components=register_components,
    )
    yield from dispatcher.walk(pages_path, on_intercept=on_intercept)


__all__ = ["FilesystemTreeDispatcher", "scan_pages_tree"]
