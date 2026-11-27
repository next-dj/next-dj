import asyncio
import contextlib
import gc
import inspect
import threading
from collections.abc import Callable
from dataclasses import replace

import pytest
from django.http import Http404

from next.deps import Depends
from next.deps.cache import RequestMemo, request_memo
from next.deps.context import ResolutionContext
from next.deps.markers import factory_key
from next.deps.resolver import current_resolver
from next.deps.scope import RequestScope, render_scope
from next.pages import PageContextShapeError
from next.pages.context import ContextResult
from next.pages.graph import (
    ContextGraph,
    Phase,
    arun_phase,
    blaming,
    callable_keys,
    closure,
    run as graph_module,
    run_phase,
)
from next.pages.registry import PageContextRegistry
from tests.support import bound_dependency
from tests.support.graphs import (
    LEAF,
    MID,
    ROOT,
    collect,
    failing,
    register_two,
    run_graph,
)


_READING_FACTORY = Depends(lambda a: a)
_JS = object()

pytestmark = pytest.mark.usefixtures("framework_providers")


def _loops(value: object = None) -> object:
    return value


_loops.__defaults__ = (Depends(_loops),)


class _Picks:
    """A seeded linear congruential stream, the same draws on every run."""

    def __init__(self, seed: int) -> None:
        """Start the stream at `seed`."""
        self._state = seed + 1

    def below(self, bound: int) -> int:
        """Return the next draw in `range(bound)`."""
        self._state = (self._state * 1103515245 + 12345) % 2**31
        return self._state % bound

    def chance(self, percent: int) -> bool:
        """Return True for `percent` draws in a hundred."""
        return self.below(100) < percent

    def choice[T](self, options: list[T]) -> T:
        """Return one of `options`."""
        return options[self.below(len(options))]


class TestSerial:
    """A graph with no layer of two async callables runs in order with plain awaits."""

    def test_the_serial_path_creates_no_task(
        self, registry, inline, monkeypatch
    ) -> None:
        async def first() -> str:
            return "A"

        async def second(first: str) -> str:
            return first + "B"

        def boom(*args, **kwargs):
            raise AssertionError

        registry.register_context(LEAF, "first", first)
        registry.register_context(LEAF, "second", second)
        monkeypatch.setattr(asyncio, "create_task", boom)

        assert collect(registry).context_data == {"first": "A", "second": "AB"}

    def test_consecutive_sync_callables_share_one_segment(
        self, registry, inline
    ) -> None:
        async def c_async(b_sync: str) -> str:
            return b_sync + "c"

        registry.register_context(LEAF, "a_sync", lambda: "a")
        registry.register_context(LEAF, "b_sync", lambda a_sync: a_sync + "b")
        registry.register_context(LEAF, "c_async", c_async)
        registry.register_context(LEAF, "d_sync", lambda c_async: c_async + "d")

        result = collect(registry)

        assert result.context_data["d_sync"] == "abcd"
        assert inline.calls == 2

    def test_an_async_callable_matches_the_sync_merge(self, registry, inline) -> None:
        async def rows() -> list[int]:
            return [1, 2]

        async def total() -> int:
            return 2

        registry.register_context(
            LEAF, None, lambda: {"rows": 0, "spread": 1}, serialize=True, serializer=_JS
        )
        registry.register_context(LEAF, "rows", rows, serialize=True)
        registry.register_context(LEAF, "total", total, serialize=True, serializer=_JS)

        result = collect(registry)

        assert result.context_data == {"rows": [1, 2], "spread": 1, "total": 2}
        assert result.js_context == {"rows": 0, "spread": 1, "total": 2}
        assert result.js_context_serializers == {
            "rows": _JS,
            "spread": _JS,
            "total": _JS,
        }

    def test_a_zone_get_skips_the_callables_of_other_zones(
        self, registry, inline
    ) -> None:
        async def boxed() -> str:
            return "box"

        registry.register_context(LEAF, "boxed", boxed, zone="box")
        registry.register_context(LEAF, "other", lambda: "other", zone="other")

        result = collect(registry, requested_zones=frozenset({"box"}))

        assert result.context_data == {"boxed": "box"}

    def test_url_kwargs_reach_both_colours(self, registry, inline) -> None:
        async def first(slug: str) -> str:
            return slug

        registry.register_context(LEAF, "first", first)
        registry.register_context(LEAF, "second", lambda slug: slug + "!")

        result = collect(registry, url_kwargs={"slug": "post"})

        assert result.context_data == {"first": "post", "second": "post!"}


class TestParallel:
    """Independent async callables of one layer overlap and merge in run order."""

    def test_async_callables_of_a_layer_overlap(self, registry) -> None:
        events: dict[str, asyncio.Event] = {}

        async def one() -> int:
            events["one"].set()
            await asyncio.wait_for(events["two"].wait(), 1)
            return 1

        async def two() -> int:
            events["two"].set()
            await asyncio.wait_for(events["one"].wait(), 1)
            return 2

        register_two(registry, one, two)

        async def main():
            events["one"] = asyncio.Event()
            events["two"] = asyncio.Event()
            return await registry.acollect_context(LEAF)

        assert asyncio.run(main()).context_data == {"one": 1, "two": 2}

    def test_a_sync_callable_overlaps_the_async_ones(self, registry) -> None:
        reached = threading.Event()

        async def one() -> int:
            reached.set()
            return 1

        async def two() -> int:
            return 2

        def three() -> bool:
            return reached.wait(1)

        register_two(registry, one, two)
        registry.register_context(LEAF, "three", three)

        assert collect(registry).context_data == {"one": 1, "three": True, "two": 2}

    def test_each_callable_gets_a_stack_of_its_own(
        self, registry, inline, monkeypatch
    ) -> None:
        stacks: list[list[str]] = []

        class Recording(ResolutionContext):
            __slots__ = ()

            def __init__(self, *args, **kwargs) -> None:
                super().__init__(*args, **kwargs)
                stacks.append(self.stack)

        async def one() -> int:
            return 1

        async def two() -> int:
            return 2

        register_two(registry, one, two)
        monkeypatch.setattr(graph_module, "ResolutionContext", Recording)
        collect(registry)

        assert len({id(stack) for stack in stacks}) == len(stacks) == 3

    @pytest.mark.parametrize("seed", range(25))
    def test_random_graphs_merge_as_the_sequential_run(self, inline, seed) -> None:
        registry = PageContextRegistry()
        picks = _Picks(seed)
        for file_path, inherit in ((ROOT, True), (MID, True), (LEAF, False)):
            for position in range(1 + picks.below(4)):
                key = picks.choice(["k1", "k2", "k3", "k4", None])
                reads = [name for name in ("k1", "k2", "k3") if picks.chance(30)]
                func = _random_callable(
                    f"f{file_path.parent.name}{position}",
                    key,
                    reads,
                    is_async=picks.chance(60),
                    delay=picks.below(10) / 10000,
                )
                inherited = inherit and picks.chance(70)
                registry.register_context(
                    file_path,
                    key,
                    func,
                    inherit_context=inherited,
                    serialize=picks.chance(50),
                    zone=None if inherited else picks.choice([None, "z1", "z2"]),
                )
        zones = picks.choice([None, frozenset({"z1"})])
        graph = registry.graph(LEAF)

        parallel = run_graph(replace(graph, parallel=True), requested_zones=zones)
        serial = run_graph(replace(graph, parallel=False), requested_zones=zones)

        assert parallel == serial
        assert list(parallel.context_data) == list(serial.context_data)

    def test_sync_only_graphs_merge_as_the_sync_collector(self, inline) -> None:
        registry = PageContextRegistry()
        registry.register_context(ROOT, "k", lambda: "root", inherit_context=True)
        registry.register_context(
            MID, None, lambda: {"k": "mid", "x": 1}, inherit_context=True
        )
        registry.register_context(MID, "k", lambda: "late", inherit_context=True)
        registry.register_context(MID, "y", lambda k: k + "y", inherit_context=True)
        registry.register_context(LEAF, None, lambda: {"y": "own", "z": 1})
        registry.register_context(LEAF, "w", lambda k, y: k + y, serialize=True)

        expected = registry.collect_context(LEAF)
        parallel = run_graph(replace(registry.graph(LEAF), parallel=True))

        assert parallel == expected
        assert list(parallel.context_data) == list(expected.context_data)

    def test_the_earliest_failure_wins_and_later_siblings_are_cancelled(
        self, registry, inline
    ) -> None:
        cancelled: list[str] = []

        async def a_late() -> int:
            await asyncio.sleep(0.01)
            msg = "a"
            raise LookupError(msg)

        async def b_slow() -> int:
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                cancelled.append("b")
                raise
            return 1

        async def c_fast() -> int:
            msg = "c"
            raise ValueError(msg)

        registry.register_context(LEAF, "a_late", a_late)
        registry.register_context(LEAF, "b_slow", b_slow)
        registry.register_context(LEAF, "c_fast", c_fast)

        with pytest.raises(LookupError, match="a"):
            collect(registry)
        assert cancelled == ["b"]

    def test_http404_keeps_its_type(self, registry, inline) -> None:
        from_view = Http404("missing")

        async def one() -> int:
            raise from_view

        async def two() -> int:
            return 2

        register_two(registry, one, two)

        with pytest.raises(Http404) as caught:
            collect(registry)
        assert caught.value is from_view

    def test_callables_after_a_failure_never_start(self, registry, inline) -> None:
        started: list[str] = []

        async def a_fail() -> int:
            msg = "a"
            raise ValueError(msg)

        async def b_free() -> int:
            return 2

        async def c_later(b_free: int) -> int:
            started.append("c")
            return b_free

        registry.register_context(LEAF, "a_fail", a_fail)
        registry.register_context(LEAF, "b_free", b_free)
        registry.register_context(LEAF, "c_later", c_later)

        with pytest.raises(ValueError, match="a"):
            collect(registry)
        assert started == []

    def test_a_failing_sync_callable_is_raised_at_its_place(
        self, registry, inline
    ) -> None:
        async def a_one() -> int:
            await asyncio.sleep(0.01)
            return 1

        async def a_two() -> int:
            return 2

        def b_fails() -> int:
            msg = "sync"
            raise KeyError(msg)

        registry.register_context(LEAF, "a_one", a_one)
        registry.register_context(LEAF, "a_two", a_two)
        registry.register_context(LEAF, "b_fails", b_fails)

        with pytest.raises(KeyError, match="sync"):
            collect(registry)

    def test_a_lane_stops_before_a_callable_past_the_failure(self, registry) -> None:
        registry.register_context(LEAF, "late", lambda: 1)
        base = ResolutionContext.for_page(None, {}, {})
        run = graph_module._Run(registry.graph(LEAF), LEAF, base)
        lane = graph_module._Lane(((0, {}),), -1, ())

        run._lane(lane, lane.steps, run.base)

        assert lane.done == []

    def test_skipped_callables_stay_out_of_a_parallel_merge(
        self, registry, inline
    ) -> None:
        called: list[str] = []

        async def again() -> str:
            called.append("again")
            return "mid"

        async def one() -> int:
            return 1

        async def two() -> int:
            return 2

        registry.register_context(ROOT, "k", lambda: "root", inherit_context=True)
        registry.register_context(MID, "k", again, inherit_context=True)
        registry.register_context(LEAF, "one", one)
        registry.register_context(LEAF, "two", two)
        registry.register_context(LEAF, "zoned", lambda: 3, zone="other")

        result = collect(registry, requested_zones=frozenset({"box"}))

        assert result.context_data == {"k": "root", "one": 1, "two": 2}
        assert called == []

    def test_a_parallel_run_shares_a_dependency_two_callables_name(
        self, registry, inline
    ) -> None:
        calls: list[int] = []

        async def user() -> object:
            calls.append(1)
            await asyncio.sleep(0.002)
            return object()

        async def one(value=Depends("graph_user")) -> object:
            return value

        async def two(value=Depends("graph_user")) -> object:
            return value

        register_two(registry, one, two)

        async def main() -> tuple[object, object]:
            async with render_scope({}):
                memo = request_memo.get()
                result = await registry.acollect_context(LEAF)
                return result.context_data, request_memo.get() is memo

        with bound_dependency("graph_user", user):
            shared = registry.graph(LEAF).shared
            context_data, same_memo = asyncio.run(main())

        assert shared == frozenset({"graph_user"})
        assert context_data["one"] is context_data["two"]
        assert calls == [1]
        assert same_memo is True

    def test_a_memo_already_sharing_the_keys_is_kept(self, registry, inline) -> None:
        async def one(value=Depends("graph_user")) -> object:
            return request_memo.get()

        async def two(value=Depends("graph_user")) -> object:
            return value

        register_two(registry, one, two)
        memo = RequestMemo(RequestScope(), shared=frozenset({"graph_user"}))

        async def main(held: RequestMemo | None) -> object:
            token = request_memo.set(held)
            try:
                return (await registry.acollect_context(LEAF)).context_data["one"]
            finally:
                request_memo.reset(token)

        with bound_dependency("graph_user", lambda: 1):
            assert asyncio.run(main(memo)) is memo
            assert asyncio.run(main(None)) is None

    def test_a_factory_two_callables_name_runs_once(self, registry, inline) -> None:
        """The factory yields to the loop, so the second callable meets it pending."""
        calls: list[int] = []

        async def fresh() -> list[str]:
            calls.append(1)
            await asyncio.sleep(0)
            return []

        async def one(bucket=Depends(fresh)) -> list[str]:
            bucket.append("one")
            return bucket

        async def two(bucket=Depends(fresh)) -> list[str]:
            bucket.append("two")
            return bucket

        register_two(registry, one, two)

        async def main() -> dict[str, object]:
            async with render_scope({}):
                return (await registry.acollect_context(LEAF)).context_data

        assert registry.graph(LEAF).shared == frozenset({factory_key(fresh)})
        context_data = asyncio.run(main())
        assert context_data["one"] is context_data["two"]
        assert sorted(context_data["one"]) == ["one", "two"]
        assert calls == [1]

    @pytest.mark.parametrize("colour", ["async", "sync"])
    def test_a_keyless_shape_error_cancels_the_later_siblings(
        self, registry, colour
    ) -> None:
        cancelled: list[str] = []

        async def spread_async() -> list[int]:
            return [1]

        def spread_sync() -> list[int]:
            return [1]

        async def a_slow() -> int:
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                cancelled.append("a")
                raise
            return 1

        async def b_slow() -> int:
            await asyncio.sleep(1)
            return 2

        spread = spread_async if colour == "async" else spread_sync
        registry.register_context(LEAF, None, spread)
        register_two(registry, a_slow, b_slow)

        with pytest.raises(
            PageContextShapeError,
            match=r"keyless `@context` spread_\w+ returned a non-mapping",
        ):
            collect(registry)
        assert cancelled == ["a"]

    def test_a_failure_the_run_abandons_is_retrieved(self, registry) -> None:
        handled: list[dict[str, object]] = []

        async def one() -> int:
            try:
                await asyncio.sleep(1)
            except asyncio.CancelledError:
                msg = "cleanup failed"
                raise RuntimeError(msg) from None
            return 1

        async def two() -> int:
            await asyncio.sleep(1)
            return 2

        register_two(registry, one, two)

        async def main() -> None:
            loop = asyncio.get_running_loop()
            loop.set_exception_handler(lambda _loop, context: handled.append(context))
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(0.01):
                    await registry.acollect_context(LEAF)
            gc.collect()
            await asyncio.sleep(0)

        asyncio.run(main())

        assert handled == []


def _random_callable(
    name: str, key: str | None, reads: list[str], *, is_async: bool, delay: float
) -> Callable[..., object]:
    """Return a callable named `name` reading `reads` by name, of either colour."""

    def value(arguments: dict[str, object]) -> object:
        if key is None:
            return {"x1": name, "k2": name + "k2", f"from_{name}": arguments}
        return (name, arguments)

    async def async_body(**arguments: object) -> object:
        await asyncio.sleep(delay)
        return value(arguments)

    def sync_body(**arguments: object) -> object:
        return value(arguments)

    body: Callable[..., object] = async_body if is_async else sync_body
    body.__name__ = name
    body.__signature__ = inspect.Signature(
        [
            inspect.Parameter(read, inspect.Parameter.KEYWORD_ONLY, default=None)
            for read in reads
        ]
    )
    return body


def _phase(
    registry: PageContextRegistry,
    selected: set[int],
    prior: dict[int, object] | None = None,
    timings: dict[int, float] | None = None,
) -> tuple[ContextGraph, Phase]:
    graph = registry.graph(LEAF)
    return graph, Phase(frozenset(selected), prior or {}, timings)


def _arun(graph: ContextGraph, phase: Phase) -> dict[int, object]:
    return asyncio.run(
        arun_phase(graph, LEAF, phase, request=None, url_kwargs={}, dep_cache={})
    )


class TestCollect:
    """The sync collection of a render, from no step at all to inherited ones."""

    def test_a_page_without_callables_gets_fresh_mappings(self, registry) -> None:
        first = registry.collect_context(LEAF)
        second = registry.collect_context(LEAF)

        assert first == ContextResult({}, {}, {})
        assert first.context_data is not second.context_data
        assert first.js_context is not second.js_context
        assert first.js_context_serializers is not second.js_context_serializers

    def test_each_step_reads_what_the_steps_before_it_merged(self, registry) -> None:
        registry.register_context(ROOT, "nav", lambda: "root", inherit_context=True)
        registry.register_context(
            MID, "crumb", lambda nav: f"{nav}/mid", inherit_context=True
        )
        registry.register_context(LEAF, "a", lambda crumb: f"{crumb}/leaf")
        registry.register_context(LEAF, "b", lambda a: f"{a}!")

        result = registry.collect_context(LEAF)

        assert result.context_data == {
            "nav": "root",
            "crumb": "root/mid",
            "a": "root/mid/leaf",
            "b": "root/mid/leaf!",
        }


class TestPhases:
    """A gated render runs its steps in parts, merged as one run merges them."""

    def test_a_phase_runs_only_its_steps_over_the_prior_results(self, registry) -> None:
        registry.register_context(LEAF, "a", lambda: 1)
        registry.register_context(LEAF, "b", lambda a: a + 1)
        graph, phase = _phase(registry, {1}, {0: 10})

        taken = run_phase(graph, LEAF, phase, request=None, url_kwargs={}, dep_cache={})

        assert taken == {1: 11}

    def test_a_step_outside_the_phase_is_left_for_a_later_one(self, registry) -> None:
        registry.register_context(LEAF, "a", lambda: 1)
        registry.register_context(LEAF, "b", lambda: 2)
        graph, phase = _phase(registry, {0})

        taken = run_phase(graph, LEAF, phase, request=None, url_kwargs={}, dep_cache={})

        assert taken == {0: 1}

    def test_an_inherited_key_an_outer_group_took_is_skipped(self, registry) -> None:
        registry.register_context(ROOT, "nav", lambda: "root", inherit_context=True)
        registry.register_context(MID, "nav", lambda: "mid", inherit_context=True)
        graph, phase = _phase(registry, {0, 1})

        taken = run_phase(graph, LEAF, phase, request=None, url_kwargs={}, dep_cache={})

        assert taken == {0: "root"}

    def test_an_inherited_result_settles_for_the_blame(self, registry) -> None:
        registry.register_context(MID, "nav", lambda: "mid", inherit_context=True)
        graph, phase = _phase(registry, {0})

        with blaming() as blamed:
            run_phase(graph, LEAF, phase, request=None, url_kwargs={}, dep_cache={})

        assert blamed.settled == [(0, "nav", "mid")]

    def test_a_failure_blames_its_group(self, registry) -> None:
        registry.register_context(MID, "nav", failing("nav"), inherit_context=True)
        graph, phase = _phase(registry, {0})

        with blaming() as blamed, pytest.raises(ValueError, match="nav"):
            run_phase(graph, LEAF, phase, request=None, url_kwargs={}, dep_cache={})

        assert blamed.group == 0

    def test_timings_record_each_step(self, registry) -> None:
        registry.register_context(LEAF, "a", lambda: 1)
        graph, phase = _phase(registry, {0}, timings={})

        run_phase(graph, LEAF, phase, request=None, url_kwargs={}, dep_cache={})

        assert phase.timings is not None
        assert set(phase.timings) == {0}

    def test_a_serial_async_phase_merges_the_prior_results(
        self, registry, inline
    ) -> None:
        async def later(a: int) -> int:
            return a * 2

        registry.register_context(LEAF, "a", lambda: 1)
        registry.register_context(LEAF, "b", lambda: 2)
        registry.register_context(LEAF, "c", later)
        graph, phase = _phase(registry, {1, 2}, {0: 5}, timings={})

        taken = _arun(graph, phase)

        assert taken == {1: 2, 2: 10}
        assert phase.timings is not None
        assert set(phase.timings) == {1, 2}

    def test_a_serial_async_phase_runs_its_sync_steps_before_a_later_prior_result(
        self, registry, inline
    ) -> None:
        async def later(a: int, b: int) -> int:
            return a + b

        registry.register_context(LEAF, "a", lambda: 1)
        registry.register_context(LEAF, "b", lambda: 2)
        registry.register_context(LEAF, "c", later)
        graph, phase = _phase(registry, {0, 2}, {1: 5})

        taken = _arun(graph, phase)

        assert not graph.parallel
        assert taken == {0: 1, 2: 6}

    def test_a_parallel_async_phase_skips_the_prior_results(
        self, registry, inline
    ) -> None:
        async def one() -> int:
            return 1

        async def two() -> int:
            return 2

        async def three() -> int:
            return 3

        registry.register_context(LEAF, "one", one)
        registry.register_context(LEAF, "three", three)
        registry.register_context(LEAF, "two", two)
        graph, phase = _phase(registry, {1, 2}, {0: 100}, timings={})

        taken = _arun(graph, phase)

        assert graph.parallel
        assert taken == {1: 3, 2: 2}

    def test_a_phase_leaves_out_a_step_it_did_not_select(
        self, registry, inline
    ) -> None:
        async def one() -> int:
            return 1

        registry.register_context(LEAF, "one", one)
        registry.register_context(LEAF, "two", lambda: 2)
        graph, phase = _phase(registry, {0})

        assert _arun(graph, phase) == {0: 1}

    def test_the_closure_follows_every_read(self, registry) -> None:
        registry.register_context(LEAF, "a", lambda: 1)
        registry.register_context(LEAF, "b", lambda a: a)
        registry.register_context(LEAF, "c", lambda b: b)
        registry.register_context(LEAF, "d", lambda: 4)
        graph = registry.graph(LEAF)

        assert closure(graph, frozenset({2})) == frozenset({0, 1, 2})

    def test_the_closure_takes_a_shared_read_once(self, registry) -> None:
        registry.register_context(LEAF, "a", lambda: 1)
        registry.register_context(LEAF, "b", lambda a: a)
        registry.register_context(LEAF, "c", lambda a, b: a + b)
        graph = registry.graph(LEAF)

        assert closure(graph, frozenset({2})) == frozenset({0, 1, 2})

    def test_the_keys_a_callable_reads_include_its_dependencies(self) -> None:
        def reads(a: int, b: int = _READING_FACTORY) -> int:
            return a + b

        assert callable_keys(reads, current_resolver()) == frozenset({"a"})
