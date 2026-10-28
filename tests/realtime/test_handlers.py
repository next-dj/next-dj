import asyncio
import functools
import threading
import types
from collections.abc import Callable, Iterator
from typing import cast
from unittest.mock import patch

import pytest
from django.test import override_settings

from next import realtime
from next.realtime import Upstream, checks, handlers
from next.realtime.checks import check_realtime_settings, check_upstream_handlers
from next.realtime.handlers import (
    MAX_PATTERN_LENGTH,
    Handler,
    HandlerRegistry,
    PoolFullError,
    Route,
    compile_pattern,
    dispatch,
    handler_modules,
    valid_modules,
    valid_name,
)
from next.realtime.signals import upstream_received
from next.testing import capture_signals
from tests.support import check_ids


@pytest.fixture()
def registry() -> Iterator[HandlerRegistry]:
    """Swap in an empty registry for the decorator, dispatch and the checks."""
    fresh = HandlerRegistry()
    with (
        patch.object(handlers, "handler_registry", fresh),
        patch.object(checks, "handler_registry", fresh),
    ):
        yield fresh


def _send(topic: str, name: str, data: object = None) -> bool:
    return asyncio.run(dispatch(Upstream(topic, name, data, "alice", "subject", "c1")))


def _first(upstream: Upstream) -> None:
    del upstream


def _second(upstream: Upstream) -> None:
    del upstream


def _shared[F: Callable[..., object]](handler: F) -> F:
    """Wrap a handler the way a project-wide decorator with `functools.wraps` does."""

    @functools.wraps(handler)
    def wrapper(*args: object, **kwargs: object) -> object:
        return handler(*args, **kwargs)

    return cast("F", wrapper)


def _unnamed[F: Callable[..., object]](handler: F) -> F:
    """Wrap a handler without leaving a trace of it."""

    def wrapper(*args: object, **kwargs: object) -> object:
        return handler(*args, **kwargs)

    return cast("F", wrapper)


def _defined_in(filename: str, line: int = 1) -> Callable[[Upstream], None]:
    """Return `_first` as if `def handle` stood at `line` of another module."""
    code = _first.__code__.replace(
        co_filename=filename,
        co_firstlineno=line,
        co_name="handle",
        co_qualname="handle",
    )
    return types.FunctionType(code, globals(), "handle")


class _Calling:
    """A handler object whose `__call__` is a coroutine function."""

    def __init__(self) -> None:
        self.threads: list[threading.Thread] = []

    async def __call__(self, upstream: Upstream) -> None:
        self.threads.append(threading.current_thread())


class _Slotted:
    """A sync handler object no weak reference reaches."""

    __slots__ = ()

    def __call__(self, upstream: Upstream) -> None:
        del upstream


class TestPattern:
    """A topic pattern compiles to a strict matcher or is refused."""

    @pytest.mark.parametrize(
        ("pattern", "topic", "params"),
        [
            ("room:{code}", "room:abc", {"code": "abc"}),
            (
                "board:{board}:card:{card}",
                "board:7:card:9",
                {"board": "7", "card": "9"},
            ),
            ("{kind}.live", "poll.live", {"kind": "poll"}),
            ("dm:{a}-x:{b}", "dm:al-ice-x:bob", {"a": "al-ice", "b": "bob"}),
            ("lobby", "lobby", {}),
        ],
    )
    def test_a_valid_pattern_matches_its_topics(
        self, pattern: str, topic: str, params: dict[str, str]
    ) -> None:
        matcher = compile_pattern(pattern)
        assert matcher is not None
        found = matcher.fullmatch(topic)
        assert found is not None
        assert found.groupdict() == params

    @pytest.mark.parametrize(
        "topic", ["room:a:b", "room:a.b", "room:", "xroom:a", "room:a "]
    )
    def test_a_placeholder_spans_one_segment(self, topic: str) -> None:
        matcher = compile_pattern("room:{code}")
        assert matcher is not None
        assert matcher.fullmatch(topic) is None

    @pytest.mark.parametrize(
        "pattern",
        [
            "",
            "x" * 201,
            None,
            "next:{code}",
            "room/{code}",
            "room:{code",
            "room:{1code}",
            "room:{}",
            "{a}{b}",
            "dm:{a}-{b}",
            "{a}_x{b}",
            "{a}:{a}",
        ],
        ids=[
            "empty",
            "long",
            "not-a-string",
            "reserved",
            "bad-literal",
            "unclosed",
            "bad-placeholder",
            "empty-placeholder",
            "adjacent",
            "segment-literal",
            "segment-word",
            "repeated",
        ],
    )
    def test_an_invalid_pattern_compiles_to_none(self, pattern: object) -> None:
        assert compile_pattern(pattern) is None

    def test_a_pattern_of_exactly_the_maximum_length_compiles(self) -> None:
        assert compile_pattern("a" * MAX_PATTERN_LENGTH) is not None
        assert compile_pattern("a" * (MAX_PATTERN_LENGTH + 1)) is None

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("typing", True),
            ("room:typing", True),
            ("", False),
            ("a b", False),
            (1, False),
        ],
    )
    def test_event_names(self, name: object, expected) -> None:
        assert valid_name(name) is expected


class TestBinding:
    """A binding matches its own name and topic."""

    def test_an_invalid_name_leaves_no_matcher(self) -> None:
        bound = Route.bind("room:{code}", "bad name", _first)
        assert bound.valid is False
        assert bound.placeholders == 0
        assert bound.match("room:a", "bad name") is None

    def test_a_literal_pattern_matches_without_params(self) -> None:
        bound = Route.bind("lobby", "typing", _first)
        assert bound.match("lobby", "typing") == {}
        assert bound.match("lobby", "cursor") is None
        assert bound.match("hall", "typing") is None

    def test_params_are_read_only(self) -> None:
        params = Route.bind("room:{code}", "typing", _first).match("room:a", "typing")
        assert params == {"code": "a"}
        with pytest.raises(TypeError):
            cast("dict[str, str]", params)["code"] = "b"


class TestRegistry:
    """The registry keeps one binding per pair and resolves the most specific."""

    def test_add_keeps_registration_order_and_bumps_the_version(self) -> None:
        held = HandlerRegistry()
        first = held.add(Route.bind("a:{x}", "typing", _first))
        second = held.add(Route.bind("b:{x}", "typing", _second))
        assert list(held) == [first, second]
        assert len(held) == 2
        assert held.version == 2

    def test_the_same_function_again_replaces_its_pair(self) -> None:
        held = HandlerRegistry()
        held.add(Route.bind("a", "typing", _first))
        again = held.add(Route.bind("a", "typing", _first))
        assert list(held) == [again]
        assert held.duplicates() == ()

    def test_a_redefined_function_replaces_its_pair(self) -> None:
        held = HandlerRegistry()
        held.add(Route.bind("a", "typing", _first))
        reloaded = types.FunctionType(_first.__code__, globals(), "_first")
        redefined = held.add(Route.bind("a", "typing", reloaded))
        assert list(held) == [redefined]
        assert held.duplicates() == ()

    def test_another_function_is_kept_aside_as_a_duplicate(self) -> None:
        held = HandlerRegistry()
        first = held.add(Route.bind("a", "typing", _first))
        other = Route.bind("a", "typing", _second)
        assert held.add(other) is first
        assert list(held) == [first]
        assert held.duplicates() == ((first, other),)
        assert held.version == 1

    def test_a_shared_decorator_keeps_two_modules_apart(self) -> None:
        held = HandlerRegistry()
        first = held.add(Route.bind("a", "typing", _shared(_defined_in("one.py"))))
        other = Route.bind("a", "typing", _shared(_defined_in("two.py")))
        assert held.add(other) is first
        assert held.duplicates() == ((first, other),)

    def test_a_shared_decorator_over_a_redefinition_replaces_its_pair(self) -> None:
        held = HandlerRegistry()
        held.add(Route.bind("a", "typing", _shared(_defined_in("one.py"))))
        again = held.add(Route.bind("a", "typing", _shared(_defined_in("one.py"))))
        assert list(held) == [again]
        assert held.duplicates() == ()

    def test_two_definitions_in_one_file_are_duplicates(self) -> None:
        held = HandlerRegistry()
        held.add(Route.bind("a", "typing", _defined_in("one.py")))
        held.add(Route.bind("a", "typing", _defined_in("one.py", line=9)))
        assert len(held.duplicates()) == 1

    def test_a_closure_without_wraps_is_a_duplicate(self) -> None:
        held = HandlerRegistry()
        held.add(Route.bind("a", "typing", _unnamed(_first)))
        held.add(Route.bind("a", "typing", _unnamed(_second)))
        assert len(held.duplicates()) == 1

    def test_a_wrapper_cycle_is_a_duplicate(self) -> None:
        looped = _shared(_first)
        looped.__wrapped__ = looped
        held = HandlerRegistry()
        held.add(Route.bind("a", "typing", looped))
        held.add(Route.bind("a", "typing", _shared(_first)))
        assert len(held.duplicates()) == 1

    def test_a_handler_without_code_is_a_duplicate(self) -> None:
        held = HandlerRegistry()
        first = held.add(Route.bind("a", "typing", print))
        assert held.add(Route.bind("a", "typing", repr)) is first
        assert len(held.duplicates()) == 1

    def test_the_fewest_placeholders_win_then_the_first(self) -> None:
        held = HandlerRegistry()
        wide = held.add(Route.bind("room:{code}", "typing", _first))
        held.add(Route.bind("{kind}:{code}", "typing", _second))
        exact = held.add(Route.bind("room:lobby", "typing", _second))
        assert held.resolve("room:lobby", "typing") == (exact, {})
        assert held.resolve("room:abc", "typing") == (wide, {"code": "abc"})
        assert held.resolve("room:abc", "cursor") is None

    def test_equal_placeholders_resolve_to_the_first_registered(self) -> None:
        for patterns in (
            ("room:{code}", "{kind}:lobby"),
            ("{kind}:lobby", "room:{code}"),
        ):
            held = HandlerRegistry()
            first = held.add(Route.bind(patterns[0], "typing", _first))
            held.add(Route.bind(patterns[1], "typing", _second))
            resolved = held.resolve("room:lobby", "typing")
            assert resolved is not None
            assert resolved[0] is first

    def test_fewer_placeholders_beat_an_earlier_binding_with_more(self) -> None:
        held = HandlerRegistry()
        held.add(Route.bind("{kind}:{code}", "typing", _first))
        narrow = held.add(Route.bind("room:{code}", "typing", _second))
        resolved = held.resolve("room:abc", "typing")
        assert resolved is not None
        assert resolved[0] is narrow

    def test_resolve_reads_only_the_routes_of_the_event_name(self) -> None:
        held = HandlerRegistry()
        cursor = held.add(Route.bind("{kind}:{code}", "cursor", _first))
        typing = held.add(Route.bind("{kind}:{code}", "typing", _second))
        assert held.resolve("room:abc", "typing") == (
            typing,
            {"kind": "room", "code": "abc"},
        )
        assert held.resolve("room:abc", "cursor") == (
            cursor,
            {"kind": "room", "code": "abc"},
        )
        assert held.resolve("room:abc", "scroll") is None

    def test_a_replaced_pair_resolves_to_the_new_route(self) -> None:
        held = HandlerRegistry()
        held.add(Route.bind("room:{code}", "typing", _first))
        again = held.add(Route.bind("room:{code}", "typing", _first))
        resolved = held.resolve("room:abc", "typing")
        assert resolved is not None
        assert resolved[0] is again

    def test_reset_drops_everything(self) -> None:
        held = HandlerRegistry()
        held.add(Route.bind("a", "typing", _first))
        held.add(Route.bind("a", "typing", _second))
        held.reset()
        assert len(held) == 0
        assert held.duplicates() == ()
        assert held.resolve("a", "typing") is None
        assert held.version == 2


class TestDispatch:
    """`dispatch` runs the handler of one event, sync or async."""

    def test_no_handler_answers_false(self, registry: HandlerRegistry) -> None:
        assert _send("room:a", "typing") is False

    def test_an_async_handler_gets_the_upstream(
        self, registry: HandlerRegistry
    ) -> None:
        seen: list[Upstream] = []

        @realtime.on("room:{code}", "typing")
        async def typing(event: Upstream) -> None:
            seen.append(event)

        assert _send("room:abc", "typing", {"on": True}) is True
        assert seen == [
            Upstream(
                topic="room:abc",
                name="typing",
                data={"on": True},
                user="alice",
                subject="subject",
                connection="c1",
                params={"code": "abc"},
            )
        ]

    def test_a_sync_handler_runs_off_the_event_loop(
        self, registry: HandlerRegistry
    ) -> None:
        threads: list[threading.Thread] = []

        @realtime.on("lobby", "cursor")
        def cursor(event: Upstream) -> None:
            threads.append(threading.current_thread())

        assert _send("lobby", "cursor") is True
        assert threads
        assert threads[0] is not threading.main_thread()
        assert threads[0].name.startswith("next-handler")

    def test_a_sync_wrapper_over_a_coroutine_is_awaited(
        self, registry: HandlerRegistry
    ) -> None:
        done: list[str] = []

        async def typing(event: Upstream) -> None:
            await asyncio.sleep(0)
            done.append(event.name)

        realtime.on("lobby", "typing")(_shared(typing))
        assert _send("lobby", "typing") is True
        assert done == ["typing"]

    def test_an_object_with_an_async_call_runs_on_the_loop(
        self, registry: HandlerRegistry
    ) -> None:
        handler = _Calling()
        realtime.on("lobby", "typing")(handler)
        assert _send("lobby", "typing") is True
        assert handler.threads == [threading.main_thread()]

    def test_the_decorator_returns_the_function(
        self, registry: HandlerRegistry
    ) -> None:
        assert realtime.on("lobby", "x")(_first) is _first
        assert [entry.handler for entry in registry] == [_first]

    def test_the_signal_names_the_handler_and_the_upstream(
        self, registry: HandlerRegistry
    ) -> None:
        realtime.on("lobby", "cursor")(_first)
        with capture_signals(upstream_received) as recorder:
            _send("lobby", "cursor", 1)
        [event] = recorder.events
        assert event.sender is _first
        assert event.kwargs["upstream"].data == 1

    def test_the_signal_names_a_handler_without_weak_references(
        self, registry: HandlerRegistry
    ) -> None:
        handler = _Slotted()
        realtime.on("lobby", "cursor")(handler)
        with capture_signals(upstream_received) as recorder:
            assert _send("lobby", "cursor") is True
        [event] = recorder.events
        assert event.sender is handler

    def test_a_handler_error_reaches_the_caller(
        self, registry: HandlerRegistry
    ) -> None:
        @realtime.on("lobby", "boom")
        async def boom(event: Upstream) -> None:
            raise LookupError(event.name)

        with pytest.raises(LookupError, match="boom"):
            _send("lobby", "boom")


async def _quiet(upstream: Upstream) -> None:
    del upstream


@pytest.fixture()
def permits() -> Iterator[threading.BoundedSemaphore]:
    """Swap in a one-thread bound for the handler pool."""
    held = threading.BoundedSemaphore(1)
    with patch.object(handlers, "handler_threads", lambda: held):
        yield held


def _upstream() -> Upstream:
    return Upstream("lobby", "typing", None, "alice", "subject", "c1")


class TestPoolThreads:
    """A sync handler holds a handler pool thread until it returns."""

    def test_a_sync_route_claims_a_thread_when_called_without_one(
        self, permits: threading.BoundedSemaphore
    ) -> None:
        threads: list[str] = []

        def typing(event: Upstream) -> None:
            threads.append(threading.current_thread().name)

        asyncio.run(Route.bind("lobby", "typing", typing)(_upstream()))
        assert threads[0].startswith("next-handler")
        assert permits.acquire(blocking=False)

    def test_a_full_pool_refuses_before_the_signal(
        self, registry: HandlerRegistry, permits: threading.BoundedSemaphore
    ) -> None:
        realtime.on("lobby", "typing")(_first)
        assert permits.acquire(blocking=False)
        with (
            capture_signals(upstream_received) as recorder,
            pytest.raises(PoolFullError),
        ):
            _send("lobby", "typing")
        assert len(recorder) == 0

    def test_an_async_handler_takes_no_thread(
        self, registry: HandlerRegistry, permits: threading.BoundedSemaphore
    ) -> None:
        realtime.on("lobby", "typing")(_quiet)
        assert permits.acquire(blocking=False)
        assert _send("lobby", "typing") is True

    @pytest.mark.parametrize("handler", [_first, _quiet], ids=["sync", "async"])
    def test_a_cancel_during_the_signal_gives_the_thread_back(
        self,
        registry: HandlerRegistry,
        permits: threading.BoundedSemaphore,
        handler: Handler,
    ) -> None:
        realtime.on("lobby", "typing")(handler)

        async def scenario() -> None:
            heard = asyncio.Event()

            async def stall(**kwargs: object) -> None:
                heard.set()
                await asyncio.Event().wait()

            upstream_received.connect(stall, sender=handler, weak=False)
            try:
                task = asyncio.create_task(dispatch(_upstream()))
                await heard.wait()
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            finally:
                upstream_received.disconnect(stall, sender=handler)

        asyncio.run(scenario())
        assert permits.acquire(blocking=False)

    def test_a_cancelled_handler_keeps_its_thread_until_it_returns(
        self, registry: HandlerRegistry, permits: threading.BoundedSemaphore
    ) -> None:
        entered = threading.Event()
        release = threading.Event()

        @realtime.on("lobby", "typing")
        def typing(event: Upstream) -> None:
            entered.set()
            release.wait(5)
            raise LookupError(event.name)

        async def scenario() -> None:
            task = asyncio.create_task(dispatch(_upstream()))
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert not permits.acquire(blocking=False)
            release.set()
            assert await asyncio.to_thread(permits.acquire, timeout=5)

        asyncio.run(scenario())


class TestModules:
    """`REALTIME['HANDLERS']` selects the modules discovery imports."""

    def test_the_default_discovers_every_app(self) -> None:
        assert handler_modules() is None

    def test_a_list_is_returned_as_a_tuple(self) -> None:
        with override_settings(
            NEXT_FRAMEWORK={"REALTIME": {"HANDLERS": ["a.realtime", "b"]}}
        ):
            assert handler_modules() == ("a.realtime", "b")

    def test_an_invalid_value_imports_nothing(self) -> None:
        with override_settings(NEXT_FRAMEWORK={"REALTIME": {"HANDLERS": "a.b"}}):
            assert handler_modules() == ()

    @pytest.mark.parametrize(
        ("modules", "expected"),
        [
            ([], True),
            (("a", "a.b_c"), True),
            ("a.b", False),
            ([""], False),
            (["a..b"], False),
            (["a-b"], False),
            ([1], False),
        ],
    )
    def test_valid_modules(self, modules: object, expected) -> None:
        assert valid_modules(modules) is expected


class TestChecks:
    """The checks report bad settings, unmatchable handlers and duplicates."""

    @pytest.mark.parametrize("value", ["a.b", ["a b"], 3])
    def test_an_invalid_handlers_setting_is_e197(self, value: object) -> None:
        with override_settings(NEXT_FRAMEWORK={"REALTIME": {"HANDLERS": value}}):
            assert "next.E197" in check_ids(check_realtime_settings())

    @pytest.mark.parametrize("value", [None, [], ["a.realtime"]])
    def test_a_valid_handlers_setting_passes(self, value: object) -> None:
        with override_settings(NEXT_FRAMEWORK={"REALTIME": {"HANDLERS": value}}):
            assert "next.E197" not in check_ids(check_realtime_settings())

    def test_a_clean_registry_passes(self, registry: HandlerRegistry) -> None:
        realtime.on("room:{code}", "typing")(_first)
        assert check_upstream_handlers() == []

    @pytest.mark.parametrize(
        ("pattern", "name", "fragment"),
        [
            ("room:{code}", "bad name", "event name"),
            ("room:{a}{b}", "typing", "topic pattern"),
        ],
    )
    def test_an_unmatchable_binding_is_e198(
        self, registry: HandlerRegistry, pattern: str, name: str, fragment: str
    ) -> None:
        realtime.on(pattern, name)(_first)
        [message] = check_upstream_handlers()
        assert message.id == "next.E198"
        assert fragment in message.msg
        assert message.obj == f"{__name__}._first"

    def test_a_duplicate_pair_is_e199(self, registry: HandlerRegistry) -> None:
        realtime.on("lobby", "typing")(_first)
        realtime.on("lobby", "typing")(_second)
        [message] = check_upstream_handlers()
        assert message.id == "next.E199"
        assert "_first" in message.msg
        assert "_second" in message.msg
