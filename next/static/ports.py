"""Static assets implementation bound into the `next.ports` slot at app startup.

Every method reads the manager when it is called, so binding the port at startup
costs nothing until a render asks for an asset.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from next.ports import StaticAssets

from .manager import collect_component_assets, get_static_manager


if TYPE_CHECKING:
    from pathlib import Path

    from django.http import HttpRequest

    from next.components.info import ComponentInfo

    from .collector import StaticCollector


class StaticAssetsImpl(StaticAssets):
    """Binds the port to the discovery and injection entry points of the area."""

    @override
    def create_collector(self) -> StaticCollector:
        """Return a fresh sink for the assets one render references."""
        return get_static_manager().create_collector()

    @override
    def discover_page_assets(self, file_path: Path, collector: StaticCollector) -> None:
        """Collect the assets co-located with one page."""
        get_static_manager().discover_page_assets(file_path, collector)

    @override
    def collect_component_assets(
        self, info: ComponentInfo, collector: StaticCollector | None
    ) -> None:
        """Collect the assets co-located with one composite component."""
        collect_component_assets(info, collector)

    @override
    def inject(
        self,
        html: str,
        collector: StaticCollector,
        *,
        page_path: Path,
        request: HttpRequest | None,
    ) -> str:
        """Replace every placeholder token in `html` with rendered tags."""
        return get_static_manager().inject(
            html, collector, page_path=page_path, request=request
        )


__all__ = ["StaticAssetsImpl"]
