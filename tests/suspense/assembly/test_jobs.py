import asyncio
import threading
from collections.abc import Sequence
from contextvars import copy_context
from unittest.mock import patch

import pytest
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.db import transaction
from django.test import RequestFactory, override_settings

from next.defer import BoundaryOptions
from next.deps import Loader
from next.deps.runner import batch_scope
from next.deps.scope import RequestScope
from next.pages.responses import mark_shared_render
from next.suspense.assembly import assembled
from next.suspense.assembly.jobs import Source, _closing, _job_scope, prepare
from next.suspense.components import current_deferring, deferring
from next.suspense.connections import forcing_pool
from next.suspense.coordinator import Coordinator
from next.suspense.executors import background_loop
from next.suspense.markers import Rendered
from next.suspense.render import Delta
from next.suspense.response import shell
from next.suspense.session import BoundarySession
from next.suspense.signals import boundary_rendered
from next.testing import capture_signals
from tests.support.boundaries import make_call, make_job, open_session


_SESSIONS = "django.contrib.sessions.middleware.SessionMiddleware"
_AUTH = "django.contrib.auth.middleware.AuthenticationMiddleware"


def _closed(scope: RequestScope) -> bool:
    fired: list[bool] = []
    scope.on_close(lambda: fired.append(True))
    return bool(fired)


def _keyed(calls: list[list[int]], keys: Sequence[int]) -> dict[int, int]:
    calls.append(sorted(keys))
    return {key: key * 10 for key in keys}


def _loads(
    loader: Loader[int, int],
    key: int,
    seen: list[RequestScope | None],
    barrier: threading.Barrier | None = None,
) -> str:
    """Queue one job that loads `key` through `loader`, noting its batch scope."""
    held = current_deferring()
    assert held is not None
    session = held.open()

    def run() -> Rendered[Delta]:
        if barrier is not None:
            barrier.wait()
        seen.append(batch_scope.get())
        return Rendered(str(loader.load_sync(key)), Delta())

    job = make_job(session, run=run)
    session.queue(job)
    return shell(job.id, make_call(), "L")


class TestJobScope:
    """Jobs load in the scope that reaches them, else in one of their own."""

    def test_pool_jobs_share_one_batch_in_a_scope_closed_after_them(self) -> None:
        calls: list[list[int]] = []
        loader = Loader(lambda keys: _keyed(calls, keys), max_batch=3, window=3600)
        barrier = threading.Barrier(3, timeout=5)
        seen: list[RequestScope | None] = []

        def render() -> str:
            return "".join(_loads(loader, key, seen, barrier) for key in (1, 2, 3))

        with forcing_pool():
            html = assembled(render, collector=None, request=None)
        assert calls == [[1, 2, 3]]
        assert all(f">{key * 10}</div>" in html for key in (1, 2, 3))
        scope = seen[0]
        assert scope is not None
        assert seen == [scope] * 3
        assert _closed(scope) is True
        assert batch_scope.get() is None

    def test_a_render_reached_by_a_live_scope_borrows_it_open(self) -> None:
        loader = Loader(lambda keys: _keyed([], keys))
        scope = RequestScope()
        seen: list[RequestScope | None] = []
        context = copy_context()
        context.run(batch_scope.set, scope)
        html = context.run(
            assembled, lambda: _loads(loader, 4, seen), collector=None, request=None
        )
        assert ">40</div>" in html
        assert seen == [scope]
        assert _closed(scope) is False

    @pytest.mark.parametrize("borrowed", [False, True])
    def test_a_streamed_run_closes_only_a_scope_of_its_own(self, rf, borrowed) -> None:
        loader = Loader(lambda keys: _keyed([], keys))
        seen: list[RequestScope | None] = []
        lent = RequestScope()
        context = copy_context()
        if borrowed:
            context.run(batch_scope.set, lent)
        with deferring() as held:
            _loads(loader, 5, seen)
        session = held.session
        assert session is not None
        context.run(prepare, session, None)
        source = Source(session, rf.get("/"))

        async def read() -> list[str]:
            return [
                piece.outcome.rendered.html
                async for batch in source.arun()
                for piece in batch
            ]

        assert asyncio.run(read()) == ["50"]
        [scope] = seen
        assert scope is not None
        assert (scope is lent) is borrowed
        assert _closed(scope) is not borrowed

    def test_a_scope_released_on_its_loop_closes_without_blocking_it(self) -> None:
        with deferring() as held:
            _loads(Loader(lambda keys: _keyed([], keys)), 6, [])
        session = held.session
        assert session is not None
        copy_context().run(prepare, session, None)
        holding = _job_scope(session, None)
        holding.__enter__()
        scope = session.scope
        assert scope is not None
        scope.on_close(lambda: None)

        async def release() -> bool:
            holding.__exit__(None, None, None)
            [closing] = _closing
            await asyncio.wrap_future(closing)
            return _closed(scope)

        loop = background_loop.get()
        assert asyncio.run_coroutine_threadsafe(release(), loop).result(5) is True
        assert _closing == set()

    def test_a_scope_its_loop_never_touched_closes_without_a_round_trip(
        self, queued
    ) -> None:
        with deferring() as held:
            queued()
        session = held.session
        assert session is not None
        copy_context().run(prepare, session, None)
        with (
            patch.object(asyncio, "run_coroutine_threadsafe") as scheduled,
            _job_scope(session, None),
        ):
            scope = session.scope
        scheduled.assert_not_called()
        assert scope is not None
        assert _closed(scope) is True

    def test_an_inline_render_runs_its_jobs_buffered(self, queued) -> None:
        seen: list[dict[str, object]] = []
        original = Coordinator.run

        def run(self, session, **kwargs):
            seen.append(kwargs)
            return original(self, session, **kwargs)

        with patch.object(Coordinator, "run", run):
            assembled(lambda: queued()[0], collector=None, request=None)
        assert seen == [{"buffered": True}]


class TestStreamFacts:
    """A stream places its jobs by the transaction state they run in, after the view."""

    @pytest.fixture()
    def streamed(
        self, queued, rf: RequestFactory
    ) -> tuple[BoundarySession[Delta], Source]:
        """Return the session and source of a stream prepared inside a view's block."""
        request = rf.get("/")
        with deferring() as held:
            queued()
        session = held.session
        assert session is not None
        with transaction.atomic():
            prepare(session, request, stream=True)
            source = Source(session, request)
        return session, source

    @staticmethod
    def _placed(
        session: BoundarySession[Delta], source: Source
    ) -> tuple[BoundarySession[Delta], list[str]]:
        with capture_signals(boundary_rendered) as recorder:
            list(source.run())
        return session, [str(event.kwargs["reason"]) for event in recorder]

    @pytest.mark.django_db(transaction=True)
    def test_a_transaction_the_view_committed_pins_no_job(self, streamed) -> None:
        session, reasons = self._placed(*streamed)
        assert session.facts is not None
        assert session.facts.atomic == ()
        assert reasons == ["sqlite"]

    @pytest.mark.django_db()
    def test_a_transaction_still_open_keeps_its_jobs_on_the_request_lane(
        self, streamed
    ) -> None:
        session, reasons = self._placed(*streamed)
        assert session.facts is not None
        assert session.facts.atomic == ("default",)
        assert reasons == ["atomic"]

    @pytest.mark.django_db(transaction=True)
    def test_the_lane_is_read_after_the_view_committed(self, streamed) -> None:
        _, source = streamed
        assert source.pinning_rule() == "sqlite"

    @pytest.mark.django_db()
    def test_the_lane_of_a_transaction_still_open_is_atomic(self, streamed) -> None:
        _, source = streamed
        assert source.pinning_rule() == "atomic"

    def test_a_session_without_facts_names_no_lane(self, rf) -> None:
        session = open_session()
        assert Source(session, rf.get("/")).pinning_rule() is None


class TestSettledRequest:
    """Request state settles only for pool jobs, a shared page keeping its cache."""

    @staticmethod
    def _queued_session(*, parallel: bool = True) -> BoundarySession[Delta]:
        session = open_session()
        session.queue(make_job(session, options=BoundaryOptions(parallel=parallel)))
        return session

    @override_settings(MIDDLEWARE=[_SESSIONS])
    def test_request_lane_jobs_settle_nothing(self, rf) -> None:
        request = rf.get("/")
        request.session = SessionStore()
        prepare(self._queued_session(parallel=False), request)
        assert request.session.accessed is False

    @override_settings(MIDDLEWARE=[_SESSIONS])
    def test_pool_jobs_of_a_private_page_settle_the_session(self, rf) -> None:
        request = rf.get("/")
        request.session = SessionStore()
        with forcing_pool():
            prepare(self._queued_session(), request)
        assert request.session.accessed is True

    @override_settings(MIDDLEWARE=[_SESSIONS, _AUTH])
    def test_pool_jobs_of_a_shared_page_load_it_unmarked(self, rf) -> None:
        request = rf.get("/")
        request.session = SessionStore()
        mark_shared_render(request)
        with forcing_pool():
            prepare(self._queued_session(), request)
        assert request.session.accessed is False
        assert request.user.is_authenticated is False
        assert request.session.accessed is True

    def test_a_render_without_a_request_settles_nothing(self) -> None:
        session = self._queued_session()
        with forcing_pool():
            prepare(session, None)
        assert session.facts is not None
        assert session.snapshot is not None
