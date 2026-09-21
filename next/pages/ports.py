"""Page-scan implementation bound into the `next.ports` slot at app startup.

The scan reads the router manager from `next.discovery`, so discovery reaches it
back through the port instead of through an import that would close the cycle.
"""

from typing import TYPE_CHECKING, override

from next.ports import PageScan

from .scan import load_scanned_page_modules


if TYPE_CHECKING:
    from pathlib import Path

    from next.urls import RouterManager


class PageScanImpl(PageScan):
    """Binds the port to the page-tree scan of the pages area."""

    @override
    def load_scanned_page_modules(
        self, router_manager: "RouterManager"
    ) -> "list[tuple[str, Path]]":
        """Execute every routed `page.py`, answering the ones that loaded."""
        return load_scanned_page_modules(router_manager)


__all__ = ["PageScanImpl"]
