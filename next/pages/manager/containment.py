"""How a page contains a failure of its callables in the boundary that holds it."""

from __future__ import annotations

from typing import TYPE_CHECKING

from next.boundaries.protection import aguarded, guarded, protect
from next.pages.boundaries import Caught, catching
from next.pages.graph import blaming


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from pathlib import Path

    from next.pages.graph import Blame
    from next.pages.loaders import BoundaryChain
    from next.pages.registry import PageContextRegistry


class PageContainment:
    """Run a part of a page render inside the page boundaries of its chain."""

    _context_manager: PageContextRegistry

    def _failed_level(self, file_path: Path, blamed: Blame) -> int:
        """Return how many directories up the failed callable sits, `-1` for none."""
        return blamed.level(self._context_manager.inherited_levels(file_path))

    def _caught(
        self, exc: Exception, chain: BoundaryChain, file_path: Path, blamed: Blame
    ) -> Caught | None:
        """Return the boundary of `chain` that holds `exc`, `None` for none."""
        guard = catching(chain, exc, self._failed_level(file_path, blamed))
        return None if guard is None else Caught(guard, exc)

    def _contained[T](
        self,
        chain: BoundaryChain,
        file_path: Path,
        blamed: Blame,
        work: Callable[[], T],
    ) -> T | Caught:
        """Return what `work` returns, or the boundary of `chain` holding its failure.

        The protection undoes what the failed part wrote, as the template boundaries do.
        """
        return guarded(protect(), work, self._caught, chain, file_path, blamed)

    async def _acontained[T](
        self, chain: BoundaryChain, file_path: Path, blamed: Blame, work: Awaitable[T]
    ) -> T | Caught:
        """Await `work` as `_contained` runs it, the savepoint on the request thread."""
        return await aguarded(work, self._caught, chain, file_path, blamed)

    async def acontain[T](
        self, chain: BoundaryChain, file_path: Path, work: Awaitable[T]
    ) -> T | Caught:
        """Await `work`, or return the boundary of `chain` that contains its failure."""
        with blaming() as blamed:
            return await self._acontained(chain, file_path, blamed, work)


__all__ = ["PageContainment"]
