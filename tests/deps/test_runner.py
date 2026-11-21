import asyncio
import logging
import threading

import pytest

from next.caches import BoundedCache
from next.deps.cache import (
    _CACHE_MISS,
    _IN_PROGRESS,
    DependencyCache,
    RequestMemo,
    request_memo,
)
from next.deps.runner import (
    _note_adapted,
    batch_scope,
    run_isolated,
    run_on_request_thread,
    run_segment,
    segment_runner,
)
from next.deps.scope import RequestScope
from next.testing import make_resolution_context


class TestSegmentRunner:
    """Blocking segments go through one runner, read from a context variable."""

    def test_the_default_runner_is_the_request_thread_runner(self) -> None:
        assert segment_runner.get() is run_on_request_thread

    def test_the_request_thread_runner_leaves_the_loop_thread(self) -> None:
        async def run():
            return threading.get_ident(), await run_on_request_thread(
                threading.get_ident
            )

        loop_thread, ran_on = asyncio.run(run())
        assert ran_on != loop_thread

    def test_run_segment_uses_the_runner_set_for_the_context(self) -> None:
        segments: list[object] = []

        async def finish(segment):
            segments.append(segment)
            return segment()

        def runner(segment):
            return finish(segment)

        def segment() -> int:
            return 5

        async def run():
            segment_runner.set(runner)
            return await run_segment(segment)

        assert asyncio.run(run()) == 5
        assert segments == [segment]
        assert segment_runner.get() is run_on_request_thread


async def _inline(segment):
    return segment()


class TestRunIsolated:
    """A segment fills a private cache copy that merges back once it returns."""

    def test_the_copy_is_seeded_marked_privately_and_merged_for_misses(self) -> None:
        cache = DependencyCache({"seed": 1, "kept": "loop"})
        cache.mark_in_progress("pending")
        context = make_resolution_context(cache=cache, stack=["outer"])
        seen: dict[str, object] = {}

        def fill(branch):
            seen["seed"] = branch.cache.get("seed")
            seen["pending"] = branch.cache.get("pending")
            seen["memo"] = request_memo.get()
            seen["batched"] = batch_scope.get()
            seen["stack"] = branch.stack
            branch.cache.mark_in_progress("inner")
            branch.cache.set("kept", "segment")
            branch.cache.set("pending", "segment")
            branch.cache.set("fresh", 2)
            branch.stack.append("inner")
            return "done"

        scope = RequestScope()

        async def run():
            segment_runner.set(_inline)
            request_memo.set(RequestMemo(scope))
            value = await run_isolated(fill, context)
            return value, request_memo.get() is not None, batch_scope.get()

        assert asyncio.run(run()) == ("done", True, None)
        assert seen["seed"] == 1
        assert seen["pending"] is _CACHE_MISS
        assert seen["memo"] is None
        assert seen["batched"] is scope
        assert seen["stack"] == ["outer", "inner"]
        assert context.stack == ["outer"]
        assert cache.get("kept") == "loop"
        assert cache.get("pending") is _IN_PROGRESS
        assert cache.get("fresh") == 2
        assert cache.get("inner") is _CACHE_MISS

    def test_a_failing_segment_merges_nothing(self) -> None:
        cache = DependencyCache()
        context = make_resolution_context(cache=cache)

        def fill(branch):
            branch.cache.set("partial", 1)
            assert batch_scope.get() is None
            msg = "segment failed"
            raise RuntimeError(msg)

        async def run():
            segment_runner.set(_inline)
            await run_isolated(fill, context)

        with pytest.raises(RuntimeError, match="segment failed"):
            asyncio.run(run())
        assert "partial" not in cache


class TestAdaptedLog:
    """DEBUG names each bridged site once, in a memo of bounded size."""

    def test_a_site_another_thread_logged_is_not_logged_again(
        self, caplog, monkeypatch
    ) -> None:
        adapted: BoundedCache[str, None] = BoundedCache()
        monkeypatch.setattr("next.deps.runner._adapted", adapted)
        with caplog.at_level(logging.DEBUG, logger="next.deps.runner"):
            _note_adapted("dependency 'feed'")
            _note_adapted("dependency 'feed'")
        assert [
            r.getMessage() for r in caplog.records if r.name == "next.deps.runner"
        ] == ["Asynchronous dependency 'feed' adapted for a sync resolve."]
        assert list(adapted) == ["dependency 'feed'"]

    def test_the_memo_drops_its_oldest_site_past_its_bound(self, monkeypatch) -> None:
        adapted: BoundedCache[str, None] = BoundedCache(2)
        monkeypatch.setattr("next.deps.runner._adapted", adapted)
        for site in ("a", "b", "c"):
            _note_adapted(site)
        assert list(adapted) == ["b", "c"]
