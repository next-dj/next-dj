from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

import pytest

from next.deps import Loader
from next.deps.cache import RequestMemo, request_memo
from next.deps.scope import RequestScope
from tests.support.deps import counted


if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence


_JOBS = pytest.mark.parametrize("jobs", [10, 100], ids=["ten", "hundred"])
_CALLS: list[int] = []


def _prices(keys: Sequence[int]) -> dict[int, int]:
    """Batch function answering from memory, so the loader itself is what is timed."""
    _CALLS.append(len(keys))
    return {key: key * 10 for key in keys}


async def _job(loader: Loader[int, int], scope: RequestScope, key: int) -> int:
    """Load one key as a deferred job of the response does, counted on its scope."""
    with counted(scope, time.monotonic() + 60):
        return await loader.load(key)


async def _response(loader: Loader[int, int], keys: Iterable[int]) -> list[int]:
    """Run one job of a request scope per key, each loading its own."""
    scope = RequestScope()
    request_memo.set(RequestMemo(scope))
    try:
        return await asyncio.gather(*(_job(loader, scope, key) for key in keys))
    finally:
        await scope.close()


@pytest.fixture()
def loop() -> Iterator[asyncio.AbstractEventLoop]:
    """One event loop for every round, so no round pays for starting a loop."""
    running = asyncio.new_event_loop()
    yield running
    running.close()


class TestBenchLoaderBatching:
    """The keys of concurrent deferred jobs gathered into one batch call.

    The window is an hour, so a batch closes on its jobs alone and no timer is waited.
    """

    @_JOBS
    @pytest.mark.benchmark(group="deps.loader")
    def test_jobs_resolved_by_one_batch(
        self, loop: asyncio.AbstractEventLoop, jobs: int, benchmark
    ) -> None:
        loader = Loader(_prices, window=3600)

        def serve() -> list[int]:
            return loop.run_until_complete(_response(loader, range(jobs)))

        _CALLS.clear()
        assert serve() == [key * 10 for key in range(jobs)]
        assert [jobs] == _CALLS
        benchmark(serve)

    @_JOBS
    @pytest.mark.benchmark(group="deps.loader")
    def test_jobs_closing_full_batches(
        self, loop: asyncio.AbstractEventLoop, jobs: int, benchmark
    ) -> None:
        """Five keys a batch, so the cap closes each one before its jobs all wait."""
        loader = Loader(_prices, max_batch=5, window=3600)

        def serve() -> list[int]:
            return loop.run_until_complete(_response(loader, range(jobs)))

        _CALLS.clear()
        assert serve() == [key * 10 for key in range(jobs)]
        assert [5] * (jobs // 5) == _CALLS
        benchmark(serve)

    @pytest.mark.benchmark(group="deps.loader")
    def test_jobs_sharing_one_key(
        self, loop: asyncio.AbstractEventLoop, benchmark
    ) -> None:
        """Ten jobs asking for the same row, answered by a batch of one key."""
        loader = Loader(_prices, window=3600)

        def serve() -> list[int]:
            return loop.run_until_complete(_response(loader, [7] * 10))

        _CALLS.clear()
        assert serve() == [70] * 10
        assert _CALLS == [1]
        benchmark(serve)

    @pytest.mark.benchmark(group="deps.loader")
    def test_gather_without_a_loader(
        self, loop: asyncio.AbstractEventLoop, benchmark
    ) -> None:
        """Ten jobs that load nothing, the loop and scope floor of the cases above."""

        async def idle(scope: RequestScope, key: int) -> int:
            with counted(scope, time.monotonic() + 60):
                return key

        async def response() -> list[int]:
            scope = RequestScope()
            request_memo.set(RequestMemo(scope))
            try:
                return await asyncio.gather(*(idle(scope, key) for key in range(10)))
            finally:
                await scope.close()

        def serve() -> list[int]:
            return loop.run_until_complete(response())

        assert serve() == list(range(10))
        benchmark(serve)
