"""Router access implementation bound into the `next.ports` slot at app startup.

The urls area routes to pages and so imports `next.pages`. The watcher and the system
checks read this port instead, which keeps them out of that loop.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from next.ports import RouterAccess

from .backends import FileRouterBackend, RouterFactory
from .manager import RouterManager, router_manager
from .parser import default_url_parser
from .routes import collect_routes


if TYPE_CHECKING:
    from pathlib import Path

    from .backends import RouterBackend
    from .parser import URLPatternParser


class RouterAccessImpl(RouterAccess):
    """Builds routers and router managers straight from the urls area."""

    @override
    def create_backend(self, config: dict[str, Any]) -> RouterBackend:
        """Return the router one `PAGE_BACKENDS` entry names."""
        return RouterFactory.create_backend(config)

    @override
    def create_manager(self) -> RouterManager:
        """Return a fresh manager over every configured router."""
        return RouterManager()

    @override
    def url_parser(self) -> URLPatternParser:
        """Return the parser the file router routes bracket segments through."""
        return default_url_parser

    @override
    def page_routes(self) -> tuple[tuple[Path, str], ...]:
        """Return the tree root and the absolute route of every routed page."""
        roots = [
            root.path
            for router in router_manager.backends
            if isinstance(router, FileRouterBackend)
            for root in router.page_roots()
        ]
        return tuple(
            (max(trees, key=lambda tree: len(tree.parts)), route.url)
            for route in collect_routes()
            if (trees := [tree for tree in roots if route.file.is_relative_to(tree)])
        )


__all__ = ["RouterAccessImpl"]
