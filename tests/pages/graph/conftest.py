from collections.abc import Awaitable, Callable, Iterator

import pytest

from next.deps.runner import segment_runner
from next.pages.registry import PageContextRegistry


class InlineRunner:
    """Segment runner that runs each segment on the loop thread and counts its calls."""

    def __init__(self) -> None:
        """Start with no call counted."""
        self.calls = 0

    def __call__(self, segment: Callable[[], object]) -> Awaitable[object]:
        self.calls += 1
        return self._run(segment)

    async def _run(self, segment: Callable[[], object]) -> object:
        return segment()


@pytest.fixture()
def registry() -> PageContextRegistry:
    """Return an empty registry of page context callables."""
    return PageContextRegistry()


@pytest.fixture()
def inline() -> Iterator[InlineRunner]:
    """Run every blocking segment on the loop thread for the test."""
    runner = InlineRunner()
    token = segment_runner.set(runner)
    yield runner
    segment_runner.reset(token)
