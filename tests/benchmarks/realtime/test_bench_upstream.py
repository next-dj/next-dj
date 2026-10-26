import asyncio
from collections.abc import Iterator
from unittest.mock import patch

import pytest

from next.realtime import handlers
from next.realtime.handlers import HandlerRegistry, Route, Upstream, dispatch


_EVENT = Upstream("room:1", "vote", {"choice": 2}, None, "subject", "c1")


class _CountingHandler:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, upstream: Upstream) -> None:
        self.calls += 1


class _AsyncCountingHandler:
    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, upstream: Upstream) -> None:
        self.calls += 1


def _registry(routes: int, handler: object) -> HandlerRegistry:
    """Bind the target route, then fill the rest with routes under other event names."""
    registry = HandlerRegistry()
    registry.add(Route.bind("room:{id}", "vote", handler))
    for index in range(routes - 1):
        registry.add(Route.bind(f"noise{index}:{{id}}", f"noise{index}", handler))
    assert len(registry) == routes
    return registry


@pytest.fixture()
def fresh_registry() -> Iterator[HandlerRegistry]:
    """Swap an empty registry in for `dispatch`, never the process-wide one."""
    registry = HandlerRegistry()
    with patch.object(handlers, "handler_registry", registry):
        yield registry


class TestBenchResolve:
    """Find the route serving one upstream event among the registered handlers."""

    @pytest.mark.parametrize("routes", [1, 100])
    @pytest.mark.benchmark(group="realtime.upstream")
    def test_hit(self, routes: int, benchmark) -> None:
        """The event name indexes the one route, the noise never reaches a regex."""
        handler = _CountingHandler()
        registry = _registry(routes, handler)
        found = benchmark(registry.resolve, "room:1", "vote")
        assert found is not None
        route, params = found
        assert route.handler is handler
        assert params == {"id": "1"}

    @pytest.mark.parametrize("routes", [1, 100])
    @pytest.mark.benchmark(group="realtime.upstream")
    def test_miss(self, routes: int, benchmark) -> None:
        """An event name no route serves answers None from the index alone."""
        registry = _registry(routes, _CountingHandler())
        assert benchmark(registry.resolve, "room:1", "typing") is None


class TestBenchDispatch:
    """Run one upstream event through its handler, the signal included."""

    @pytest.mark.benchmark(group="realtime.upstream")
    def test_async_handler(
        self,
        fresh_registry: HandlerRegistry,
        bench_loop: asyncio.AbstractEventLoop,
        benchmark,
    ) -> None:
        """The handler is awaited on the loop, no thread hop."""
        handler = _AsyncCountingHandler()
        fresh_registry.add(Route.bind("room:{id}", "vote", handler))

        def run() -> bool:
            return bench_loop.run_until_complete(dispatch(_EVENT))

        assert benchmark(run) is True
        before = handler.calls
        assert run() is True
        assert handler.calls == before + 1

    @pytest.mark.benchmark(group="realtime.upstream")
    def test_sync_handler(
        self,
        fresh_registry: HandlerRegistry,
        bench_loop: asyncio.AbstractEventLoop,
        benchmark,
    ) -> None:
        """The handler runs on the handler pool, one thread hop per event."""
        handler = _CountingHandler()
        fresh_registry.add(Route.bind("room:{id}", "vote", handler))

        def run() -> bool:
            return bench_loop.run_until_complete(dispatch(_EVENT))

        assert benchmark(run) is True
        before = handler.calls
        assert run() is True
        assert handler.calls == before + 1
