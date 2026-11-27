import asyncio
import contextvars
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import pytest
from asgiref.local import Local
from asgiref.sync import sync_to_async

from next.bridge import context, request
from next.bridge.context import ContextSnapshot
from next.bridge.deadline import deadline
from next.bridge.loop import background_loop
from tests.support.hops import stale_marks
from tests.support.waits import WAIT


_view_local = Local()

UNSERVED = "The request thread no longer serves this coroutine."


def _in_thread(target: Callable[[], object]) -> list[object]:
    outcome: list[object] = []

    def run() -> None:
        try:
            outcome.append(target())
        except (RuntimeError, SystemExit, asyncio.CancelledError) as error:
            outcome.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(WAIT)
    assert not thread.is_alive()
    return outcome


class TestContextSnapshot:
    """A snapshot reads as itself on its thread and as a kept copy elsewhere."""

    def test_the_capturing_thread_gets_the_context_itself(self) -> None:
        context = contextvars.copy_context()
        assert ContextSnapshot(context).get() is context

    def test_another_thread_gets_none_until_it_keeps_a_copy(self) -> None:
        captured = ContextSnapshot(contextvars.Context(), thread=-1)
        assert captured.get() is None
        kept = contextvars.Context()
        assert captured.keep(kept) is kept
        assert captured.get() is kept


_marker = contextvars.ContextVar[str]("tasks_marker", default="unset")
_local = Local()
_REUSE_ATTEMPTS = 50


def _read_marks() -> tuple[str, str]:
    return _marker.get(), getattr(_local, "value", "unset")


def _on_thread(call: Callable[[], object]) -> int:
    seen: list[int] = []

    def body() -> None:
        seen.append(threading.get_ident())
        call()

    thread = threading.Thread(target=body)
    thread.start()
    thread.join(WAIT)
    assert not thread.is_alive()
    return seen[0]


class TestReusedThreadIdent:
    """A thread that reuses the ident of a dead one reads what a fresh restore reads."""

    def test_a_kept_copy_serves_a_successor_like_its_own_restore(self) -> None:
        def view() -> ContextSnapshot:
            _marker.set("request")
            _local.value = "request"
            return ContextSnapshot(contextvars.copy_context())

        snapshot = contextvars.Context().run(view)
        reads: dict[str, tuple[str, str]] = {}

        def first() -> None:
            reads["first"] = snapshot.restore().copy().run(_read_marks)

        def successor() -> None:
            kept = snapshot.get()
            if kept is None:
                return
            fresh = ContextSnapshot(snapshot.context, snapshot.thread)
            reads["kept"] = kept.copy().run(_read_marks)
            reads["fresh"] = fresh.restore().copy().run(_read_marks)

        dead = _on_thread(first)
        for _ in range(_REUSE_ATTEMPTS):
            if _on_thread(successor) == dead and "kept" in reads:
                break
        else:
            pytest.skip("the platform never reused a thread ident")
        assert reads == dict.fromkeys(
            ("first", "kept", "fresh"), ("request", "request")
        )


def _captured_elsewhere() -> ContextSnapshot:
    def capture() -> ContextSnapshot:
        _view_local.value = "set"
        return ContextSnapshot(contextvars.copy_context())

    with ThreadPoolExecutor(1) as pool:
        return pool.submit(capture).result(WAIT)


def _read(context: contextvars.Context) -> object:
    return context.run(getattr, _view_local, "value", None)


def _marked(state: str) -> Callable[[], object]:
    """Return a call that reads `_view_local` on a thread a stopped loop marked."""

    def read() -> object:
        loop = stale_marks(close=state == "closed")
        try:
            return _read(_captured_elsewhere().restore())
        finally:
            loop.close()

    return read


class TestRestore:
    """Tests for ``ContextSnapshot.restore`` restoring a snapshot on a sync thread."""

    def test_the_capturing_thread_gets_the_context_itself(self) -> None:
        captured = ContextSnapshot(contextvars.copy_context())
        assert captured.restore() is captured.context

    def test_the_background_loop_carries_a_context_captured_elsewhere(self) -> None:
        captured = _captured_elsewhere()
        restored = captured.restore()
        assert _read(restored) == "set"
        assert captured.restore() is restored

    def test_the_server_loop_of_the_thread_carries_it_instead(self) -> None:
        def read() -> object:
            return _read(_captured_elsewhere().restore())

        async def server() -> object:
            return await sync_to_async(read)()

        assert asyncio.run(server()) == "set"
        assert background_loop.current() is None

    @pytest.mark.parametrize("state", ["stopped", "closed"])
    def test_a_server_loop_that_stopped_leaves_the_carry_to_the_background(
        self, state: str
    ) -> None:
        assert _in_thread(_marked(state)) == ["set"]

    def test_a_thread_running_a_loop_is_refused(self) -> None:
        async def on_the_loop() -> None:
            _captured_elsewhere().restore()

        with pytest.raises(RuntimeError, match="running an event loop"):
            asyncio.run(on_the_loop())

    def test_the_wait_ends_at_the_deadline(self) -> None:
        captured = _captured_elsewhere()
        release = threading.Event()
        background_loop.call_soon(release.wait, WAIT)
        try:
            with deadline(time.monotonic() + 0.05), pytest.raises(TimeoutError):
                captured.restore()
        finally:
            release.set()
        assert captured.get() is None

    @pytest.mark.parametrize(
        "error", [RuntimeError(UNSERVED), asyncio.CancelledError(UNSERVED)]
    )
    def test_a_failing_hop_raises_on_the_waiting_thread(
        self, error: BaseException, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def refused(_settle: object) -> None:
            raise error

        monkeypatch.setattr(request, "_ARRIVE", refused)
        with pytest.raises(type(error), match=UNSERVED):
            _captured_elsewhere().restore()


class TestArestore:
    """Tests for ``ContextSnapshot.arestore`` restoring a snapshot on the running loop."""

    def test_the_capturing_loop_gets_the_context_itself(self) -> None:
        async def scenario() -> bool:
            captured = ContextSnapshot(contextvars.copy_context())
            return await captured.arestore() is captured.context

        assert asyncio.run(scenario()) is True

    def test_one_hop_carries_it_for_every_caller_on_the_loop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured = _captured_elsewhere()
        landings: list[int] = []
        landed = context._landed

        async def counted() -> contextvars.Context:
            landings.append(threading.get_ident())
            return await landed()

        monkeypatch.setattr(context, "_landed", counted)

        async def scenario() -> tuple[object, bool]:
            first, second = await asyncio.gather(
                captured.arestore(), captured.arestore()
            )
            again = await captured.arestore()
            return _read(first), first is second is again

        assert asyncio.run(scenario()) == ("set", True)
        assert len(landings) == 1

    def test_a_cancelled_caller_leaves_the_carry_to_the_others(self) -> None:
        captured = _captured_elsewhere()

        async def scenario() -> object:
            cancelled = asyncio.ensure_future(captured.arestore())
            waiting = asyncio.ensure_future(captured.arestore())
            await asyncio.sleep(0)
            cancelled.cancel()
            return _read(await waiting)

        assert asyncio.run(scenario()) == "set"

    def test_a_saturated_default_executor_holds_no_carry_up(self) -> None:
        captured = _captured_elsewhere()

        async def scenario() -> object:
            loop = asyncio.get_running_loop()
            loop.set_default_executor(ThreadPoolExecutor(max_workers=1))
            released = threading.Event()
            blocker = loop.run_in_executor(None, released.wait, WAIT)

            async def carried() -> object:
                restored = await captured.arestore()
                released.set()
                return _read(restored)

            try:
                return await asyncio.wait_for(carried(), WAIT)
            finally:
                released.set()
                await blocker

        assert asyncio.run(scenario()) == "set"


class TestLandingContext:
    """The hop that lands a snapshot on a loop runs in a copy of the captured context."""

    def test_the_restored_context_carries_the_captured_values(self) -> None:
        marker = contextvars.ContextVar[str]("marker")
        captured_context = contextvars.Context()
        captured_context.run(marker.set, "captured")
        captured = ContextSnapshot(captured_context, thread=-1)

        restored = asyncio.run(captured.arestore())
        assert restored is not captured_context
        assert restored[marker] == "captured"
