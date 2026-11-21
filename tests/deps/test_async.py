import asyncio
import importlib
import logging
import threading
from collections.abc import Awaitable, Iterator
from dataclasses import dataclass

import pytest
from django.http import Http404

from next.caches import BoundedCache
from next.deps import (
    DependencyCycleError,
    DependencyResolver,
    Depends,
    RegisteredParameterProvider,
    UnknownDependencyError,
)
from next.deps.cache import DependencyCache, RequestMemo, request_memo
from next.deps.errors import AsyncDependencyError
from next.deps.introspect import introspect_key
from next.deps.markers import DependsProvider, factory_key
from next.deps.plan import Awaited, Blocking, Colour, empty_async_plan
from next.deps.runner import bridge, segment_runner
from next.deps.scope import RequestScope
from next.errors import AsyncCallableError
from next.testing import make_resolution_context
from tests.support import inspect_parameter


_resolver_module = importlib.import_module("next.deps.resolver")

PURE = Colour.PURE
BLOCKING = Colour.BLOCKING
ASYNC = Colour.ASYNC


class InlineRunner:
    """Segment runner that runs each segment on the loop thread and counts its calls."""

    def __init__(self) -> None:
        """Start with no call counted."""
        self.calls = 0

    def __call__(self, segment) -> Awaitable[object]:
        self.calls += 1
        return self._run(segment)

    async def _run(self, segment):
        return segment()


class HoldingRunner:
    """Segment runner that never returns, running the segment first when asked."""

    def __init__(self, *, run_first: bool) -> None:
        """Note whether the segment runs before the runner hangs."""
        self.run_first = run_first
        self.entered = asyncio.Event()

    def __call__(self, segment) -> Awaitable[None]:
        return self._hold(segment)

    async def _hold(self, segment):
        if self.run_first:
            segment()
        self.entered.set()
        await asyncio.Event().wait()


def _logged(caplog) -> list[BaseException]:
    """Return the errors the resolver logged."""
    return [
        record.exc_info[1]
        for record in caplog.records
        if record.name == "next.deps.resolver" and record.exc_info
    ]


def _drive(runner, make, memo=None):
    """Run the coroutine `make()` builds on a fresh loop with `runner` installed."""

    async def main():
        segment_runner.set(runner)
        request_memo.set(memo)
        return await make()

    return asyncio.run(main())


class RecordingScope:
    """Request scope stand-in that computes every key and notes how it was asked."""

    def __init__(self) -> None:
        """Start with no key asked for."""
        self.calls: list[tuple[str, bool]] = []

    async def resolve(self, key, compute, *, shared=True):
        self.calls.append((key, shared))
        return await compute()


class _Ticket:
    """Awaitable that is no coroutine."""

    def __await__(self) -> Iterator[None]:
        return iter(())


class Claiming:
    """Provider claiming one name for good, logging every sync fill and its thread."""

    def __init__(self, name, value=None, *, blocking=True, log=None) -> None:
        """Claim `name`, answering `value` or the upper-cased name."""
        self.name = name
        self.value = name.upper() if value is None else value
        self.blocking = blocking
        self.log = [] if log is None else log
        self.threads: list[int] = []

    def can_handle(self, param, context) -> bool:
        return param.name == self.name

    def static_can_handle(self, param) -> bool | None:
        return param.name == self.name

    def resolve(self, param, context) -> object:
        self.log.append(self.name)
        self.threads.append(threading.get_ident())
        return self.value


class Deferring(Claiming):
    """Provider leaving its name to `can_handle`, so it stays a candidate entry."""

    def __init__(self, name, value=None, *, blocking=True, claims=True) -> None:
        """Defer on `name`, claiming it at resolve time only when `claims` holds."""
        super().__init__(name, value, blocking=blocking)
        self.claims = claims

    def can_handle(self, param, context) -> bool:
        return self.claims and param.name == self.name

    def static_can_handle(self, param) -> bool | None:
        return None if param.name == self.name else False


class Awaiting(Claiming):
    """Provider offering an async filler for its name."""

    def __init__(self, name, fill=None, *, log=None) -> None:
        """Offer `fill`, or one answering the upper-cased name."""
        super().__init__(name, log=log)
        self.fill = fill if fill is not None else _returning(self.value, name, log)

    def compile_aresolve(self, param):
        return self.fill


class PerParam(Claiming):
    """Provider whose fillers differ in cost from one parameter to the next."""

    def static_can_handle(self, param) -> bool | None:
        return param.name in {"cheap", "dear"}

    def static_blocking(self, param) -> bool:
        return param.name == "dear"


class Inert(Claiming):
    """Provider carrying an async hook attribute that is not callable."""

    compile_aresolve = None


class Broken(Claiming):
    """Provider whose async hook hands back something no one can await."""

    def compile_aresolve(self, param):
        return "nope"


def _returning(value, name=None, log=None):
    async def fill(context):
        if log is not None:
            log.append(name)
        return value

    return fill


def _mixed(pure, slow, fast, missing=7, *rest):
    return None


def _grouped(b1, p, b2, a1, a2, b3):
    return None


def _one(x):
    return x


def _costed(cheap, dear):
    return None


def _pair(a1, a2):
    return None


def _single(a1):
    return a1


async def _atarget(slow, p) -> str:
    return f"{slow}-{p}"


def _sync_blocking(b1, b2) -> str:
    return f"{b1}+{b2}"


def _sync_split(b1, a1, b3) -> str:
    return f"{b1}{a1}{b3}"


def _sync_after_await(a1) -> str:
    return f"after-{a1}"


def _nothing() -> int:
    return 3


async def _agen(p):
    yield p


def _hands_coroutine(b1):
    return _afactory(b1)


def _unresolvable(value=None) -> None:
    return None


_unresolvable.__annotations__["value"] = "NoSuchTypeAtAll"


@dataclass
class _UnhashableHandler:
    tag: str = "x"

    def __call__(self, plain: int = 3) -> int:
        return plain


def _both(a=Depends("a"), b=Depends("b")) -> str:
    return a + b


def _lost(x=Depends("missing")) -> object:
    return x


def _reader(feed=Depends()) -> object:
    return feed


def _digest_reader(digest=Depends("digest")) -> object:
    return digest


def _cycle_a(b=Depends("b")) -> object:
    return b


def _cycle_b(a=Depends("a")) -> object:
    return a


def _cycle_root(a=Depends("a")) -> object:
    return a


async def _afactory(p) -> str:
    return f"made-{p}"


def _sfactory() -> str:
    return "plain"


def _factories(made=Depends(_afactory), plain=Depends(_sfactory), n=Depends(5)):
    return None


def _left(x=Depends("x")) -> object:
    return x


def _right(x=Depends("x")) -> object:
    return x


def _diamond(left=Depends("left"), right=Depends("right")) -> object:
    return (left, right)


def _over_mid(mid=Depends("mid")) -> object:
    return mid


def _mid(y=Depends("y")) -> object:
    return y


def _twice(a=Depends("x"), b=Depends("x")) -> object:
    return (a, b)


def _pair_reader(pair=Depends("pair")) -> object:
    return pair


def _broken_a(b=Depends("b")) -> object:
    return b


def _depends_resolver(*providers) -> DependencyResolver:
    instance = DependencyResolver(*providers)
    instance.prepend_provider(DependsProvider(instance))
    return instance


class TestAsyncPlanCompile:
    """Each parameter is classified once, at the first `aresolve`."""

    def test_entries_are_pure_blocking_or_async(self) -> None:
        instance = DependencyResolver(
            Claiming("pure", blocking=False), Claiming("slow"), Awaiting("fast")
        )
        plan = instance._aplan_for(_mixed)
        assert plan.colours == (
            ("pure", PURE),
            ("slow", BLOCKING),
            ("fast", ASYNC),
            ("missing", PURE),
        )
        assert plan.target is BLOCKING
        assert plan.awaits

    def test_consecutive_entries_of_one_colour_share_a_step(self) -> None:
        instance = DependencyResolver(
            *(Claiming(name) for name in ("b1", "b2", "b3")),
            Claiming("p", blocking=False),
            Awaiting("a1"),
            Awaiting("a2"),
        )
        plan = instance._aplan_for(_grouped)
        assert [type(step) for step in plan.steps] == [Blocking, Awaited, Blocking]
        assert [[entry[0] for entry in step.entries] for step in plan.steps] == [
            ["b1", "b2"],
            ["a1", "a2"],
            ["b3"],
        ]
        assert [entry[0] for entry in plan.pure] == ["p"]

    def test_an_async_target_awaits_and_a_sync_one_without_async_does_not(self) -> None:
        instance = DependencyResolver(Claiming("slow"), Claiming("p", blocking=False))
        async_plan = instance._aplan_for(_atarget)
        assert async_plan.target is ASYNC
        assert async_plan.awaits
        sync_plan = instance._aplan_for(_sync_blocking)
        assert sync_plan.target is BLOCKING
        assert not sync_plan.awaits

    @pytest.mark.parametrize(
        ("providers", "colour"),
        [
            pytest.param(lambda: [Deferring("x")], BLOCKING, id="blocking-candidate"),
            pytest.param(
                lambda: [Deferring("x", blocking=False)], PURE, id="pure-candidate"
            ),
            pytest.param(
                lambda: [Deferring("x", blocking=False), Claiming("x")],
                BLOCKING,
                id="blocking-terminal",
            ),
            pytest.param(
                lambda: [Deferring("x", blocking=False), Claiming("x", blocking=False)],
                PURE,
                id="pure-candidate-and-terminal",
            ),
            pytest.param(lambda: [Inert("x")], BLOCKING, id="uncallable-hook"),
        ],
    )
    def test_every_provider_an_entry_may_reach_decides_its_colour(
        self, providers, colour
    ) -> None:
        instance = DependencyResolver(*providers())
        assert instance._aplan_for(_one).colours == (("x", colour),)

    def test_a_provider_classifies_each_parameter_on_its_own(self) -> None:
        instance = DependencyResolver(PerParam("cheap"))
        assert instance._aplan_for(_costed).colours == (
            ("cheap", PURE),
            ("dear", BLOCKING),
        )

    def test_an_async_hook_returning_no_callable_raises(self) -> None:
        instance = DependencyResolver(Broken("x"))
        with pytest.raises(
            TypeError, match=r"Broken\.compile_aresolve returned 'nope'"
        ):
            instance._aplan_for(_one)

    def test_an_uninspectable_callable_has_an_empty_plan(self) -> None:
        instance = DependencyResolver()
        runner = InlineRunner()
        context = make_resolution_context()
        assert instance._aplan_for(int) == empty_async_plan(BLOCKING)
        assert _drive(runner, lambda: instance.aresolve(int, context)) == {}
        assert _drive(runner, lambda: instance.acall(int, context)) == 0
        assert runner.calls == 1

    def test_the_empty_plan_of_an_async_target_awaits(self) -> None:
        assert empty_async_plan(ASYNC).awaits
        assert not empty_async_plan(BLOCKING).awaits


class TestAsyncPlanCache:
    """Async plans live in their own cache, stamped like the sync ones."""

    def test_a_second_aresolve_reuses_the_plan_and_leaves_the_sync_cache(self) -> None:
        instance = DependencyResolver(Claiming("x", blocking=False))
        context = make_resolution_context()
        key = introspect_key(_one)
        _drive(InlineRunner(), lambda: instance.aresolve(_one, context))
        cached = instance._aplan_cache.get(key)
        _drive(InlineRunner(), lambda: instance.aresolve(_one, context))
        assert cached is not None
        assert instance._aplan_cache.get(key) is cached
        assert key not in instance._plan_cache

    def test_a_provider_change_recompiles_the_plan(self) -> None:
        instance = DependencyResolver(Claiming("x", blocking=False))
        assert instance._aplan_for(_one).colours == (("x", PURE),)
        instance.prepend_provider(Awaiting("x"))
        assert instance._aplan_for(_one).colours == (("x", ASYNC),)

    def test_a_binding_change_recompiles_the_plan(self) -> None:
        instance = DependencyResolver()
        instance.register_dependency("feed", _sfactory)
        before = instance._aplan_for(_reader)
        assert before.colours == (("feed", BLOCKING),)

        async def feed() -> str:
            return "async"

        instance.register_dependency("feed", feed)
        assert instance._aplan_for(_reader).colours == (("feed", ASYNC),)
        version = instance._dependencies_version
        instance.unregister_dependency("feed")
        assert instance._dependencies_version == version + 1
        assert instance._aplan_for(_reader).colours == (("feed", BLOCKING),)

    def test_forget_caches_drops_the_async_plans(self) -> None:
        instance = DependencyResolver()
        instance._aplan_for(_one)
        assert instance._aplan_cache
        instance.forget_caches()
        assert not instance._aplan_cache

    def test_an_unresolvable_hint_compiles_again_each_time(self) -> None:
        instance = DependencyResolver()
        context = make_resolution_context()
        result = _drive(
            InlineRunner(), lambda: instance.aresolve(_unresolvable, context)
        )
        assert result == {"value": None}
        assert introspect_key(_unresolvable) not in instance._aplan_cache

    def test_an_unhashable_callable_compiles_per_resolve(self) -> None:
        instance = DependencyResolver()
        context = make_resolution_context()
        handler = _UnhashableHandler()
        result = _drive(InlineRunner(), lambda: instance.aresolve(handler, context))
        assert result == {"plain": 3}
        assert not instance._aplan_cache


class TestAresolve:
    """`aresolve` fills pure entries inline, segments through the runner, and awaits."""

    def test_pure_entries_fill_on_the_loop_thread_with_no_runner_call(self) -> None:
        pure = Claiming("x", blocking=False)
        instance = DependencyResolver(pure)
        runner = InlineRunner()

        async def run():
            result = await instance.aresolve(_one, make_resolution_context())
            return result, threading.get_ident()

        result, loop_thread = _drive(runner, run)
        assert result == {"x": "X"}
        assert pure.threads == [loop_thread]
        assert runner.calls == 0

    @pytest.mark.parametrize(
        ("claims", "expected"),
        [
            pytest.param(True, "D", id="claimed"),
            pytest.param(False, None, id="fallback"),
        ],
    )
    def test_a_blocking_candidate_is_asked_inside_the_segment(
        self, claims, expected
    ) -> None:
        instance = DependencyResolver(Deferring("x", "D", claims=claims))
        runner = InlineRunner()
        context = make_resolution_context()
        assert _drive(runner, lambda: instance.aresolve(_one, context)) == {
            "x": expected
        }
        assert runner.calls == 1

    def test_each_segment_is_one_runner_call_in_signature_order(self) -> None:
        log: list[str] = []
        instance = DependencyResolver(
            *(Claiming(name, log=log) for name in ("b1", "b2", "b3")),
            Claiming("p", blocking=False, log=log),
            Awaiting("a1", log=log),
            Awaiting("a2", log=log),
        )
        runner = InlineRunner()
        context = make_resolution_context()
        result = _drive(runner, lambda: instance.aresolve(_grouped, context))
        assert result == {
            "b1": "B1",
            "p": "P",
            "b2": "B2",
            "a1": "A1",
            "a2": "A2",
            "b3": "B3",
        }
        assert runner.calls == 2
        assert log[:3] == ["p", "b1", "b2"]
        assert set(log[3:5]) == {"a1", "a2"}
        assert log[5] == "b3"

    def test_the_default_runner_fills_a_segment_off_the_loop_thread(self) -> None:
        slow = Claiming("x")
        instance = DependencyResolver(slow)

        async def run():
            result = await instance.aresolve(_one, make_resolution_context())
            return result, threading.get_ident()

        result, loop_thread = asyncio.run(run())
        assert result == {"x": "X"}
        assert slow.threads
        assert slow.threads[0] != loop_thread

    def test_two_async_entries_run_concurrently(self) -> None:
        async def run():
            first_in = asyncio.Event()
            second_in = asyncio.Event()

            async def first(context):
                first_in.set()
                await second_in.wait()
                return 1

            async def second(context):
                second_in.set()
                await first_in.wait()
                return 2

            instance = DependencyResolver(Awaiting("a1", first), Awaiting("a2", second))
            return await asyncio.wait_for(
                instance.aresolve(_pair, make_resolution_context()), timeout=5
            )

        assert _drive(InlineRunner(), run) == {"a1": 1, "a2": 2}

    def test_a_lone_async_entry_shares_the_context_and_a_group_branches_it(
        self,
    ) -> None:
        seen: list[object] = []

        async def record(context):
            seen.append(context)

        context = make_resolution_context(stack=["outer"])
        single = DependencyResolver(Awaiting("a1", record))
        _drive(InlineRunner(), lambda: single.aresolve(_single, context))
        assert seen == [context]

        seen.clear()
        group = DependencyResolver(Awaiting("a1", record), Awaiting("a2", record))
        _drive(InlineRunner(), lambda: group.aresolve(_pair, context))
        assert len(seen) == 2
        for branch in seen:
            assert branch is not context
            assert branch.stack == ["outer"]
            assert branch.stack is not context.stack
            assert branch.cache is context.cache

    @pytest.mark.parametrize(
        "error",
        [
            pytest.param(Http404("gone"), id="http404"),
            pytest.param(DependencyCycleError(["x", "x"]), id="cycle"),
        ],
    )
    def test_a_failing_entry_cancels_its_sibling_and_keeps_its_type(
        self, error
    ) -> None:
        cancelled: list[bool] = []

        async def run():
            sibling_in = asyncio.Event()

            async def failing(context):
                await sibling_in.wait()
                raise error

            async def sibling(context):
                sibling_in.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled.append(True)
                    raise

            instance = DependencyResolver(
                Awaiting("a1", failing), Awaiting("a2", sibling)
            )
            with pytest.raises(type(error)) as caught:
                await instance.aresolve(_pair, make_resolution_context())
            return caught.value

        assert _drive(InlineRunner(), run) is error
        assert cancelled == [True]

    def test_cancelling_aresolve_cancels_its_async_entries(self) -> None:
        cancelled: list[int] = []

        async def run():
            entered = [asyncio.Event(), asyncio.Event()]

            def waiting(index):
                async def fill(context):
                    entered[index].set()
                    try:
                        await asyncio.Event().wait()
                    except asyncio.CancelledError:
                        cancelled.append(index)
                        raise

                return fill

            instance = DependencyResolver(
                Awaiting("a1", waiting(0)), Awaiting("a2", waiting(1))
            )
            task = asyncio.create_task(
                instance.aresolve(_pair, make_resolution_context())
            )
            await entered[0].wait()
            await entered[1].wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        _drive(InlineRunner(), run)
        assert sorted(cancelled) == [0, 1]

    def test_a_timeout_stays_a_timeout_when_an_entry_fails_on_cancel(
        self, caplog
    ) -> None:
        cleanup = RuntimeError("cleanup failed")

        async def run():
            entered = [asyncio.Event(), asyncio.Event()]

            async def failing_on_cancel(context):
                entered[0].set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    raise cleanup from None

            async def waiting(context):
                entered[1].set()
                await asyncio.Event().wait()

            instance = DependencyResolver(
                Awaiting("a1", failing_on_cancel), Awaiting("a2", waiting)
            )
            timeouts = []

            async def bounded():
                async with asyncio.timeout(None) as timeout:
                    timeouts.append(timeout)
                    await instance.aresolve(_pair, make_resolution_context())

            task = asyncio.create_task(bounded())
            await entered[0].wait()
            await entered[1].wait()
            timeouts[0].reschedule(asyncio.get_running_loop().time())
            with pytest.raises(TimeoutError):
                await task

        with caplog.at_level(logging.DEBUG, logger="next.deps.resolver"):
            _drive(InlineRunner(), run)
        assert _logged(caplog) == [cleanup]

    def test_several_failures_raise_the_earliest_entry_and_log_the_rest(
        self, caplog
    ) -> None:
        earlier = KeyError("first entry")
        later = ValueError("second entry")

        async def run():
            second_waiting = asyncio.Event()
            first_waiting = asyncio.Event()
            go = asyncio.Event()

            async def first(context):
                await second_waiting.wait()
                first_waiting.set()
                await go.wait()
                raise earlier

            async def second(context):
                second_waiting.set()
                await go.wait()
                raise later

            instance = DependencyResolver(Awaiting("a1", first), Awaiting("a2", second))
            task = asyncio.create_task(
                instance.aresolve(_pair, make_resolution_context())
            )
            await first_waiting.wait()
            go.set()
            with pytest.raises(KeyError) as caught:
                await task
            return caught.value

        with caplog.at_level(logging.DEBUG, logger="next.deps.resolver"):
            assert _drive(InlineRunner(), run) is earlier
        assert _logged(caplog) == [later]

    def test_a_finished_segment_lands_whole(self) -> None:
        instance = DependencyResolver()
        instance.register_dependency("a", lambda: "A")
        instance.register_dependency("b", lambda: "B")
        cache = DependencyCache()
        context = make_resolution_context(cache=cache)
        _drive(InlineRunner(), lambda: instance.aresolve(_both, context))
        assert (cache.get("a"), cache.get("b")) == ("A", "B")

    @pytest.mark.parametrize("run_first", [True, False], ids=["ran", "pending"])
    def test_a_cancelled_segment_lands_nothing(self, run_first) -> None:
        instance = DependencyResolver()
        instance.register_dependency("a", lambda: "A")
        instance.register_dependency("b", lambda: "B")
        cache = DependencyCache()
        runner = HoldingRunner(run_first=run_first)

        async def run():
            task = asyncio.create_task(
                instance.aresolve(_both, make_resolution_context(cache=cache))
            )
            await runner.entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        _drive(runner, run)
        assert ("a" in cache, "b" in cache) == (False, False)

    def test_a_missing_dependency_names_the_resolved_callable(self) -> None:
        instance = DependencyResolver()
        context = make_resolution_context()
        with pytest.raises(UnknownDependencyError) as caught:
            _drive(InlineRunner(), lambda: instance.aresolve(_lost, context))
        assert caught.value.func is _lost

    @pytest.mark.parametrize(
        ("deferring", "calls", "expected"),
        [
            pytest.param(Deferring("x", "D", blocking=False), 0, "D", id="pure"),
            pytest.param(Deferring("x", "D"), 1, "D", id="blocking"),
            pytest.param(Deferring("x", "D", claims=False), 0, "X", id="declined"),
        ],
    )
    def test_a_candidate_ahead_of_an_async_filler_is_asked_first(
        self, deferring, calls, expected
    ) -> None:
        instance = DependencyResolver(deferring, Awaiting("x"))
        runner = InlineRunner()
        context = make_resolution_context()
        assert instance._aplan_for(_one).colours == (("x", ASYNC),)
        assert _drive(runner, lambda: instance.aresolve(_one, context)) == {
            "x": expected
        }
        assert runner.calls == calls


class TestAcall:
    """`acall` returns the value of a coroutine function and of a plain one."""

    def test_an_async_target_is_awaited_after_its_arguments(self) -> None:
        instance = DependencyResolver(Claiming("slow"), Claiming("p", blocking=False))
        runner = InlineRunner()

        async def run():
            value: str = await instance.acall(_atarget, make_resolution_context())
            return value

        assert _drive(runner, run) == "SLOW-P"
        assert runner.calls == 1

    @pytest.mark.parametrize(
        ("func", "calls", "expected"),
        [
            pytest.param(_sync_blocking, 1, "B1+B2", id="joins-the-only-segment"),
            pytest.param(_sync_split, 2, "B1A1B3", id="joins-the-last-segment"),
            pytest.param(_sync_after_await, 1, "after-A1", id="after-an-await"),
            pytest.param(_nothing, 1, 3, id="no-steps"),
        ],
    )
    def test_a_sync_target_joins_its_last_segment(self, func, calls, expected) -> None:
        instance = DependencyResolver(
            Claiming("b1"), Claiming("b2"), Claiming("b3"), Awaiting("a1")
        )
        runner = InlineRunner()
        context = make_resolution_context()
        assert _drive(runner, lambda: instance.acall(func, context)) == expected
        assert runner.calls == calls

    def test_the_overloads_return_the_value_type(self) -> None:
        instance = DependencyResolver(
            Claiming("slow"),
            Claiming("p", blocking=False),
            Claiming("b1"),
            Claiming("b2"),
        )

        async def run():
            context = make_resolution_context()
            awaited: str = await instance.acall(_atarget, context)
            called: str = await instance.acall(_sync_blocking, context)
            return awaited, called

        assert _drive(InlineRunner(), run) == ("SLOW-P", "B1+B2")

    @pytest.mark.parametrize("call", ["aresolve", "acall"])
    def test_an_async_generator_target_is_refused_at_compile(self, call) -> None:
        instance = DependencyResolver(Claiming("p", blocking=False))
        context = make_resolution_context()
        with pytest.raises(AsyncDependencyError, match="is an async generator"):
            _drive(InlineRunner(), lambda: getattr(instance, call)(_agen, context))
        assert introspect_key(_agen) not in instance._aplan_cache

    def test_a_coroutine_a_sync_target_returns_is_awaited(self) -> None:
        instance = DependencyResolver(Claiming("b1"))
        context = make_resolution_context()
        result = _drive(
            InlineRunner(), lambda: instance.acall(_hands_coroutine, context)
        )
        assert result == "made-B1"

    def test_another_awaitable_a_sync_target_returns_comes_back(self) -> None:
        ticket = _Ticket()

        def hands(b1):
            return ticket

        instance = DependencyResolver(Claiming("b1"))
        context = make_resolution_context()
        assert _drive(InlineRunner(), lambda: instance.acall(hands, context)) is ticket

    def test_a_missing_dependency_names_the_called_callable(self) -> None:
        instance = DependencyResolver()
        context = make_resolution_context()
        with pytest.raises(UnknownDependencyError) as caught:
            _drive(InlineRunner(), lambda: instance.acall(_lost, context))
        assert caught.value.func is _lost


class TestNamedAsyncDependencies:
    """Async binds live apart from sync ones and only the async path awaits them."""

    def test_an_async_bind_is_kept_apart_and_moves_on_rebind(self) -> None:
        instance = DependencyResolver()

        async def feed() -> str:
            return "async"

        instance.register_dependency("feed", feed)
        assert instance._async_dependency_callables == {"feed": feed}
        assert "feed" not in instance._dependency_callables
        assert instance.get_dependency("feed") is feed
        assert dict(instance.dependencies()) == {"feed": feed}

        instance.register_dependency("feed", _sfactory)
        assert instance._async_dependency_callables == {}
        assert instance.get_dependency("feed") is _sfactory

        instance.register_dependency("feed", feed)
        instance.unregister_dependency("feed")
        assert instance.get_dependency("feed") is None

    def test_an_async_generator_bind_is_refused(self) -> None:
        instance = DependencyResolver()
        with pytest.raises(
            AsyncDependencyError, match=r"dependency 'feed'.* is an async generator"
        ):
            instance.register_dependency("feed", _agen)
        assert instance.get_dependency("feed") is None

    def test_the_sync_path_bridges_an_async_bind(self) -> None:
        instance = DependencyResolver()
        calls: list[int] = []

        async def feed() -> str:
            calls.append(1)
            return "async"

        instance.register_dependency("feed", feed)
        cache: dict[str, object] = {}
        for _ in range(2):
            assert instance.resolve_dependencies(_reader, _cache=cache) == {
                "feed": "async"
            }
        assert calls == [1]
        assert cache["feed"] == "async"

    def test_a_finished_value_is_read_without_another_bridge(self, monkeypatch) -> None:
        """Only the first reference pays the bridge, the later ones read the cache."""
        instance = DependencyResolver()
        sites: list[str] = []

        def counting(site, work):
            sites.append(site)
            return bridge(site, work)

        async def feed() -> str:
            return "async"

        monkeypatch.setattr(_resolver_module, "bridge", counting)
        instance.register_dependency("feed", feed)
        cache: dict[str, object] = {}
        for _ in range(3):
            assert instance.resolve_dependencies(_reader, _cache=cache) == {
                "feed": "async"
            }
        assert sites == ["dependency 'feed'"]

    def test_an_in_progress_mark_still_crosses_the_bridge(self, monkeypatch) -> None:
        """A mark is no finished value, so the async side decides what it means."""
        instance = DependencyResolver()
        sites: list[str] = []

        def counting(site, work):
            sites.append(site)
            return bridge(site, work)

        async def feed() -> str:
            return "async"

        monkeypatch.setattr(_resolver_module, "bridge", counting)
        instance.register_dependency("feed", feed)
        cache = DependencyCache()
        cache.mark_in_progress("feed")
        context = make_resolution_context(cache=cache)
        assert instance.resolve_callable_dependency("feed", context) == "async"
        assert sites == ["dependency 'feed'"]

    def test_a_bridged_bind_reaching_back_is_a_cycle(self) -> None:
        instance = DependencyResolver()

        async def b(a=Depends("a")) -> object:
            return a

        instance.register_dependency("a", _cycle_a)
        instance.register_dependency("b", b)
        with pytest.raises(DependencyCycleError) as caught:
            instance.resolve_dependencies(_cycle_root)
        assert caught.value.cycle == ["a", "b", "a"]

    def test_debug_logs_a_bridged_name_once(
        self, caplog, monkeypatch, settings
    ) -> None:
        settings.DEBUG = True
        monkeypatch.setattr("next.deps.runner._adapted", BoundedCache())
        instance = DependencyResolver()

        async def feed() -> str:
            return "async"

        instance.register_dependency("feed", feed)
        with caplog.at_level(logging.DEBUG, logger="next.deps.runner"):
            instance.resolve_dependencies(_reader)
            instance.resolve_dependencies(_reader)
        assert [
            record.getMessage()
            for record in caplog.records
            if record.name == "next.deps.runner"
        ] == ["Asynchronous dependency 'feed' adapted for a sync resolve."]

    def test_without_debug_a_bridge_logs_nothing(self, caplog, monkeypatch) -> None:
        monkeypatch.setattr("next.deps.runner._adapted", BoundedCache())
        instance = DependencyResolver()

        async def feed() -> str:
            return "async"

        instance.register_dependency("feed", feed)
        with caplog.at_level(logging.DEBUG, logger="next.deps.runner"):
            instance.resolve_dependencies(_reader)
        assert not [r for r in caplog.records if r.name == "next.deps.runner"]

    def test_the_async_path_awaits_an_async_bind_once_per_cache(self) -> None:
        instance = DependencyResolver()
        calls: list[int] = []

        async def feed() -> list[str]:
            calls.append(1)
            return ["item"]

        instance.register_dependency("feed", feed)
        context = make_resolution_context()
        assert instance._aplan_for(_reader).colours == (("feed", ASYNC),)
        for _ in range(2):
            result = _drive(InlineRunner(), lambda: instance.aresolve(_reader, context))
            assert result == {"feed": ["item"]}
        assert calls == [1]
        assert context.cache.get("feed") == ["item"]
        assert context.stack == []

    def test_an_in_progress_mark_reads_as_a_miss(self) -> None:
        instance = DependencyResolver()

        async def feed() -> str:
            return "fresh"

        instance.register_dependency("feed", feed)
        cache = DependencyCache()
        cache.mark_in_progress("feed")
        context = make_resolution_context(cache=cache)
        result = _drive(InlineRunner(), lambda: instance.aresolve(_reader, context))
        assert result == {"feed": "fresh"}

    def test_a_name_already_on_the_stack_is_a_cycle(self) -> None:
        instance = DependencyResolver()

        async def feed() -> str:
            return "never"

        instance.register_dependency("feed", feed)
        context = make_resolution_context(stack=["feed"])
        with pytest.raises(DependencyCycleError) as caught:
            _drive(InlineRunner(), lambda: instance.aresolve(_reader, context))
        assert caught.value.cycle == ["feed", "feed"]

    def test_an_unbound_name_raises_with_a_suggestion(self) -> None:
        instance = DependencyResolver()

        async def feed() -> str:
            return "async"

        instance.register_dependency("feed", feed)
        context = make_resolution_context()
        with pytest.raises(UnknownDependencyError) as caught:
            _drive(
                InlineRunner(),
                lambda: instance.aresolve_callable_dependency("fed", context),
            )
        assert caught.value.suggestion == "feed"
        assert caught.value.param_name is None

    def test_a_sync_bind_reaching_an_async_one_is_awaited(self) -> None:
        instance = DependencyResolver()

        async def feed() -> list[str]:
            return ["item"]

        def digest(feed=Depends("feed")) -> int:
            return len(feed)

        instance.register_dependency("feed", feed)
        instance.register_dependency("digest", digest)
        runner = InlineRunner()
        context = make_resolution_context()
        assert instance._aplan_for(_digest_reader).colours == (("digest", ASYNC),)
        result = _drive(runner, lambda: instance.aresolve(_digest_reader, context))
        assert result == {"digest": 1}
        assert runner.calls == 1

    def test_a_wholly_sync_bind_stays_in_the_segment(self) -> None:
        instance = DependencyResolver()
        instance.register_dependency("digest", _nothing)
        runner = InlineRunner()
        context = make_resolution_context()
        assert instance._aplan_for(_digest_reader).colours == (("digest", BLOCKING),)
        result = _drive(runner, lambda: instance.aresolve(_digest_reader, context))
        assert result == {"digest": 3}
        assert runner.calls == 1

    def test_a_static_cycle_raises_at_compile(self) -> None:
        instance = DependencyResolver()
        instance.register_dependency("a", _cycle_a)
        instance.register_dependency("b", _cycle_b)
        context = make_resolution_context()
        with pytest.raises(DependencyCycleError) as caught:
            _drive(InlineRunner(), lambda: instance.aresolve(_cycle_root, context))
        assert caught.value.cycle == ["a", "b", "a"]
        assert introspect_key(_cycle_root) not in instance._aplan_cache

    def test_a_cycle_a_deferring_provider_breaks_resolves_on_both_paths(self) -> None:
        instance = DependencyResolver(Deferring("b", "D", blocking=False))
        instance.add_provider(DependsProvider(instance))
        instance.register_dependency("a", _broken_a)
        instance.register_dependency("b", _cycle_b)
        context = make_resolution_context()
        result = _drive(InlineRunner(), lambda: instance.aresolve(_cycle_root, context))
        assert result == {"a": "D"}
        assert introspect_key(_cycle_root) in instance._aplan_cache
        assert instance.resolve_dependencies(_cycle_root) == {"a": "D"}

    def test_a_cycle_the_deferring_provider_leaves_raises_on_both_paths(self) -> None:
        instance = DependencyResolver(Deferring("b", "D", claims=False))
        instance.add_provider(DependsProvider(instance))
        instance.register_dependency("a", _broken_a)
        instance.register_dependency("b", _cycle_b)
        context = make_resolution_context()
        with pytest.raises(DependencyCycleError) as awaited:
            _drive(InlineRunner(), lambda: instance.aresolve(_cycle_root, context))
        with pytest.raises(DependencyCycleError) as called:
            instance.resolve_dependencies(_cycle_root)
        assert awaited.value.cycle == called.value.cycle == ["a", "b", "a"]

    def test_awaits_outside_a_compile_names_no_consumer(self) -> None:
        instance = DependencyResolver()
        assert not instance.awaits(_nothing, key="nothing")
        assert instance._aplan_for(_nothing).names == ()


class TestDependsProviderAsync:
    """Each `Depends` form takes the colour of the work it names."""

    def test_the_forms_compile_to_their_colours(self) -> None:
        instance = _depends_resolver(Claiming("p", blocking=False))
        assert instance._aplan_for(_factories).colours == (
            ("made", ASYNC),
            ("plain", BLOCKING),
            ("n", PURE),
        )
        context = make_resolution_context()
        result = _drive(InlineRunner(), lambda: instance.aresolve(_factories, context))
        assert result == {"made": "made-P", "plain": "plain", "n": 5}

    def test_the_sync_path_bridges_an_async_factory(self) -> None:
        instance = _depends_resolver(Claiming("p", blocking=False))
        assert instance.resolve_dependencies(_factories) == {
            "made": "made-P",
            "plain": "plain",
            "n": 5,
        }

    def test_an_uncompiled_resolve_bridges_an_async_factory(self) -> None:
        instance = _depends_resolver(Claiming("p", blocking=False))
        provider = DependsProvider(instance)
        param = inspect_parameter("made", default=Depends(_afactory))
        assert provider.resolve(param, make_resolution_context()) == "made-P"

    def test_a_factory_already_on_the_stack_is_a_cycle(self) -> None:
        instance = _depends_resolver(Claiming("p", blocking=False))
        key = factory_key(_afactory)
        context = make_resolution_context(stack=[key])
        with pytest.raises(DependencyCycleError) as caught:
            _drive(InlineRunner(), lambda: instance.aresolve(_factories, context))
        assert caught.value.cycle == [key, key]

    @pytest.mark.parametrize(
        ("default", "blocks"),
        [
            pytest.param(Depends("feed"), True, id="name"),
            pytest.param(Depends(), True, id="bare"),
            pytest.param(Depends(_sfactory), True, id="factory"),
            pytest.param(Depends(5), False, id="constant"),
        ],
    )
    def test_static_blocking_follows_the_form(self, default, blocks) -> None:
        provider = DependsProvider(DependencyResolver())
        assert (
            provider.static_blocking(inspect_parameter("d", default=default)) is blocks
        )


class TestProviderDefaults:
    """A registered provider blocks and offers no async filler unless it says so."""

    def test_defaults_and_the_pure_declaration(self) -> None:
        class Plain(RegisteredParameterProvider):
            def can_handle(self, param, context) -> bool:
                return False

            def resolve(self, param, context) -> object:
                return None

        class Cheap(Plain):
            blocking = False

        param = inspect_parameter("x")
        assert Plain().static_blocking(param) is True
        assert Cheap().static_blocking(param) is False
        assert Plain().compile_aresolve(param) is None


class TestAsyncDependencyError:
    """The sync refusal names the async entry points."""

    def test_the_message_names_aresolve_and_acall(self) -> None:
        error = AsyncDependencyError(_atarget)
        assert isinstance(error, AsyncCallableError)
        assert error.role == "dependency-injected callable"
        assert "_atarget" in error.subject
        message = str(error)
        assert "await resolver.aresolve(func, context)" in message
        assert "await resolver.acall(func, context)" in message

    def test_an_async_generator_is_told_to_return_its_value(self) -> None:
        message = str(AsyncDependencyError(_agen, name="feed"))
        assert "dependency 'feed'" in message
        assert "is an async generator" in message
        assert "'async def' and return the value" in message

    def test_a_named_bind_is_named_in_the_role(self) -> None:
        assert AsyncDependencyError(_afactory, name="feed").role == "dependency 'feed'"

    @pytest.mark.parametrize("func", [_atarget, _agen], ids=["coroutine", "generator"])
    def test_the_sync_compile_refuses_an_async_target(self, func) -> None:
        instance = DependencyResolver()
        with pytest.raises(AsyncDependencyError, match=r"resolver\.acall"):
            instance.resolve_dependencies(func)
        assert introspect_key(func) not in instance._plan_cache


async def _x_value() -> str:
    return "x"


def _sharing_resolver(x=_x_value) -> DependencyResolver:
    instance = DependencyResolver()
    instance.register_dependency("x", x)
    instance.register_dependency("left", _left)
    instance.register_dependency("right", _right)
    return instance


class TestShareCounting:
    """A plan counts the consumers it reaches that name each key."""

    def test_a_key_two_consumers_name_is_shared(self) -> None:
        instance = _sharing_resolver()
        plan = instance._aplan_for(_diamond)
        assert plan.names == ("left", "right")
        assert dict(plan.reach) == {"x": (), "left": ("x",), "right": ("x",)}
        assert plan.shared == frozenset({"x"})
        assert instance._aplan_for(_left).shared == frozenset()

    def test_a_consumer_reached_twice_counts_once(self) -> None:
        instance = DependencyResolver()
        instance.register_dependency("y", _x_value)
        instance.register_dependency("mid", _mid)
        instance.register_dependency("left", _over_mid)
        instance.register_dependency("right", _over_mid)
        assert instance._aplan_for(_diamond).shared == frozenset({"mid"})

    def test_one_consumer_naming_a_key_twice_shares_it(self) -> None:
        instance = _sharing_resolver()
        assert instance._aplan_for(_twice).shared == frozenset({"x"})


class TestRequestMemo:
    """Keyed computations go through the request scope a memo carries."""

    @pytest.mark.parametrize(
        ("memo", "expected"),
        [
            pytest.param({}, {"left": False, "right": False, "x": True}, id="counted"),
            pytest.param(
                {"shared": frozenset({"left"})},
                {"left": True, "right": False, "x": True},
                id="declared",
            ),
            pytest.param(
                {"share_all": True}, {"left": True, "right": True, "x": True}, id="all"
            ),
        ],
    )
    def test_shared_follows_the_counts_and_the_owner(self, memo, expected) -> None:
        instance = _sharing_resolver()
        scope = RecordingScope()
        context = make_resolution_context()
        result = _drive(
            InlineRunner(),
            lambda: instance.aresolve(_diamond, context),
            RequestMemo(scope, **memo),
        )
        assert result == {"left": "x", "right": "x"}
        assert dict(scope.calls) == expected

    def test_a_nested_plan_keeps_the_root_shares(self) -> None:
        instance = _sharing_resolver()
        instance.register_dependency("pair", _twice)
        scope = RecordingScope()
        context = make_resolution_context()
        _drive(
            InlineRunner(),
            lambda: instance.aresolve(_pair_reader, context),
            RequestMemo(scope),
        )
        assert scope.calls == [("pair", False), ("x", True)]

    def test_a_factory_goes_through_the_memo(self) -> None:
        instance = _depends_resolver(Claiming("p", blocking=False))
        scope = RecordingScope()
        context = make_resolution_context()
        result = _drive(
            InlineRunner(),
            lambda: instance.aresolve(_factories, context),
            RequestMemo(scope, share_all=True),
        )
        assert result["made"] == "made-P"
        assert scope.calls == [(factory_key(_afactory), True)]

    def test_a_shared_key_is_computed_once_for_concurrent_consumers(self) -> None:
        calls: list[int] = []

        async def x() -> str:
            calls.append(1)
            return "x"

        instance = _sharing_resolver(x)
        context = make_resolution_context()
        result = _drive(
            InlineRunner(),
            lambda: instance.aresolve(_diamond, context),
            RequestMemo(RequestScope()),
        )
        assert result == {"left": "x", "right": "x"}
        assert calls == [1]

    def test_the_computation_outlives_a_cancelled_claimer_on_its_own_stack(
        self,
    ) -> None:
        stacks: list[list[str]] = []

        async def run():
            entered = asyncio.Event()
            release = asyncio.Event()

            async def probe(context):
                entered.set()
                await release.wait()
                stacks.append(list(context.stack))
                return "P"

            async def feed(probe) -> str:
                return probe

            instance = DependencyResolver(Awaiting("probe", probe))
            instance.add_provider(DependsProvider(instance))
            instance.register_dependency("feed", feed)
            claimer = make_resolution_context()
            task = asyncio.create_task(instance.aresolve(_reader, claimer))
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert claimer.stack == []
            release.set()
            return await instance.aresolve(_reader, make_resolution_context())

        memo = RequestMemo(RequestScope(), share_all=True)
        assert _drive(InlineRunner(), run, memo) == {"feed": "P"}
        assert stacks == [["feed"]]


class _Made:
    """Factories of both colours bound to one object, counting how often they run."""

    def __init__(self) -> None:
        """Start with no call counted."""
        self.calls = 0

    def make(self) -> str:
        self.calls += 1
        return f"made-{self.calls}"

    async def amake(self) -> str:
        self.calls += 1
        return f"made-{self.calls}"

    async def afail(self) -> str:
        self.calls += 1
        msg = "factory failed"
        raise RuntimeError(msg)


def _amaking(value: int):
    async def make() -> int:
        return value

    return make


def _factory_consumers(factory):
    """Return an async callable naming `factory` three times, and two naming it once."""

    async def apage(a=Depends(factory), b=Depends(factory), c=Depends(factory)):
        return (a, b, c)

    def page(value=Depends(factory)) -> object:
        return value

    async def ajob(value=Depends(factory)) -> object:
        return value

    return apage, page, ajob


def _jobs(instance, func, contexts):
    """Await `func` once per context concurrently, as the jobs of one response run."""
    return asyncio.gather(
        *(instance.acall(func, context) for context in contexts), return_exceptions=True
    )


class TestFactoryMemoAsync:
    """A factory `Depends` runs once per cache and request scope on the async path."""

    @pytest.mark.parametrize("method", ["make", "amake"], ids=["sync", "async"])
    def test_three_consumers_of_one_call_share_one_run(self, method) -> None:
        made = _Made()
        apage, _page, _ajob = _factory_consumers(getattr(made, method))
        context = make_resolution_context()
        result = _drive(
            InlineRunner(),
            lambda: DependencyResolver().acall(apage, context),
            RequestMemo(RequestScope()),
        )
        assert result == ("made-1",) * 3
        assert made.calls == 1

    @pytest.mark.parametrize("method", ["make", "amake"], ids=["sync", "async"])
    def test_a_later_pass_reads_the_value_the_cache_holds(self, method) -> None:
        made = _Made()
        _apage, _page, ajob = _factory_consumers(getattr(made, method))
        instance = DependencyResolver()
        context = make_resolution_context()
        for _ in range(2):
            assert _drive(InlineRunner(), lambda: instance.acall(ajob, context)) == (
                "made-1"
            )
        assert made.calls == 1
        assert context.cache.get(factory_key(getattr(made, method))) == "made-1"
        assert context.stack == []

    @pytest.mark.parametrize("method", ["make", "amake"], ids=["sync", "async"])
    @pytest.mark.parametrize(
        "order",
        [("sync", "async", "sync"), ("async", "sync", "sync")],
        ids=["sync-first", "async-first"],
    )
    def test_sync_and_async_consumers_of_one_render_share_one_run(
        self, method, order
    ) -> None:
        made = _Made()
        _apage, page, ajob = _factory_consumers(getattr(made, method))
        instance = DependencyResolver()
        cache = DependencyCache()
        context = make_resolution_context(cache=cache)
        values = [
            instance.resolve_dependencies(page, _cache=cache)["value"]
            if colour == "sync"
            else _drive(InlineRunner(), lambda: instance.acall(ajob, context))
            for colour in order
        ]
        assert values == ["made-1"] * 3
        assert made.calls == 1

    def test_jobs_sharing_the_scope_compute_a_shared_factory_once(self) -> None:
        made = _Made()
        _apage, _page, ajob = _factory_consumers(made.amake)
        instance = DependencyResolver()
        memo = RequestMemo(RequestScope(), shared=frozenset({factory_key(made.amake)}))

        async def respond():
            concurrent = await _jobs(
                instance, ajob, [make_resolution_context() for _ in range(2)]
            )
            later = await instance.acall(ajob, make_resolution_context())
            return [*concurrent, later]

        assert _drive(InlineRunner(), respond, memo) == ["made-1"] * 3
        assert made.calls == 1

    def test_a_shared_failure_reaches_every_job_from_one_run(self) -> None:
        """The scope memoises the error, as it does for a named dependency."""
        made = _Made()
        _apage, _page, ajob = _factory_consumers(made.afail)
        memo = RequestMemo(RequestScope(), shared=frozenset({factory_key(made.afail)}))
        contexts = [make_resolution_context() for _ in range(2)]
        first, second = _drive(
            InlineRunner(), lambda: _jobs(DependencyResolver(), ajob, contexts), memo
        )
        assert isinstance(first, RuntimeError)
        assert second is first
        assert made.calls == 1

    def test_an_unshared_failure_is_run_again_by_the_next_pass(self) -> None:
        made = _Made()
        _apage, _page, ajob = _factory_consumers(made.afail)
        instance = DependencyResolver()
        context = make_resolution_context()
        for _ in range(2):
            with pytest.raises(RuntimeError, match="factory failed"):
                _drive(InlineRunner(), lambda: instance.acall(ajob, context))
        assert made.calls == 2
        assert context.stack == []

    def test_factories_sharing_a_path_stay_apart_in_the_scope(self) -> None:
        low_factory, high_factory = _amaking(1), _amaking(2)

        async def apage(low=Depends(low_factory), high=Depends(high_factory)):
            return (low, high)

        result = _drive(
            InlineRunner(),
            lambda: DependencyResolver().acall(apage, make_resolution_context()),
            RequestMemo(RequestScope(), share_all=True),
        )
        assert result == (1, 2)
