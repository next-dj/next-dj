"""Router access implementation bound into the `next.ports` slot at app startup.

The urls area routes to pages and so imports `next.pages`. The watcher and the system
checks read this port instead, which keeps them out of that loop.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from next.ports import RouterAccess

from .backends import RouterFactory
from .manager import RouterManager
from .parser import default_url_parser


if TYPE_CHECKING:
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


__all__ = ["RouterAccessImpl"]
