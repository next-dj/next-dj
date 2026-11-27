import asyncio
import contextvars
import logging
import threading
from collections.abc import Callable
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from contextlib import suppress
from types import SimpleNamespace

import asgiref
import pytest
from asgiref.current_thread_executor import CurrentThreadExecutor
from asgiref.local import Local
from asgiref.sync import (
    AsyncToSync,
    SyncToAsync,
    ThreadSensitiveContext,
    async_to_sync,
    sync_to_async,
)
from django.core.exceptions import ImproperlyConfigured

from next.bridge import drive, hops
from next.bridge.checks import check_asgiref_release
from next.bridge.hops import (
    TESTED,
    Handoff,
    InlineExecutor,
    QuitParentExecutor,
    Verdict,
    check_asgiref,
    current_executor,
    enter_sensitive,
    hop_on,
    parent_loop,
    parent_tasks,
    release,
    route_to,
    sensitive_context,
    unmarked,
    verdict,
)
from next.bridge.tasks import StealableTask
from tests.support.hops import stale_marks
from tests.support.waits import WAIT


_local = Local()
_held = contextvars.ContextVar[str]("held")


class _Bare:
    """Stand-in for an asgiref executor class without the attributes the walk reads."""


class _Counted(Executor):
    """Run each call on one thread of its own, counting the calls."""

    def __init__(self, pool: ThreadPoolExecutor) -> None:
        self.pool = pool
        self.calls = 0

    def submit(self, fn: Callable[..., object], /, *args: object) -> Future[object]:
        self.calls += 1
        return self.pool.submit(fn, *args)


def _in_thread[R](call: Callable[[], R]) -> R:
    """Return what `call` returns on a fresh thread, failing when it never ends."""
    pool = ThreadPoolExecutor(1)
    try:
        return pool.submit(call).result(WAIT)
    finally:
        pool.shutdown(wait=False)


class TestRouting:
    """asgiref sends a thread-sensitive call to the executor a context routes it to."""

    def test_a_thread_sensitive_call_reaches_the_routed_executor(self) -> None:
        with ThreadPoolExecutor(1) as pool:
            executor = _Counted(pool)
            worker = pool.submit(threading.get_ident).result(WAIT)

            async def main() -> int:
                route_to(executor)
                assert current_executor() is executor
                return await sync_to_async(threading.get_ident)()

            assert asyncio.run(main()) == worker
        assert executor.calls == 1

    def test_a_route_stays_in_the_context_that_set_it(self) -> None:
        executor = QuitParentExecutor()

        async def main() -> tuple[object, object]:
            before = current_executor()

            async def child() -> object:
                route_to(executor)
                return current_executor()

            inside = await asyncio.create_task(child())
            assert current_executor() is before
            return before, inside

        assert asyncio.run(main())[1] is executor
        contextvars.Context().run(route_to, executor)
        assert current_executor() is not executor

    def test_a_nested_async_to_sync_on_the_request_thread_does_not_deadlock(
        self,
    ) -> None:
        async def innermost() -> int:
            return await sync_to_async(threading.get_ident)()

        def nested() -> int:
            return async_to_sync(innermost)()

        async def outer() -> int:
            return await sync_to_async(nested)()

        def scenario() -> tuple[int, int]:
            return drive(outer), threading.get_ident()

        reached, request = _in_thread(scenario)
        assert reached == request

    def test_a_late_call_past_a_quit_parent_gets_the_asgiref_error(self) -> None:
        done: Future[None] = Future()
        done.set_result(None)
        child = CurrentThreadExecutor(QuitParentExecutor())
        child.run_until_future(done)
        with ThreadPoolExecutor(1) as pool:
            submitted = pool.submit(child.submit, print)
            with pytest.raises(RuntimeError, match="already quit"):
                submitted.result(WAIT)


class TestSensitiveContext:
    """A context that enters a `ThreadSensitiveContext` shares its thread."""

    def test_an_entered_context_runs_its_calls_on_the_thread_of_the_owner(self) -> None:
        async def main() -> tuple[object, int, int]:
            async with ThreadSensitiveContext():
                held = sensitive_context()
                assert held is not None
                first = await sync_to_async(threading.get_ident)()

                async def elsewhere() -> int:
                    assert sensitive_context() is None
                    enter_sensitive(held)
                    return await sync_to_async(threading.get_ident)()

                loop = asyncio.get_running_loop()
                other = await loop.create_task(
                    elsewhere(), context=contextvars.Context()
                )
            return held, first, other

        held, first, other = asyncio.run(main())
        assert isinstance(held, ThreadSensitiveContext)
        assert first == other != threading.get_ident()
        assert sensitive_context() is None


class TestLoopMarkers:
    """A `sync_to_async` call marks its thread with its loop and its child tasks."""

    def test_a_call_from_a_loop_reads_the_loop_and_a_task_list(self) -> None:
        async def main() -> tuple[object, object, object]:
            seen = await sync_to_async(
                lambda: (parent_loop(), parent_tasks()), thread_sensitive=False
            )()
            return asyncio.get_running_loop(), *seen

        loop, marked, tasks = asyncio.run(main())
        assert marked is loop
        assert isinstance(tasks, list)

    def test_a_loop_marked_by_another_process_reads_as_none(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def scenario() -> tuple[object, object, object]:
            loop = stale_marks()
            before = parent_loop()
            monkeypatch.setattr(hops, "os", SimpleNamespace(getpid=lambda: -1))
            return loop, before, parent_loop()

        loop, before, forked = _in_thread(scenario)
        assert before is loop
        assert forked is None

    def test_asgiref_leaves_its_marks_and_unmarked_puts_them_back(self) -> None:
        def scenario() -> tuple[object, object, object]:
            kept = unmarked(stale_marks)
            after_unmarked = parent_loop()
            left = stale_marks()
            return kept, after_unmarked, (left, parent_loop())

        kept, after_unmarked, (left, marked) = _in_thread(scenario)
        assert isinstance(kept, asyncio.AbstractEventLoop)
        assert after_unmarked is None
        assert marked is left

    def test_cancelling_the_call_cancels_the_first_task_listed_on_its_thread(
        self,
    ) -> None:
        cancelled: Future[bool] = Future()
        listed = threading.Event()

        async def child() -> None:
            try:
                await asyncio.sleep(WAIT)
            except asyncio.CancelledError:
                cancelled.set_result(True)
                raise

        async def main() -> None:
            loop = asyncio.get_running_loop()

            def body() -> None:
                tasks = parent_tasks()
                assert tasks is not None

                def begin() -> None:
                    tasks.append(loop.create_task(child()))
                    listed.set()

                loop.call_soon_threadsafe(begin)
                cancelled.result(WAIT)

            outer = loop.create_task(sync_to_async(body, thread_sensitive=False)())
            await asyncio.to_thread(listed.wait, WAIT)
            outer.cancel()
            with suppress(asyncio.CancelledError):
                await outer

        asyncio.run(main())
        assert cancelled.result(0) is True


class TestHandoff:
    """asgiref submits an eagerly started hop before its first await."""

    def test_start_returns_the_unit_asgiref_queued_in_the_context_given(self) -> None:
        queued: list[StealableTask[object]] = []
        handoff = Handoff(queued.append)

        def read() -> tuple[str, object, int]:
            return _held.get(), getattr(_local, "value", None), threading.get_ident()

        async def main() -> object:
            _held.set("request")
            _local.value = "request"
            context = contextvars.copy_context()
            task, unit = handoff.start(read, context, asyncio.get_running_loop())
            assert queued == [unit]
            await asyncio.to_thread(unit.run)
            return await task

        held, local, ident = asyncio.run(main())
        assert (held, local) == ("request", "request")
        assert ident != threading.get_ident()

    def test_a_refusal_of_the_queue_is_raised_by_start(self) -> None:
        def refuse(_task: StealableTask[object]) -> None:
            msg = "queue full"
            raise OverflowError(msg)

        handoff = Handoff(refuse)

        async def main() -> None:
            context = contextvars.copy_context()
            with pytest.raises(OverflowError, match="queue full"):
                handoff.start(lambda: None, context, asyncio.get_running_loop())

        asyncio.run(main())

    def test_a_hop_that_awaits_before_submitting_breaks_the_invariant_loudly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        original = SyncToAsync.__call__

        async def late(self: SyncToAsync[[], object]) -> object:
            await asyncio.sleep(0)
            return await original(self)

        monkeypatch.setattr(SyncToAsync, "__call__", late)
        queued: list[StealableTask[object]] = []
        handoff = Handoff(queued.append)

        async def main() -> None:
            context = contextvars.copy_context()
            with pytest.raises(RuntimeError, match=r"reorders SyncToAsync\.__call__"):
                handoff.start(lambda: None, context, asyncio.get_running_loop())
            await asyncio.sleep(0)

        asyncio.run(main())
        assert queued == []


class TestInlineExecutor:
    """A hop on the inline executor lands a context on the loop thread itself."""

    def test_the_hop_moves_a_local_of_another_thread_onto_the_loop(self) -> None:
        def capture() -> contextvars.Context:
            _local.value = "elsewhere"
            return contextvars.copy_context()

        captured = _in_thread(capture)
        land = hop_on(threading.get_ident, InlineExecutor())

        async def landed() -> tuple[int, object]:
            ident = await land()
            return ident, getattr(_local, "value", None)

        async def main() -> tuple[int, object, int, object]:
            before = parent_loop()
            loop = asyncio.get_running_loop()
            ident, value = await loop.create_task(landed(), context=captured.copy())
            return ident, value, threading.get_ident(), (before, parent_loop())

        ident, value, here, (before, after) = asyncio.run(main())
        assert ident == here
        assert value == "elsewhere"
        assert after is before


class TestBridgeContract:
    """A coroutine the bridge drives shares the request thread and its `Local`."""

    def test_a_thread_sensitive_call_of_the_coroutine_runs_on_the_request_thread(
        self,
    ) -> None:
        async def hop() -> int:
            return await sync_to_async(threading.get_ident)()

        def scenario() -> tuple[int, int]:
            return drive(hop), threading.get_ident()

        reached, request = _in_thread(scenario)
        assert reached == request

    def test_a_local_crosses_into_the_coroutine_and_back(self) -> None:
        async def switch() -> object:
            seen = getattr(_local, "value", None)
            _local.value = "by the coroutine"
            return seen

        def scenario() -> tuple[object, object]:
            _local.value = "by the view"
            return drive(switch), _local.value

        assert _in_thread(scenario) == ("by the view", "by the coroutine")


class TestVerdict:
    """The installed asgiref is judged by its private surface and its release."""

    def test_the_installed_asgiref_is_tested(self) -> None:
        assert TESTED[0] <= release() < TESTED[1]
        assert verdict() == Verdict()
        check_asgiref()
        assert check_asgiref_release() == []

    @pytest.mark.parametrize(
        ("holder", "name"),
        [
            (AsyncToSync, "executors"),
            (SyncToAsync, "threadlocal"),
            (SyncToAsync, "thread_sensitive_context"),
        ],
    )
    def test_a_missing_attribute_is_named(
        self, monkeypatch: pytest.MonkeyPatch, holder: type, name: str
    ) -> None:
        monkeypatch.delattr(holder, name)
        with pytest.raises(
            ImproperlyConfigured, match=rf"lacks {holder.__name__}\.{name},"
        ):
            check_asgiref()

    def test_the_error_names_the_floor_and_the_check_shares_its_text(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delattr(AsyncToSync, "executors")
        with pytest.raises(ImproperlyConfigured) as raised:
            check_asgiref()
        assert str(raised.value).endswith("Install asgiref 3.12 or later.")
        [error] = check_asgiref_release()
        assert (error.id, error.msg) == ("next.E244", str(raised.value))
        assert error.hint == "Install asgiref>=3.12,<3.14."

    def test_attributes_of_another_kind_count_as_missing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(AsyncToSync, "executors", object())
        monkeypatch.setattr(SyncToAsync, "threadlocal", {})
        monkeypatch.setattr(SyncToAsync, "thread_sensitive_context", None)
        expected = (
            "lacks AsyncToSync.executors, SyncToAsync.threadlocal, "
            "SyncToAsync.thread_sensitive_context,"
        )
        with pytest.raises(ImproperlyConfigured, match=expected):
            check_asgiref()

    def test_an_executor_without_the_walked_attributes_is_named(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(hops, "CurrentThreadExecutor", _Bare)
        expected = (
            "lacks CurrentThreadExecutor._work_ready, CurrentThreadExecutor._broken, "
            "CurrentThreadExecutor._old_executor,"
        )
        with pytest.raises(ImproperlyConfigured, match=expected):
            check_asgiref()

    def test_a_release_below_the_floor_is_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(asgiref, "__version__", "3.11.0")
        with pytest.raises(ImproperlyConfigured, match=r"asgiref 3\.11 predates 3\.12"):
            check_asgiref()
        [error] = check_asgiref_release()
        assert error.id == "next.E244"

    @pytest.mark.parametrize(
        ("version", "named"),
        [("3.14.0", "3.14"), ("4.0", "4.0"), ("main", "of an unknown release")],
    )
    def test_an_untested_release_warns_once(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        version: str,
        named: str,
    ) -> None:
        monkeypatch.setattr(asgiref, "__version__", version)
        with caplog.at_level(logging.WARNING, logger="next.bridge.hops"):
            check_asgiref()
            check_asgiref()
        [record] = [r for r in caplog.records if r.name == "next.bridge.hops"]
        assert record.getMessage().startswith(f"The installed asgiref {named} is ")
        [warning] = check_asgiref_release()
        assert (warning.id, warning.msg) == ("next.W188", record.getMessage())
        assert warning.hint == (
            "Pin asgiref>=3.12,<3.14 until next.dj lists the release as tested."
        )

    def test_a_verdict_is_kept_for_the_process(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        check_asgiref()
        monkeypatch.setattr(AsyncToSync, "executors", object())
        check_asgiref()
        assert check_asgiref_release() == []

    def test_a_refusal_is_kept_for_the_process(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(AsyncToSync, "executors", object())
        with pytest.raises(ImproperlyConfigured):
            check_asgiref()
        monkeypatch.undo()
        with pytest.raises(ImproperlyConfigured, match=r"AsyncToSync\.executors"):
            check_asgiref()
