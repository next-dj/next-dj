import asyncio
from collections.abc import Callable
from pathlib import Path

from next.pages.graph import ContextGraph, acollect
from next.pages.registry import PageContextRegistry


ROOT = Path("/graph/page.py")
MID = Path("/graph/mid/page.py")
LEAF = Path("/graph/mid/leaf/page.py")


def run_graph(graph: ContextGraph, file_path: Path = LEAF, **kwargs):
    """Run `graph` through `acollect` on a fresh loop with empty inputs."""
    options = {
        "request": None,
        "url_kwargs": {},
        "dep_cache": {},
        "requested_zones": None,
        **kwargs,
    }
    return asyncio.run(acollect(graph, file_path, **options))


def collect(registry: PageContextRegistry, file_path: Path = LEAF, **kwargs):
    """Run `acollect_context` of `file_path` on a fresh loop."""
    return asyncio.run(registry.acollect_context(file_path, **kwargs))


def failing(message: str) -> Callable[[], str]:
    """Return a callable that raises `ValueError` with `message`."""

    def fail() -> str:
        raise ValueError(message)

    return fail


def register_two(registry: PageContextRegistry, one, two) -> None:
    """Register `one` and `two` on the leaf page under their own names."""
    registry.register_context(LEAF, "one", one)
    registry.register_context(LEAF, "two", two)


__all__ = ["LEAF", "MID", "ROOT", "collect", "failing", "register_two", "run_graph"]
