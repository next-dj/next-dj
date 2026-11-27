import asyncio
import contextlib
import contextvars
import threading
import traceback
from collections.abc import Callable, Sequence
from concurrent.futures import Future
from functools import partial

import pytest
from asgiref.local import Local
from asgiref.sync import (
    AsyncToSync,
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

from next.bridge import drive, driver, hops
from next.bridge.context import ContextSnapshot
from next.bridge.hops import current_executor, parent_loop
from next.bridge.loop import background_loop
from next.bridge.request import serve_until
from next.deps import Loader
from next.deps.cache import RequestMemo, request_memo
from next.deps.loader import open_loaders
from next.deps.scope import RequestScope
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
    thread.join(WAIT)
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
        assert started.wait(WAIT)
        loop = background_loop.current()
        [failed] = _in_thread(lambda: drive(victim))
        release.set_result(None)
        waiting.join(WAIT)
        assert not waiting.is_alive()
        assert isinstance(failed, SystemExit)
        assert results == [["spared"]]
        assert drive(_answer) == 42
        assert background_loop.current() is loop
        reports = [r for r in caplog.records if r.name == "next.bridge"]
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
        assert started.wait(WAIT)
        background_loop.stop()
        waiting.join(WAIT)
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
        monkeypatch.setattr(hops, "CurrentThreadExecutor", _Bare)
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
        assert parent_loop() is not loop
        assert async_to_sync(loop_ident)() not in {
            background_ident,
            threading.get_ident(),
        }

    def test_a_stale_marker_of_a_closed_loop_is_ignored(self) -> None:
        async_to_sync(_hop_ident)()
        assert drive(_hop_ident) == threading.get_ident()

    def test_concurrent_drives_share_one_loop_and_keep_their_hops(self) -> None:
        outcomes: dict[int, tuple[set[int], asyncio.AbstractEventLoop]] = {}
        together = threading.Barrier(6, timeout=WAIT)

        async def many() -> tuple[set[int], asyncio.AbstractEventLoop]:
            idents = set()
            for _ in range(10):
                idents.add(await sync_to_async(threading.get_ident)())
                await asyncio.sleep(0)
            return idents, asyncio.get_running_loop()

        def request() -> None:
            # Every thread is alive before one drives, so no ident is reused as a key.
            together.wait(WAIT)
            outcomes[threading.get_ident()] = drive(many)

        threads = [threading.Thread(target=request, daemon=True) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(WAIT)
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
        assert finished.wait(WAIT)
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


class TestDriveCarry:
    """Tests for ``drive`` restoring the context of the view on the loop and back."""

    def test_a_failed_handback_keeps_the_outcome_and_starts_no_other_hop(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        launched: list[object] = []
        launch = driver.launch

        def refused(*_args: object) -> None:
            raise RuntimeError(UNSERVED)

        def counted(begin: Callable[..., object]) -> asyncio.AbstractEventLoop:
            launched.append(begin)
            return launch(begin)

        async def write() -> int:
            _view_local.value = "by the coroutine"
            return await _answer()

        def scenario() -> tuple[int, object]:
            _view_local.value = "by the view"
            return drive(write), _view_local.value

        monkeypatch.setattr(driver, "_handed", refused)
        monkeypatch.setattr(driver, "launch", counted)
        assert contextvars.copy_context().run(scenario) == (42, "by the view")
        assert len(launched) == 1

    def test_a_cancelled_handback_keeps_the_outcome_and_the_values_of_the_view(
        self,
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

        assert contextvars.copy_context().run(scenario) == (42, "by the view")

    @pytest.mark.parametrize(
        "error", [ValueError("no carry"), asyncio.CancelledError("no carry")]
    )
    def test_a_carry_that_fails_on_the_loop_fails_the_drive(
        self, error: BaseException, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def refused(_captured: ContextSnapshot) -> contextvars.Context:
            raise error

        monkeypatch.setattr(ContextSnapshot, "arestore", refused)
        with pytest.raises(type(error), match="no carry"):
            drive(_answer)


class TestDriveContext:
    """Tests for ``drive`` sharing context variables with the caller."""

    def test_variables_are_visible_and_writes_come_back(self) -> None:
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
            assert current_executor() is None

        contextvars.copy_context().run(scenario)

    def test_the_coroutine_reads_and_writes_the_language_of_the_view(self) -> None:
        async def switch() -> str:
            seen = translation.get_language()
            translation.activate("fr")
            return seen

        def scenario() -> None:
            translation.activate("de")
            assert drive(switch) == "de"
            assert translation.get_language() == "fr"

        contextvars.copy_context().run(scenario)

    def test_the_coroutine_reads_and_writes_a_local_of_the_view(self) -> None:
        async def switch() -> object:
            seen = getattr(_view_local, "value", None)
            _view_local.value = "by the coroutine"
            return seen

        def scenario() -> None:
            _view_local.value = "by the view"
            assert drive(switch) == "by the view"
            assert _view_local.value == "by the coroutine"

        contextvars.copy_context().run(scenario)

    def test_a_value_whose_comparison_raises_comes_back(self) -> None:
        ambiguous = contextvars.ContextVar[object]("ambiguous")
        written = _Ambiguous()

        async def write() -> None:
            ambiguous.set(written)

        def scenario() -> None:
            ambiguous.set(_Ambiguous())
            drive(write)
            assert ambiguous.get() is written
            assert current_executor() is None

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
