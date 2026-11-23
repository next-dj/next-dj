import asyncio
import contextlib
import contextvars
import logging
import threading
from collections.abc import Callable, Coroutine, Iterator, Sequence
from typing import Any

import pytest
from asgiref.local import Local
from asgiref.sync import ThreadSensitiveContext, sync_to_async

from next.defer import ErrorKind
from next.deps import Loader
from next.deps.runner import batch_scope
from next.deps.scope import RequestScope
from next.suspense import Admit, Lane, Placement, Verdict, bridge
from next.suspense.coordinator import Coordinator, Emit, Foreground, drivers
from next.suspense.markers import ForegroundFacts, Rendered
from next.suspense.session import BoundarySession
from tests.support.waits import until_loop
from tests.suspense.coordinator.drivers.doubles import (
    ASGI,
    ATOMIC_ASGI,
    RUNNING_TIMEOUT,
    WAIT,
    AwaitedPermits,
    ExpiringPermits,
    FailingAdmission,
    FullPool,
    HeldPool,
    LingeringPermits,
    RefusingPermits,
    ScriptedAdmission,
    consume_stream,
    coordinator_over,
    forever_body,
    gated_body,
    hop_body,
    html_of,
    ident_body,
    items_of,
    left_tasks,
    loading_body,
    loop_turns,
    meeting_body,
    new_session,
    queue_job,
    queue_timeout,
    ready_body,
    recorded_fetch,
    sleeping_body,
    view_session,
)


view_local = Local()


def local_body() -> Rendered[str]:
    return Rendered(str(getattr(view_local, "value", None)), "")


async def alocal_body() -> Rendered[str]:
    return Rendered(str(getattr(view_local, "value", None)), "")


class TestAsyncDriver:
    """Tests for ``Coordinator.arun`` on a server loop."""

    @pytest.fixture(autouse=True)
    def _queued_units_stay_queued(self) -> Iterator[None]:
        """Keep the view thread from taking over pool units unless a test asks."""
        with queue_timeout(3600):
            yield

    def test_request_lane_jobs_and_async_hops_land_on_the_view_thread(
        self, pools, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        hops: list[object] = []

        def counted(
            func: Callable[..., object],
            *,
            thread_sensitive: bool,
            context: contextvars.Context | None = None,
        ) -> Callable[..., Coroutine[Any, Any, object]]:
            hops.append(func)
            return sync_to_async(
                func, thread_sensitive=thread_sensitive, context=context
            )

        monkeypatch.setattr(drivers, "sync_to_async", counted)
        executor = pools()
        coordinator = coordinator_over(executor)
        batches, view = asyncio.run(
            consume_stream(coordinator, ATOMIC_ASGI, ident_body, ident_body, hop_body)
        )
        items = items_of(batches)
        assert {html_of(item) for item in items.values()} == {str(view)}
        assert len(hops) == 1
        assert executor.submitted == 0

    @pytest.mark.parametrize(
        "facts",
        [pytest.param(ATOMIC_ASGI, id="request"), pytest.param(ASGI, id="pool")],
    )
    def test_sync_and_loop_jobs_read_a_local_of_the_view(
        self, pools, hops, facts: ForegroundFacts
    ) -> None:
        async def server() -> list[str]:
            async with ThreadSensitiveContext():
                view_local.value = "set"
                session = new_session(facts=facts)
                queue_job(session, local_body)
                queue_job(session, abody=alocal_body)
                coordinator = coordinator_over(pools())
                batches = [batch async for batch in coordinator.arun(session)]
            return [html_of(item) for item in items_of(batches).values()]

        assert asyncio.run(server()) == ["set", "set"]

    def test_a_request_lane_job_is_carried_by_the_hop_that_runs_it(
        self, pools, hops, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        carries: list[object] = []

        def refuse(*args: object) -> None:
            carries.append(args)
            msg = "carried by a hop of its own"
            raise AssertionError(msg)

        monkeypatch.setattr(bridge, "_begin", refuse)

        async def server() -> list[str]:
            async with ThreadSensitiveContext():
                view_local.value = "set"
                session = new_session(facts=ATOMIC_ASGI)
                queue_job(session, local_body)
                coordinator = coordinator_over(pools())
                batches = [batch async for batch in coordinator.arun(session)]
            return [html_of(item) for item in items_of(batches).values()]

        assert asyncio.run(server()) == ["set"]
        assert carries == []

    def test_pool_units_run_in_parallel_off_the_loop(self, pools) -> None:
        executor = pools()
        coordinator = coordinator_over(executor)
        barrier = threading.Barrier(3, timeout=WAIT)
        bodies = [meeting_body(barrier) for _ in range(3)]
        batches, view = asyncio.run(consume_stream(coordinator, ASGI, *bodies))
        assert executor.submitted == 3
        threads = {html_of(item) for item in items_of(batches).values()}
        assert len(threads) == 3
        assert str(view) not in threads

    def test_an_admitted_request_unit_without_a_permit_goes_to_the_pool(
        self, pools
    ) -> None:
        permits = Foreground(1)
        assert permits.take()
        coordinator = coordinator_over(
            pools(), admission=ScriptedAdmission(Admit.REQUEST), permits=permits
        )
        batches, _ = asyncio.run(consume_stream(coordinator, ASGI, ident_body))
        (item,) = items_of(batches).values()
        assert item.timing.lane is Lane.POOL
        assert item.timing.placement == Placement(Verdict.FOREGROUND_FULL)

    @pytest.mark.parametrize("permits", [LingeringPermits(1), ExpiringPermits(1)])
    def test_a_forced_unit_waits_for_a_permit_until_its_deadline(
        self, pools, permits: Foreground
    ) -> None:
        assert permits.take()
        coordinator = coordinator_over(pools(), permits=permits)

        async def server() -> list[Emit[str]]:
            async with ThreadSensitiveContext():
                session, _ = await sync_to_async(view_session, thread_sensitive=True)(
                    ATOMIC_ASGI, ident_body, deadline=0.2
                )
                batches = [batch async for batch in coordinator.arun(session)]
            assert await left_tasks() == []
            return batches

        (item,) = items_of(asyncio.run(server())).values()
        assert item.kind is ErrorKind.TIMEOUT
        permits.release()
        assert permits.take()

    def test_a_forced_unit_runs_once_a_permit_frees(self, pools) -> None:
        permits = AwaitedPermits(1)
        assert permits.take()
        coordinator = coordinator_over(pools(), permits=permits)

        async def server() -> tuple[list[Emit[str]], int]:
            consuming = asyncio.ensure_future(
                consume_stream(coordinator, ATOMIC_ASGI, ident_body)
            )
            await asyncio.to_thread(permits.waiting.wait, WAIT)
            permits.release()
            return await consuming

        batches, view = asyncio.run(server())
        (item,) = items_of(batches).values()
        assert html_of(item) == str(view)
        assert permits.take()

    def test_a_batch_without_a_permit_sends_admitted_units_to_the_pool(
        self, pools
    ) -> None:
        permits = AwaitedPermits(1)
        assert permits.take()
        coordinator = coordinator_over(
            pools(), admission=ScriptedAdmission(Admit.REQUEST), permits=permits
        )

        async def server() -> tuple[BoundarySession[str], int, list[Emit[str]]]:
            async with ThreadSensitiveContext():
                session, view = await sync_to_async(
                    view_session, thread_sensitive=True
                )(ASGI)
                queue_job(session, ident_body)
                queue_job(session, ident_body, parallel=False)
                stream = coordinator.arun(session)
                first = asyncio.ensure_future(anext(stream))
                await asyncio.to_thread(permits.waiting.wait, WAIT)
                permits.release()
                batches = [await first]
                batches.extend([batch async for batch in stream])
            return session, view, batches

        session, view, batches = asyncio.run(server())
        chosen, forced = session.jobs
        items = items_of(batches)
        assert items[chosen.id].timing.placement == Placement(Verdict.FOREGROUND_FULL)
        assert items[chosen.id].timing.lane is Lane.POOL
        assert html_of(items[forced.id]) == str(view)

    def test_closing_mid_hop_skips_the_rest_of_the_batch(self, pools) -> None:
        started = threading.Event()
        release = threading.Event()
        ran: list[str] = []

        def first() -> Rendered[str]:
            started.set()
            release.wait(WAIT)
            return Rendered("first", "")

        def second() -> Rendered[str]:
            ran.append("second")
            return Rendered("second", "")

        coordinator = coordinator_over(pools())

        async def server() -> list[asyncio.Task[Any]]:
            async with ThreadSensitiveContext():
                session, _ = await sync_to_async(view_session, thread_sensitive=True)(
                    ATOMIC_ASGI, first, second
                )
                stream = coordinator.arun(session)
                pending = asyncio.ensure_future(anext(stream))
                assert await asyncio.to_thread(started.wait, WAIT)
                pending.cancel()
                await until_loop(lambda: coordinator.finish is not None)
                assert not pending.done()
                release.set()
                with contextlib.suppress(asyncio.CancelledError):
                    await pending
            current = asyncio.current_task()
            return [task for task in asyncio.all_tasks() if task is not current]

        assert asyncio.run(server()) == []
        assert ran == []
        assert coordinator.finish is not None
        assert coordinator.finish.aborted

    @pytest.mark.parametrize("ending", ["exhausted", "closed", "cancelled"])
    def test_the_stream_ends_only_once_its_running_request_job_returns(
        self, pools, ending: str
    ) -> None:
        started = threading.Event()
        release = threading.Event()
        coordinator = coordinator_over(pools())

        async def server() -> tuple[Emit[str], bool]:
            async with ThreadSensitiveContext():
                session, _ = await sync_to_async(view_session, thread_sensitive=True)(
                    ATOMIC_ASGI
                )
                queue_job(
                    session, gated_body(started, release), timeout=RUNNING_TIMEOUT
                )
                stream = coordinator.arun(session)
                settled = await anext(stream)
                rest = anext(stream, None) if ending == "exhausted" else stream.aclose()
                waiting = asyncio.ensure_future(rest)
                await until_loop(lambda: coordinator.finish is not None)
                if ending == "cancelled":
                    waiting.cancel()
                    await asyncio.sleep(0)
                held = not waiting.done()
                release.set()
                with contextlib.suppress(asyncio.CancelledError):
                    await waiting
                cancelled = waiting.cancelled()
            assert cancelled is (ending == "cancelled")
            return settled, held

        settled, held = asyncio.run(server())
        assert started.is_set()
        (item,) = settled.items
        assert item.kind is ErrorKind.TIMEOUT
        assert held

    def test_the_stream_ends_only_once_a_cancelled_jobs_hop_returns(
        self, pools
    ) -> None:
        started = threading.Event()
        release = threading.Event()
        returned: list[bool] = []

        def hop() -> None:
            started.set()
            release.wait(WAIT)
            returned.append(True)

        async def abody() -> Rendered[str]:
            await sync_to_async(hop, thread_sensitive=True)()
            return Rendered("hopped", "")

        coordinator = coordinator_over(pools())

        async def server() -> tuple[Emit[str], bool, list[bool]]:
            async with ThreadSensitiveContext():
                session, _ = await sync_to_async(view_session, thread_sensitive=True)(
                    ASGI
                )
                queue_job(session, abody=abody, timeout=RUNNING_TIMEOUT)
                stream = coordinator.arun(session)
                settled = await anext(stream)
                closing = asyncio.ensure_future(stream.aclose())
                assert await asyncio.to_thread(started.wait, WAIT)
                await loop_turns()
                held = not closing.done()
                release.set()
                await asyncio.wait_for(closing, WAIT)
                return settled, held, list(returned)

        settled, held, ended = asyncio.run(server())
        (item,) = settled.items
        assert item.kind is ErrorKind.TIMEOUT
        assert held
        assert ended == [True]

    def test_the_stream_ends_only_once_its_sync_batch_calls_return(self, pools) -> None:
        started = threading.Event()
        release = threading.Event()
        calls: list[list[object]] = []

        def fetch(keys: Sequence[int]) -> dict[int, int]:
            calls.append(list(keys))
            started.set()
            release.wait(WAIT)
            return {key: key for key in keys}

        loader = Loader(fetch, window=3600)

        async def abody() -> Rendered[str]:
            await loader.load(1)
            return Rendered("loaded", "")

        coordinator = coordinator_over(pools())

        async def server() -> tuple[Emit[str], bool]:
            scope = RequestScope()
            session = new_session(facts=ASGI)
            session.scope = scope
            queue_job(session, abody=abody, timeout=RUNNING_TIMEOUT)
            stream = coordinator.arun(session)
            try:
                settled = await anext(stream)
                closing = asyncio.ensure_future(stream.aclose())
                assert await asyncio.to_thread(started.wait, WAIT)
                await loop_turns()
                held = not closing.done()
                release.set()
                await asyncio.wait_for(closing, WAIT)
            finally:
                await scope.close()
            return settled, held

        settled, held = asyncio.run(server())
        (item,) = settled.items
        assert item.kind is ErrorKind.TIMEOUT
        assert held
        assert calls == [[1]]

    def test_async_jobs_share_a_batch_however_late_one_loads(self, pools) -> None:
        log: list[object] = []
        loader = Loader(recorded_fetch(log), window=3600)

        async def early() -> Rendered[str]:
            return Rendered(str(await loader.load(1)), "")

        async def late() -> Rendered[str]:
            await loop_turns()
            return Rendered(str(await loader.load(2)), "")

        coordinator = coordinator_over(pools())

        async def server() -> list[Emit[str]]:
            scope = RequestScope()
            session = new_session(facts=ASGI)
            session.scope = scope
            queue_job(session, abody=early)
            queue_job(session, abody=late)
            try:
                return [batch async for batch in coordinator.arun(session)]
            finally:
                await scope.close()

        items = items_of(asyncio.run(server()))
        assert sorted(html_of(item) for item in items.values()) == ["10", "20"]
        assert log == [[1, 2]]

    def test_request_lane_jobs_queued_behind_a_parked_one_leave_its_batch(
        self, pools
    ) -> None:
        log: list[object] = []
        loader = Loader(recorded_fetch(log), window=3600)
        coordinator = coordinator_over(pools())

        async def server() -> list[Emit[str]]:
            scope = RequestScope()
            session = new_session(facts=ATOMIC_ASGI)
            session.scope = scope
            assert session.snapshot is not None
            session.snapshot.context.run(batch_scope.set, scope)
            queue_job(session, loading_body(loader, 1))
            queue_job(session, loading_body(loader, 2))
            try:
                async with ThreadSensitiveContext():
                    return [batch async for batch in coordinator.arun(session)]
            finally:
                await scope.close()

        items = items_of(asyncio.run(server()))
        assert sorted(html_of(item) for item in items.values()) == ["10", "20"]
        assert {item.timing.lane for item in items.values()} == {Lane.REQUEST}
        assert log == [[1], [2]]

    def test_a_full_queue_runs_units_on_the_view_thread_under_a_permit(self) -> None:
        executor = FullPool()
        coordinator = coordinator_over(executor)
        batches, view = asyncio.run(
            consume_stream(coordinator, ASGI, ident_body, ident_body)
        )
        assert {html_of(item) for item in items_of(batches).values()} == {str(view)}
        assert executor.refused == 2

    def test_a_unit_neither_the_queue_nor_a_permit_takes_is_shed_once(self) -> None:
        permits = Foreground(1)
        assert permits.take()
        executor = FullPool()
        coordinator = coordinator_over(executor, permits=permits)
        batches, _ = asyncio.run(
            consume_stream(coordinator, ASGI, ident_body, ident_body)
        )
        kinds = [item.kind for item in items_of(batches).values()]
        assert kinds == [ErrorKind.OVERLOADED, ErrorKind.OVERLOADED]
        assert executor.refused == 4
        assert coordinator.totals is not None
        assert coordinator.totals.shed == 2

    def test_a_unit_queued_past_the_queue_timeout_runs_on_the_view_thread(self) -> None:
        executor = HeldPool()
        coordinator = coordinator_over(executor)
        with queue_timeout(0.01):
            batches, view = asyncio.run(consume_stream(coordinator, ASGI, ident_body))
        (item,) = items_of(batches).values()
        assert html_of(item) == str(view)
        assert item.timing.lane is Lane.REQUEST
        assert executor.queued == []

    def test_a_queued_unit_waits_for_a_permit_to_be_taken_over(self) -> None:
        permits = RefusingPermits(1)
        assert permits.take()
        executor = HeldPool()
        coordinator = coordinator_over(executor, permits=permits)

        async def server() -> tuple[list[Emit[str]], int]:
            async with ThreadSensitiveContext():
                session, view = await sync_to_async(
                    view_session, thread_sensitive=True
                )(ASGI, ident_body)
                stream = coordinator.arun(session)
                first = asyncio.ensure_future(anext(stream))
                await until_loop(lambda: permits.refused > 0)
                assert len(executor.queued) == 1
                assert not first.done()
                permits.release()
                batches = [await first]
                batches.extend([batch async for batch in stream])
            return batches, view

        with queue_timeout(0.01):
            batches, view = asyncio.run(server())
        (item,) = items_of(batches).values()
        assert html_of(item) == str(view)

    def test_aclose_cancels_loop_tasks_and_drops_queued_units(self) -> None:
        executor = HeldPool()
        coordinator = coordinator_over(executor)

        async def server() -> list[asyncio.Task[Any]]:
            async with ThreadSensitiveContext():
                session, _ = await sync_to_async(view_session, thread_sensitive=True)(
                    ASGI
                )
                queue_job(session, ready_body("first"), parallel=False)
                queue_job(session, abody=forever_body)
                queue_job(session, ready_body("queued"))
                stream = coordinator.arun(session)
                await anext(stream)
                await stream.aclose()
            return await left_tasks()

        assert asyncio.run(server()) == []
        assert executor.queued == []
        assert coordinator.finish is not None
        assert coordinator.finish.totals.cancelled == 1
        assert coordinator.finish.totals.abandoned == 1

    def test_a_timed_out_async_job_settles_and_its_task_ends(self, pools) -> None:
        coordinator = coordinator_over(pools())

        async def server() -> tuple[list[Emit[str]], list[asyncio.Task[Any]]]:
            async with ThreadSensitiveContext():
                session, _ = await sync_to_async(view_session, thread_sensitive=True)(
                    ASGI
                )
                job = queue_job(session, abody=forever_body, timeout=0.05)
                queue_job(session, sleeping_body(0.15))
                batches = [batch async for batch in coordinator.arun(session)]
                assert items_of(batches)[job.id].kind is ErrorKind.TIMEOUT
            return batches, await left_tasks()

        batches, left = asyncio.run(server())
        assert len(items_of(batches)) == 2
        assert left == []

    def test_a_capacity_reading_sheds_queued_units(self) -> None:
        executor = HeldPool(stealable=False)
        coordinator = coordinator_over(
            executor, admission=ScriptedAdmission(Admit.POOL, Admit.SHED)
        )

        async def server() -> list[Emit[str]]:
            session = new_session(facts=ASGI, deadline=0.2, per_request=1)
            queue_job(session, ready_body("held"))
            queue_job(session, ready_body("shed"))
            return [batch async for batch in coordinator.arun(session)]

        batches = asyncio.run(server())
        kinds = [item.kind for batch in batches for item in batch.items]
        assert kinds == [ErrorKind.OVERLOADED, ErrorKind.TIMEOUT]
        assert len(executor.asked) == 1

    def test_an_empty_session_finishes_at_once(self, pools) -> None:
        coordinator = coordinator_over(pools())

        async def server() -> list[Emit[str]]:
            return [batch async for batch in coordinator.arun(new_session(facts=None))]

        assert asyncio.run(server()) == []
        assert coordinator.finish is not None
        assert not coordinator.finish.aborted
        assert coordinator.failed is False

    def test_the_process_permits_serve_a_coordinator_without_them(self, pools) -> None:
        coordinator: Coordinator[str] = Coordinator(
            executor=pools(), admission=ScriptedAdmission()
        )
        batches, view = asyncio.run(
            consume_stream(coordinator, ATOMIC_ASGI, ident_body)
        )
        assert html_of(batches[0].items[0]) == str(view)

    def test_a_machinery_failure_ends_the_async_stream(
        self, pools, caplog: pytest.LogCaptureFixture
    ) -> None:
        coordinator = coordinator_over(pools(), admission=FailingAdmission())

        async def server() -> list[Emit[str]]:
            session = new_session(facts=ASGI)
            queue_job(session, ready_body("a"))
            queue_job(session, ready_body("b"))
            return [batch async for batch in coordinator.arun(session)]

        with caplog.at_level(logging.ERROR, logger="next.suspense"):
            assert asyncio.run(server()) == []
        assert [r.getMessage() for r in caplog.records] == [drivers._FAILED]
        assert coordinator.finish is not None
        assert coordinator.finish.aborted
        assert coordinator.failed is True

    def test_an_error_the_consumer_throws_propagates(self) -> None:
        coordinator = coordinator_over(HeldPool(stealable=False))

        async def server() -> None:
            session = new_session(facts=ASGI)
            queue_job(session, ready_body("first"), parallel=False)
            queue_job(session, ready_body("held"))
            stream = coordinator.arun(session)
            await anext(stream)
            await stream.athrow(ValueError("consumer broke"))

        with pytest.raises(ValueError, match="consumer broke"):
            asyncio.run(server())
        assert coordinator.finish is not None
        assert coordinator.finish.aborted
