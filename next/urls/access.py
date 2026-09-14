"""Router builder bound into the `next.ports` slot at app startup.

The urls area routes to pages and so imports `next.pages`. The watcher and the system
checks read this port instead, which keeps them out of that loop.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .backends import RouterFactory
from .manager import RouterManager


if TYPE_CHECKING:
    from .backends import RouterBackend


class RouterAccessImpl:
    """Builds routers and router managers straight from the urls area."""

    def create_backend(self, config: dict[str, Any]) -> RouterBackend:
        """Return the router one `PAGE_BACKENDS` entry names."""
        return RouterFactory.create_backend(config)

    def create_manager(self) -> RouterManager:
        """Return a fresh manager over every configured router."""
        return RouterManager()


__all__ = ["RouterAccessImpl"]
