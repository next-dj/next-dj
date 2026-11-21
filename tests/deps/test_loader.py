import asyncio
import logging
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import Context, ContextVar, copy_context
from functools import partial
from unittest.mock import Mock

import pytest
from asgiref.sync import ThreadSensitiveContext, async_to_sync, sync_to_async
from django.contrib.auth.models import Group
from django.db import connection
from django.test import override_settings

from next.deps import DependencyCycleError, Loader, MissingKeyError, rows
from next.deps.cache import RequestMemo, request_memo
from next.deps.loader import (
    _HUB,
    _hub,
    _loaders,
    counted_job,
    drain_loaders,
    open_loaders,
)
from next.deps.objects import visible_rows
from next.deps.runner import run_isolated, segment_runner
from next.deps.scope import RequestScope
from next.suspense import background_loop, drive
from next.testing import make_resolution_context


CLOSED = "scope is closed"
WAIT = 5.0

marker: ContextVar[str] = ContextVar("marker", default="unset")


class BackendDownError(Exception):
    """A failure a batch function raises, distinct from anything the loader raises."""


class HaltError(BaseException):
    """An interrupt-like failure that is not an `Exception`."""


class Clash:
    """A key every other `Clash` collides with, whose comparison raises `error`."""

    def __init__(self, error: BaseException | None = None) -> None:
        """Hold the error a comparison raises, None to compare by identity."""
        self.error = error

    def __hash__(self) -> int:
        """Collide with every other `Clash`."""
        return 1

    def __eq__(self, other: object) -> bool:
        """Raise the error of either side, or match by identity."""
        if self.error is not None or (
            isinstance(other, Clash) and other.error is not None
        ):
            raise self.error or other.error
        return other is self


def scoped() -> RequestScope:
    scope = RequestScope()
    request_memo.set(RequestMemo(scope))
    return scope


class LostRace:
    """A lock granted only after another caller made the hub of the request."""

    def __init__(self, scope: RequestScope, loop: asyncio.AbstractEventLoop) -> None:
        """Hold the loader state to race on and the real lock that takes over."""
        self.state = _loaders(scope)
        self.loop = loop
        self.lock = threading.Lock()
        self.winner: object = None

    def __enter__(self) -> None:
        """Let the winner make the hub through the real lock, then take that lock."""
        self.state._lock = self.lock
        self.winner = self.state.made(self.loop)
        self.lock.acquire()

    def __exit__(self, *exc_info: object) -> None:
        """Release the real lock."""
        self.lock.release()


def recording(calls: list[list[object]]) -> Callable[[Sequence[int]], dict[int, int]]:
    def batch(keys: Sequence[int]) -> dict[int, int]:
        calls.append(list(keys))
        return {key: key * 10 for key in keys}

    return batch


async def inline(segment):
    return segment()


async def on_thread(segment):
    return await asyncio.to_thread(segment)


def stuck_runner(reached: asyncio.Event):
    async def stuck(segment):
        reached.set()
        await asyncio.Event().wait()

    return stuck


def rejecting_runner(reached: asyncio.Event, reject: asyncio.Event):
    """Return a runner that holds a call until `reject`, then fails it unserved."""

    async def rejecting(segment):
        reached.set()
        await reject.wait()
        msg = "The request thread no longer serves this coroutine."
        raise RuntimeError(msg)

    return rejecting


async def waiting_through(runner, loader: Loader[int, int], key: int) -> object:
    """Load `key` from a loop task whose route runs segments through `runner`."""
    segment_runner.set(runner)
    return await loader.load(key)


def on_threads(pool: ThreadPoolExecutor, *calls: Callable[[], object]):
    loop = asyncio.get_running_loop()
    return [loop.run_in_executor(pool, copy_context().run, call) for call in calls]


async def next_turn() -> None:
    """Yield once, so every task step scheduled before this call runs first."""
    loop = asyncio.get_running_loop()
    turn = loop.create_future()
    loop.call_soon(turn.set_result, None)
    await turn


async def polled(ready: Callable[[], bool]) -> None:
    """Yield to the loop until `ready` holds, as a thread moves on its own."""
    for _ in range(5000):
        if ready():
            return
        await asyncio.sleep(0.001)
    pytest.fail("the thread never got there")


def outcome(call: Callable[[], object]) -> object:
    try:
        return call()
    except (RuntimeError, TypeError) as error:
        return error


def cycle_of(call: Callable[[], object]) -> object:
    try:
        return call()
    except DependencyCycleError as error:
        return error


class TestBatching:
    """The keys of one request gather into as few batch calls as the limits allow."""

    def test_concurrent_loads_share_one_batch(self) -> None:
        calls: list[list[object]] = []
        loader = Loader(recording(calls))

        async def scenario():
            scope = scoped()
            segment_runner.set(inline)
            open_loaders(scope, copy_context())
            return await asyncio.gather(*(loader.load(key) for key in (1, 2, 3)))

        assert asyncio.run(scenario()) == [10, 20, 30]
        assert calls == [[1, 2, 3]]

    def test_loads_from_the_loop_and_from_threads_share_one_batch(self) -> None:
        calls: list[list[object]] = []
        loader = Loader(recording(calls), max_batch=6, window=3600)

        async def scenario():
            scoped()
            segment_runner.set(on_thread)
            threaded = (partial(loader.load_sync, key) for key in (4, 5, 6))
            with ThreadPoolExecutor(3) as pool:
                return await asyncio.gather(
                    *on_threads(pool, *threaded),
                    *(loader.load(key) for key in (1, 2, 3)),
                )

        assert asyncio.run(scenario()) == [40, 50, 60, 10, 20, 30]
        assert [sorted(keys) for keys in calls] == [[1, 2, 3, 4, 5, 6]]

    def test_a_blocking_segment_loads_in_the_batch_of_its_request(self) -> None:
        calls: list[list[object]] = []
        loader = Loader(recording(calls), max_batch=3, window=3600)

        def fill(_context: object) -> object:
            assert request_memo.get() is None
            return loader.load_sync(3)

        async def scenario():
            scoped()
            segment_runner.set(on_thread)
            return await asyncio.gather(
                loader.load(1),
                loader.load(2),
                run_isolated(fill, make_resolution_context()),
            )

        assert asyncio.run(scenario()) == [10, 20, 30]
        assert [sorted(keys) for keys in calls] == [[1, 2, 3]]

    def test_a_full_batch_closes_and_the_waiting_job_closes_the_rest(self) -> None:
        calls: list[list[object]] = []
        loader = Loader(recording(calls), max_batch=2, window=3600)

        async def scenario():
            scope = scoped()
            segment_runner.set(inline)
            open_loaders(scope, copy_context())
            with counted_job(scope, time.monotonic() + WAIT):
                return await asyncio.gather(*(loader.load(key) for key in range(5)))

        assert asyncio.run(scenario()) == [0, 10, 20, 30, 40]
        assert calls == [[0, 1], [2, 3], [4]]

    def test_the_window_closes_a_batch_its_delay_after_the_first_key(
        self, monkeypatch
    ) -> None:
        calls: list[list[object]] = []
        loader = Loader(recording(calls))

        async def scenario():
            scope = scoped()
            segment_runner.set(inline)
            open_loaders(scope, copy_context())
            loop = asyncio.get_running_loop()
            armed: list[tuple[float, Callable[..., object], tuple[object, ...]]] = []
            ready = asyncio.Event()
            real = loop.call_later

            def call_later(delay, callback, *args, **kwargs):
                armed.append((delay, callback, args))
                ready.set()
                return real(3600, callback, *args, **kwargs)

            monkeypatch.setattr(loop, "call_later", call_later)
            pending = [asyncio.create_task(loader.load(key)) for key in (1, 2)]
            await ready.wait()
            assert calls == []
            [(delay, close, args)] = armed
            close(*args)
            return delay, await asyncio.gather(*pending)

        assert asyncio.run(scenario()) == (0.001, [10, 20])
        assert calls == [[1, 2]]

    def test_duplicate_keys_are_sent_once(self) -> None:
        calls: list[list[object]] = []
        loader = Loader(recording(calls))

        async def scenario():
            scoped()
            segment_runner.set(inline)
            return await asyncio.gather(loader.load(1), loader.load(1), loader.load(2))

        assert asyncio.run(scenario()) == [10, 10, 20]
        assert calls == [[1, 2]]

    def test_two_requests_never_share_a_batch(self) -> None:
        calls: list[list[object]] = []
        loader = Loader(recording(calls), window=3600)

        async def request():
            scope = scoped()
            segment_runner.set(inline)
            with counted_job(scope, time.monotonic() + WAIT):
                return await asyncio.gather(loader.load(1), loader.load(2))

        async def scenario():
            return await asyncio.gather(request(), request())

        assert asyncio.run(scenario()) == [[10, 20], [10, 20]]
        assert calls == [[1, 2], [1, 2]]

    def test_layers_and_their_threads_join_the_batches_of_the_root(self) -> None:
        calls: list[list[object]] = []
        loader = Loader(recording(calls), max_batch=4, window=3600)

        def on_layer(layer: RequestScope, key: int) -> object:
            request_memo.set(RequestMemo(layer))
            return loader.load_sync(key)

        async def through(scope: RequestScope, key: int) -> object:
            request_memo.set(RequestMemo(scope))
            return await loader.load(key)

        async def scenario():
            root = scoped()
            segment_runner.set(on_thread)
            with ThreadPoolExecutor(1) as pool:
                return await asyncio.gather(
                    through(root.layer(), 1),
                    through(root.layer(), 2),
                    *on_threads(pool, partial(on_layer, root.layer(), 3)),
                    through(root, 4),
                )

        assert asyncio.run(scenario()) == [10, 20, 30, 40]
        assert [sorted(keys) for keys in calls] == [[1, 2, 3, 4]]

    def test_unset_limits_follow_the_defer_settings(self) -> None:
        loader = Loader(recording([]))
        assert (loader.max_batch, loader.window) == (500, 0.001)
        with override_settings(
            NEXT_FRAMEWORK={"DEFER": {"LOADER_MAX_BATCH": 3, "BATCH_WINDOW": "20ms"}}
        ):
            assert (loader.max_batch, loader.window) == (3, 0.02)
        assert (loader.max_batch, loader.window) == (500, 0.001)

    def test_a_misfit_defer_setting_keeps_its_default(self) -> None:
        loader = Loader(recording([]))
        with override_settings(
            NEXT_FRAMEWORK={"DEFER": {"LOADER_MAX_BATCH": 0, "BATCH_WINDOW": "soon"}}
        ):
            assert (loader.max_batch, loader.window) == (500, 0.001)

    def test_set_limits_win_over_the_defer_settings(self) -> None:
        loader = Loader(recording([]), max_batch=2, window=5)
        with override_settings(NEXT_FRAMEWORK={"DEFER": {"LOADER_MAX_BATCH": 3}}):
            assert (loader.max_batch, loader.window) == (2, 5)

    def test_max_batch_takes_at_least_one_key(self) -> None:
        with pytest.raises(ValueError, match="max_batch of at least 1, got 0"):
            Loader(recording([]), max_batch=0)

    def test_an_async_generator_batch_is_refused(self) -> None:
        async def fetch(keys):
            yield {}

        with pytest.raises(TypeError, match="an async generator yields them"):
            Loader(fetch)

    def test_an_async_batch_behind_a_partial_awaits(self) -> None:
        async def fetch(scale, keys):
            return [key * scale for key in keys]

        loader = Loader(partial(fetch, 3))

        async def scenario():
            scoped()
            return await loader.load(2)

        assert loader.awaits
        assert asyncio.run(scenario()) == 6


class TestCountedJobs:
    """A batch closes once every counted job of the request waits on a key or left."""

    def test_a_batch_stays_open_while_a_counted_job_still_runs(self) -> None:
        calls: list[list[object]] = []
        loader = Loader(recording(calls), window=3600)

        async def scenario():
            scope = scoped()
            segment_runner.set(inline)
            go = asyncio.Event()
            deadline = time.monotonic() + WAIT

            async def job(key: int, gate: asyncio.Event | None = None) -> object:
                with counted_job(scope, deadline):
                    if gate is not None:
                        await gate.wait()
                    return await loader.load(key)

            first = asyncio.create_task(job(1))
            third = asyncio.create_task(job(3))
            second = asyncio.create_task(job(2, go))
            for _ in range(5):
                await next_turn()
            held = list(calls)
            go.set()
            return held, await first, await second, await third

        assert asyncio.run(scenario()) == ([], 10, 20, 30)
        assert calls == [[1, 3, 2]]

    @pytest.mark.parametrize("ending", ["returns", "raises", "is cancelled"])
    def test_a_job_ending_without_a_load_closes_the_batch(self, ending: str) -> None:
        calls: list[list[object]] = []
        loader = Loader(recording(calls), window=3600)

        async def scenario():
            scope = scoped()
            segment_runner.set(inline)
            go = asyncio.Event()
            deadline = time.monotonic() + WAIT

            async def loading() -> object:
                with counted_job(scope, deadline):
                    return await loader.load(1)

            async def other() -> None:
                with counted_job(scope, deadline):
                    await go.wait()
                    if ending == "raises":
                        raise BackendDownError

            first = asyncio.create_task(loading())
            second = asyncio.create_task(other())
            for _ in range(5):
                await next_turn()
            held = list(calls)
            if ending == "is cancelled":
                second.cancel()
            else:
                go.set()
            await asyncio.gather(second, return_exceptions=True)
            return held, await first

        assert asyncio.run(scenario()) == ([], 10)
        assert calls == [[1]]

    def test_a_job_past_its_deadline_stops_holding_the_batch(self) -> None:
        calls: list[list[object]] = []
        loader = Loader(recording(calls), window=3600)

        async def scenario():
            scope = scoped()
            segment_runner.set(inline)
            stuck = asyncio.Event()

            async def blocked() -> None:
                with counted_job(scope, time.monotonic() + 0.05):
                    await stuck.wait()

            async def loading() -> object:
                with counted_job(scope, time.monotonic() + WAIT):
                    return await loader.load(1)

            held = asyncio.create_task(blocked())
            value = await asyncio.wait_for(loading(), WAIT)
            assert not held.done()
            stuck.set()
            await held
            return value

        assert asyncio.run(scenario()) == 10
        assert calls == [[1]]

    def test_a_waiting_job_past_its_deadline_holds_no_batch_open(self) -> None:
        calls: list[list[object]] = []
        loader = Loader(recording(calls), window=3600)

        async def scenario():
            scope = scoped()
            segment_runner.set(inline)
            go = asyncio.Event()

            async def late() -> object:
                with counted_job(scope, time.monotonic() + 0.05):
                    return await loader.load(1)

            async def other() -> None:
                with counted_job(scope, time.monotonic() + WAIT):
                    await go.wait()

            waiting = asyncio.create_task(late())
            running = asyncio.create_task(other())
            await asyncio.sleep(0.1)
            held = list(calls)
            go.set()
            await running
            return held, await waiting

        assert asyncio.run(scenario()) == ([], 10)
        assert calls == [[1]]

    def test_a_load_after_the_deadline_waits_for_the_window(self) -> None:
        calls: list[list[object]] = []
        loader = Loader(recording(calls), window=0.01)

        async def scenario():
            scope = scoped()
            segment_runner.set(inline)
            with counted_job(scope, time.monotonic() - 1):
                await asyncio.sleep(0.01)
                return await asyncio.gather(loader.load(1), loader.load(2))

        assert asyncio.run(scenario()) == [10, 20]
        assert calls == [[1, 2]]

    def test_counted_threads_share_the_batch_they_all_wait_on(self) -> None:
        calls: list[list[object]] = []
        loader = Loader(recording(calls), window=3600)
        barrier = threading.Barrier(2, timeout=WAIT)

        def job(scope: RequestScope, key: int) -> object:
            with counted_job(scope, time.monotonic() + WAIT):
                barrier.wait()
                return loader.load_sync(key)

        async def scenario():
            scope = scoped()
            segment_runner.set(on_thread)
            jobs = (partial(job, scope, key) for key in (1, 2))
            with ThreadPoolExecutor(2) as pool:
                return await asyncio.wait_for(
                    asyncio.gather(*on_threads(pool, *jobs)), WAIT
                )

        assert asyncio.run(scenario()) == [10, 20]
        assert [sorted(keys) for keys in calls] == [[1, 2]]

    def test_a_waiting_job_opening_another_batch_closes_it(self) -> None:
        calls: list[list[object]] = []
        second = Loader(recording(calls), window=3600)

        async def scenario():
            scope = scoped()
            segment_runner.set(inline)
            started = asyncio.Event()
            release = asyncio.Event()

            async def held(keys):
                started.set()
                await release.wait()
                return {key: key for key in keys}

            first = Loader(held, window=3600)

            async def later() -> object:
                await started.wait()
                return await second.load(2)

            with counted_job(scope, time.monotonic() + 2 * WAIT):
                waiting = asyncio.create_task(first.load(1))
                value = await asyncio.wait_for(later(), WAIT)
                release.set()
                return await waiting, value

        assert asyncio.run(scenario()) == (1, 20)
        assert calls == [[2]]

    def test_a_load_inside_a_batch_function_closes_its_batch(self) -> None:
        calls: list[list[object]] = []

        async def fetch(keys):
            calls.append(list(keys))
            return {key: key * 10 for key in keys}

        inner = Loader(fetch, window=3600)

        async def nested(keys):
            values = await asyncio.gather(*(inner.load(key) for key in keys))
            return dict(zip(keys, values, strict=True))

        outer = Loader(nested, window=3600)

        async def scenario():
            scope = scoped()
            with counted_job(scope, time.monotonic() + 2 * WAIT):
                return await asyncio.wait_for(outer.load(3), WAIT)

        assert asyncio.run(scenario()) == 30
        assert calls == [[3]]

    def test_a_job_joining_a_key_another_job_loads_counts_as_waiting(self) -> None:
        calls: list[list[object]] = []
        loader = Loader(recording(calls), window=3600)

        async def scenario():
            scope = scoped()
            segment_runner.set(inline)
            deadline = time.monotonic() + 2 * WAIT

            async def compute() -> object:
                return await loader.load(1)

            async def job() -> object:
                with counted_job(scope, deadline):
                    return await scope.resolve("user", compute)

            return await asyncio.wait_for(asyncio.gather(job(), job()), WAIT)

        assert asyncio.run(scenario()) == [10, 10]
        assert calls == [[1]]

    def test_a_joiner_cut_short_stops_counting_as_waiting(self) -> None:
        async def scenario():
            scope = scoped()
            gate = asyncio.Event()
            deadline = time.monotonic() + WAIT

            async def gated() -> object:
                await gate.wait()
                return "ann"

            async def never() -> object:
                pytest.fail("a joiner computed the key")

            async def claiming() -> object:
                with counted_job(scope, deadline):
                    return await scope.resolve("user", gated)

            claimant = asyncio.create_task(claiming())
            await next_turn()
            hub = _hub(scope, asyncio.get_running_loop())
            with counted_job(scope, deadline):
                joiner = asyncio.create_task(scope.resolve("user", never))
                await next_turn()
                parked = hub._parked
                joiner.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await joiner
                counts = (hub._jobs, parked, hub._parked)
            gate.set()
            return counts, await claimant

        assert asyncio.run(scenario()) == ((2, 1, 0), "ann")

    def test_a_join_in_another_request_leaves_the_count_alone(self) -> None:
        async def scenario():
            scope = scoped()
            other = RequestScope()
            gate = asyncio.Event()

            async def gated() -> object:
                await gate.wait()
                return "ann"

            claimant = asyncio.create_task(other.resolve("user", gated))
            await next_turn()
            hub = _hub(scope, asyncio.get_running_loop())
            with counted_job(scope, time.monotonic() + WAIT):
                joiner = asyncio.create_task(other.resolve("user", gated))
                await next_turn()
                parked = hub._parked
                gate.set()
                value = await joiner
            return parked, value, await claimant

        assert asyncio.run(scenario()) == (0, "ann", "ann")

    def test_a_load_of_another_request_leaves_the_count_alone(self) -> None:
        calls: list[list[object]] = []
        loader = Loader(recording(calls), window=0.01)

        async def scenario():
            scope = scoped()
            segment_runner.set(inline)
            with counted_job(scope, time.monotonic() + WAIT):
                request_memo.set(RequestMemo(RequestScope()))
                value = await loader.load(1)
            return value, scope._slots[_HUB].hub

        assert asyncio.run(scenario()) == (10, None)
        assert calls == [[1]]

    def test_a_job_on_a_scope_with_no_loop_counts_nothing(self) -> None:
        scope = RequestScope()
        with counted_job(scope, 0.0), counted_job(None, 0.0):
            pass
        assert scope._slots == {}

    def test_a_join_that_ends_before_any_load_counts_nothing(self) -> None:
        async def scenario():
            scope = scoped()
            deadline = time.monotonic() + WAIT

            async def compute() -> object:
                await next_turn()
                return "ann"

            async def job() -> object:
                with counted_job(scope, deadline):
                    return await scope.resolve("user", compute)

            values = await asyncio.wait_for(asyncio.gather(job(), job()), WAIT)
            return values, scope._slots[_HUB].hub

        assert asyncio.run(scenario()) == (["ann", "ann"], None)

    def test_jobs_that_never_load_leave_the_loop_alone(self) -> None:
        loop = Mock(spec=asyncio.AbstractEventLoop)
        scope = RequestScope(loop=loop)
        open_loaders(scope, copy_context())
        with (
            counted_job(scope, time.monotonic() + WAIT),
            counted_job(scope, time.monotonic() + WAIT),
        ):
            pass
        assert loop.method_calls == []
        assert scope._slots[_HUB].hub is None


class TestOutcomes:
    """Each key settles with its own value or error, memoised for the request."""

    def test_a_request_serves_a_loaded_key_and_its_error_from_memory(self) -> None:
        calls: list[list[object]] = []

        def fetch(keys):
            calls.append(list(keys))
            return {key: key * 10 for key in keys if key != 2}

        loader = Loader(fetch)

        async def scenario():
            scoped()
            segment_runner.set(inline)
            first = await loader.load(1)
            again = await loader.load(1)
            with pytest.raises(MissingKeyError) as missing:
                await loader.load(2)
            with pytest.raises(MissingKeyError) as memoised:
                await loader.load(2)
            return first, again, missing.value, memoised.value

        first, again, missing, memoised = asyncio.run(scenario())
        assert (first, again) == (10, 10)
        assert memoised is missing
        assert calls == [[1], [2]]

    def test_a_missing_key_fails_that_key_only_and_is_no_key_error(self) -> None:
        loader = Loader(lambda _keys: {1: "one"})

        async def scenario():
            scoped()
            segment_runner.set(inline)
            return await asyncio.gather(
                loader.load(1), loader.load(2), return_exceptions=True
            )

        found, missing = asyncio.run(scenario())
        assert found == "one"
        assert isinstance(missing, MissingKeyError)
        assert isinstance(missing, LookupError)
        assert not isinstance(missing, KeyError)
        assert missing.key == 2
        assert str(missing) == "The batch function returned no value for the key 2."

    def test_a_failing_batch_fails_every_key_once(self) -> None:
        calls: list[list[object]] = []

        def fetch(keys):
            calls.append(list(keys))
            raise BackendDownError

        loader = Loader(fetch)

        async def scenario():
            scoped()
            segment_runner.set(inline)
            failures = await asyncio.gather(
                loader.load(1), loader.load(2), return_exceptions=True
            )
            with pytest.raises(BackendDownError) as again:
                await loader.load(1)
            return failures, again.value

        [first, second], again = asyncio.run(scenario())
        assert isinstance(first, BackendDownError)
        assert first is second
        assert again is first
        assert calls == [[1, 2]]

    def test_a_failing_async_batch_fails_every_key(self) -> None:
        async def fetch(keys):
            raise BackendDownError

        loader = Loader(fetch)

        async def scenario():
            scoped()
            return await asyncio.gather(
                loader.load(1), loader.load(2), return_exceptions=True
            )

        first, second = asyncio.run(scenario())
        assert isinstance(first, BackendDownError)
        assert first is second

    def test_an_interrupt_in_an_async_batch_fails_its_keys_and_is_logged(
        self, caplog
    ) -> None:
        async def fetch(keys):
            raise HaltError

        loader = Loader(fetch, max_batch=2, window=3600)

        async def scenario():
            scope = scoped()
            loads = asyncio.gather(
                loader.load(1), loader.load(2), return_exceptions=True
            )
            await next_turn()
            [driver] = scope._slots[_HUB].hub._drivers
            failures = await loads
            await driver
            return failures

        with caplog.at_level(logging.ERROR, logger="next.deps.loader"):
            first, second = asyncio.run(scenario())
        assert isinstance(first, HaltError)
        assert second is first
        [record] = [r for r in caplog.records if r.name == "next.deps.loader"]
        assert record.exc_info is not None
        assert record.exc_info[1] is first
        assert "raised HaltError" in record.getMessage()

    def test_values_in_key_order_map_and_a_short_sequence_fails_every_key(self) -> None:
        ordered = Loader(lambda keys: [key * 2 for key in keys])
        short = Loader(lambda _keys: [])

        async def scenario():
            scoped()
            segment_runner.set(inline)
            values = await asyncio.gather(ordered.load(1), ordered.load(2))
            failures = await asyncio.gather(
                short.load(1), short.load(2), return_exceptions=True
            )
            return values, failures

        values, [first, second] = asyncio.run(scenario())
        assert values == [2, 4]
        assert isinstance(first, TypeError)
        assert first is second
        assert "a sequence of 2 values in key order, got list of 0." in str(first)

    @pytest.mark.parametrize("result", ["x", b"x", bytearray(b"x"), 7])
    def test_text_bytes_and_scalars_are_no_sequence_of_values(self, result) -> None:
        loader = Loader(lambda _keys: result)
        with pytest.raises(TypeError) as caught:
            loader.load_sync(1)
        assert str(caught.value).endswith(
            f"a sequence of 1 values in key order, got {type(result).__name__}."
        )

    def test_cancelling_one_waiter_leaves_the_batch_to_the_others(self) -> None:
        calls: list[list[object]] = []

        async def scenario():
            scope = scoped()
            open_loaders(scope, copy_context())
            started = asyncio.Event()
            gate = asyncio.Event()

            async def fetch(keys):
                calls.append(list(keys))
                started.set()
                await gate.wait()
                return {key: key * 10 for key in keys}

            loader = Loader(fetch, max_batch=2, window=3600)
            cancelled = asyncio.create_task(loader.load(1))
            same = asyncio.create_task(loader.load(1))
            other = asyncio.create_task(loader.load(2))
            await started.wait()
            cancelled.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cancelled
            gate.set()
            return await same, await other

        assert asyncio.run(scenario()) == (10, 20)
        assert calls == [[1, 2]]

    def test_a_runner_that_fails_fails_the_keys_of_its_batch(self) -> None:
        async def broken(segment):
            raise BackendDownError

        loader = Loader(recording([]))

        async def scenario():
            scoped()
            segment_runner.set(broken)
            return await asyncio.gather(
                loader.load(1), loader.load(2), return_exceptions=True
            )

        first, second = asyncio.run(scenario())
        assert isinstance(first, BackendDownError)
        assert second is first


class TestRoutes:
    """A sync batch runs on the route of a loop waiter that still waits on it."""

    def test_a_waiter_gone_before_the_close_lends_the_batch_no_route(self) -> None:
        calls: list[list[object]] = []
        loader = Loader(recording(calls), max_batch=2, window=3600)

        async def dead(segment):
            raise BackendDownError

        async def scenario():
            scoped()
            gone = asyncio.create_task(waiting_through(dead, loader, 1))
            await next_turn()
            gone.cancel()
            with pytest.raises(asyncio.CancelledError):
                await gone
            return await waiting_through(inline, loader, 2), await loader.load(1)

        assert asyncio.run(scenario()) == (20, 10)
        assert calls == [[1, 2]]

    def test_a_call_its_gone_waiter_left_unserved_runs_on_a_live_route(self) -> None:
        calls: list[list[object]] = []
        loader = Loader(recording(calls), max_batch=2, window=3600)

        async def scenario():
            scoped()
            reached = asyncio.Event()
            reject = asyncio.Event()
            runner = rejecting_runner(reached, reject)
            gone = asyncio.create_task(waiting_through(runner, loader, 1))
            await next_turn()
            live = asyncio.create_task(waiting_through(inline, loader, 2))
            await reached.wait()
            gone.cancel()
            with pytest.raises(asyncio.CancelledError):
                await gone
            reject.set()
            return await asyncio.wait_for(live, WAIT), await loader.load(1)

        assert asyncio.run(scenario()) == (20, 10)
        assert calls == [[1, 2]]

    def test_a_call_no_live_waiter_can_carry_waits_for_the_next_waiter(self) -> None:
        calls: list[list[object]] = []
        loader = Loader(recording(calls), max_batch=1, window=3600)

        async def scenario():
            scope = scoped()
            reached = asyncio.Event()
            reject = asyncio.Event()
            runner = rejecting_runner(reached, reject)
            gone = asyncio.create_task(waiting_through(runner, loader, 1))
            await reached.wait()
            hub = scope._slots[_HUB].hub
            gone.cancel()
            with pytest.raises(asyncio.CancelledError):
                await gone
            reject.set()
            await polled(lambda: not hub._drivers)
            assert calls == []
            return await waiting_through(inline, loader, 1)

        assert asyncio.run(scenario()) == 10
        assert calls == [[1]]


class TestKeys:
    """A key is hashed by its caller, so a bad one fails there and nothing hangs."""

    def test_an_unhashable_key_fails_its_caller_at_once(self) -> None:
        loader = Loader(recording([]))

        async def scenario():
            scoped()
            with pytest.raises(TypeError, match="unhashable"):
                await loader.load([1])
            return await asyncio.to_thread(
                copy_context().run, outcome, partial(loader.load_sync, [1])
            )

        assert isinstance(asyncio.run(scenario()), TypeError)

    def test_a_key_failing_on_the_loop_still_answers_its_admission(self) -> None:
        """A collision compares keys on the loop, which a thread's hash cannot catch."""
        loader = Loader(recording([]), window=3600)
        error = BackendDownError()

        async def scenario():
            scope = scoped()
            hub = _hub(scope, asyncio.get_running_loop())
            hub.enter(loader, Clash())
            admitted: Future[object] = Future()
            hub.admit(loader, Clash(error), admitted)
            await scope.close()
            return admitted.exception(0)

        assert asyncio.run(scenario()) is error

    def test_an_interrupt_on_the_loop_answers_the_admission_and_goes_on(self) -> None:
        loader = Loader(recording([]), window=3600)
        error = HaltError()

        async def scenario():
            scope = scoped()
            hub = _hub(scope, asyncio.get_running_loop())
            hub.enter(loader, Clash())
            admitted: Future[object] = Future()
            with pytest.raises(HaltError):
                hub.admit(loader, Clash(error), admitted)
            await scope.close()
            return admitted.exception(0)

        assert asyncio.run(scenario()) is error


class TestThreads:
    """A thread joins the open batch and runs it itself when no one has started it."""

    def test_a_waiting_thread_runs_an_unstarted_batch_itself(self) -> None:
        ran_on: list[int] = []

        def fetch(keys):
            ran_on.append(threading.get_ident())
            return {key: key * 10 for key in keys}

        loader = Loader(fetch, max_batch=1, window=3600)

        def job() -> tuple[int, object, object]:
            return threading.get_ident(), loader.load_sync(5), loader.load_sync(5)

        async def scenario():
            scope = scoped()
            segment_runner.set(stuck_runner(asyncio.Event()))
            with ThreadPoolExecutor(1) as pool:
                [result] = await asyncio.wait_for(
                    asyncio.gather(*on_threads(pool, job)), WAIT
                )
            await scope.close()
            return result

        ident, value, again = asyncio.run(scenario())
        assert (value, again) == (50, 50)
        assert ran_on == [ident]

    def test_racing_threads_run_a_batch_exactly_once(self) -> None:
        count = 8
        runners: list[int] = []

        def fetch(keys):
            runners.append(threading.get_ident())
            return {key: key * 10 for key in keys}

        loader = Loader(fetch, max_batch=count, window=3600)

        async def scenario():
            scoped()
            segment_runner.set(on_thread)
            loads = (partial(loader.load_sync, key) for key in range(count))
            with ThreadPoolExecutor(count) as pool:
                return await asyncio.gather(*on_threads(pool, *loads))

        assert asyncio.run(scenario()) == [key * 10 for key in range(count)]
        assert len(runners) == 1

    def test_a_hub_made_while_waiting_for_the_lock_is_the_one_returned(self) -> None:
        async def scenario():
            scope = scoped()
            loop = asyncio.get_running_loop()
            race = LostRace(scope, loop)
            race.state._lock = race
            hub = _hub(scope, loop)
            await scope.close()
            return hub is race.winner, race.state.hub is hub

        assert asyncio.run(scenario()) == (True, True)

    def test_an_interrupt_in_a_stolen_batch_reaches_every_waiter(self) -> None:
        def fetch(keys):
            raise HaltError

        loader = Loader(fetch, max_batch=2, window=3600)

        async def scenario():
            scope = scoped()
            reached = asyncio.Event()
            segment_runner.set(stuck_runner(reached))
            waiter = asyncio.create_task(loader.load(1))
            with ThreadPoolExecutor(1) as pool:
                [stolen] = await asyncio.gather(
                    *on_threads(pool, partial(loader.load_sync, 2)),
                    return_exceptions=True,
                )
            with pytest.raises(HaltError):
                await waiter
            await reached.wait()
            await scope.close()
            return stolen

        assert isinstance(asyncio.run(scenario()), HaltError)

    def test_a_thread_waits_for_an_async_batch_the_loop_runs(self) -> None:
        started = threading.Event()
        release = threading.Event()
        ran_on: list[int] = []

        async def fetch(keys):
            ran_on.append(threading.get_ident())
            started.set()
            await asyncio.to_thread(release.wait, WAIT)
            return {key: key * 10 for key in keys}

        loader = Loader(fetch, max_batch=1)

        async def scenario():
            scoped()
            threaded = asyncio.create_task(
                asyncio.to_thread(copy_context().run, loader.load_sync, 1)
            )
            assert await asyncio.to_thread(started.wait, WAIT)
            joined = asyncio.create_task(loader.load(1))
            await next_turn()
            assert not threaded.done()
            release.set()
            found = await asyncio.wait_for(asyncio.gather(threaded, joined), WAIT)
            return found, threading.get_ident()

        found, loop_thread = asyncio.run(scenario())
        assert found == [10, 10]
        assert ran_on == [loop_thread]

    def test_a_load_on_the_loop_of_another_thread_waits_as_a_thread(self) -> None:
        calls: list[list[object]] = []
        loader = Loader(recording(calls))

        async def elsewhere():
            return await loader.load(2)

        async def owner():
            scope = scoped()
            open_loaders(scope, copy_context())
            return await asyncio.to_thread(copy_context().run, asyncio.run, elsewhere())

        assert asyncio.run(owner()) == 20
        assert calls == [[2]]

    def test_a_scope_with_no_loop_loads_alone(self) -> None:
        calls: list[list[object]] = []
        loader = Loader(recording(calls))

        def job() -> tuple[object, object]:
            request_memo.set(RequestMemo(RequestScope()))
            return loader.load_sync(3), loader.load_sync(3)

        assert copy_context().run(job) == (30, 30)
        assert calls == [[3], [3]]

    def test_load_sync_refuses_an_event_loop_thread(self) -> None:
        async def scenario():
            Loader(recording([])).load_sync(1)

        with pytest.raises(RuntimeError, match="Await load"):
            asyncio.run(scenario())


class TestRequestThread:
    """A sync batch runs on a thread that waits on it or on the request thread."""

    def test_a_sync_batch_runs_on_the_request_thread_that_waits(self) -> None:
        ran_on: list[int] = []

        def fetch(keys):
            ran_on.append(threading.get_ident())
            return {key: key for key in keys}

        loader = Loader(fetch)

        async def work():
            scoped()
            load = partial(loader.load_sync, 4)
            return await sync_to_async(load, thread_sensitive=True)()

        assert copy_context().run(drive, work) == 4
        assert ran_on == [threading.get_ident()]

    def test_a_load_on_another_loop_hops_to_the_loop_of_the_request(self) -> None:
        calls: list[list[object]] = []
        loader = Loader(recording(calls))
        home = background_loop.get()
        scope = RequestScope(loop=home)

        async def work() -> object:
            request_memo.set(RequestMemo(scope))
            return await loader.load(3)

        assert asyncio.run(work()) == 30
        assert scope._slots[_HUB].hub.loop is home
        assert calls == [[3]]

    def test_a_sync_batch_a_loop_task_awaits_runs_on_the_view_thread(self) -> None:
        ran_on: list[int] = []

        def fetch(keys):
            ran_on.append(threading.get_ident())
            return {key: key * 10 for key in keys}

        loader = Loader(fetch, max_batch=2, window=3600)

        def job() -> tuple[int, object]:
            return threading.get_ident(), loader.load_sync(2)

        async def scenario():
            async with ThreadSensitiveContext():
                scope = scoped()
                open_loaders(scope, copy_context())
                waiter = asyncio.create_task(loader.load(1))
                await next_turn()
                threaded = sync_to_async(job, thread_sensitive=True)()
                try:
                    return await asyncio.wait_for(
                        asyncio.gather(threaded, waiter), WAIT
                    )
                finally:
                    await scope.close()

        (view, value), first = asyncio.run(scenario())
        assert (value, first) == (20, 10)
        assert ran_on == [view]

    def test_a_later_drive_loads_after_the_drive_that_opened_the_batches(self) -> None:
        ran_on: list[int] = []

        def fetch(keys):
            ran_on.append(threading.get_ident())
            return {key: key * 10 for key in keys}

        loader = Loader(fetch)

        def view() -> tuple[object, object]:
            request_memo.set(RequestMemo(RequestScope()))
            return drive(lambda: loader.load(1)), drive(lambda: loader.load(2))

        assert copy_context().run(view) == (10, 20)
        assert ran_on == [threading.get_ident()] * 2

    def test_threads_waiting_on_one_async_batch_share_its_one_call(self) -> None:
        calls: list[list[object]] = []

        async def fetch(keys):
            calls.append(sorted(keys))
            await asyncio.sleep(0)
            return {key: key * 10 for key in keys}

        loader = Loader(fetch, max_batch=3, window=3600)

        async def scenario():
            scope = scoped()
            waiter = asyncio.create_task(loader.load(1))
            await next_turn()
            with ThreadPoolExecutor(2) as pool:
                threaded = on_threads(
                    pool, partial(loader.load_sync, 2), partial(loader.load_sync, 3)
                )
                try:
                    return await asyncio.wait_for(
                        asyncio.gather(waiter, *threaded), WAIT
                    )
                finally:
                    await scope.close()

        assert asyncio.run(scenario()) == [10, 20, 30]
        assert calls == [[1, 2, 3]]


class TestScopeLifetime:
    """Closing the scope fails every key no batch answered and wakes each waiter."""

    def test_closing_the_scope_fails_the_keys_of_a_batch_no_one_started(self) -> None:
        async def scenario():
            scope = scoped()
            reached = asyncio.Event()
            segment_runner.set(stuck_runner(reached))
            loader = Loader(recording([]), max_batch=1)
            waiter = asyncio.create_task(loader.load(1))
            await reached.wait()
            await scope.close()
            with pytest.raises(RuntimeError, match=CLOSED):
                await waiter

        asyncio.run(scenario())

    def test_closing_the_scope_fails_a_running_async_batch_then_cancels_it(
        self,
    ) -> None:
        cancelled: list[bool] = []

        async def scenario():
            scope = scoped()
            started = asyncio.Event()

            async def fetch(keys):
                started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled.append(True)
                    raise

            waiter = asyncio.create_task(Loader(fetch, max_batch=1).load(1))
            await started.wait()
            await scope.close()
            with pytest.raises(RuntimeError, match=CLOSED):
                await waiter

        asyncio.run(scenario())
        assert cancelled == [True]

    @pytest.mark.parametrize("kind", ["sync", "async"])
    def test_closing_the_scope_wakes_a_thread_waiting_for_the_window(
        self, kind: str
    ) -> None:
        async def fetch(keys):
            return dict.fromkeys(keys)

        batch = recording([]) if kind == "sync" else fetch
        loader = Loader(batch, window=3600)

        async def scenario():
            scope = scoped()
            waiting = asyncio.create_task(
                asyncio.to_thread(
                    copy_context().run, outcome, partial(loader.load_sync, 1)
                )
            )
            await polled(
                lambda: (
                    bool(scope._slots)
                    and scope._slots[_HUB].hub is not None
                    and scope._slots[_HUB].hub.cached(loader, 1) is not None
                )
            )
            await scope.close()
            return await waiting

        failed = asyncio.run(scenario())
        assert isinstance(failed, RuntimeError)
        assert CLOSED in str(failed)

    def test_closing_the_scope_wakes_a_thread_waiting_on_a_running_batch(self) -> None:
        started = threading.Event()

        async def fetch(keys):
            started.set()
            await asyncio.Event().wait()

        loader = Loader(fetch, max_batch=1)

        async def scenario():
            scope = scoped()
            waiting = asyncio.create_task(
                asyncio.to_thread(
                    copy_context().run, outcome, partial(loader.load_sync, 1)
                )
            )
            await asyncio.to_thread(started.wait, 5)
            await scope.close()
            return await waiting

        failed = asyncio.run(scenario())
        assert isinstance(failed, RuntimeError)
        assert CLOSED in str(failed)

    def test_closing_the_scope_lets_a_thread_finish_the_sync_batch_it_runs(
        self,
    ) -> None:
        started = threading.Event()
        release = threading.Event()

        def fetch(keys):
            started.set()
            release.wait(WAIT)
            return {key: key * 10 for key in keys}

        loader = Loader(fetch, max_batch=1)

        async def scenario():
            scope = scoped()
            segment_runner.set(stuck_runner(asyncio.Event()))
            waiting = asyncio.create_task(
                asyncio.to_thread(copy_context().run, loader.load_sync, 1)
            )
            assert await asyncio.to_thread(started.wait, WAIT)
            await scope.close()
            release.set()
            return await waiting

        assert asyncio.run(scenario()) == 10

    def test_an_admission_the_loop_has_not_reached_fails_on_close(self) -> None:
        loader = Loader(recording([]))

        async def scenario():
            scope = scoped()
            hub = _hub(scope, asyncio.get_running_loop())
            admitted = hub.admission(loader, 1)
            await scope.close()
            await next_turn()
            return admitted.exception(0), hub.cached(loader, 1)

        error, entry = asyncio.run(scenario())
        assert isinstance(error, RuntimeError)
        assert CLOSED in str(error)
        assert entry is None

    def test_a_closed_scope_answers_its_loaded_keys_and_refuses_new_ones(self) -> None:
        loader = Loader(recording([]))

        async def scenario():
            scope = scoped()
            segment_runner.set(inline)
            assert await loader.load(1) == 10
            await scope.close()
            late = await asyncio.to_thread(
                copy_context().run, outcome, partial(loader.load_sync, 2)
            )
            with pytest.raises(RuntimeError, match=CLOSED):
                await loader.load(3)
            return await loader.load(1), late

        loaded, late = asyncio.run(scenario())
        assert loaded == 10
        assert isinstance(late, RuntimeError)

    def test_a_thread_reaching_a_closed_scope_first_is_refused(self) -> None:
        loader = Loader(recording([]))

        async def scenario():
            scope = scoped()
            await scope.close()
            return await asyncio.to_thread(
                copy_context().run, outcome, partial(loader.load_sync, 1)
            )

        failed = asyncio.run(scenario())
        assert isinstance(failed, RuntimeError)
        assert CLOSED in str(failed)


class TestAsyncBatches:
    """An async batch function runs on the loop and every thread-sensitive hop is refused."""

    def test_a_hop_fails_every_waiter_of_the_batch_with_the_refusal(self) -> None:
        async def fetch(keys):
            ident = await sync_to_async(threading.get_ident, thread_sensitive=True)()
            return dict.fromkeys(keys, ident)

        loader = Loader(fetch, max_batch=3, window=3600)

        async def scenario():
            async with ThreadSensitiveContext():
                scope = scoped()
                waiters = [asyncio.create_task(loader.load(key)) for key in (1, 2)]
                await next_turn()
                threaded = sync_to_async(
                    partial(outcome, partial(loader.load_sync, 3)),
                    thread_sensitive=True,
                )()
                try:
                    return await asyncio.wait_for(
                        asyncio.gather(*waiters, threaded, return_exceptions=True), WAIT
                    )
                finally:
                    await scope.close()

        first, second, third = asyncio.run(scenario())
        assert isinstance(first, RuntimeError)
        assert second is first
        assert third is first
        assert '"fetch"' in str(first)
        assert "cannot use the Django ORM" in str(first)
        assert "as a plain def" in str(first)

    def test_a_request_thread_waiting_in_a_drive_gets_the_refusal(self) -> None:
        async def fetch(keys):
            await sync_to_async(threading.get_ident, thread_sensitive=True)()

        loader = Loader(fetch)

        async def work():
            scoped()
            load = partial(outcome, partial(loader.load_sync, 1))
            return await sync_to_async(load, thread_sensitive=True)()

        refused = copy_context().run(drive, work)
        assert isinstance(refused, RuntimeError)
        assert "cannot use the Django ORM" in str(refused)

    def test_threads_it_starts_itself_stay_allowed(self) -> None:
        async def fetch(keys):
            plain = await sync_to_async(threading.get_ident, thread_sensitive=False)()
            pooled = await asyncio.to_thread(threading.get_ident)
            return dict.fromkeys(keys, (plain, pooled))

        loader = Loader(fetch)

        async def scenario():
            scoped()
            found = await asyncio.wait_for(loader.load(1), WAIT)
            return found, threading.get_ident()

        (plain, pooled), loop_thread = asyncio.run(scenario())
        assert loop_thread not in (plain, pooled)

    def test_waiting_on_a_sync_loader_is_refused_and_an_async_one_allowed(self) -> None:
        nested_calls: list[list[object]] = []
        plain = Loader(recording([]))

        async def doubled(keys):
            nested_calls.append(list(keys))
            return {key: key * 2 for key in keys}

        nested = Loader(doubled)

        async def fetch(keys):
            return {key: await nested.load(key) + await plain.load(key) for key in keys}

        loader = Loader(fetch)

        async def scenario():
            scoped()
            segment_runner.set(inline)
            with pytest.raises(RuntimeError, match="cannot use the Django ORM"):
                await asyncio.wait_for(loader.load(1), WAIT)

        asyncio.run(scenario())
        assert nested_calls == [[1]]

    def test_a_load_outside_a_request_refuses_a_hop_as_well(self) -> None:
        async def fetch(keys):
            await sync_to_async(threading.get_ident, thread_sensitive=True)()

        loader = Loader(fetch)
        with pytest.raises(RuntimeError, match="cannot use the Django ORM"):
            loader.load_sync(1)

    def test_the_refusal_reads_as_a_quit_executor_to_asgiref(self) -> None:
        async def fetch(keys):
            return dict.fromkeys(keys)

        refusal = Loader(fetch).refusal
        assert refusal is not None
        with refusal._work_ready:
            assert refusal._broken
        assert refusal._old_executor is None
        assert Loader(recording([])).refusal is None


class TestCycles:
    """A wait that only its own batch call could answer raises instead of hanging."""

    def test_a_sync_batch_loading_a_key_of_its_own_batch_fails_the_batch(self) -> None:
        def fetch(keys):
            return {key: loader.load_sync(keys[-1]) for key in keys}

        loader = Loader(fetch, max_batch=2, window=3600)

        async def scenario():
            scoped()
            segment_runner.set(on_thread)
            return await asyncio.wait_for(
                asyncio.gather(loader.load(1), loader.load(2), return_exceptions=True),
                WAIT,
            )

        first, second = asyncio.run(scenario())
        assert isinstance(first, DependencyCycleError)
        assert second is first
        assert str(first) == "Circular dependency: fetch -> fetch[2]"

    def test_a_sync_batch_may_load_through_another_loader(self) -> None:
        inner = Loader(recording([]))

        def fetch(keys):
            return {key: inner.load_sync(key) + 1 for key in keys}

        loader = Loader(fetch)

        def job() -> object:
            return loader.load_sync(3)

        async def scenario():
            scoped()
            return await asyncio.wait_for(
                asyncio.to_thread(copy_context().run, job), WAIT
            )

        assert asyncio.run(scenario()) == 31

    def test_a_wait_on_a_batch_an_outer_frame_runs_raises(self) -> None:
        """A fresh context lists no batch, so only the thread knows it runs one."""

        def fetch(keys):
            fresh = Context()
            fresh.run(request_memo.set, request_memo.get())
            load = partial(cycle_of, partial(loader.load_sync, keys[0]))
            return dict.fromkeys(keys, fresh.run(load))

        loader = Loader(fetch)

        async def scenario():
            scoped()
            return await asyncio.wait_for(
                asyncio.to_thread(copy_context().run, loader.load_sync, 5), WAIT
            )

        found = asyncio.run(scenario())
        assert isinstance(found, DependencyCycleError)
        assert str(found) == "Circular dependency: fetch -> fetch[5]"

    def test_an_async_batch_awaiting_its_own_key_fails_the_batch(self) -> None:
        async def fetch(keys):
            return {key: await loader.load(keys[-1]) for key in keys}

        loader = Loader(fetch, max_batch=2, window=3600)

        async def scenario():
            scoped()
            return await asyncio.wait_for(
                asyncio.gather(loader.load(1), loader.load(2), return_exceptions=True),
                WAIT,
            )

        first, second = asyncio.run(scenario())
        assert isinstance(first, DependencyCycleError)
        assert second is first
        assert str(first) == "Circular dependency: fetch -> fetch[2]"


class TestRequestContext:
    """`open_loaders` hands the batches of a request the context they run in."""

    def test_batches_run_in_the_adopted_context_not_the_first_loader(self) -> None:
        seen: list[str] = []

        def fetch(keys):
            seen.append(marker.get())
            return dict.fromkeys(keys)

        loader = Loader(fetch)

        async def job(key: int):
            marker.set("job")
            return await loader.load(key)

        async def scenario():
            scope = scoped()
            segment_runner.set(inline)
            await asyncio.wait_for(asyncio.create_task(job(1)), WAIT)
            request = copy_context()
            request.run(marker.set, "request")
            open_loaders(scope, request)
            await asyncio.wait_for(asyncio.create_task(job(2)), WAIT)

        asyncio.run(scenario())
        assert seen == ["job", "request"]

    def test_a_scope_with_no_loop_has_no_batches_to_open(self) -> None:
        scope = RequestScope()
        open_loaders(scope, copy_context())
        assert scope._slots == {}


class TestDrain:
    """`drain_loaders` closes the open batches and returns the sync calls still running."""

    def test_it_returns_the_call_of_a_batch_it_closed_until_that_call_returns(
        self,
    ) -> None:
        started = threading.Event()
        release = threading.Event()

        def fetch(keys):
            started.set()
            release.wait(WAIT)
            return {key: key * 10 for key in keys}

        loader = Loader(fetch, window=3600)

        async def scenario():
            scope = scoped()
            segment_runner.set(on_thread)
            waiter = asyncio.create_task(loader.load(1))
            await next_turn()
            assert len(drain_loaders(scope)) == 1
            assert await asyncio.to_thread(started.wait, WAIT)
            waiter.cancel()
            await scope.close()
            await next_turn()
            running = drain_loaders(scope)
            assert running
            release.set()
            await asyncio.wait_for(
                asyncio.gather(*running, return_exceptions=True), WAIT
            )
            return drain_loaders(scope)

        assert asyncio.run(scenario()) == set()

    def test_a_scope_of_no_loop_or_of_another_loop_drains_nothing(self) -> None:
        async def elsewhere(scope: RequestScope):
            return drain_loaders(scope)

        async def scenario():
            scope = scoped()
            return await asyncio.to_thread(asyncio.run, elsewhere(scope))

        assert asyncio.run(scenario()) == set()
        assert drain_loaders(RequestScope()) == set()


class TestUnscoped:
    """Outside a request each load calls the batch function with its key alone."""

    def test_each_load_calls_the_batch_with_its_key_alone(self) -> None:
        calls: list[list[object]] = []

        async def fetch(keys):
            calls.append(["async", *keys])
            return [key + 1 for key in keys]

        plain = Loader(recording(calls))
        awaiting = Loader(fetch)
        assert plain.load_sync(4) == 40
        assert plain.load_sync(4) == 40
        assert awaiting.load_sync(5) == 6

        async def scenario():
            return await plain.load(6), await awaiting.load(7)

        assert asyncio.run(scenario()) == (60, 8)
        assert calls == [[4], [4], ["async", 5], [6], ["async", 7]]


class TestRows:
    """The built-in row loader reads a batch of primary keys in one query."""

    def test_the_loader_of_a_model_is_shared(self) -> None:
        assert rows(Group) is rows(Group)

    def test_the_row_loaders_batch_through_a_sync_function(self) -> None:
        assert not rows(Group).awaits
        assert not visible_rows(Group).awaits

    @pytest.mark.django_db(transaction=True)
    def test_an_async_consumer_reads_its_rows_on_the_view_thread(self) -> None:
        groups = [Group.objects.create(name=f"view {index}") for index in range(4)]
        idents: list[int] = []

        def where(execute, sql, params, many, context):
            idents.append(threading.get_ident())
            return execute(sql, params, many, context)

        def watch(*, on: bool) -> int:
            if on:
                connection.execute_wrappers.append(where)
            else:
                connection.execute_wrappers.remove(where)
            return threading.get_ident()

        async def scenario():
            async with ThreadSensitiveContext():
                view = await sync_to_async(watch)(on=True)
                scope = scoped()
                try:
                    found = await asyncio.wait_for(
                        asyncio.gather(*(rows(Group).load(g.pk) for g in groups)), WAIT
                    )
                finally:
                    await scope.close()
                    await sync_to_async(watch)(on=False)
            return found, view

        found, view = asyncio.run(scenario())
        assert found == groups
        assert idents == [view]

    @pytest.mark.django_db()
    def test_a_batch_of_keys_is_one_query_and_a_row_is_cached(
        self, django_assert_num_queries
    ) -> None:
        groups = [Group.objects.create(name=f"group {index}") for index in range(5)]
        loader = rows(Group)

        async def scenario():
            scope = scoped()
            found = await asyncio.gather(*(loader.load(group.pk) for group in groups))
            again = await loader.load(str(groups[0].pk))
            await scope.close()
            return found, again

        with django_assert_num_queries(1):
            found, again = copy_context().run(async_to_sync(scenario))
        assert found == groups
        assert again is found[0]

    @pytest.mark.django_db()
    def test_a_key_is_read_as_the_primary_key_field_reads_it(self) -> None:
        group = Group.objects.create(name="text key")
        assert rows(Group).load_sync(str(group.pk)) == group
        with pytest.raises(MissingKeyError):
            rows(Group).load_sync(group.pk + 1)
