import asyncio
import contextlib
import contextvars
import os
import threading
import time
import traceback
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from functools import partial

import asgiref
import pytest
from asgiref.local import Local
from asgiref.sync import (
    AsyncToSync,
    SyncToAsync,
    ThreadSensitiveContext,
    async_to_sync,
    sync_to_async,
)
from django.contrib.auth.models import Group
from django.core.exceptions import ImproperlyConfigured, SynchronousOnlyOperation
from django.http import HttpRequest, HttpResponse
from django.test import AsyncClient, Client, override_settings
from django.urls import path
from django.utils import translation
from django.utils.asyncio import async_unsafe

from next.defer import deadline
from next.deps import Loader
from next.deps.cache import RequestMemo, request_memo
from next.deps.loader import open_loaders
from next.deps.scope import RequestScope
from next.ports import context_propagator_slot
from next.suspense import bridge, drive, executors, serve_until
from next.suspense.bridge import BridgePropagator, contexts_carry_locals
from next.suspense.executors import background_loop
from next.tasks import ContextSnapshot
from tests.support.errors import fail_hop
from tests.support.waits import QUIET, WAIT


@async_unsafe
def _unsafe() -> int:
    return threading.get_ident()


def _interrupt() -> None:
    raise KeyboardInterrupt


async def _answer() -> int:
    return 42


async def _hop_ident() -> int:
    return await sync_to_async(threading.get_ident)()


async def _raise_value() -> None:
    msg = "coroutine failed"
    raise ValueError(msg)


async def _cancel_self() -> None:
    msg = "why"
    raise asyncio.CancelledError(msg)


async def _leave() -> None:
    raise SystemExit(1)


_view_local = Local()

UNSERVED = "The request thread no longer serves this coroutine."


class _Ambiguous:
    """A value whose inequality with any other value raises."""

    def __ne__(self, other: object) -> bool:
        if other is self:
            return False
        msg = "ambiguous"
        raise ValueError(msg)


class _Bare:
    """Stand-in for an asgiref executor class without the attributes the walk reads."""


def _group_names(keys: Sequence[int]) -> dict[int, str]:
    return {group.pk: group.name for group in Group.objects.filter(pk__in=keys)}


_names = Loader(_group_names)


def _load_here(pk: int) -> tuple[str, int]:
    return _names.load_sync(pk), threading.get_ident()


async def _load_on_a_hop(pk: int) -> tuple[str, int]:
    scope = RequestScope()
    token = request_memo.set(RequestMemo(scope))
    open_loaders(scope, ContextSnapshot(contextvars.copy_context()))
    try:
        return await sync_to_async(partial(_load_here, pk))()
    finally:
        await scope.close()
        request_memo.reset(token)


def _group_view(request: HttpRequest, pk: int) -> HttpResponse:
    name, hop = drive(_load_on_a_hop(pk))
    return HttpResponse(
        f"{request.method} {name} {hop == threading.get_ident()}",
        content_type="text/plain",
    )


urlpatterns = [path("groups/<int:pk>/", _group_view)]


def _in_thread(target: Callable[[], object]) -> list[object]:
    outcome: list[object] = []

    def run() -> None:
        try:
            outcome.append(target())
        except (RuntimeError, SystemExit, asyncio.CancelledError) as error:
            outcome.append(error)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(5)
    assert not thread.is_alive()
    return outcome


class TestDriveBasics:
    """Tests for ``drive`` results, errors and the loop it starts."""

    def test_returns_the_result_of_a_coroutine_or_a_factory(self) -> None:
        assert drive(_answer()) == 42
        assert drive(_answer) == 42

    def test_reraises_the_coroutine_error_with_its_traceback(self) -> None:
        with pytest.raises(ValueError, match="coroutine failed") as caught:
            drive(_raise_value)
        frames = [
            frame.name for frame in traceback.extract_tb(caught.value.__traceback__)
        ]
        assert frames[-1] == "_raise_value"

    def test_starts_one_daemon_thread_on_first_use(self) -> None:
        before = set(threading.enumerate())
        assert background_loop.current() is None
        drive(_answer)
        started = set(threading.enumerate()) - before
        assert [thread.name for thread in started] == ["next-background-loop"]

    def test_an_interrupt_raised_by_the_coroutine_keeps_the_loop(self) -> None:
        async def leave() -> None:
            raise SystemExit(3)

        with pytest.raises(SystemExit) as caught:
            drive(leave)
        assert caught.value.code == 3
        loop = background_loop.current()
        assert loop is not None
        assert loop.is_running()
        assert drive(_answer) == 42
        assert background_loop.current() is loop

    def test_a_coroutine_that_cancels_itself_reports_cancellation(self) -> None:
        with pytest.raises(asyncio.CancelledError, match="why"):
            drive(_cancel_self)

    def test_an_interrupt_in_a_child_task_spares_the_other_drives(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        started = threading.Event()
        release: Future[None] = Future()

        async def bystander() -> str:
            started.set()
            await asyncio.wrap_future(release)
            return "spared"

        async def victim() -> None:
            await asyncio.gather(_leave(), asyncio.sleep(0.05))

        results: list[list[object]] = []
        waiting = threading.Thread(
            target=lambda: results.append(_in_thread(lambda: drive(bystander))),
            daemon=True,
        )
        waiting.start()
        assert started.wait(5)
        loop = background_loop.current()
        [failed] = _in_thread(lambda: drive(victim))
        release.set_result(None)
        waiting.join(5)
        assert not waiting.is_alive()
        assert isinstance(failed, SystemExit)
        assert results == [["spared"]]
        assert drive(_answer) == 42
        assert background_loop.current() is loop
        reports = [r for r in caplog.records if r.name == "next.suspense"]
        assert [r.exc_info[0] for r in reports if r.exc_info] == [SystemExit]

    def test_stop_settles_a_drive_in_flight_with_a_clear_error(self) -> None:
        started = threading.Event()

        async def pending() -> None:
            started.set()
            await asyncio.sleep(30)

        outcome: list[list[object]] = []
        waiting = threading.Thread(
            target=lambda: outcome.append(_in_thread(lambda: drive(pending))),
            daemon=True,
        )
        waiting.start()
        assert started.wait(5)
        background_loop.stop()
        waiting.join(5)
        assert not waiting.is_alive()
        [[error]] = outcome
        assert isinstance(error, asyncio.CancelledError)
        assert error.args == (
            "The background loop stopped before the coroutine finished.",
        )

    def test_the_asgiref_check_runs_once_per_process(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert drive(_answer) == 42
        monkeypatch.setattr(executors, "CurrentThreadExecutor", _Bare)
        assert drive(_answer) == 42

    def test_driving_from_a_running_loop_raises_and_closes_the_coroutine(self) -> None:
        coroutine = _answer()

        async def inside() -> None:
            with pytest.raises(RuntimeError, match="running an event loop"):
                drive(coroutine)
            with pytest.raises(RuntimeError, match="await it directly"):
                drive(_answer)

        asyncio.run(inside())
        assert coroutine.cr_frame is None

    def test_a_broken_asgiref_contract_refuses_before_the_loop_starts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delattr(AsyncToSync, "executors")
        with pytest.raises(ImproperlyConfigured, match=r"AsyncToSync\.executors"):
            drive(_answer)
        assert background_loop.current() is None


class TestDriveHops:
    """Tests for ``drive`` routing thread-sensitive calls to the calling thread."""

    def test_hops_land_on_the_calling_thread(self) -> None:
        assert drive(_hop_ident) == threading.get_ident()

    def test_an_async_unsafe_call_runs_in_a_hop_but_not_on_the_loop(self) -> None:
        async def both() -> int:
            with pytest.raises(SynchronousOnlyOperation):
                _unsafe()
            return await sync_to_async(_unsafe)()

        assert drive(both) == threading.get_ident()

    @pytest.mark.django_db()
    def test_the_async_orm_runs_in_the_request_transaction(self) -> None:
        Group.objects.create(name="editors")

        async def read() -> tuple[str, int]:
            group = await Group.objects.aget(name="editors")
            return group.name, await sync_to_async(threading.get_ident)()

        assert drive(read) == ("editors", threading.get_ident())

    def test_a_failing_hop_raises_inside_the_coroutine(self) -> None:
        async def recover() -> str:
            try:
                await sync_to_async(fail_hop)()
            except ValueError as error:
                return str(error)
            return "unreached"

        assert drive(recover) == "hop failed"

    def test_loop_markers_are_restored_so_async_to_sync_still_works(self) -> None:
        async def loop_ident() -> int:
            return threading.get_ident()

        background_ident = drive(loop_ident)
        assert drive(_hop_ident) == threading.get_ident()
        loop = background_loop.current()
        assert getattr(SyncToAsync.threadlocal, "main_event_loop", None) is not loop
        assert async_to_sync(loop_ident)() not in {
            background_ident,
            threading.get_ident(),
        }

    def test_a_stale_marker_of_a_closed_loop_is_ignored(self) -> None:
        async_to_sync(_hop_ident)()
        assert drive(_hop_ident) == threading.get_ident()

    def test_concurrent_drives_share_one_loop_and_keep_their_hops(self) -> None:
        outcomes: dict[int, tuple[set[int], asyncio.AbstractEventLoop]] = {}
        together = threading.Barrier(6)

        async def many() -> tuple[set[int], asyncio.AbstractEventLoop]:
            idents = set()
            for _ in range(10):
                idents.add(await sync_to_async(threading.get_ident)())
                await asyncio.sleep(0)
            return idents, asyncio.get_running_loop()

        def request() -> None:
            # Every thread is alive before one drives, so no ident is reused as a key.
            together.wait(10)
            outcomes[threading.get_ident()] = drive(many)

        threads = [threading.Thread(target=request, daemon=True) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(10)
        assert not any(thread.is_alive() for thread in threads)
        assert len(outcomes) == 6
        assert all(idents == {ident} for ident, (idents, _) in outcomes.items())
        assert len({loop for _, loop in outcomes.values()}) == 1
        loops = [t for t in threading.enumerate() if t.name == "next-background-loop"]
        assert len(loops) == 1

    def test_a_hop_that_drives_again_shares_the_calling_thread(self) -> None:
        def nested() -> int:
            return drive(_hop_ident)

        async def outer() -> int:
            return await sync_to_async(nested)()

        assert drive(outer) == threading.get_ident()

    def test_a_hop_reaching_the_request_executor_from_its_thread_raises(self) -> None:
        def own_loop() -> int:
            return asyncio.run(_hop_ident())

        async def outer() -> int:
            return await sync_to_async(own_loop)()

        [error] = _in_thread(lambda: drive(outer))
        assert isinstance(error, RuntimeError)
        assert str(error) == (
            "You cannot submit onto RequestThreadExecutor from its own thread"
        )

    def test_a_late_hop_after_a_nested_async_to_sync_gets_asgirefs_error(self) -> None:
        errors: list[str] = []
        late_tasks: list[asyncio.Task[None]] = []
        returned = asyncio.Event()

        async def late() -> None:
            await returned.wait()
            try:
                await sync_to_async(threading.get_ident)()
            except RuntimeError as error:
                errors.append(str(error))

        async def spawn() -> None:
            late_tasks.append(asyncio.create_task(late()))

        def nested() -> None:
            async_to_sync(spawn)()

        async def outer() -> None:
            await sync_to_async(nested)()
            returned.set()
            async with asyncio.timeout(5):
                await late_tasks[0]

        drive(outer)
        assert errors == ["CurrentThreadExecutor already quit or is broken"]

    def test_hops_after_the_coroutine_finished_are_rejected(self) -> None:
        errors: list[str] = []
        finished = threading.Event()
        returned: Future[None] = Future()
        late_tasks: list[asyncio.Task[None]] = []

        async def late() -> None:
            await asyncio.wrap_future(returned)
            try:
                await sync_to_async(threading.get_ident)()
            except RuntimeError as error:
                errors.append(str(error))
            finally:
                finished.set()

        async def spawn() -> int:
            late_tasks.append(asyncio.create_task(late()))
            return 1

        assert drive(spawn) == 1
        returned.set_result(None)
        assert finished.wait(5)
        assert errors == ["The request thread no longer serves this coroutine."]


class TestDriveCancellation:
    """Tests for ``drive`` cancelling the coroutine when the caller fails."""

    def test_an_interrupt_while_waiting_cancels_and_drains_the_coroutine(self) -> None:
        cleanup: list[int] = []
        finished: list[bool] = []

        async def side() -> None:
            with contextlib.suppress(RuntimeError, asyncio.CancelledError):
                await sync_to_async(_interrupt)()

        async def waiting() -> None:
            helper = asyncio.ensure_future(side())
            try:
                await asyncio.sleep(5)
                finished.append(True)
            finally:
                cleanup.append(await sync_to_async(threading.get_ident)())
                await helper

        with pytest.raises(KeyboardInterrupt):
            drive(waiting)
        assert finished == []
        assert cleanup == [threading.get_ident()]


class _FailingPropagator:
    """A propagator whose loop side raises `error`, as a broken custom one would."""

    def __init__(self, error: BaseException) -> None:
        self.error = error

    def restore(self, captured: ContextSnapshot, /) -> contextvars.Context:
        return captured.context

    async def arestore(self, captured: ContextSnapshot, /) -> contextvars.Context:
        raise self.error


@contextlib.contextmanager
def _bound(propagator: object) -> Iterator[None]:
    previous = context_propagator_slot.get()
    context_propagator_slot.set(propagator)
    try:
        yield
    finally:
        context_propagator_slot.set(previous)


class TestDriveCarry:
    """Tests for ``drive`` restoring the context through the bound propagator."""

    def test_a_failed_handback_brings_the_writes_back_by_a_hop_of_its_own(
        self, hops: BridgePropagator, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def refused(*_args: object) -> None:
            raise RuntimeError(UNSERVED)

        async def write() -> int:
            _view_local.value = "by the coroutine"
            return await _answer()

        def scenario() -> tuple[int, object]:
            _view_local.value = "by the view"
            return drive(write), _view_local.value

        monkeypatch.setattr(bridge, "_handed", refused)
        assert contextvars.copy_context().run(scenario) == (42, "by the coroutine")

    def test_a_cancelled_handback_brings_the_writes_back_by_a_hop_of_its_own(
        self, hops: BridgePropagator
    ) -> None:
        gate = threading.Event()
        helpers: list[asyncio.Future[None]] = []

        async def busy() -> None:
            await sync_to_async(gate.wait)(WAIT)

        async def write() -> int:
            loop = asyncio.get_running_loop()
            body = asyncio.current_task()
            assert body is not None
            helpers.append(asyncio.ensure_future(busy()))
            await asyncio.sleep(0)
            loop.call_later(QUIET, body.cancel)
            loop.call_later(2 * QUIET, gate.set)
            _view_local.value = "by the coroutine"
            return 42

        def scenario() -> tuple[int, object]:
            _view_local.value = "by the view"
            return drive(write), _view_local.value

        assert contextvars.copy_context().run(scenario) == (42, "by the coroutine")

    @pytest.mark.parametrize(
        "error", [ValueError("no carry"), asyncio.CancelledError("no carry")]
    )
    def test_a_carry_that_fails_on_the_loop_fails_the_drive(
        self, error: BaseException
    ) -> None:
        with (
            _bound(_FailingPropagator(error)),
            pytest.raises(type(error), match="no carry"),
        ):
            drive(_answer)


def _captured_elsewhere() -> ContextSnapshot:
    def capture() -> ContextSnapshot:
        _view_local.value = "set"
        return ContextSnapshot(contextvars.copy_context())

    with ThreadPoolExecutor(1) as pool:
        return pool.submit(capture).result()


def _read(context: contextvars.Context) -> object:
    return context.run(getattr, _view_local, "value", None)


def _marked(loop: asyncio.AbstractEventLoop) -> Callable[[], object]:
    """Return a call that reads `_view_local` on a thread a server `loop` marks."""

    def read() -> object:
        SyncToAsync.threadlocal.main_event_loop = loop
        SyncToAsync.threadlocal.main_event_loop_pid = os.getpid()
        return _read(BridgePropagator(plain=False).restore(_captured_elsewhere()))

    return read


class TestBridgePropagator:
    """Tests for ``BridgePropagator.restore`` restoring a snapshot on a sync thread."""

    def test_the_capturing_thread_gets_the_context_itself(self) -> None:
        captured = ContextSnapshot(contextvars.copy_context())
        assert BridgePropagator(plain=False).restore(captured) is captured.context

    def test_a_plain_carry_hands_the_captured_context_back_without_a_hop(self) -> None:
        captured = _captured_elsewhere()
        assert BridgePropagator(plain=True).restore(captured) is captured.context
        assert background_loop.current() is None

    def test_the_background_loop_carries_a_context_captured_elsewhere(self) -> None:
        propagator = BridgePropagator(plain=False)
        captured = _captured_elsewhere()
        context = propagator.restore(captured)
        assert _read(context) == "set"
        assert propagator.restore(captured) is context

    def test_the_server_loop_of_the_thread_carries_it_instead(self) -> None:
        def read() -> object:
            return _read(BridgePropagator(plain=False).restore(_captured_elsewhere()))

        async def server() -> object:
            return await sync_to_async(read)()

        assert asyncio.run(server()) == "set"
        assert background_loop.current() is None

    @pytest.mark.parametrize("state", ["stopped", "closed"])
    def test_a_server_loop_that_stopped_leaves_the_carry_to_the_background(
        self, state: str
    ) -> None:
        loop = asyncio.new_event_loop()
        if state == "closed":
            loop.close()
        try:
            assert _in_thread(_marked(loop)) == ["set"]
        finally:
            loop.close()

    def test_a_thread_running_a_loop_is_refused(self) -> None:
        async def on_the_loop() -> None:
            BridgePropagator(plain=False).restore(_captured_elsewhere())

        with pytest.raises(RuntimeError, match="running an event loop"):
            asyncio.run(on_the_loop())

    def test_the_wait_ends_at_the_deadline(self) -> None:
        captured = _captured_elsewhere()
        release = threading.Event()
        background_loop.call_soon(release.wait, WAIT)
        try:
            with deadline(time.monotonic() + 0.05), pytest.raises(TimeoutError):
                BridgePropagator(plain=False).restore(captured)
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

        monkeypatch.setattr(bridge, "_ARRIVE", refused)
        with pytest.raises(type(error), match=UNSERVED):
            BridgePropagator(plain=False).restore(_captured_elsewhere())


class TestBridgePropagatorOnALoop:
    """Tests for ``BridgePropagator.arestore`` restoring a snapshot on the running loop."""

    def test_the_capturing_loop_gets_the_context_itself(self) -> None:
        async def scenario() -> bool:
            captured = ContextSnapshot(contextvars.copy_context())
            return (
                await BridgePropagator(plain=False).arestore(captured)
                is captured.context
            )

        assert asyncio.run(scenario()) is True

    def test_a_plain_carry_hands_the_captured_context_back(self) -> None:
        captured = _captured_elsewhere()

        async def scenario() -> bool:
            return (
                await BridgePropagator(plain=True).arestore(captured)
                is captured.context
            )

        assert asyncio.run(scenario()) is True

    def test_one_hop_carries_it_for_every_caller_on_the_loop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        propagator = BridgePropagator(plain=False)
        captured = _captured_elsewhere()
        landings: list[int] = []
        landed = bridge._landed

        async def counted() -> contextvars.Context:
            landings.append(threading.get_ident())
            return await landed()

        monkeypatch.setattr(bridge, "_landed", counted)

        async def scenario() -> tuple[object, bool]:
            first, second = await asyncio.gather(
                propagator.arestore(captured), propagator.arestore(captured)
            )
            again = await propagator.arestore(captured)
            return _read(first), first is second is again

        assert asyncio.run(scenario()) == ("set", True)
        assert len(landings) == 1

    def test_a_cancelled_caller_leaves_the_carry_to_the_others(self) -> None:
        propagator = BridgePropagator(plain=False)
        captured = _captured_elsewhere()

        async def scenario() -> object:
            cancelled = asyncio.ensure_future(propagator.arestore(captured))
            waiting = asyncio.ensure_future(propagator.arestore(captured))
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
                context = await BridgePropagator(plain=False).arestore(captured)
                released.set()
                return _read(context)

            try:
                return await asyncio.wait_for(carried(), WAIT)
            finally:
                released.set()
                await blocker

        assert asyncio.run(scenario()) == "set"


class TestContextsCarryLocals:
    """Tests for the probe of whether a context shows its `Local` values anywhere."""

    def test_it_tells_whether_asgiref_tags_a_local_with_its_thread(self) -> None:
        major, minor = (int(part) for part in asgiref.__version__.split(".")[:2])
        assert contexts_carry_locals() is ((major, minor) < (3, 12))


class TestDriveContext:
    """Tests for ``drive`` sharing context variables with the caller."""

    def test_variables_are_visible_and_writes_come_back(
        self, carry: BridgePropagator
    ) -> None:
        kept = contextvars.ContextVar[str]("kept")
        written = contextvars.ContextVar[str]("written")
        created = contextvars.ContextVar[str]("created")

        async def touch() -> tuple[str, str]:
            seen = (kept.get(), written.get())
            written.set("by the coroutine")
            created.set("new")
            return seen

        def scenario() -> None:
            kept.set("request")
            written.set("request")
            assert drive(touch) == ("request", "request")
            assert kept.get() == "request"
            assert written.get() == "by the coroutine"
            assert created.get() == "new"
            assert getattr(AsyncToSync.executors, "current", None) is None

        contextvars.copy_context().run(scenario)

    def test_the_coroutine_reads_and_writes_the_language_of_the_view(
        self, hops: BridgePropagator
    ) -> None:
        async def switch() -> str:
            seen = translation.get_language()
            translation.activate("fr")
            return seen

        def scenario() -> None:
            translation.activate("de")
            assert drive(switch) == "de"
            assert translation.get_language() == "fr"

        contextvars.copy_context().run(scenario)

    def test_the_coroutine_reads_and_writes_a_local_of_the_view(
        self, hops: BridgePropagator
    ) -> None:
        async def switch() -> object:
            seen = getattr(_view_local, "value", None)
            _view_local.value = "by the coroutine"
            return seen

        def scenario() -> None:
            _view_local.value = "by the view"
            assert drive(switch) == "by the view"
            assert _view_local.value == "by the coroutine"

        contextvars.copy_context().run(scenario)

    def test_a_value_whose_comparison_raises_comes_back(
        self, carry: BridgePropagator
    ) -> None:
        ambiguous = contextvars.ContextVar[object]("ambiguous")
        written = _Ambiguous()

        async def write() -> None:
            ambiguous.set(written)

        def scenario() -> None:
            ambiguous.set(_Ambiguous())
            drive(write)
            assert ambiguous.get() is written
            assert getattr(AsyncToSync.executors, "current", None) is None

        contextvars.copy_context().run(scenario)


class TestDriveUnderServerLoop:
    """Tests for ``drive`` on a ``sync_to_async`` thread of a running server loop."""

    def test_it_runs_on_the_server_loop_and_hops_to_the_view_thread(self) -> None:
        async def where() -> tuple[asyncio.AbstractEventLoop, int]:
            return asyncio.get_running_loop(), await sync_to_async(
                threading.get_ident
            )()

        def view() -> tuple[asyncio.AbstractEventLoop, int, int]:
            loop, hop = drive(where)
            return loop, hop, threading.get_ident()

        async def server() -> None:
            loop, hop, view_ident = await sync_to_async(view, thread_sensitive=True)()
            assert loop is asyncio.get_running_loop()
            assert hop == view_ident

        asyncio.run(server())
        assert background_loop.current() is None

    def test_a_hop_that_drives_again_stays_on_the_view_thread(self) -> None:
        def nested() -> int:
            return drive(_hop_ident())

        async def outer() -> int:
            return await sync_to_async(nested)()

        def view() -> tuple[int, int]:
            return drive(outer), threading.get_ident()

        async def server() -> tuple[int, int]:
            return await sync_to_async(view, thread_sensitive=True)()

        hop, view_ident = asyncio.run(server())
        assert hop == view_ident
        assert background_loop.current() is None

    def test_a_coroutine_that_cancels_itself_reports_cancellation(self) -> None:
        def view() -> tuple[object, ...]:
            with pytest.raises(asyncio.CancelledError) as caught:
                drive(_cancel_self)
            return caught.value.args

        async def server() -> tuple[object, ...]:
            return await sync_to_async(view, thread_sensitive=True)()

        assert asyncio.run(server()) == ("why",)
        assert background_loop.current() is None

    def test_a_wait_inside_a_hop_serves_the_hop_another_task_needs(self) -> None:
        def view() -> tuple[int, int]:
            done: Future[int] = Future()

            async def other() -> None:
                done.set_result(await sync_to_async(threading.get_ident)())

            def blocked() -> int:
                serve_until(done)
                return done.result()

            async def main() -> int:
                helper = asyncio.create_task(other())
                ident = await sync_to_async(blocked)()
                await helper
                return ident

            return drive(main), threading.get_ident()

        async def server() -> tuple[int, int]:
            async with ThreadSensitiveContext():
                return await sync_to_async(view, thread_sensitive=True)()

        hop, view_ident = asyncio.run(server())
        assert hop == view_ident

    def test_cancelling_the_view_cancels_the_coroutine_and_serves_its_cleanup(
        self,
    ) -> None:
        started = threading.Event()
        cleanup: list[int] = []
        outcome: list[object] = []

        async def slow() -> None:
            started.set()
            try:
                await asyncio.sleep(30)
            finally:
                cleanup.append(await sync_to_async(threading.get_ident)())

        def view() -> None:
            outcome.append(threading.get_ident())
            try:
                drive(slow)
            except asyncio.CancelledError:
                outcome.append("cancelled")
                raise

        async def server() -> None:
            async with ThreadSensitiveContext():
                request = asyncio.create_task(sync_to_async(view)())
                assert await asyncio.to_thread(started.wait, 5)
                request.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await request

        asyncio.run(server())
        view_ident, *rest = outcome
        assert rest == ["cancelled"]
        assert cleanup == [view_ident]


class TestDriveUnderDjangoHandlers:
    """Tests for a sync view that waits on a sync ORM batch under Django's handlers."""

    @pytest.mark.django_db(transaction=True)
    def test_the_async_client_serves_the_batch_hop_on_the_view_thread(self) -> None:
        group = Group.objects.create(name="reviewers")
        with override_settings(ROOT_URLCONF=__name__):
            response = asyncio.run(AsyncClient().get(f"/groups/{group.pk}/"))
        assert response.content == b"GET reviewers True"
        assert background_loop.current() is None

    @pytest.mark.django_db()
    def test_the_client_serves_the_batch_hop_on_the_view_thread(self) -> None:
        group = Group.objects.create(name="authors")
        with override_settings(ROOT_URLCONF=__name__):
            response = Client().get(f"/groups/{group.pk}/")
        assert response.content == b"GET authors True"
