"""Per-page path facts memoised for the render path.

Nothing here depends on a request, so a value is read once per page path
and dropped by the lifecycle that rebuilds a composition.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from next.utils import MAX_ANCESTOR_WALK_DEPTH, store_capped


if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, slots=True)
class PagePathInfo:
    """The path facts of one `page.py` that no request can change.

    `template_path` falls back to the page itself where no sibling
    `template.djx` exists, and `ancestors` runs from the page outwards.
    """

    module_path: str
    template_path: str
    ancestors: tuple[Path, ...]


# Bounded because a router is free to name a page path no earlier read named, and
# each entry pins an ancestor tuple until the process ends. The bound catches
# that growth rather than working as an eviction policy, because a project holds
# far fewer pages than it allows, so the stalest insert is the one to drop and a
# hit reorders nothing.
_PAGE_PATH_INFO_CACHE_MAX_SIZE = 2048

_PAGE_PATH_INFO_CACHE: OrderedDict[Path, PagePathInfo] = OrderedDict()


def page_path_info(file_path: Path) -> PagePathInfo:
    """Return the memoised facts of `file_path`, touching the disk on a miss."""
    info = _PAGE_PATH_INFO_CACHE.get(file_path)
    if info is None:
        info = _build_page_path_info(file_path)
        store_capped(
            _PAGE_PATH_INFO_CACHE, file_path, info, _PAGE_PATH_INFO_CACHE_MAX_SIZE
        )
    return info


def _build_page_path_info(file_path: Path) -> PagePathInfo:
    """Read every path fact of `file_path` in one pass over the disk."""
    ancestors: list[Path] = []
    current_dir = file_path.parent
    for _ in range(MAX_ANCESTOR_WALK_DEPTH):
        if current_dir == current_dir.parent:
            break
        ancestors.append(current_dir / "page.py")
        current_dir = current_dir.parent
    template_djx = file_path.parent / "template.djx"
    return PagePathInfo(
        module_path=str(file_path.resolve()),
        template_path=str(template_djx) if template_djx.exists() else str(file_path),
        ancestors=tuple(ancestors),
    )


def forget_page_path_info(file_path: Path) -> None:
    """Drop the facts of one page so the next read consults the disk again."""
    _PAGE_PATH_INFO_CACHE.pop(file_path, None)


def clear_page_path_info() -> None:
    """Drop every memoised page-path fact."""
    _PAGE_PATH_INFO_CACHE.clear()
