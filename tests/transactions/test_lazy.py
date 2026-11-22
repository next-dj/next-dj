import re
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from django.contrib.auth.models import Group
from django.db import connection, connections, transaction
from django.db.backends.base.base import BaseDatabaseWrapper
from django.test.utils import CaptureQueriesContext

from next.transactions import (
    WRITES,
    AtomicMode,
    AtomicScope,
    LazySavepoint,
    probe_atomic_aliases,
    request_atomic,
    savepoints,
)


LAZY = AtomicScope(("default",), atomic=WRITES)
MODES: tuple[AtomicMode, ...] = (True, WRITES, False)

_SAVEPOINT_NAME = re.compile(r'"s\d+_x\d+"')
_TABLE = re.compile(r'^(INSERT INTO|DELETE FROM|UPDATE) "(\w+)".*')


def _statement(sql: str) -> str:
    sql = _SAVEPOINT_NAME.sub("sp", sql)
    if sql.startswith("SELECT"):
        return "SELECT"
    return _TABLE.sub(r"\1 \2", sql)


@contextmanager
def _log() -> Iterator[list[str]]:
    statements: list[str] = []
    with CaptureQueriesContext(connection) as queries:
        yield statements
    statements.extend(_statement(query["sql"]) for query in queries.captured_queries)


def _names() -> list[str]:
    return sorted(Group.objects.values_list("name", flat=True))


def _close(wrapper: BaseDatabaseWrapper) -> None:
    if wrapper.connection is not None:
        wrapper.connection.close()


@contextmanager
def _fresh_default() -> Iterator[None]:
    """Swap in a new `default` connection object and close it on the way out."""
    original = connections["default"]
    del connections["default"]
    try:
        yield
    finally:
        fresh = connections["default"]
        connections["default"] = original
        _close(fresh)


def _fail() -> None:
    msg = "view failed"
    raise ValueError(msg)


def _write_twice_then_fail() -> None:
    with request_atomic(LAZY):
        Group.objects.create(name="one")
        Group.objects.create(name="two")
        _fail()


def _read_then_fail() -> None:
    with request_atomic(LAZY):
        list(Group.objects.all())
        _fail()


def _get_or_create_then_fail() -> None:
    with request_atomic(LAZY):
        Group.objects.get_or_create(name="created")
        Group.objects.create(name="after")
        _fail()


def _hooks_then_fail(events: list[str]) -> None:
    with request_atomic(LAZY):
        transaction.on_commit(lambda: events.append("early"))
        Group.objects.create(name="written")
        transaction.on_commit(lambda: events.append("late"))
        _fail()


def _boundary_write_then_view_fails(mode: AtomicMode) -> None:
    with request_atomic(LAZY):
        with LazySavepoint(probe_atomic_aliases(), atomic=mode):
            Group.objects.create(name="boundary")
        _fail()


@pytest.mark.django_db(transaction=True)
class TestLazyViewTransaction:
    """A `"writes"` view opens its transaction before its first write and not sooner."""

    def test_a_query_free_view_issues_nothing(self) -> None:
        with _log() as log, request_atomic(LAZY):
            assert not connection.in_atomic_block
        assert log == []
        assert connection.execute_wrappers == []

    def test_a_query_free_view_opens_no_connection(self) -> None:
        with _fresh_default():
            fresh = connections["default"]
            with request_atomic(LAZY):
                pass
            assert fresh.connection is None

    def test_reads_run_in_autocommit_until_the_first_write(self) -> None:
        with _log() as log, request_atomic(LAZY):
            list(Group.objects.all())
            assert not connection.in_atomic_block
            Group.objects.create(name="one")
            assert connection.in_atomic_block
            Group.objects.create(name="two")
        assert log == [
            "SELECT",
            "BEGIN",
            "INSERT INTO auth_group",
            "INSERT INTO auth_group",
            "COMMIT",
        ]
        assert _names() == ["one", "two"]
        assert connection.get_autocommit()

    def test_a_failure_after_the_first_write_rolls_the_whole_view_back(self) -> None:
        with pytest.raises(ValueError, match="view failed"):
            _write_twice_then_fail()
        assert _names() == []
        assert connection.get_autocommit()
        assert connection.execute_wrappers == []

    def test_a_failure_before_any_write_leaves_nothing_open(self) -> None:
        with pytest.raises(ValueError, match="view failed"):
            _read_then_fail()
        assert not connection.in_atomic_block
        assert connection.execute_wrappers == []

    def test_inside_a_transaction_the_view_rolls_back_to_its_savepoint(self) -> None:
        with transaction.atomic():
            with pytest.raises(ValueError, match="view failed"):
                _write_twice_then_fail()
            Group.objects.create(name="outer")
        assert _names() == ["outer"]

    def test_a_block_django_opens_before_the_first_write_commits_on_its_own(
        self,
    ) -> None:
        with pytest.raises(ValueError, match="view failed"):
            _get_or_create_then_fail()
        assert _names() == ["created"]

    def test_a_connection_a_subtree_meets_later_stays_out_of_the_view(self) -> None:
        with request_atomic(LAZY), LazySavepoint() as point, _fresh_default():
            fresh = connections["default"]
            Group.objects.create(name="subtree")
            point.rollback()
            assert not fresh.in_atomic_block
            assert len(fresh.execute_wrappers) == 1
        assert _names() == []


@pytest.mark.django_db(transaction=True)
class TestOnCommitByMode:
    """When an `on_commit` hook runs follows the mode the view's transaction opens in."""

    @pytest.mark.parametrize(
        ("atomic", "expected"),
        [
            (True, ["write", "end", "early", "late"]),
            (WRITES, ["early", "write", "end", "late"]),
            (False, ["early", "write", "late", "end"]),
        ],
    )
    def test_a_hook_before_and_after_the_first_write(
        self, atomic: AtomicMode, expected: list[str]
    ) -> None:
        events: list[str] = []
        with request_atomic(AtomicScope(("default",), atomic=atomic)):
            transaction.on_commit(lambda: events.append("early"))
            Group.objects.create(name="written")
            events.append("write")
            transaction.on_commit(lambda: events.append("late"))
            events.append("end")
        assert events == expected

    def test_a_rollback_drops_only_the_hook_set_after_the_first_write(self) -> None:
        events: list[str] = []
        with pytest.raises(ValueError, match="view failed"):
            _hooks_then_fail(events)
        assert events == ["early"]
        assert _names() == []


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("mode", MODES)
class TestBoundaryInsideALazyView:
    """A boundary inside a view transaction still waiting nests in it once it opens."""

    def test_a_write_only_in_the_boundary_rolls_back_with_it(
        self, mode: AtomicMode
    ) -> None:
        with request_atomic(LAZY):
            with LazySavepoint(probe_atomic_aliases(), atomic=mode) as boundary:
                Group.objects.create(name="boundary")
                assert boundary.rollback()
            assert connection.in_atomic_block
            Group.objects.create(name="view")
        assert _names() == ["view"]

    def test_a_view_write_outlives_a_failed_boundary(self, mode: AtomicMode) -> None:
        with request_atomic(LAZY):
            Group.objects.create(name="view")
            with LazySavepoint(probe_atomic_aliases(), atomic=mode) as boundary:
                Group.objects.create(name="boundary")
                assert boundary.rollback()
        assert _names() == ["view"]

    def test_a_kept_boundary_write_rolls_back_with_the_view(
        self, mode: AtomicMode
    ) -> None:
        with pytest.raises(ValueError, match="view failed"):
            _boundary_write_then_view_fails(mode)
        assert _names() == []


@pytest.mark.django_db(transaction=True)
class TestPendingViewProbe:
    """A view transaction waiting for its first write counts as an open one."""

    def test_the_probe_names_a_waiting_view_alias(self) -> None:
        with request_atomic(LAZY):
            assert probe_atomic_aliases() == ("default",)
            Group.objects.create(name="written")
            assert probe_atomic_aliases() == ("default",)
        assert probe_atomic_aliases() == ()

    def test_a_waiting_subtree_is_no_view_transaction(self) -> None:
        with LazySavepoint():
            assert probe_atomic_aliases() == ()

    def test_a_reading_boundary_that_opens_on_reads_opens_the_view(self) -> None:
        with _log() as log, request_atomic(LAZY):
            with LazySavepoint(probe_atomic_aliases(), atomic=True):
                list(Group.objects.all())
            assert connection.in_atomic_block
        assert log == [
            "BEGIN",
            "SAVEPOINT sp",
            "SELECT",
            "RELEASE SAVEPOINT sp",
            "COMMIT",
        ]

    def test_a_subtree_that_opens_nothing_skips_an_alias_no_view_waits_on(self) -> None:
        with LazySavepoint(("default",), atomic=False):
            assert connection.execute_wrappers == []


def test_the_first_alias_settles_first_as_nested_blocks_do(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    replica = {**connections.settings["default"], "NAME": "replica"}
    monkeypatch.setitem(connections.settings, "replica", replica)
    settled: list[str] = []
    settle = savepoints._Guard.settle

    def recording(guard: savepoints._Guard, *args: object, **kwargs: bool) -> None:
        settled.append(guard.connection.alias)
        settle(guard, *args, **kwargs)

    monkeypatch.setattr(savepoints._Guard, "settle", recording)
    try:
        with request_atomic(AtomicScope(("default", "replica"), atomic=WRITES)):
            pass
    finally:
        del connections["replica"]
    assert settled == ["default", "replica"]
