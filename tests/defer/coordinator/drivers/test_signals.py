import asyncio
import contextvars
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field

import pytest
from asgiref.sync import ThreadSensitiveContext, sync_to_async

from next.boundaries.calls import Addressing
from next.boundaries.failures import ErrorKind
from next.boundaries.options import DeferMode
from next.defer import Job
from next.defer.markers import Lane, LaneFacts, LaneRule, PoolVerdict, Rendered
from next.defer.signals import boundary_job_finished, boundary_job_started
from tests.defer.coordinator.drivers.doubles import (
    ASGI,
    ATOMIC_ASGI,
    ATOMIC_WSGI,
    WSGI,
    coordinator_over,
    items_of,
    new_session,
    queue_job,
)


span = contextvars.ContextVar[str | None]("span", default=None)


@dataclass
class Tracer:
    """Open a span on the start of a job and close it on its end, as an APM does."""

    opened: list[tuple[str, int, dict[str, object]]] = field(default_factory=list)
    closed: list[tuple[str | None, int, dict[str, object]]] = field(
        default_factory=list
    )
    ran: list[tuple[str | None, int]] = field(default_factory=list)

    def start(self, sender: object, **kwargs: object) -> None:
        span.set(str(kwargs["id"]))
        self.opened.append((str(kwargs["id"]), threading.get_ident(), kwargs))

    def finish(self, sender: object, **kwargs: object) -> None:
        self.closed.append((span.get(), threading.get_ident(), kwargs))

    def body(self) -> Rendered:
        self.ran.append((span.get(), threading.get_ident()))
        return Rendered("ok", "")


@pytest.fixture()
def tracer() -> Iterator[Tracer]:
    held = Tracer()
    boundary_job_started.connect(held.start, sender=Job)
    boundary_job_finished.connect(held.finish, sender=Job)
    yield held
    boundary_job_started.disconnect(held.start, sender=Job)
    boundary_job_finished.disconnect(held.finish, sender=Job)


def _traced(tracer: Tracer, job: Job) -> None:
    (opened,) = tracer.opened
    (closed,) = tracer.closed
    (ran,) = tracer.ran
    assert opened[0] == job.id
    assert ran == (job.id, opened[1])
    assert closed[:2] == (job.id, opened[1])
    facts = opened[2]
    assert {key: facts[key] for key in ("id", "name", "addressing", "mode")} == {
        "id": job.id,
        "name": job.site.name,
        "addressing": Addressing.COMPONENT,
        "mode": DeferMode.STREAM,
    }
    finished = closed[2]
    assert finished["error"] is None
    assert isinstance(finished["run_ms"], float)
    shared = facts.keys() - {"signal"}
    assert {key: finished[key] for key in shared} == {key: facts[key] for key in shared}


class TestJobSignals:
    """The job signals pair up around each body, on its thread and in its context."""

    @pytest.mark.parametrize(
        ("facts", "lane", "reason"),
        [
            pytest.param(WSGI, Lane.POOL, PoolVerdict.POOL, id="pool"),
            pytest.param(ATOMIC_WSGI, Lane.REQUEST, LaneRule.ATOMIC, id="request"),
        ],
    )
    def test_a_thread_driven_job_is_traced_where_it_runs(
        self,
        pools,
        tracer: Tracer,
        facts: LaneFacts,
        lane: Lane,
        reason: LaneRule | PoolVerdict,
    ) -> None:
        session = new_session(facts=facts)
        job = queue_job(session, tracer.body)
        list(coordinator_over(pools()).run(session))
        _traced(tracer, job)
        assert (tracer.opened[0][2]["lane"], tracer.opened[0][2]["reason"]) == (
            lane,
            reason,
        )
        on_request = tracer.ran[0][1] == threading.get_ident()
        assert on_request is (lane is Lane.REQUEST)

    @pytest.mark.parametrize(
        ("facts", "lane"),
        [
            pytest.param(ASGI, Lane.POOL, id="pool"),
            pytest.param(ATOMIC_ASGI, Lane.REQUEST, id="request"),
        ],
    )
    def test_a_loop_driven_job_is_traced_where_it_runs(
        self, pools, tracer: Tracer, facts: LaneFacts, lane: Lane
    ) -> None:
        async def server() -> tuple[Job, int]:
            async with ThreadSensitiveContext():
                view = await sync_to_async(threading.get_ident, thread_sensitive=True)()
                session = new_session(facts=facts)
                job = queue_job(session, tracer.body)
                [batch async for batch in coordinator_over(pools()).arun(session)]
            return job, view

        job, view = asyncio.run(server())
        _traced(tracer, job)
        assert tracer.opened[0][2]["lane"] is lane
        assert (tracer.ran[0][1] == view) is (lane is Lane.REQUEST)

    def test_a_raising_body_still_closes_its_span(self, pools, tracer: Tracer) -> None:
        def fail() -> Rendered:
            msg = "job failed"
            raise ValueError(msg)

        session = new_session()
        job = queue_job(session, fail)
        items = items_of(list(coordinator_over(pools()).run(session)))
        assert items[job.id].kind is ErrorKind.ERROR
        (closed,) = tracer.closed
        assert closed[0] == job.id
        assert isinstance(closed[2]["error"], ValueError)

    def test_a_raising_receiver_leaves_the_job_alone(self, pools) -> None:
        def broken(sender: object, **kwargs: object) -> None:
            msg = "tracer broke"
            raise RuntimeError(msg)

        boundary_job_started.connect(broken, sender=Job)
        try:
            session = new_session()
            job = queue_job(session, Tracer().body)
            items = items_of(list(coordinator_over(pools()).run(session)))
        finally:
            boundary_job_started.disconnect(broken, sender=Job)
        assert items[job.id].kind is None

    def test_a_finish_receiver_alone_hears_the_end(self, pools) -> None:
        heard: list[object] = []

        def finish(sender: object, **kwargs: object) -> None:
            heard.append(kwargs["id"])

        boundary_job_finished.connect(finish, sender=Job)
        try:
            session = new_session()
            job = queue_job(session, Tracer().body)
            list(coordinator_over(pools()).run(session))
        finally:
            boundary_job_finished.disconnect(finish, sender=Job)
        assert heard == [job.id]
