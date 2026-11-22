import re
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from django.contrib.auth.models import Group
from django.db import DatabaseError, connection, connections, transaction
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.transaction import TransactionManagementError
from django.test.utils import CaptureQueriesContext

from next.suspense import LaneExecutor
from next.suspense.connections import (
    DEADLINE_SQL,
    States,
    deadline_statement,
    guard,
    job_guard,
    timeout_ms,
)
from next.suspense.markers import Captured
from next.tasks import StealableTask
from tests.suspense.connections.shared import (
    Broken,
    Recorder,
    Refusing,
    Tenant,
    Unresettable,
    defer,
    query,
)


TENANT = "SELECT 'tenant acme'"
_SAVEPOINT_NAME = re.compile(r'"s\d+_x\d+"')


def _statement(sql: str) -> str:
    return _SAVEPOINT_NAME.sub("sp", sql)


@contextmanager
def _log(alias: str = "default") -> Iterator[list[str]]:
    statements: list[str] = []
    with CaptureQueriesContext(connections[alias]) as queries:
        try:
            yield statements
        finally:
            statements.extend(
                _statement(entry["sql"]) for entry in queries.captured_queries
            )


def _tenant(alias: str = "default") -> States:
    return States(connections=(Captured(Tenant(), "acme", alias),))


def _guarded(
    body: Callable[[], object], states: States | None = None, deadline: float = 0.0
) -> list[str]:
    with _log() as log, job_guard(states or _tenant(), deadline=deadline):
        body()
    return log


def _failing_block() -> None:
    with transaction.atomic():
        query("SELECT 1")
        msg = "block failed"
        raise ValueError(msg)


def _fail_job() -> None:
    msg = "job failed"
    raise ValueError(msg)


def _create_then_fail(name: str) -> None:
    Group.objects.create(name=name)
    _fail_job()


def _close(wrapper: BaseDatabaseWrapper) -> None:
    if wrapper.connection is not None:
        wrapper.connection.close()


@contextmanager
def _fresh_default() -> Iterator[BaseDatabaseWrapper]:
    original = connections["default"]
    del connections["default"]
    fresh = connections["default"]
    try:
        yield fresh
    finally:
        connections["default"] = original
        _close(fresh)


def _set_config(calls: list[tuple[object, ...]]) -> None:
    connection.ensure_connection()
    connection.connection.create_function(
        "set_config", 3, lambda *args: calls.append(args) or args[1]
    )


class TestThreadReplay:
    """Thread states apply in order and reset in reverse, whatever the job does."""

    def _states(self, events: list[str], *names: str) -> States:
        return States(
            threads=tuple(Captured(Recorder(name, events), name) for name in names)
        )

    def test_apply_in_order_and_reset_in_reverse(self) -> None:
        events: list[str] = []
        with job_guard(self._states(events, "a", "b"), deadline=0.0):
            events.append("job")
        assert events == ["apply a", "apply b", "job", "reset token b", "reset token a"]

    def test_a_failing_job_still_resets(self) -> None:
        events: list[str] = []
        with (
            pytest.raises(ValueError, match="job failed"),
            job_guard(self._states(events, "a", "b"), deadline=0.0),
        ):
            _fail_job()
        assert events == ["apply a", "apply b", "reset token b", "reset token a"]

    def test_a_failing_apply_resets_the_ones_before_it(self) -> None:
        events: list[str] = []
        states = States(
            threads=(
                Captured(Recorder("a", events), "a"),
                Captured(Broken("b", events), "b"),
            )
        )
        with (
            pytest.raises(RuntimeError, match="thread state failed"),
            job_guard(states, deadline=0.0),
        ):
            events.append("job")
        assert events == ["apply a", "reset token a"]

    @pytest.mark.parametrize(
        ("backend", "error"),
        [(Broken, "thread state failed"), (Unresettable, "thread state stuck")],
    )
    def test_a_state_that_fails_to_apply_or_reset_retires_the_pool_thread(
        self, backend: type[Recorder], error: str
    ) -> None:
        states = States(threads=(Captured(backend("a", []), "a"),))
        executor = LaneExecutor({"OPTIONS": {"MAX_WORKERS": 1, "CONN_IDLE": 60.0}})
        seen: list[threading.Thread] = []

        def guarded() -> None:
            seen.append(threading.current_thread())
            with job_guard(states, deadline=0.0):
                pass

        try:
            failed = executor.submit(StealableTask(guarded))
            with pytest.raises(RuntimeError, match=error):
                failed.result(5)
            seen[0].join(5)
            assert not seen[0].is_alive()
            later = executor.submit(StealableTask(threading.current_thread))
            assert later.result(5) is not seen[0]
        finally:
            executor.shutdown(5)

    def test_nothing_captured_wraps_nothing(self) -> None:
        with job_guard(States(), deadline=0.0):
            assert connection.execute_wrappers == []

    def test_a_connection_state_without_an_alias_is_ignored(self) -> None:
        states = States(connections=(Captured(Tenant(), "acme"),))
        with job_guard(states, deadline=0.0):
            assert connection.execute_wrappers == []


@pytest.mark.django_db(transaction=True)
class TestLazyConnectionState:
    """The state replays in a transaction opened by the job's first data statement."""

    def test_a_query_free_job_issues_nothing(self) -> None:
        assert _guarded(lambda: None) == []
        assert connection.execute_wrappers == []

    def test_the_first_statement_opens_the_transaction_and_applies_in_it(self) -> None:
        def body() -> None:
            assert connection.execute_wrappers != []
            query("SELECT 1")
            assert connection.in_atomic_block
            query("SELECT 2")

        assert _guarded(body) == ["BEGIN", TENANT, "SELECT 1", "SELECT 2", "COMMIT"]
        assert connection.execute_wrappers == []
        assert not connection.in_atomic_block

    def test_transaction_control_alone_never_applies(self) -> None:
        def body() -> None:
            with transaction.atomic():
                pass

        assert _guarded(body) == ["BEGIN", "COMMIT"]

    def test_a_session_statement_alone_never_applies(self) -> None:
        assert _guarded(lambda: query("PRAGMA foreign_keys")) == ["PRAGMA foreign_keys"]

    def test_a_user_block_first_takes_the_state_and_it_returns_after(self) -> None:
        def body() -> None:
            with transaction.atomic():
                query("SELECT 1")
            query("SELECT 2")

        assert _guarded(body) == [
            "BEGIN",
            TENANT,
            "SELECT 1",
            "COMMIT",
            "BEGIN",
            TENANT,
            "SELECT 2",
            "COMMIT",
        ]

    def test_a_durable_block_first_takes_the_state(self) -> None:
        def body() -> None:
            with transaction.atomic(durable=True):
                Group.objects.create(name="durable")
            query("SELECT 2")

        log = _guarded(body)
        assert log[:2] == ["BEGIN", TENANT]
        assert log[2].startswith('INSERT INTO "auth_group"')
        assert log[3:] == ["COMMIT", "BEGIN", TENANT, "SELECT 2", "COMMIT"]
        assert Group.objects.filter(name="durable").exists()

    def test_back_to_back_user_blocks_each_take_the_state(self) -> None:
        def body() -> None:
            with transaction.atomic():
                query("SELECT 1")
            with transaction.atomic():
                query("SELECT 2")

        assert _guarded(body) == [
            "BEGIN",
            TENANT,
            "SELECT 1",
            "COMMIT",
            "BEGIN",
            TENANT,
            "SELECT 2",
            "COMMIT",
        ]

    def test_a_nested_block_keeps_the_state_after_its_release(self) -> None:
        hooks: list[int] = []

        def body() -> None:
            with transaction.atomic():
                query("SELECT 1")
                with transaction.atomic():
                    query("SELECT 2")
                query("SELECT 3")
                hooks.append(len(connection.run_on_commit))

        assert _guarded(body) == [
            "BEGIN",
            TENANT,
            "SELECT 1",
            "SAVEPOINT sp",
            "SELECT 2",
            "RELEASE SAVEPOINT sp",
            "SELECT 3",
            "COMMIT",
        ]
        assert hooks == [1]

    def test_a_rolled_back_savepoint_that_took_the_state_drops_it(self) -> None:
        def body() -> None:
            with transaction.atomic():
                with pytest.raises(ValueError, match="block failed"):
                    _failing_block()
                query("SELECT 2")

        assert _guarded(body) == [
            "BEGIN",
            "SAVEPOINT sp",
            TENANT,
            "SELECT 1",
            "ROLLBACK TO SAVEPOINT sp",
            "RELEASE SAVEPOINT sp",
            TENANT,
            "SELECT 2",
            "COMMIT",
        ]

    def test_the_commit_hooks_of_the_user_keep_their_order(self) -> None:
        ran: list[str] = []

        def body() -> None:
            with transaction.atomic():
                transaction.on_commit(lambda: ran.append("before"))
                query("SELECT 1")
                transaction.on_commit(lambda: ran.append("after"))

        _guarded(body)
        assert ran == ["before", "after"]

    def test_unreadable_commit_hooks_replay_before_every_data_statement(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(guard, "_HOOK_FIELDS", 4)
        hooks: list[int] = []

        def body() -> None:
            with transaction.atomic():
                query("SELECT 1")
                query("SELECT 2")
                hooks.append(len(connection.run_on_commit))

        assert _guarded(body) == [
            "BEGIN",
            TENANT,
            "SELECT 1",
            TENANT,
            "SELECT 2",
            "COMMIT",
        ]
        assert hooks == [1]

    def test_commit_hooks_held_in_no_list_read_as_unknown(self) -> None:
        unknown = SimpleNamespace(run_on_commit=None)
        assert guard._pending(unknown, None) is None

    def test_a_session_statement_between_blocks_marks_the_state_stale(self) -> None:
        def body() -> None:
            with transaction.atomic():
                query("SELECT 1")
            query("PRAGMA foreign_keys")
            with transaction.atomic():
                query("SELECT 2")

        assert _guarded(body) == [
            "BEGIN",
            TENANT,
            "SELECT 1",
            "COMMIT",
            "PRAGMA foreign_keys",
            "BEGIN",
            TENANT,
            "SELECT 2",
            "COMMIT",
        ]

    def test_a_commit_is_seen_when_no_begin_passes_the_wrappers(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        connection.ensure_connection()
        raw = connection.connection
        monkeypatch.setattr(
            connection,
            "_start_transaction_under_autocommit",
            lambda: raw.execute("BEGIN"),
        )

        def body() -> None:
            with transaction.atomic():
                query("SELECT 1")
            with transaction.atomic():
                query("SELECT 2")

        assert _guarded(body) == [
            TENANT,
            "SELECT 1",
            "COMMIT",
            TENANT,
            "SELECT 2",
            "COMMIT",
        ]

    def test_a_rollback_then_a_new_block_reapplies_without_a_begin(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        connection.ensure_connection()
        raw = connection.connection
        monkeypatch.setattr(
            connection,
            "_start_transaction_under_autocommit",
            lambda: raw.execute("BEGIN"),
        )

        def body() -> None:
            with pytest.raises(ValueError, match="block failed"):
                _failing_block()
            with transaction.atomic():
                query("SELECT 2")

        assert _guarded(body) == [
            TENANT,
            "SELECT 1",
            "ROLLBACK",
            TENANT,
            "SELECT 2",
            "COMMIT",
        ]

    def test_a_doomed_block_gets_nothing(self) -> None:
        def body() -> None:
            with transaction.atomic():
                transaction.set_rollback(True)
                with pytest.raises(TransactionManagementError):
                    query("SELECT 1")

        assert _guarded(body) == ["BEGIN", "SELECT 1", "ROLLBACK"]

    def test_a_failed_first_statement_rolls_back_and_the_next_reapplies(self) -> None:
        def body() -> None:
            with pytest.raises(DatabaseError):
                query("SELECT * FROM missing_table")
            query("SELECT 1")

        assert _guarded(body) == [
            "BEGIN",
            TENANT,
            "ROLLBACK",
            "SELECT * FROM missing_table",
            "BEGIN",
            TENANT,
            "SELECT 1",
            "COMMIT",
        ]

    def test_a_failed_apply_rolls_back_and_raises(self) -> None:
        states = States(connections=(Captured(Refusing(), "refused", "default"),))
        with (
            pytest.raises(DatabaseError, match="apply failed"),
            _log() as log,
            job_guard(states, deadline=0.0),
        ):
            query("SELECT 1")
        assert log == ["BEGIN", "ROLLBACK", "SELECT 1"]
        assert connection.execute_wrappers == []
        assert not connection.in_atomic_block

    def test_an_exception_rolls_the_state_transaction_back(self) -> None:
        with (
            pytest.raises(ValueError, match="job failed"),
            _log() as log,
            job_guard(_tenant(), deadline=0.0),
        ):
            _create_then_fail("lost")
        assert log[:2] == ["BEGIN", TENANT]
        assert log[-1] == "ROLLBACK"
        assert not Group.objects.filter(name="lost").exists()
        assert connection.execute_wrappers == []

    def test_a_connection_opened_inside_the_job_is_covered(self) -> None:
        seen: list[str] = []

        def record(execute, sql, params, many, context):
            result = execute(sql, params, many, context)
            seen.append(sql)
            return result

        with _fresh_default() as fresh:
            with fresh.execute_wrapper(record), job_guard(_tenant(), deadline=0.0):
                assert fresh.connection is None
                with fresh.cursor() as cursor:
                    cursor.execute("SELECT 1")
                assert fresh.in_atomic_block
            assert seen == ["BEGIN", "SELECT %s", "SELECT 1"]
            assert fresh.execute_wrappers == []
            assert not fresh.in_atomic_block

    def test_states_of_another_alias_leave_this_one_bare(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        declared = {
            **connections.settings,
            "replica": {**connections.settings["default"]},
        }
        monkeypatch.setattr(connections, "settings", declared)
        states = States(connections=(Captured(Tenant(), "acme", "replica"),))
        try:
            with job_guard(states, deadline=0.0):
                assert connection.execute_wrappers == []
                assert len(connections["replica"].execute_wrappers) == 1
            assert connections["replica"].execute_wrappers == []
        finally:
            del connections["replica"]


class TestDeadlineStatement:
    """The remaining budget becomes one parameterised statement, never formatted."""

    def test_the_sql_is_fixed_and_parameterised(self) -> None:
        assert DEADLINE_SQL == (
            "SELECT set_config('statement_timeout', %s, true), "
            "set_config('idle_in_transaction_session_timeout', %s, true)"
        )

    @pytest.mark.parametrize(
        ("remaining", "floor", "ms"),
        [
            (1.2341, 0.05, 1235),
            (2.0, 0.05, 2000),
            (0.0101, 0.05, 50),
            (-3.0, 0.05, 50),
            (0.0, 0.0, 0),
            (0.0001, 0.0, 1),
        ],
    )
    def test_milliseconds_round_up_and_floor(
        self, remaining: float, floor: float, ms: int
    ) -> None:
        assert timeout_ms(remaining, floor) == ms
        assert deadline_statement(remaining, floor) == (
            DEADLINE_SQL,
            (str(ms), str(ms)),
        )


@pytest.mark.django_db(transaction=True)
class TestDeadlineIntoTheDatabase:
    """On PostgreSQL aliases the deadline rides the state transaction or opens one."""

    @pytest.fixture()
    def calls(self, monkeypatch: pytest.MonkeyPatch) -> list[tuple[object, ...]]:
        calls: list[tuple[object, ...]] = []
        _set_config(calls)
        monkeypatch.setattr(connection, "vendor", "postgresql")
        return calls

    def _setting(self, ms: str) -> str:
        return (
            f"SELECT set_config('statement_timeout', '{ms}', true), "
            f"set_config('idle_in_transaction_session_timeout', '{ms}', true)"
        )

    def test_state_mode_folds_the_deadline_into_the_state_transaction(
        self, calls: list[tuple[object, ...]]
    ) -> None:
        log = _guarded(query, deadline=time.monotonic() - 1)
        assert log == ["BEGIN", self._setting("50"), TENANT, "SELECT 1", "COMMIT"]
        assert calls == [
            ("statement_timeout", "50", 1),
            ("idle_in_transaction_session_timeout", "50", 1),
        ]

    def test_the_floor_setting_is_read(self, calls: list[tuple[object, ...]]) -> None:
        with defer(DB_TIMEOUT_FLOOR="2s"):
            log = _guarded(query, deadline=time.monotonic() - 1)
        assert log[1] == self._setting("2000")

    def test_state_mode_without_a_state_opens_nothing(
        self, calls: list[tuple[object, ...]]
    ) -> None:
        assert _guarded(query, States()) == ["SELECT 1"]
        assert calls == []

    def test_always_opens_the_transaction_for_the_deadline_alone(
        self, calls: list[tuple[object, ...]]
    ) -> None:
        with defer(DEADLINE_TO_DB="always"):
            log = _guarded(query, States(), deadline=time.monotonic() - 1)
        assert log == ["BEGIN", self._setting("50"), "SELECT 1", "COMMIT"]

    def test_always_leaves_a_query_free_job_bare(
        self, calls: list[tuple[object, ...]]
    ) -> None:
        with defer(DEADLINE_TO_DB="always"):
            assert _guarded(lambda: None, States()) == []

    def test_false_sends_no_deadline(self, calls: list[tuple[object, ...]]) -> None:
        with defer(DEADLINE_TO_DB=False):
            log = _guarded(query)
        assert log == ["BEGIN", TENANT, "SELECT 1", "COMMIT"]
        assert calls == []

    def test_a_misfit_mode_reads_as_state(
        self, calls: list[tuple[object, ...]]
    ) -> None:
        with defer(DEADLINE_TO_DB="sometimes"):
            log = _guarded(query, deadline=time.monotonic() - 1)
        assert log[1] == self._setting("50")


@pytest.mark.django_db(transaction=True)
class TestAlwaysOnOtherVendors:
    """`always` touches PostgreSQL aliases only."""

    def test_a_sqlite_alias_gets_no_deadline(self) -> None:
        with defer(DEADLINE_TO_DB="always"):
            assert _guarded(query, States()) == ["SELECT 1"]
            assert _guarded(query) == ["BEGIN", TENANT, "SELECT 1", "COMMIT"]
