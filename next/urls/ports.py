"""Router access implementation bound into the `next.ports` slot at app startup.

Importing `next.pages` here would loop, so the watcher and checks read this port.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from next.ports import RouterAccess

from .backends import RouterFactory
from .manager import RouterManager
from .parser import default_url_parser
from .routes import page_route


if TYPE_CHECKING:
    from next.utils import PageRoot

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
    def page_kwargs(self, root: PageRoot, trail: str) -> frozenset[str] | None:
        """Return the kwargs a page at `trail` of `root` answers, `None` if refused."""
        route = page_route(root, trail)
        return None if route is None else route.kwargs


__all__ = ["RouterAccessImpl"]
