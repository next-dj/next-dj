import asyncio
import gc
import threading
import traceback
from collections.abc import Generator
from contextlib import contextmanager
from types import TracebackType
from typing import NoReturn

import pytest

from next.deps import DependencyCycleError
from next.deps.cache import _CACHE_MISS, DependencyCache, request_memo
from next.deps.scope import RequestScope, join_waits, render_scope, running_loop
from tests.support.errors import BackendDownError


class Counted:
    """A compute factory that counts its calls and can hold until a gate opens."""

    def __init__(self, value: object, gate: asyncio.Event | None = None) -> None:
        """Hold the value to return and the gate the computation waits on."""
        self.value = value
        self.gate = gate
        self.calls = 0
        self.started = asyncio.Event()

    async def __call__(self) -> object:
        self.calls += 1
        self.started.set()
        if self.gate is not None:
            await self.gate.wait()
        return self.value


async def never() -> object:
    pytest.fail("a memoised key was computed again")


def depth(trace: TracebackType | None) -> int:
    return len(list(traceback.walk_tb(trace)))


def lookup_user() -> NoReturn:
    raise KeyError


def handle_unrelated() -> NoReturn:
    raise OSError


def loop_errors() -> list[dict[str, object]]:
    """Collect what the loop would log, such as an exception never retrieved."""
    seen: list[dict[str, object]] = []
    asyncio.get_running_loop().set_exception_handler(
        lambda _loop, context: seen.append(context)
    )
    return seen


def test_an_uncontended_key_makes_no_waiter_future() -> None:
    """A lone claimer awaits its task, and a later read is a plain dict hit."""

    async def scenario() -> None:
        scope = RequestScope()
        futures: list[object] = []

        async def user() -> object:
            futures.append(scope._pending["user"])
            return "ann"

        assert await scope.resolve("user", user) == "ann"
        assert await scope.resolve("user", never) == "ann"
        assert futures == [None]
        assert scope._values == {"user": "ann"}
        assert not scope._pending
        assert not scope._tasks

    asyncio.run(scenario())


def test_an_eagerly_finished_claim_reads_its_task_at_once() -> None:
    """Under an eager task factory a claim that never suspends is done when awaited."""

    async def scenario() -> None:
        asyncio.get_running_loop().set_task_factory(asyncio.eager_task_factory)
        scope = RequestScope()
        compute = Counted("ann")
        assert await scope.resolve("user", compute) == "ann"
        assert await scope.resolve("user", never) == "ann"
        assert compute.calls == 1

    asyncio.run(scenario())


def test_a_contended_key_computes_once() -> None:
    async def scenario() -> None:
        scope = RequestScope()
        compute = Counted("ann", asyncio.Event())
        waiters = [asyncio.create_task(scope.resolve("user", compute)) for _ in "abc"]
        await compute.started.wait()
        assert scope._pending["user"] is not None
        assert compute.gate is not None
        compute.gate.set()

        assert await asyncio.gather(*waiters) == ["ann", "ann", "ann"]
        assert compute.calls == 1

    asyncio.run(scenario())


def test_cancelling_a_waiter_leaves_the_computation_to_the_others() -> None:
    """Neither the claimer nor a joiner owns the task, so cancelling them spares it."""

    async def scenario() -> None:
        scope = RequestScope()
        gate = asyncio.Event()
        compute = Counted("ann", gate)
        claimer, joiner, survivor = (
            asyncio.create_task(scope.resolve("user", compute)) for _ in "abc"
        )
        await compute.started.wait()
        claimer.cancel()
        joiner.cancel()
        gate.set()

        assert await survivor == "ann"
        for cancelled in (claimer, joiner):
            with pytest.raises(asyncio.CancelledError):
                await cancelled
        assert compute.calls == 1

    asyncio.run(scenario())


def test_a_computation_outlives_its_last_waiter_for_later_readers() -> None:
    """The scope outlives one boundary, so the value is kept for whoever reads next."""

    async def scenario() -> None:
        scope = RequestScope()
        gate = asyncio.Event()
        compute = Counted("ann", gate)
        waiters = [asyncio.create_task(scope.resolve("user", compute)) for _ in "ab"]
        await compute.started.wait()
        (task,) = scope._tasks
        for waiter in waiters:
            waiter.cancel()
        outcomes = await asyncio.gather(*waiters, return_exceptions=True)
        assert all(isinstance(o, asyncio.CancelledError) for o in outcomes)
        assert not task.done()

        gate.set()
        await task
        assert scope.get("user") == "ann"
        assert await scope.resolve("user", never) == "ann"
        assert compute.calls == 1

    asyncio.run(scenario())


def test_a_failure_is_memoised_for_every_current_and_later_reader() -> None:
    async def scenario() -> None:
        scope = RequestScope()
        gate = asyncio.Event()
        started = asyncio.Event()
        error = BackendDownError("db down")
        calls = 0

        async def failing() -> object:
            nonlocal calls
            calls += 1
            started.set()
            await gate.wait()
            raise error

        waiters = [asyncio.create_task(scope.resolve("user", failing)) for _ in "ab"]
        await started.wait()
        gate.set()

        outcomes = await asyncio.gather(*waiters, return_exceptions=True)
        assert outcomes == [error, error]
        assert all(outcome is error for outcome in outcomes)
        with pytest.raises(BackendDownError) as caught:
            await scope.resolve("user", never)
        assert caught.value is error
        with pytest.raises(BackendDownError):
            scope.get("user")
        assert calls == 1

    asyncio.run(scenario())


def test_a_memoised_failure_keeps_its_traceback_and_context_on_every_read() -> None:
    """Every read raises the same object, so it must not pile frames or chain a cause."""

    async def scenario() -> None:
        scope = RequestScope()
        seen = loop_errors()

        async def failing() -> object:
            try:
                lookup_user()
            except KeyError:
                raise BackendDownError from None

        def read_in_handler() -> None:
            try:
                handle_unrelated()
            except OSError:
                scope.get("user")

        waiters = [asyncio.create_task(scope.resolve("user", failing)) for _ in "ab"]
        outcomes = await asyncio.gather(*waiters, return_exceptions=True)
        error = outcomes[0]
        assert isinstance(error, BackendDownError)
        origin = error.__context__
        assert isinstance(origin, KeyError)

        depths = set()
        for _ in range(10):
            with pytest.raises(BackendDownError) as caught:
                read_in_handler()
            assert caught.value is error
            assert caught.value.__context__ is origin
            assert caught.value.__suppress_context__
            depths.add(depth(caught.value.__traceback__))
        assert len(depths) == 1

        depths.clear()
        for _ in range(10):
            try:
                handle_unrelated()
            except OSError:
                with pytest.raises(BackendDownError) as caught:
                    await scope.resolve("user", never)
            assert caught.value.__context__ is origin
            depths.add(depth(caught.value.__traceback__))
        assert len(depths) == 1
        assert not seen

    asyncio.run(scenario())


def test_a_cancelled_computation_is_not_memoised_and_recomputes() -> None:
    async def scenario() -> None:
        scope = RequestScope()
        gate = asyncio.Event()
        started = asyncio.Event()
        attempts = 0

        async def flaky() -> object:
            nonlocal attempts
            attempts += 1
            started.set()
            await gate.wait()
            if attempts == 1:
                raise asyncio.CancelledError
            return "ann"

        waiters = [asyncio.create_task(scope.resolve("user", flaky)) for _ in "ab"]
        await started.wait()
        gate.set()

        outcomes = await asyncio.gather(*waiters, return_exceptions=True)
        assert all(isinstance(o, asyncio.CancelledError) for o in outcomes)
        assert scope.get("user") is _CACHE_MISS
        assert await scope.resolve("user", flaky) == "ann"
        assert attempts == 2

    asyncio.run(scenario())


def test_an_unshared_key_is_computed_in_place_with_no_bookkeeping() -> None:
    async def scenario() -> None:
        scope = RequestScope({"theme": "dark"})
        compute = Counted("ann")

        assert await scope.resolve("user", compute, shared=False) == "ann"
        assert await scope.resolve("user", compute, shared=False) == "ann"
        assert compute.calls == 2
        assert scope.get("user") is _CACHE_MISS
        assert not scope._pending
        assert not scope._tasks
        assert await scope.resolve("theme", never, shared=False) == "dark"

    asyncio.run(scenario())


def test_an_unshared_miss_runs_in_the_callers_task() -> None:
    async def scenario() -> None:
        scope = RequestScope()

        async def task_of_compute() -> object:
            return asyncio.current_task()

        resolved = await scope.resolve("task", task_of_compute, shared=False)
        assert resolved is asyncio.current_task()

    asyncio.run(scenario())


def test_a_key_waiting_on_itself_is_a_cycle() -> None:
    async def scenario() -> None:
        scope = RequestScope()

        async def itself() -> object:
            return await scope.resolve("loop", itself)

        with pytest.raises(DependencyCycleError) as caught:
            await scope.resolve("loop", itself)
        assert caught.value.cycle == ["loop", "loop"]
        with pytest.raises(DependencyCycleError):
            scope.get("loop")

    asyncio.run(scenario())


@pytest.mark.parametrize("keys", [("a", "b"), ("a", "b", "c")], ids=["two", "three"])
def test_a_cross_task_cycle_raises_in_every_chain(keys: tuple[str, ...]) -> None:
    """Whichever chain waits last finds the cycle, and the error reaches the rest."""

    async def scenario() -> None:
        scope = RequestScope()
        barrier = asyncio.Barrier(len(keys))
        following = dict(zip(keys, (*keys[1:], keys[0]), strict=True))

        def computing(key: str):
            async def compute() -> object:
                await barrier.wait()
                return await scope.resolve(following[key], computers[following[key]])

            return compute

        computers = {key: computing(key) for key in keys}
        outcomes = await asyncio.gather(
            *(scope.resolve(key, computers[key]) for key in keys),
            return_exceptions=True,
        )

        error = outcomes[0]
        assert isinstance(error, DependencyCycleError)
        assert all(outcome is error for outcome in outcomes)
        assert error.cycle[0] == error.cycle[-1]
        assert sorted(error.cycle[1:]) == sorted(keys)
        for key in keys:
            with pytest.raises(DependencyCycleError):
                scope.get(key)
        assert not scope._chains

    asyncio.run(scenario())


def test_a_key_joined_after_its_cycle_raised_gets_the_memoised_error() -> None:
    """A reader that joins before the failed task settles waits for the same error."""

    async def scenario() -> None:
        scope = RequestScope()
        resume = asyncio.Event()
        entered = asyncio.Event()
        window: dict[str, object] = {}

        async def first() -> object:
            return await scope.resolve("second", second)

        async def second() -> object:
            entered.set()
            await resume.wait()
            return await scope.resolve("first", first)

        async def outsider() -> object:
            await resume.wait()
            window["done"] = sorted(task.done() for task in scope._tasks)
            window["unsettled"] = "second" in scope._pending
            return await scope.resolve("second", never)

        claimer = asyncio.create_task(scope.resolve("first", first))
        await entered.wait()
        joiner = asyncio.create_task(outsider())
        await asyncio.sleep(0)
        resume.set()

        error, joined = await asyncio.gather(claimer, joiner, return_exceptions=True)
        assert window == {"done": [False, True], "unsettled": True}
        assert isinstance(error, DependencyCycleError)
        assert joined is error
        assert not scope._pending
        assert not scope._chains

    asyncio.run(scenario())


def test_a_diamond_shares_its_base_without_a_false_cycle() -> None:
    """Two paths into one base are visited once, and the walk finds no cycle."""

    async def scenario() -> None:
        scope = RequestScope()
        gate = asyncio.Event()
        base = Counted(1, gate)

        async def left() -> object:
            return ("left", await scope.resolve("base", base))

        async def right() -> object:
            return ("right", await scope.resolve("base", base))

        async def top() -> object:
            return await asyncio.gather(
                scope.resolve("left", left), scope.resolve("right", right)
            )

        async def probe() -> object:
            gate.set()
            return await scope.resolve("top", top)

        waiter = asyncio.create_task(scope.resolve("top", top))
        await base.started.wait()
        assert scope._chains == {
            "top": ["left", "right"],
            "left": ["base"],
            "right": ["base"],
        }

        expected = [("left", 1), ("right", 1)]
        assert await scope.resolve("probe", probe) == expected
        assert await waiter == expected
        assert base.calls == 1
        assert not scope._chains

    asyncio.run(scenario())


def test_another_thread_reads_without_blocking_or_writing() -> None:
    """A pool thread gets a miss for a pending key and computes it privately."""

    async def scenario() -> None:
        scope = RequestScope({"theme": "dark"})
        compute = Counted("ann", asyncio.Event())
        waiter = asyncio.create_task(scope.resolve("user", compute))
        await compute.started.wait()

        assert await asyncio.to_thread(scope.get, "user") is _CACHE_MISS
        assert scope._pending == {"user": None}
        assert await asyncio.to_thread(scope.get, "theme") == "dark"

        assert compute.gate is not None
        compute.gate.set()
        await waiter
        assert await asyncio.to_thread(scope.get, "user") == "ann"
        snapshot = await asyncio.to_thread(scope.snapshot)
        assert snapshot == {"theme": "dark", "user": "ann"}
        assert DependencyCache(snapshot).get("user") == "ann"

        async def failing() -> object:
            raise BackendDownError

        with pytest.raises(BackendDownError):
            await scope.resolve("broken", failing)
        with pytest.raises(BackendDownError):
            await asyncio.to_thread(scope.get, "broken")

    asyncio.run(scenario())


def test_a_scope_seeded_from_the_shell_cache_never_writes_back() -> None:
    async def scenario() -> None:
        backing: dict[str, object] = {"user": "ann"}
        shell = DependencyCache(backing)
        scope = RequestScope(backing)

        assert await scope.resolve("user", never) == "ann"
        assert await scope.resolve("theme", Counted("dark")) == "dark"
        assert "theme" not in shell
        backing["late"] = 1
        assert scope.get("late") is _CACHE_MISS

    asyncio.run(scenario())


def test_a_key_computed_through_a_layer_never_reaches_the_scope() -> None:
    """A component job computes per instance, and the request never sees it."""

    async def scenario() -> None:
        scope = RequestScope({"user": "ann"})
        layer = scope.layer()

        async def failing() -> object:
            raise BackendDownError

        assert await layer.resolve("card", Counted("card")) == "card"
        with pytest.raises(BackendDownError):
            await layer.resolve("broken", failing)

        assert layer._values == {"card": "card"}
        assert scope._values == {"user": "ann"}
        assert not scope._errors
        assert scope.get("card") is _CACHE_MISS
        assert scope.get("broken") is _CACHE_MISS
        with pytest.raises(BackendDownError):
            layer.get("broken")

        await layer.close()
        assert scope.get("user") == "ann"
        assert await scope.resolve("theme", Counted("dark")) == "dark"
        assert await scope.layer().resolve("card", Counted("card")) == "card"
        with pytest.raises(RuntimeError, match="closed"):
            await layer.resolve("late", Counted("late"))

    asyncio.run(scenario())


def test_a_layer_reads_the_finished_values_of_its_scope() -> None:
    """The layer is a view, so a value the scope finishes later reads through too."""

    async def scenario() -> None:
        scope = RequestScope({"user": "ann"})
        layer = scope.layer()

        async def failing() -> object:
            raise BackendDownError

        assert await layer.resolve("user", never) == "ann"
        assert await scope.resolve("theme", Counted("dark")) == "dark"
        assert await layer.resolve("theme", never) == "dark"
        with pytest.raises(BackendDownError):
            await scope.resolve("broken", failing)
        with pytest.raises(BackendDownError):
            await layer.resolve("broken", never)
        assert not layer._values
        assert layer.snapshot() == {"user": "ann", "theme": "dark"}

    asyncio.run(scenario())


def test_a_layer_awaits_a_key_pending_in_its_scope() -> None:
    async def scenario() -> None:
        scope = RequestScope()
        gate = asyncio.Event()
        rows = Counted("rows", gate)
        waiter = asyncio.create_task(scope.resolve("rows", rows))
        await rows.started.wait()
        layer = scope.layer()

        async def card() -> object:
            gate.set()
            return ("card", await layer.resolve("rows", never))

        assert await layer.resolve("card", card) == ("card", "rows")
        assert await waiter == "rows"
        assert rows.calls == 1
        assert scope._values == {"rows": "rows"}
        assert layer._values == {"card": ("card", "rows")}
        assert not layer._pending
        assert not layer._tasks

    asyncio.run(scenario())


def test_close_cancels_running_computations_and_drops_every_map() -> None:
    async def scenario() -> None:
        scope = RequestScope({"theme": "dark"})
        gate = asyncio.Event()
        solo = Counted("solo", gate)
        pair = Counted("pair", gate)
        waiters = [
            asyncio.create_task(scope.resolve("solo", solo)),
            asyncio.create_task(scope.resolve("pair", pair)),
            asyncio.create_task(scope.resolve("pair", pair)),
        ]
        await solo.started.wait()
        await pair.started.wait()
        tasks = set(scope._tasks)
        assert scope._pending["pair"] is not None

        await scope.close()

        assert len(tasks) == 2
        assert all(task.cancelled() for task in tasks)
        outcomes = await asyncio.gather(*waiters, return_exceptions=True)
        assert all(isinstance(o, asyncio.CancelledError) for o in outcomes)
        assert not scope._tasks
        assert not scope._pending
        assert not scope._values
        assert not scope._errors
        assert scope.get("theme") is _CACHE_MISS

    asyncio.run(scenario())


def test_close_during_a_claim_cancels_every_waiter_and_memoises_nothing() -> None:
    async def scenario() -> None:
        scope = RequestScope()
        seen = loop_errors()
        compute = Counted("ann", asyncio.Event())
        claimer = asyncio.create_task(scope.resolve("user", compute))
        await compute.started.wait()
        layer = scope.layer()
        joiners = [
            asyncio.create_task(scope.resolve("user", never)),
            asyncio.create_task(layer.resolve("user", never)),
        ]
        await asyncio.sleep(0)
        assert scope._pending["user"] is not None

        await scope.close()

        outcomes = await asyncio.gather(claimer, *joiners, return_exceptions=True)
        assert all(isinstance(o, asyncio.CancelledError) for o in outcomes)
        assert compute.calls == 1
        assert scope.get("user") is _CACHE_MISS
        assert layer.get("user") is _CACHE_MISS
        assert not scope._errors
        assert not scope._pending
        gc.collect()
        await asyncio.sleep(0)
        assert not seen

    asyncio.run(scenario(), debug=True)


def test_a_cancelled_joiner_leaves_no_orphan_future_behind() -> None:
    """The lazily made waiter future still settles once every joiner is gone."""

    async def scenario(*, fails: bool) -> None:
        scope = RequestScope()
        seen = loop_errors()
        gate = asyncio.Event()
        started = asyncio.Event()

        async def compute() -> object:
            started.set()
            await gate.wait()
            if fails:
                raise BackendDownError
            return "ann"

        claimer = asyncio.create_task(scope.resolve("user", compute))
        await started.wait()
        (task,) = scope._tasks
        unstarted = asyncio.create_task(scope.resolve("user", never))
        unstarted.cancel()
        joiner = asyncio.create_task(scope.resolve("user", never))
        await asyncio.sleep(0)
        future = scope._pending["user"]
        assert future is not None
        joiner.cancel()
        claimer.cancel()
        gate.set()

        outcomes = await asyncio.gather(
            claimer, unstarted, joiner, return_exceptions=True
        )
        assert all(isinstance(o, asyncio.CancelledError) for o in outcomes)
        await asyncio.gather(task, return_exceptions=True)
        assert future.done()
        assert not future.cancelled()
        assert not scope._pending
        assert ("user" in scope._errors) is fails
        gc.collect()
        await asyncio.sleep(0)
        assert not seen

    for fails in (False, True):
        asyncio.run(scenario(fails=fails), debug=True)


def test_close_cancels_the_layers_before_the_scope() -> None:
    async def scenario() -> None:
        scope = RequestScope()
        layer = scope.layer()
        nested = layer.layer()
        gate = asyncio.Event()
        cancelled: list[str] = []

        def holding(name: str):
            async def compute() -> object:
                try:
                    await gate.wait()
                except asyncio.CancelledError:
                    cancelled.append(name)
                    raise
                return name

            return compute

        waiters = [
            asyncio.create_task(owner.resolve(name, holding(name)))
            for owner, name in ((scope, "scope"), (layer, "layer"), (nested, "nested"))
        ]
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        await scope.close()

        assert cancelled == ["nested", "layer", "scope"]
        outcomes = await asyncio.gather(*waiters, return_exceptions=True)
        assert all(isinstance(o, asyncio.CancelledError) for o in outcomes)
        assert not layer._tasks
        assert scope._layers is None
        with pytest.raises(RuntimeError, match="closed"):
            await layer.resolve("late", never)
        late = scope.layer()
        with pytest.raises(RuntimeError, match="closed"):
            await late.resolve("late", never)
        with pytest.raises(RuntimeError, match="closed"):
            await late.resolve("late", never, shared=False)
        assert scope._layers is None

    asyncio.run(scenario())


def test_a_layer_opened_off_the_loop_closes_with_its_scope() -> None:
    """A component render opens its layer on the rendering thread, not the loop."""
    scope = RequestScope()
    opened: list[RequestScope] = []
    closed: list[str] = []

    def render() -> None:
        layer = scope.layer()
        layer.on_close(lambda: closed.append("layer"))
        opened.append(layer)

    thread = threading.Thread(target=render)
    thread.start()
    thread.join()
    (layer,) = opened
    assert scope._layers == {layer}

    asyncio.run(scope.close())

    assert closed == ["layer"]
    assert scope._layers is None


def test_a_layer_that_owns_nothing_is_never_held() -> None:
    """A render that claims nothing leaves no trace on the tree it reads."""
    scope = RequestScope({"user": "ann"})
    layer = scope.layer()
    nested = layer.layer()

    async def reads() -> object:
        return await nested.resolve("user", never)

    assert asyncio.run(reads()) == "ann"
    assert (scope._layers, layer._layers, scope._used) == (None, None, False)
    assert scope.close_unused() is True


def test_a_claim_in_a_nested_layer_holds_it_and_every_layer_above() -> None:
    async def scenario() -> None:
        scope = RequestScope()
        layer = scope.layer()
        nested = layer.layer()
        sibling = layer.layer()
        await nested.resolve("card", Counted("card"))
        await sibling.resolve("row", Counted("row"))
        assert scope._layers == {layer}
        assert layer._layers == {nested, sibling}
        assert scope.close_unused() is False
        await scope.close()

    asyncio.run(scenario())


def test_close_twice_is_safe_and_a_closed_scope_computes_nothing() -> None:
    async def scenario() -> None:
        scope = RequestScope({"user": "ann"})
        await scope.close()
        await scope.close()

        with pytest.raises(RuntimeError, match="closed"):
            await scope.resolve("user", never)
        with pytest.raises(RuntimeError, match="closed"):
            await scope.resolve("user", never, shared=False)

    asyncio.run(scenario())


def test_the_loop_is_the_one_the_scope_was_built_on() -> None:
    async def scenario() -> tuple[RequestScope, asyncio.AbstractEventLoop]:
        scope = RequestScope()
        return scope, asyncio.get_running_loop()

    scope, loop = asyncio.run(scenario())
    assert scope.loop is loop
    assert scope.layer().loop is loop
    assert RequestScope().loop is None


def test_a_scope_built_off_the_loop_takes_the_loop_it_is_given() -> None:
    loop = asyncio.new_event_loop()
    try:
        scope = RequestScope({"seed": 1}, loop=loop)
        resolved = loop.run_until_complete(scope.resolve("seed", never))
    finally:
        loop.close()
    assert scope.loop is loop
    assert scope.layer().loop is loop
    assert resolved == 1


@pytest.mark.parametrize("reader", ["root", "layer"])
def test_a_scope_built_off_the_loop_adopts_the_loop_of_its_first_resolve(
    reader: str,
) -> None:
    scope = RequestScope()
    layer = scope.layer()
    first = layer if reader == "layer" else scope

    async def scenario() -> asyncio.AbstractEventLoop:
        await first.resolve("user", Counted("ann"))
        return asyncio.get_running_loop()

    loop = asyncio.run(scenario())
    assert scope.loop is loop
    assert layer.loop is loop


def test_a_slot_is_made_once_on_the_root_and_shared_by_its_layers() -> None:
    scope = RequestScope()
    layer = scope.layer()
    made: list[RequestScope] = []

    def make(root: RequestScope) -> object:
        made.append(root)
        return object()

    held = layer.slot("hub", make)
    assert scope.slot("hub", make) is held
    assert scope.layer().slot("hub", make) is held
    assert made == [scope]


def test_a_slot_another_thread_filled_meanwhile_is_not_made_again() -> None:
    """The check under the lock finds what a racing thread stored after the first."""
    late = object()

    class _FilledMeanwhile(dict):
        def get(self, key: str, default: object = None) -> object:
            reads.append(key)
            return default if len(reads) == 1 else late

    reads: list[str] = []
    scope = RequestScope()
    scope._tree.slots = _FilledMeanwhile()

    assert scope.slot("hub", lambda _root: pytest.fail("made twice")) is late
    assert reads == ["hub", "hub"]


def test_close_callbacks_run_once_before_any_computation_is_cancelled() -> None:
    async def scenario() -> None:
        scope = RequestScope()
        gate = asyncio.Event()
        compute = Counted("ann", gate)
        waiter = asyncio.create_task(scope.resolve("user", compute))
        await compute.started.wait()
        seen: list[bool] = []
        scope.on_close(lambda: seen.append(next(iter(scope._tasks)).cancelled()))
        await scope.close()
        await scope.close()
        assert seen == [False]
        with pytest.raises(asyncio.CancelledError):
            await waiter

    asyncio.run(scenario())


def test_a_callback_registered_on_a_closed_scope_runs_at_once() -> None:
    async def scenario() -> None:
        scope = RequestScope()
        layer = scope.layer()
        seen: list[str] = []
        layer.on_close(lambda: seen.append("layer"))
        layer.on_close(lambda: seen.append("again"))
        await scope.close()
        assert seen == ["layer", "again"]
        scope.on_close(lambda: seen.append("late"))
        assert seen == ["layer", "again", "late"]

    asyncio.run(scenario())


def test_a_joiner_waits_inside_the_join_hook_until_it_ends_or_is_cancelled() -> None:
    entered: list[tuple[str, RequestScope]] = []

    @contextmanager
    def hook(root: RequestScope) -> Generator[None]:
        entered.append(("in", root))
        try:
            yield
        finally:
            entered.append(("out", root))

    async def scenario() -> None:
        scope = RequestScope()
        gate = asyncio.Event()
        compute = Counted("ann", gate)
        claimant = asyncio.create_task(scope.resolve("user", compute))
        await compute.started.wait()
        token = join_waits.set(hook)
        try:
            joiner = asyncio.create_task(scope.layer().resolve("user", never))
            cancelled = asyncio.create_task(scope.resolve("user", never))
        finally:
            join_waits.reset(token)
        await asyncio.sleep(0)
        assert entered == [("in", scope)] * 2
        cancelled.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cancelled
        assert entered == [("in", scope)] * 2 + [("out", scope)]
        gate.set()
        assert await claimant == "ann"
        assert await joiner == "ann"
        assert entered == [("in", scope)] * 2 + [("out", scope)] * 2

    asyncio.run(scenario())


class TestCloseUnused:
    """A scope its loop never touched closes on the calling thread."""

    def test_an_untouched_scope_closes_at_once(self) -> None:
        scope = RequestScope({"seed": 1})
        assert scope.close_unused() is True
        assert scope._tree.closed is True
        assert scope.get("seed") is _CACHE_MISS

    def test_a_claimed_scope_is_left_to_its_loop(self) -> None:
        scope = RequestScope()

        async def compute() -> object:
            return 1

        async def claim() -> object:
            return await scope.resolve("k", compute)

        assert asyncio.run(claim()) == 1
        assert scope.close_unused() is False
        assert scope._tree.closed is False

    def test_a_scope_with_a_close_callback_or_a_layer_is_left_to_its_loop(self) -> None:
        hooked = RequestScope()
        hooked.on_close(lambda: None)
        layered = RequestScope()
        layered.layer().on_close(lambda: None)
        assert (hooked.close_unused(), layered.close_unused()) == (False, False)

    def test_an_untouched_layer_closes_alone(self) -> None:
        scope = RequestScope()
        layer = scope.layer()
        seen: list[str] = []
        assert layer.close_unused() is True
        layer.on_close(lambda: seen.append("late"))
        assert seen == ["late"]
        assert _reuse(scope) == 42
        with pytest.raises(RuntimeError, match="closed"):
            _reuse(layer)


async def _answer() -> int:
    return 42


def _reuse(scope: RequestScope) -> object:
    """Ask `scope` for a key it never held, which a closed scope refuses."""

    async def main() -> object:
        return await scope.resolve("key", _answer)

    return asyncio.run(main())


class TestRenderScope:
    """One async render opens a request scope seeded from the shell cache."""

    def test_the_scope_is_the_memo_of_the_render_and_closes_after(self) -> None:
        async def main() -> tuple[object, RequestScope, object, object]:
            async with render_scope({"user": "ada"}) as scope:
                memo = request_memo.get()
                seeded = scope.get("user")
            return memo.scope, scope, seeded, request_memo.get()

        memo_scope, scope, seeded, after = asyncio.run(main())

        assert memo_scope is scope
        assert seeded == "ada"
        assert after is None
        with pytest.raises(RuntimeError, match="closed"):
            _reuse(scope)

    def test_the_scope_closes_when_the_render_fails(self) -> None:
        scopes: list[RequestScope] = []

        async def main() -> None:
            async with render_scope({}) as scope:
                scopes.append(scope)
                msg = "render failed"
                raise ValueError(msg)

        with pytest.raises(ValueError, match="render failed"):
            asyncio.run(main())
        with pytest.raises(RuntimeError, match="closed"):
            _reuse(scopes[0])


def test_running_loop_names_the_loop_of_the_calling_thread() -> None:
    async def inside() -> asyncio.AbstractEventLoop | None:
        return running_loop()

    assert running_loop() is None
    assert asyncio.run(inside()) is not None
