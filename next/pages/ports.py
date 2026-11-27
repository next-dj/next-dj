"""Page-scan implementation bound into the `next.ports` slot at app startup.

The scan reads the route manager from `next.discovery`, which calls it via the port.
"""

from typing import TYPE_CHECKING, override

from next.ports import PageScan

from .scan import load_scanned_page_modules


if TYPE_CHECKING:
    from pathlib import Path

    from next.urls import RouteManager


class PageScanImpl(PageScan):
    """Binds the port to the page-tree scan of the pages area."""

    @override
    def load_scanned_page_modules(
        self, route_manager: "RouteManager"
    ) -> "list[tuple[str, Path]]":
        """Execute every routed `page.py` and return the ones that loaded."""
        return load_scanned_page_modules(route_manager)


__all__ = ["PageScanImpl"]
