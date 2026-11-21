import asyncio
import contextlib
import contextvars
import gc
import inspect
import threading
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from asgiref.local import Local
from django.conf import settings
from django.contrib.auth.models import Permission
from django.http import Http404
from django.utils import translation

import next.pages.graph as graph_module
from next.deps import Depends, DObject
from next.deps.cache import RequestMemo, request_memo
from next.deps.markers import factory_key
from next.deps.resolver import current_resolver
from next.deps.runner import segment_runner
from next.deps.scope import RequestScope
from next.pages import Context, PageContextShapeError
from next.pages.graph import (
    ContextGraph,
    Phase,
    acollect,
    arun_phase,
    blame,
    blaming,
    callable_keys,
    closure,
    compile_graph,
    merged,
    run_phase,
)
from next.pages.registry import PageContextRegistry
from next.pages.scope import render_scope
from next.testing import override_provider
from tests.support import bound_dependency


ROOT = Path("/graph/page.py")
MID = Path("/graph/mid/page.py")
LEAF = Path("/graph/mid/leaf/page.py")

_marker: contextvars.ContextVar[str] = contextvars.ContextVar("graph_marker")
_absent: contextvars.ContextVar[str] = contextvars.ContextVar("graph_absent")

_READS_A = Context("a")
_READS_OWN_NAME = Context()
_READS_THREE = Context(lambda: 3)
_READS_THROUGH_SOURCE = Context(lambda a: a)
_CONSTANT = Context(5)
_FACTORY = Depends(lambda: 1)
_READING_FACTORY = Depends(lambda a: a)
_CONSTANT_DEPENDS = Depends(5)
_JS = object()

pytestmark = pytest.mark.usefixtures("framework_providers")


def _reads_title(title: str) -> str:
    return title


def _reads_post(post: str, title: str = Depends("graph_title")) -> str:
    return post + title


def _loops(value: object = None) -> object:
    return value


_loops.__defaults__ = (Depends(_loops),)


class _Everything:
    """A provider the graph cannot analyse, which may read any key."""

    def static_can_handle(self, param: inspect.Parameter) -> bool | None:
        return param.name == "custom"

    def can_handle(self, param: inspect.Parameter, context: object) -> bool:
        return param.name == "custom"

    def resolve(self, param: inspect.Parameter, context: object) -> object:
        return "custom"


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


def _collect(registry: PageContextRegistry, file_path: Path = LEAF, **kwargs):
    """Run `acollect_context` of `file_path` on a fresh loop."""
    return asyncio.run(registry.acollect_context(file_path, **kwargs))


def _run_graph(graph: ContextGraph, file_path: Path = LEAF, **kwargs):
    """Run `graph` through `acollect` on a fresh loop with empty inputs."""
    options = {
        "request": None,
        "url_kwargs": {},
        "dep_cache": {},
        "requested_zones": None,
        **kwargs,
    }
    return asyncio.run(acollect(graph, file_path, **options))


class TestCompile:
    """The graph links a callable to the earlier ones whose result it reads."""

    def test_a_parameter_named_after_an_earlier_key_is_an_edge(self, registry) -> None:
        registry.register_context(LEAF, "a", lambda: 1)
        registry.register_context(LEAF, "b", lambda a: a)

        graph = registry.graph(LEAF)

        assert graph.needs == (frozenset(), frozenset({0}))
        assert graph.layers == ((0,), (1,))

    def test_a_parameter_named_after_a_later_key_is_no_edge(self, registry) -> None:
        registry.register_context(LEAF, "a", lambda b=None: b)
        registry.register_context(LEAF, "b", lambda: 1)

        assert registry.graph(LEAF).layers == ((0, 1),)

    def test_a_context_marker_naming_a_key_is_an_edge(self, registry) -> None:
        registry.register_context(LEAF, "a", lambda: 1)
        registry.register_context(LEAF, "b", lambda value=_READS_A: value)
        registry.register_context(LEAF, "c", lambda a=_READS_OWN_NAME: a)

        graph = registry.graph(LEAF)

        assert graph.needs[1] == frozenset({0})
        assert graph.needs[2] == frozenset({0})

    def test_a_reserved_or_constant_marker_is_no_edge(self, registry) -> None:
        registry.register_context(LEAF, None, lambda: {"request": 1})
        registry.register_context(LEAF, "a", lambda request=_READS_OWN_NAME: request)
        registry.register_context(LEAF, "b", lambda value=_CONSTANT: value)

        assert registry.graph(LEAF).layers == ((0, 1, 2),)

    def test_a_marker_with_a_callable_source_reads_what_the_source_reads(
        self, registry
    ) -> None:
        registry.register_context(LEAF, "a", lambda: 1)
        registry.register_context(LEAF, "b", lambda: 2)
        registry.register_context(LEAF, "c", lambda value=_READS_THREE: value)
        registry.register_context(LEAF, "d", lambda value=_READS_THROUGH_SOURCE: value)

        graph = registry.graph(LEAF)

        assert graph.needs[2:] == (frozenset(), frozenset({0}))
        assert graph.reads[3] == frozenset()

    def test_parameters_no_context_read_fills_are_no_edge(self, registry) -> None:
        def reader(
            *args,
            request=None,
            a=_FACTORY,
            b=_CONSTANT_DEPENDS,
            row: DObject = None,
            **kwargs,
        ):
            return row

        registry.register_context(LEAF, None, lambda: {"a": 1})
        registry.register_context(LEAF, "b", reader)

        assert registry.graph(LEAF).layers == ((0, 1),)

    def test_a_named_dependency_reads_the_keys_its_parameters_name(
        self, registry
    ) -> None:
        registry.register_context(LEAF, "post", lambda: "p1")
        registry.register_context(LEAF, "title", lambda t=Depends("graph_post"): t)

        with (
            bound_dependency("graph_post", _reads_post),
            bound_dependency("graph_title", _reads_title),
        ):
            graph = registry.graph(LEAF)

        assert graph.needs[1] == frozenset({0})
        assert graph.reads[1] == frozenset()

    def test_a_factory_dependency_reads_the_keys_its_parameters_name(
        self, registry
    ) -> None:
        registry.register_context(LEAF, "a", lambda: 1)
        registry.register_context(LEAF, "b", lambda value=_READING_FACTORY: value)

        assert registry.graph(LEAF).needs[1] == frozenset({0})

    def test_a_dependency_cycle_reads_nothing_twice(self, registry) -> None:
        registry.register_context(LEAF, "a", lambda: 1)
        registry.register_context(LEAF, "b", lambda value=Depends("graph_loop"): value)
        registry.register_context(LEAF, "c", _loops)

        with bound_dependency("graph_loop", lambda graph_loop=Depends(): graph_loop):
            graph = registry.graph(LEAF)

        assert graph.layers == ((0, 1, 2),)

    def test_an_unbound_dependency_reads_nothing(self, registry) -> None:
        registry.register_context(LEAF, "a", lambda: 1)
        registry.register_context(LEAF, "b", lambda value=Depends("graph_none"): value)

        assert registry.graph(LEAF).layers == ((0, 1),)

    def test_an_object_reads_its_parent_key_and_what_its_scope_reads(
        self, registry
    ) -> None:
        def perm(permission: DObject[Permission, Literal["content_type"]]) -> object:
            return permission

        def row(row: DObject[Permission]) -> object:
            return row

        registry.register_context(LEAF, "actor", lambda: None)
        registry.register_context(LEAF, "content_type", lambda: None)
        registry.register_context(LEAF, "perm", perm)
        registry.register_context(LEAF, "row", row)

        with bound_dependency("scope", lambda actor: actor):
            graph = registry.graph(LEAF)

        assert graph.needs[2:] == (frozenset({0, 1}), frozenset({0}))

    def test_unresolved_hints_read_the_raw_annotations(self, registry) -> None:
        def reader(a=None) -> object:
            return a

        reader.__annotations__ = {"a": "Missing"}
        registry.register_context(LEAF, "a", lambda: 1)
        registry.register_context(LEAF, "b", reader)

        assert registry.graph(LEAF).needs[1] == frozenset({0})

    def test_a_provider_the_graph_cannot_analyse_reads_every_earlier_key(
        self, registry
    ) -> None:
        registry.register_context(LEAF, "a", lambda: 1)
        registry.register_context(LEAF, "b", lambda: 2)
        registry.register_context(LEAF, "c", lambda custom=None: custom)

        with override_provider(_Everything()):
            graph = registry.graph(LEAF)

        assert graph.needs[2] == frozenset({0, 1})
        assert registry.graph(LEAF).needs[2] == frozenset()

    def test_a_rebound_dependency_rebuilds_the_graph(self, registry) -> None:
        registry.register_context(LEAF, "a", lambda: 1)
        registry.register_context(LEAF, "b", lambda value=Depends("graph_swap"): value)

        with bound_dependency("graph_swap", lambda: 0):
            first = registry.graph(LEAF)
            assert registry.graph(LEAF) is first
            with bound_dependency("graph_swap", lambda a: a):
                second = registry.graph(LEAF)

        assert first.needs[1] == frozenset()
        assert second.needs[1] == frozenset({0})

    def test_a_callable_after_a_keyless_one_reading_by_name_waits_for_it(
        self, registry
    ) -> None:
        registry.register_context(LEAF, None, lambda: {"spread": 1})
        registry.register_context(LEAF, "free", lambda: 2)
        registry.register_context(LEAF, "reader", lambda anything=None: anything)

        graph = registry.graph(LEAF)

        assert graph.needs == (frozenset(), frozenset(), frozenset({0}))

    def test_a_callable_without_a_signature_reads_nothing(self, registry) -> None:
        registry.register_context(LEAF, None, lambda: {"spread": 1})
        registry.register_context(LEAF, "empty", dict)

        assert registry.graph(LEAF).layers == ((0, 1),)

    def test_an_inherited_key_waits_for_the_keys_before_it(self, registry) -> None:
        registry.register_context(ROOT, "k", lambda: 1, inherit_context=True)
        registry.register_context(MID, None, lambda: {"x": 1}, inherit_context=True)
        registry.register_context(MID, "k", lambda: 2, inherit_context=True)

        graph = registry.graph(LEAF)

        assert [step.group for step in graph.steps] == [0, 1, 1]
        assert graph.needs[2] == frozenset({0, 1})

    def test_two_independent_async_callables_make_the_graph_parallel(
        self, registry
    ) -> None:
        async def one() -> int:
            return 1

        async def two() -> int:
            return 2

        registry.register_context(LEAF, "one", one)
        assert registry.graph(LEAF).parallel is False

        registry.register_context(LEAF, "two", two)
        assert registry.graph(LEAF).parallel is True

    def test_an_async_chain_stays_serial(self, registry) -> None:
        async def one() -> int:
            return 1

        async def two(one: int) -> int:
            return one + 1

        registry.register_context(LEAF, "one", one)
        registry.register_context(LEAF, "two", two)

        assert registry.graph(LEAF).parallel is False

    def test_an_empty_run_order_has_no_layer(self) -> None:
        graph = compile_graph((), frozenset(), current_resolver())

        assert graph.layers == ()
        assert graph.parallel is False

    def test_the_graph_is_built_once_per_registry_version(self, registry) -> None:
        registry.register_context(LEAF, "a", lambda: 1)
        first = registry.graph(LEAF)

        assert registry.graph(LEAF) is first

        registry.register_context(LEAF, "b", lambda: 2)
        assert registry.graph(LEAF) is not first


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

        assert _collect(registry).context_data == {"first": "A", "second": "AB"}

    def test_consecutive_sync_callables_share_one_segment(
        self, registry, inline
    ) -> None:
        async def c_async(b_sync: str) -> str:
            return b_sync + "c"

        registry.register_context(LEAF, "a_sync", lambda: "a")
        registry.register_context(LEAF, "b_sync", lambda a_sync: a_sync + "b")
        registry.register_context(LEAF, "c_async", c_async)
        registry.register_context(LEAF, "d_sync", lambda c_async: c_async + "d")

        result = _collect(registry)

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

        result = _collect(registry)

        assert result.context_data == {"rows": [1, 2], "spread": 1, "total": 2}
        assert result.js_context == {"rows": 0, "spread": 1, "total": 2}
        assert result.js_context_serializers == {
            "rows": _JS,
            "spread": _JS,
            "total": _JS,
        }

    def test_an_inherited_keyless_async_callable_must_return_a_mapping(
        self, registry, inline
    ) -> None:
        async def spread() -> list[int]:
            return [1]

        registry.register_context(ROOT, None, spread, inherit_context=True)

        with pytest.raises(PageContextShapeError):
            _collect(registry)

    def test_an_inherited_key_already_taken_skips_its_async_callable(
        self, registry, inline
    ) -> None:
        called: list[str] = []

        async def again() -> str:
            called.append("again")
            return "mid"

        registry.register_context(ROOT, "k", lambda: "root", inherit_context=True)
        registry.register_context(MID, "k", again, inherit_context=True)

        assert _collect(registry).context_data == {"k": "root"}
        assert called == []

    def test_an_inherited_key_already_taken_skips_its_sync_callable(
        self, registry, inline
    ) -> None:
        async def first() -> str:
            return "root"

        registry.register_context(ROOT, "k", first, inherit_context=True)
        registry.register_context(MID, "k", lambda: "mid", inherit_context=True)

        assert _collect(registry).context_data == {"k": "root"}

    def test_a_zone_get_skips_the_callables_of_other_zones(
        self, registry, inline
    ) -> None:
        async def boxed() -> str:
            return "box"

        registry.register_context(LEAF, "boxed", boxed, zone="box")
        registry.register_context(LEAF, "other", lambda: "other", zone="other")

        result = _collect(registry, requested_zones=frozenset({"box"}))

        assert result.context_data == {"boxed": "box"}

    def test_a_keyless_async_callable_must_return_a_mapping(
        self, registry, inline
    ) -> None:
        async def spread() -> list[int]:
            return [1]

        registry.register_context(LEAF, None, spread)

        with pytest.raises(PageContextShapeError):
            _collect(registry)

    def test_url_kwargs_reach_both_colours(self, registry, inline) -> None:
        async def first(slug: str) -> str:
            return slug

        registry.register_context(LEAF, "first", first)
        registry.register_context(LEAF, "second", lambda slug: slug + "!")

        result = _collect(registry, url_kwargs={"slug": "post"})

        assert result.context_data == {"first": "post", "second": "post!"}

    def test_a_sync_write_reaches_a_later_callable_and_the_caller(
        self, registry
    ) -> None:
        def activate() -> int:
            translation.activate("fr")
            return 1

        async def reader(activate: int) -> str:
            return translation.get_language()

        registry.register_context(LEAF, "activate", activate)
        registry.register_context(LEAF, "reader", reader)

        async def main() -> tuple[str, str]:
            translation.activate("en")
            result = await registry.acollect_context(LEAF)
            return result.context_data["reader"], translation.get_language()

        assert asyncio.run(main()) == ("fr", "fr")


def _two_async(registry: PageContextRegistry, one, two) -> None:
    registry.register_context(LEAF, "one", one)
    registry.register_context(LEAF, "two", two)


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

        _two_async(registry, one, two)

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

        _two_async(registry, one, two)
        registry.register_context(LEAF, "three", three)

        assert _collect(registry).context_data == {"one": 1, "three": True, "two": 2}

    def test_each_callable_gets_a_stack_of_its_own(
        self, registry, inline, monkeypatch
    ) -> None:
        stacks: list[list[str]] = []
        original = graph_module._Run.context

        def recording(self, view):
            context = original(self, view)
            stacks.append(context.stack)
            return context

        async def one() -> int:
            return 1

        async def two() -> int:
            return 2

        _two_async(registry, one, two)
        monkeypatch.setattr(graph_module._Run, "context", recording)
        _collect(registry)

        assert len({id(stack) for stack in stacks}) == len(stacks) == 2

    def test_the_merge_equals_the_sequential_run(self, registry, inline) -> None:
        async def one() -> dict[str, int]:
            await asyncio.sleep(0.002)
            return {"one": 1, "shared": 1}

        async def two() -> str:
            return "two"

        registry.register_context(LEAF, None, one, serialize=True)
        registry.register_context(LEAF, "shared", two, serialize=True)
        registry.register_context(LEAF, "three", lambda: 3)
        graph = registry.graph(LEAF)

        parallel = _run_graph(graph)
        serial = _run_graph(replace(graph, parallel=False))

        assert graph.parallel is True
        assert parallel == serial
        assert list(parallel.context_data) == list(serial.context_data)

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

        parallel = _run_graph(replace(graph, parallel=True), requested_zones=zones)
        serial = _run_graph(replace(graph, parallel=False), requested_zones=zones)

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
        parallel = _run_graph(replace(registry.graph(LEAF), parallel=True))

        assert parallel == expected
        assert list(parallel.context_data) == list(expected.context_data)

    def test_writes_restore_in_declaration_order(self, registry, inline) -> None:
        async def one() -> int:
            await asyncio.sleep(0.002)
            _marker.set("one")
            return 1

        async def two() -> int:
            _marker.set("two")
            _absent.set("two")
            return 2

        _two_async(registry, one, two)

        async def main() -> tuple[str, str]:
            _marker.set("start")
            await registry.acollect_context(LEAF)
            return _marker.get(), _absent.get()

        assert asyncio.run(main()) == ("two", "two")

    def test_a_layout_language_reaches_the_caller(self, registry) -> None:
        async def one() -> int:
            return 1

        async def two() -> int:
            return 2

        def language() -> int:
            translation.activate("de")
            return 3

        _two_async(registry, one, two)
        registry.register_context(LEAF, "language", language)

        async def main() -> str:
            translation.activate("en")
            await registry.acollect_context(LEAF)
            return translation.get_language()

        assert asyncio.run(main()) == "de"

    def test_a_callable_sees_the_writes_of_its_dependency(
        self, registry, inline
    ) -> None:
        async def a_one() -> int:
            _marker.set("written")
            return 1

        async def a_two() -> int:
            return 2

        async def b_reader(a_one: int) -> str:
            return _marker.get("unset")

        registry.register_context(LEAF, "a_one", a_one)
        registry.register_context(LEAF, "a_two", a_two)
        registry.register_context(LEAF, "b_reader", b_reader)

        assert _collect(registry).context_data["b_reader"] == "written"

    def test_an_equal_value_keeps_the_caller_object(self, registry, inline) -> None:
        original = "".join(["x"] * 40)

        async def one() -> int:
            _marker.set("".join(["x"] * 40))
            return 1

        async def two() -> int:
            return 2

        _two_async(registry, one, two)

        async def main() -> bool:
            _marker.set(original)
            await registry.acollect_context(LEAF)
            return _marker.get() is original

        assert asyncio.run(main()) is True

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
            _collect(registry)
        assert cancelled == ["b"]

    def test_http404_keeps_its_type(self, registry, inline) -> None:
        from_view = Http404("missing")

        async def one() -> int:
            raise from_view

        async def two() -> int:
            return 2

        _two_async(registry, one, two)

        with pytest.raises(Http404) as caught:
            _collect(registry)
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
            _collect(registry)
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
            _collect(registry)

    def test_a_keyless_result_of_a_layer_must_be_a_mapping(
        self, registry, inline
    ) -> None:
        async def spread() -> list[int]:
            return [1]

        async def two() -> int:
            return 2

        registry.register_context(LEAF, None, spread)
        registry.register_context(LEAF, "two", two)

        with pytest.raises(PageContextShapeError):
            _collect(registry)

    def test_a_lane_stops_before_a_callable_past_the_failure(self, registry) -> None:
        registry.register_context(LEAF, "late", lambda: 1)
        run = graph_module._Run(registry.graph(LEAF), LEAF, None, {}, {}, None)
        done: list[tuple[object, dict]] = []

        run._lane(((0, {}),), done, [-1], run.base)

        assert done == []

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

        result = _collect(registry, requested_zones=frozenset({"box"}))

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

        _two_async(registry, one, two)

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

        _two_async(registry, one, two)
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

        _two_async(registry, one, two)

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
        _two_async(registry, a_slow, b_slow)

        with pytest.raises(PageContextShapeError):
            _collect(registry)
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

        _two_async(registry, one, two)

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

    def test_a_local_keeps_the_attribute_a_later_override_restored(
        self, registry
    ) -> None:
        async def a_layout() -> int:
            await asyncio.sleep(0.002)
            translation.activate("de")
            return 1

        async def b_preview() -> int:
            with translation.override("fr"):
                pass
            return 2

        _two_async(registry, a_layout, b_preview)

        async def main() -> str:
            translation.activate("en")
            await registry.acollect_context(LEAF)
            return translation.get_language()

        assert asyncio.run(main()) == "de"

    def test_a_local_drops_the_attribute_a_callable_deleted(self, registry) -> None:
        async def one() -> int:
            translation.deactivate()
            return 1

        async def two() -> int:
            return 2

        _two_async(registry, one, two)

        async def main() -> str:
            translation.activate("de")
            await registry.acollect_context(LEAF)
            return translation.get_language()

        assert asyncio.run(main()) == settings.LANGUAGE_CODE

    def test_a_local_first_set_by_a_callable_reaches_the_caller(self, registry) -> None:
        local = Local()

        async def one() -> int:
            local.value = "set"
            return 1

        async def two() -> int:
            return 2

        _two_async(registry, one, two)

        async def main() -> str:
            await registry.acollect_context(LEAF)
            return local.value

        assert asyncio.run(main()) == "set"


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


def _fails(message: str) -> Callable[[], str]:
    def fail() -> str:
        raise ValueError(message)

    return fail


def _fails_async(message: str) -> Callable[[], Awaitable[str]]:
    async def fail() -> str:
        raise ValueError(message)

    return fail


class TestBlame:
    """A failed async collection names the inherited group the failure came from."""

    @pytest.mark.parametrize(("group", "level"), [(-1, -1), (0, 3), (1, 1), (2, -1)])
    def test_the_level_of_the_failed_group_reads_its_directory(
        self, group: int, level: int
    ) -> None:
        with blaming() as blamed:
            blame(group)

        assert blamed.level((3, 1)) == level

    def test_a_blame_outside_a_watch_is_dropped(self) -> None:
        blame(3)

        with blaming() as blamed:
            assert blamed.group == -1

    def test_a_failing_sync_step_of_a_serial_run_is_blamed(
        self, registry, inline
    ) -> None:
        async def own() -> str:
            return "own"

        registry.register_context(ROOT, "nav", _fails("nav"), inherit_context=True)
        registry.register_context(LEAF, "own", own)

        with blaming() as blamed, pytest.raises(ValueError, match="nav"):
            _collect(registry)

        assert blamed.group == 0

    def test_a_failing_async_step_of_a_serial_run_is_blamed(
        self, registry, inline
    ) -> None:
        registry.register_context(MID, "nav", _fails_async("nav"), inherit_context=True)

        with blaming() as blamed, pytest.raises(ValueError, match="nav"):
            _collect(registry)

        assert blamed.group == 0

    def test_the_earliest_failure_of_a_parallel_run_is_blamed(
        self, registry, inline
    ) -> None:
        async def fine() -> str:
            return "fine"

        registry.register_context(LEAF, "a_fail", _fails_async("a"))
        registry.register_context(LEAF, "b_fine", fine)

        with blaming() as blamed, pytest.raises(ValueError, match="a"):
            _collect(registry)

        assert registry.graph(LEAF).parallel
        assert blamed.group == -1


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
        registry.register_context(MID, "nav", _fails("nav"), inherit_context=True)
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

    def test_the_results_merge_in_run_order(self, registry) -> None:
        registry.register_context(MID, "nav", lambda: "mid", inherit_context=True)
        registry.register_context(LEAF, "nav", lambda: "own")
        graph = registry.graph(LEAF)

        result = merged(graph, LEAF, {1: "own", 0: "mid"})

        assert result.context_data == {"nav": "own"}

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
