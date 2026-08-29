"""Per-page path facts memoised for the render path.

Nothing here depends on a request, so a value is read once per page path
and dropped by the lifecycle that rebuilds a composition.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from pathlib import Path


# The bound every ancestor walk shares, so none reaches the filesystem root.
_MAX_ANCESTOR_WALK_DEPTH = 64


@dataclass(frozen=True, slots=True)
class PagePathInfo:
    """The path facts of one `page.py` that no request can change.

    `template_path` falls back to the page itself where no sibling
    `template.djx` exists, and `ancestors` runs from the page outwards.
    """

    module_path: str
    template_path: str
    ancestors: tuple[Path, ...]


_PAGE_PATH_INFO_CACHE: dict[Path, PagePathInfo] = {}


def page_path_info(file_path: Path) -> PagePathInfo:
    """Return the memoised facts of `file_path`, touching the disk on a miss."""
    info = _PAGE_PATH_INFO_CACHE.get(file_path)
    if info is None:
        info = _build_page_path_info(file_path)
        _PAGE_PATH_INFO_CACHE[file_path] = info
    return info


def _build_page_path_info(file_path: Path) -> PagePathInfo:
    """Read every path fact of `file_path` in one pass over the disk."""
    ancestors: list[Path] = []
    current_dir = file_path.parent
    for _ in range(_MAX_ANCESTOR_WALK_DEPTH):
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
