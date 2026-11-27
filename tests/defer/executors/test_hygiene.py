import logging
from collections.abc import Callable

import pytest
from django.db import transaction
from django.db.utils import ConnectionHandler

from next.bridge.tasks import StealableTask
from tests.defer.executors.shared import Connection, Connections, raw, select
from tests.support.waits import WAIT, until


class TestLaneExecutorConnections:
    """The pool closes, checks and returns the connections of its own threads."""

    def test_an_idle_worker_closes_its_connections(
        self, pools, fake_connections: Connections
    ) -> None:
        held = Connection()
        executor = pools(max_workers=1, conn_idle=0.02)
        executor.submit(StealableTask(lambda: fake_connections.hold(held))).result(WAIT)
        assert held.closed.wait(WAIT)
        assert held.connection is None

    def test_each_task_crosses_the_request_boundary_check(
        self, pools, fake_connections: Connections
    ) -> None:
        held = Connection()
        executor = pools(max_workers=1)
        executor.submit(StealableTask(lambda: fake_connections.hold(held))).result(WAIT)
        until(lambda: held.refreshed == 1)
        assert executor.submit(StealableTask(lambda: held.refreshed)).result(WAIT) == 2
        until(lambda: held.refreshed == 3)

    def test_a_connection_past_its_max_age_closes_after_the_task(
        self, pools, sqlite: Callable[..., ConnectionHandler]
    ) -> None:
        handler = sqlite(CONN_MAX_AGE=0)
        executor = pools(max_workers=1)
        executor.submit(StealableTask(lambda: select(handler))).result(WAIT)
        until(lambda: executor.capacity().busy == 0)
        assert executor.submit(StealableTask(lambda: raw(handler))).result(WAIT) is None

    def test_a_persistent_connection_serves_the_next_task(
        self, pools, sqlite: Callable[..., ConnectionHandler]
    ) -> None:
        handler = sqlite(CONN_MAX_AGE=None)
        executor = pools(max_workers=1)
        first = executor.submit(StealableTask(lambda: select(handler))).result(WAIT)
        second = executor.submit(StealableTask(lambda: select(handler))).result(WAIT)
        assert first is second

    def test_the_health_check_runs_again_in_each_task(
        self, pools, sqlite: Callable[..., ConnectionHandler]
    ) -> None:
        handler = sqlite(CONN_MAX_AGE=None, CONN_HEALTH_CHECKS=True)
        executor = pools(max_workers=1)
        first = executor.submit(StealableTask(lambda: select(handler))).result(WAIT)

        def failed_over() -> object:
            conn = handler["default"]
            conn.is_usable = lambda: False
            try:
                return select(handler)
            finally:
                del conn.is_usable

        second = executor.submit(StealableTask(failed_over)).result(WAIT)
        assert second is not first

    def test_an_atomic_block_a_task_left_open_never_reaches_the_next_task(
        self,
        pools,
        sqlite: Callable[..., ConnectionHandler],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        handler = sqlite(CONN_MAX_AGE=None)
        executor = pools(max_workers=1)

        def execute(sql: str) -> None:
            with handler["default"].cursor() as cursor:
                cursor.execute(sql)

        def rows() -> tuple[int, bool]:
            with handler["default"].cursor() as cursor:
                cursor.execute("SELECT COUNT(*) FROM t")
                (count,) = cursor.fetchone()
            return count, handler["default"].in_atomic_block

        def leave_open() -> None:
            transaction.atomic().__enter__()
            execute("INSERT INTO t VALUES (1)")

        executor.submit(
            StealableTask(lambda: execute("CREATE TABLE t (x int)"))
        ).result(WAIT)
        with caplog.at_level(logging.WARNING, logger="next.defer"):
            for _ in range(2):
                executor.submit(StealableTask(leave_open)).result(WAIT)
                assert executor.submit(StealableTask(rows)).result(WAIT) == (0, False)
        executor.submit(
            StealableTask(lambda: execute("INSERT INTO t VALUES (2)"))
        ).result(WAIT)
        assert executor.submit(StealableTask(rows)).result(WAIT) == (1, False)
        warnings = [r for r in caplog.records if "inside an atomic block" in r.message]
        assert len(warnings) == 1

    @pytest.mark.django_db()
    def test_execute_wrappers_a_task_leaves_never_reach_the_next_task(
        self, pools, fake_connections: Connections
    ) -> None:
        executor = pools(max_workers=1)
        held = Connection()

        def kept(execute: Callable[..., object], *args: object) -> object:
            return execute(*args)

        def leaked(execute: Callable[..., object], *args: object) -> object:
            return execute(*args)

        def open_with_one() -> None:
            fake_connections.hold(held)
            held.execute_wrappers.append(kept)

        def leave_another() -> int:
            held.execute_wrappers.append(leaked)
            return len(held.execute_wrappers)

        executor.submit(StealableTask(open_with_one)).result(WAIT)
        until(lambda: executor.capacity().busy == 0)
        assert held.execute_wrappers == []
        held.execute_wrappers.append(kept)
        assert executor.submit(StealableTask(leave_another)).result(WAIT) == 2
        until(lambda: executor.capacity().busy == 0)
        assert held.execute_wrappers == [kept]
