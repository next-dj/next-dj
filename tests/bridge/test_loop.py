import asyncio
import contextvars
import gc
import logging
import re
import threading
import time
import weakref

import pytest
from django.core.exceptions import SynchronousOnlyOperation
from django.test import override_settings

from next.bridge.loop import background_loop, running_loop
from tests.bridge.shared import connect, fail, interrupt
from tests.support.starts import Request, held, spied_starts
from tests.support.waits import WAIT


async def _executor_thread() -> threading.Thread:
    return await asyncio.get_running_loop().run_in_executor(
        None, threading.current_thread
    )


class TestBackgroundLoop:
    """Tests for ``BackgroundLoop``."""

    def test_no_thread_exists_before_the_first_get(self, loop_owner) -> None:
        before = set(threading.enumerate())
        assert loop_owner.current() is None
        assert set(threading.enumerate()) <= before
        loop = loop_owner.get()
        started = set(threading.enumerate()) - before
        assert [thread.name for thread in started] == ["next-background-loop"]
        assert all(thread.daemon for thread in started)
        assert loop_owner.get() is loop
        assert loop_owner.current() is loop
        loop_owner.stop()
        assert not any(thread.is_alive() for thread in started)
        assert loop.is_closed()

    def test_the_first_caller_leaves_no_context_on_the_loop_or_its_thread(
        self, loop_owner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen = spied_starts(monkeypatch)

        def first() -> weakref.ref[Request]:
            request = Request()
            held.set(request)
            loop_owner.get()
            return weakref.ref(request)

        pinned = contextvars.Context().run(first)
        gc.collect()
        assert pinned() is None
        assert seen == [None]
        assert loop_owner.current() is not None

    def test_the_loop_runs_submitted_coroutines(self, loop_owner) -> None:
        future = asyncio.run_coroutine_threadsafe(
            asyncio.sleep(0, "done"), loop_owner.get()
        )
        assert future.result(WAIT) == "done"

    def test_call_soon_runs_the_callback_on_the_loop(self, loop_owner) -> None:
        ran = threading.Event()
        loop = loop_owner.call_soon(ran.set)
        assert ran.wait(WAIT)
        assert loop is loop_owner.current()

    @pytest.mark.django_db()
    def test_a_database_connection_on_the_loop_raises(self, loop_owner) -> None:
        future = asyncio.run_coroutine_threadsafe(connect(), loop_owner.get())
        with pytest.raises(SynchronousOnlyOperation):
            future.result(WAIT)

    def test_stop_without_a_loop_does_nothing(self, loop_owner) -> None:
        loop_owner.stop()
        assert loop_owner.current() is None

    def test_forget_drops_the_loop_as_a_forked_child_must(self, loop_owner) -> None:
        inherited = loop_owner.get()
        running = loop_owner._running
        assert running is not None
        loop_owner.forget()
        assert loop_owner.current() is None
        assert loop_owner.get() is not inherited
        loop_owner.stop()
        running.halt()
        running.thread.join(WAIT)
        assert not running.thread.is_alive()

    def test_get_replaces_a_loop_whose_thread_ended(self, loop_owner) -> None:
        ended = loop_owner.get()
        running = loop_owner._running
        assert running is not None
        running.halt()
        running.thread.join(WAIT)
        assert ended.is_closed()
        fresh = loop_owner.get()
        assert fresh is not ended
        future = asyncio.run_coroutine_threadsafe(asyncio.sleep(0, "done"), fresh)
        assert future.result(WAIT) == "done"

    def test_stop_skips_a_loop_whose_thread_ended(self, loop_owner) -> None:
        loop_owner.get()
        running = loop_owner._running
        assert running is not None
        running.halt()
        running.thread.join(WAIT)
        loop_owner.stop()
        assert loop_owner.current() is None

    def test_a_stray_loop_stop_does_not_end_the_thread(self, loop_owner) -> None:
        loop = loop_owner.get()
        loop.call_soon_threadsafe(loop.stop)
        future = asyncio.run_coroutine_threadsafe(asyncio.sleep(0, "done"), loop)
        assert future.result(WAIT) == "done"
        assert loop_owner.get() is loop

    def test_an_interrupt_raised_by_a_callback_is_logged_and_the_loop_resumes(
        self, loop_owner, caplog: pytest.LogCaptureFixture
    ) -> None:
        loop = loop_owner.get()
        loop_owner.call_soon(interrupt)
        future = asyncio.run_coroutine_threadsafe(asyncio.sleep(0, "done"), loop)
        assert future.result(WAIT) == "done"
        assert loop_owner.get() is loop
        reports = [r for r in caplog.records if r.name == "next.bridge"]
        assert [r.getMessage() for r in reports] == [
            "A task raised an interrupt on the loop."
        ]
        assert reports[0].exc_info is not None
        assert reports[0].exc_info[0] is KeyboardInterrupt

    def test_stop_cancels_the_tasks_left_and_lets_them_settle(self, loop_owner) -> None:
        started = threading.Event()
        reasons: list[tuple[object, ...]] = []

        async def pending() -> None:
            started.set()
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError as error:
                reasons.append(error.args)
                raise

        future = asyncio.run_coroutine_threadsafe(pending(), loop_owner.get())
        assert started.wait(WAIT)
        loop_owner.stop()
        assert future.cancelled()
        assert reasons == [
            ("The background loop stopped before the coroutine finished.",)
        ]

    def test_stop_finalises_an_async_generator_left_open(self, loop_owner) -> None:
        finalised = threading.Event()
        opened = threading.Event()

        async def numbers():
            try:
                yield 1
                yield 2
            finally:
                finalised.set()

        async def take_one() -> None:
            async for _ in numbers():
                opened.set()
                break

        asyncio.run_coroutine_threadsafe(take_one(), loop_owner.get()).result(WAIT)
        assert opened.is_set()
        loop_owner.stop()
        assert finalised.is_set()

    def test_stop_shuts_the_default_executor_down(self, loop_owner) -> None:
        worker = asyncio.run_coroutine_threadsafe(_executor_thread(), loop_owner.get())
        thread = worker.result(WAIT)
        assert thread.is_alive()
        loop_owner.stop()
        assert not thread.is_alive()

    def test_debug_turns_on_slow_callback_detection_on_the_asyncio_logger(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with override_settings(DEBUG=True):
            loop = background_loop.get()
        assert loop.get_debug()
        assert loop.slow_callback_duration == pytest.approx(0.1)
        finished = threading.Event()
        background_loop.call_soon(time.sleep, 0.15)
        background_loop.call_soon(finished.set)
        assert finished.wait(WAIT)
        slow = [r for r in caplog.records if "took" in r.getMessage()]
        assert slow
        assert {r.name for r in slow} == {"asyncio"}
        assert {r.levelno for r in slow} == {logging.WARNING}

    def test_a_callback_failure_on_the_loop_reaches_the_package_logger(
        self, loop_owner, caplog: pytest.LogCaptureFixture
    ) -> None:
        loop = loop_owner.get()
        loop_owner.call_soon(fail)
        future = asyncio.run_coroutine_threadsafe(asyncio.sleep(0, "done"), loop)
        assert future.result(WAIT) == "done"
        reports = [r for r in caplog.records if r.name == "next.bridge"]
        assert len(reports) == 1
        message = reports[0].getMessage()
        assert message.startswith("Exception in callback fail()")
        assert re.search(r"handle: <\w*Handle fail\(\)", message)
        assert reports[0].exc_info is not None
        assert reports[0].exc_info[0] is ValueError
        assert not [r for r in caplog.records if r.name == "asyncio"]

    def test_the_package_logger_leaves_the_asyncio_logger_unfiltered(self) -> None:
        background_loop.get()
        assert logging.getLogger("asyncio").filters == []


class TestRunningLoop:
    """Tests for ``running_loop``."""

    def test_it_names_the_loop_of_the_calling_thread(self) -> None:
        async def inside() -> asyncio.AbstractEventLoop | None:
            return running_loop()

        assert running_loop() is None
        assert asyncio.run(inside()) is not None
