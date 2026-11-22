import re
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import pytest
from django.contrib.auth.models import Group
from django.db import (
    DatabaseError,
    IntegrityError,
    connection,
    connections,
    transaction,
)
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.backends.signals import connection_created
from django.db.transaction import TransactionManagementError
from django.test.utils import CaptureQueriesContext
from django.utils.connection import ConnectionDoesNotExist

from next.transactions import (
    WRITES,
    AtomicScope,
    LazySavepoint,
    keep_savepoints,
    probe_atomic_aliases,
    request_atomic,
)


_SAVEPOINT_NAME = re.compile(r'"s\d+_x\d+"')
_TABLE = re.compile(r'^(INSERT INTO|DELETE FROM|UPDATE) "(\w+)".*')


class _Abort(BaseException):
    pass


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


def _refuse(*args: object) -> None:
    msg = "refused"
    raise DatabaseError(msg)


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


@pytest.fixture()
def manual() -> Iterator[None]:
    """Run the test with autocommit off, as a manual transaction, and restore it."""
    transaction.set_autocommit(False)
    yield
    transaction.set_autocommit(True)


def _create_then_fail(name: str) -> None:
    with request_atomic(AtomicScope(("default",), atomic=True)):
        Group.objects.create(name=name)
        msg = "view failed"
        raise ValueError(msg)


def _create_and_raise(name: str) -> None:
    with LazySavepoint(("default",)):
        Group.objects.create(name=name)
        msg = "subtree failed"
        raise ValueError(msg)


def _write_then_durable() -> None:
    with LazySavepoint():
        Group.objects.create(name="first")
        with transaction.atomic(durable=True):
            Group.objects.create(name="durable")


def _read_write_then_fail() -> None:
    with LazySavepoint():
        with LazySavepoint(atomic=True):
            list(Group.objects.all())
            Group.objects.create(name="inner")
        msg = "outer failed"
        raise ValueError(msg)


def _create_then_refuse(monkeypatch: pytest.MonkeyPatch, method: str) -> None:
    with LazySavepoint(("default",)):
        Group.objects.create(name="doomed")
        monkeypatch.setattr(type(connections["default"]), method, _refuse)


def _create_with_savepoints_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    with transaction.atomic(), LazySavepoint(probe_atomic_aliases()):
        monkeypatch.setattr(connections["default"], "savepoint", _refuse)
        Group.objects.create(name="never")


def _write_then_catch() -> None:
    with LazySavepoint():
        Group.objects.create(name="first")
        with pytest.raises(IntegrityError):
            Group.objects.create(name="taken")
        Group.objects.count()


def _abort_after_a_write() -> None:
    with LazySavepoint():
        Group.objects.create(name="aborted")
        raise _Abort


@pytest.mark.django_db(transaction=True)
class TestLazySavepointInsideATransaction:
    """Inside a transaction the savepoint waits for the subtree's first statement."""

    def test_a_query_free_subtree_issues_nothing(self) -> None:
        with transaction.atomic(), _log() as log, LazySavepoint(probe_atomic_aliases()):
            pass
        assert log == []
        assert connection.execute_wrappers == []

    def test_one_query_is_wrapped_in_one_savepoint(self) -> None:
        with transaction.atomic(), _log() as log, LazySavepoint(probe_atomic_aliases()):
            list(Group.objects.all())
        assert log == ["SAVEPOINT sp", "SELECT", "RELEASE SAVEPOINT sp"]

    def test_a_failure_rolls_back_only_the_subtree(self) -> None:
        with transaction.atomic():
            Group.objects.create(name="before")
            with _log() as log, LazySavepoint(probe_atomic_aliases()) as point:
                Group.objects.create(name="failed")
                point.rollback()
            Group.objects.create(name="after")
        assert log == [
            "SAVEPOINT sp",
            "INSERT INTO auth_group",
            "ROLLBACK TO SAVEPOINT sp",
            "RELEASE SAVEPOINT sp",
        ]
        assert _names() == ["after", "before"]
        assert connection.execute_wrappers == []

    def test_an_on_commit_hook_of_a_rolled_back_subtree_still_runs(self) -> None:
        committed: list[str] = []
        with transaction.atomic():
            with LazySavepoint(probe_atomic_aliases()) as point:
                Group.objects.create(name="failed")
                transaction.on_commit(lambda: committed.append("hook"))
                point.rollback()
            assert committed == []
        assert committed == ["hook"]
        assert _names() == []

    def test_a_database_error_leaves_the_outer_block_usable(self) -> None:
        Group.objects.create(name="taken")
        with transaction.atomic():
            with LazySavepoint(probe_atomic_aliases()) as point:
                with pytest.raises(IntegrityError):
                    Group.objects.create(name="taken")
                assert transaction.get_rollback()
                point.rollback()
            assert not transaction.get_rollback()
            Group.objects.create(name="after")
        assert _names() == ["after", "taken"]

    def test_an_exception_through_the_block_rolls_back(self) -> None:
        with transaction.atomic():
            with pytest.raises(ValueError, match="subtree failed"):
                _create_and_raise("failed")
            Group.objects.create(name="after")
        assert _names() == ["after"]

    def test_a_clean_exit_with_a_pending_rollback_undoes_the_subtree(self) -> None:
        with transaction.atomic():
            with LazySavepoint(("default",)):
                Group.objects.create(name="doomed")
                transaction.set_rollback(True)
            Group.objects.create(name="after")
        assert _names() == ["after"]

    def test_a_broken_transaction_takes_no_savepoint(self) -> None:
        with transaction.atomic():
            transaction.set_rollback(True)
            with _log() as log, LazySavepoint(("default",)) as point:
                with pytest.raises(TransactionManagementError):
                    list(Group.objects.all())
                point.rollback()
            assert log == ["SELECT"]
            assert transaction.get_rollback()

    def test_a_savepoint_whose_transaction_ended_is_left_alone(self) -> None:
        point = LazySavepoint(("default",))
        with transaction.atomic():
            point.__enter__()
            Group.objects.create(name="kept")
        with _log() as log:
            point.rollback()
        assert log == []
        assert _names() == ["kept"]
        assert connection.execute_wrappers == []

    def test_a_failed_savepoint_rollback_dooms_the_outer_block(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with transaction.atomic():
            with LazySavepoint(("default",)) as point:
                Group.objects.create(name="doomed")
                wrapper_class = type(connections["default"])
                monkeypatch.setattr(wrapper_class, "savepoint_rollback", _refuse)
                point.rollback()
            assert transaction.get_rollback()
            assert connection.execute_wrappers == []
        assert _names() == []

    def test_a_failed_savepoint_release_raises_and_dooms_the_outer_block(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with transaction.atomic():
            with pytest.raises(DatabaseError, match="refused"):
                _create_then_refuse(monkeypatch, "savepoint_commit")
            assert transaction.get_rollback()
        assert _names() == []

    def test_a_failed_savepoint_leaves_the_outer_block_to_roll_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with pytest.raises(DatabaseError, match="refused"):
            _create_with_savepoints_refused(monkeypatch)
        monkeypatch.undo()
        with transaction.atomic(durable=True):
            Group.objects.create(name="durable")
        assert _names() == ["durable"]
        assert connection.execute_wrappers == []

    def test_nested_subtrees_roll_back_their_own_work(self) -> None:
        with transaction.atomic():
            aliases = probe_atomic_aliases()
            with LazySavepoint(aliases) as outer:
                Group.objects.create(name="outer")
                with LazySavepoint(aliases) as inner:
                    Group.objects.create(name="inner")
                    inner.rollback()
                with LazySavepoint(aliases):
                    Group.objects.create(name="sibling")
                assert _names() == ["outer", "sibling"]
                outer.rollback()
            Group.objects.create(name="after")
        assert _names() == ["after"]

    def test_an_inner_first_query_takes_the_outer_savepoint_first(self) -> None:
        with transaction.atomic():
            aliases = probe_atomic_aliases()
            with _log() as log, LazySavepoint(aliases):
                with LazySavepoint(aliases) as inner:
                    Group.objects.create(name="inner")
                    inner.rollback()
                Group.objects.create(name="outer")
        assert log == [
            "SAVEPOINT sp",
            "SAVEPOINT sp",
            "INSERT INTO auth_group",
            "ROLLBACK TO SAVEPOINT sp",
            "RELEASE SAVEPOINT sp",
            "INSERT INTO auth_group",
            "RELEASE SAVEPOINT sp",
        ]
        assert _names() == ["outer"]

    def test_a_user_block_takes_its_savepoint_after_the_subtree(self) -> None:
        with (
            transaction.atomic(),
            _log() as log,
            LazySavepoint(probe_atomic_aliases()) as point,
        ):
            with transaction.atomic():
                Group.objects.create(name="user")
            point.rollback()
        assert log == [
            "SAVEPOINT sp",
            "SAVEPOINT sp",
            "INSERT INTO auth_group",
            "RELEASE SAVEPOINT sp",
            "ROLLBACK TO SAVEPOINT sp",
            "RELEASE SAVEPOINT sp",
        ]
        assert _names() == []

    def test_a_block_without_a_savepoint_is_undone_with_the_subtree(self) -> None:
        with transaction.atomic():
            with LazySavepoint(probe_atomic_aliases()) as point:
                with transaction.atomic(savepoint=False):
                    Group.objects.create(name="inside")
                Group.objects.create(name="after")
                point.rollback()
            Group.objects.create(name="kept")
        assert _names() == ["kept"]

    def test_false_still_protects_a_transaction(self) -> None:
        with (
            transaction.atomic(),
            LazySavepoint(probe_atomic_aliases(), atomic=False) as point,
        ):
            Group.objects.create(name="failed")
            point.rollback()
        assert _names() == []

    def test_the_handle_belongs_to_its_thread(self) -> None:
        refused: list[BaseException] = []
        with transaction.atomic(), LazySavepoint(("default",)) as point:
            Group.objects.create(name="kept")

            def close() -> None:
                try:
                    point.rollback()
                except RuntimeError as exc:
                    refused.append(exc)

            worker = threading.Thread(target=close)
            worker.start()
            worker.join()
        assert [str(exc) for exc in refused] == [
            "A lazy savepoint must be closed on the thread that opened it."
        ]
        assert _names() == ["kept"]


WRITES_OF = {
    "create": lambda name: Group.objects.create(name=name),
    "get_or_create": lambda name: Group.objects.get_or_create(name=name),
}


@pytest.fixture(params=["transaction", "autocommit"])
def aliases(request: pytest.FixtureRequest) -> Iterator[tuple[str, ...]]:
    """Yield the aliases a subtree watches, from inside a transaction or outside."""
    if request.param == "transaction":
        with transaction.atomic():
            yield probe_atomic_aliases()
    else:
        yield probe_atomic_aliases()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("write", WRITES_OF.values(), ids=WRITES_OF.keys())
class TestNestedSubtreeRollback:
    """An inner subtree undoes its own work and an outer one undoes the finished inner."""

    def test_an_inner_rollback_keeps_the_outer_work(
        self, aliases: tuple[str, ...], write: Callable[[str], object]
    ) -> None:
        with LazySavepoint(aliases):
            Group.objects.create(name="outer")
            with LazySavepoint(aliases) as inner:
                write("inner")
                assert inner.rollback()
            Group.objects.create(name="after")
        assert _names() == ["after", "outer"]

    def test_an_outer_rollback_undoes_the_finished_inner_work(
        self, aliases: tuple[str, ...], write: Callable[[str], object]
    ) -> None:
        with LazySavepoint(aliases) as outer:
            Group.objects.create(name="outer")
            with LazySavepoint(aliases):
                write("inner")
            assert outer.rollback()
        assert _names() == []


def _missing_table() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM next_missing_table")


@pytest.mark.django_db(transaction=True)
class TestNestedReadsShareTheOuterSavepoint:
    """Inside a transaction an inner subtree takes a savepoint before its first write."""

    def test_inner_reads_issue_one_savepoint_pair(self) -> None:
        with transaction.atomic():
            aliases = probe_atomic_aliases()
            with _log() as log, LazySavepoint(aliases):
                list(Group.objects.all())
                for _ in range(3):
                    with LazySavepoint(aliases):
                        list(Group.objects.all())
        assert log == ["SAVEPOINT sp", *["SELECT"] * 4, "RELEASE SAVEPOINT sp"]

    def test_an_inner_first_read_takes_only_the_outer_savepoint(self) -> None:
        with transaction.atomic():
            aliases = probe_atomic_aliases()
            with _log() as log, LazySavepoint(aliases), LazySavepoint(aliases) as inner:
                list(Group.objects.all())
                Group.objects.create(name="inner")
                assert inner.rollback()
        assert log == [
            "SAVEPOINT sp",
            "SELECT",
            "SAVEPOINT sp",
            "INSERT INTO auth_group",
            "ROLLBACK TO SAVEPOINT sp",
            "RELEASE SAVEPOINT sp",
            "RELEASE SAVEPOINT sp",
        ]
        assert _names() == []

    def test_a_failed_inner_read_leaves_the_undo_to_the_outer_savepoint(self) -> None:
        with transaction.atomic():
            aliases = probe_atomic_aliases()
            with LazySavepoint(aliases) as outer:
                Group.objects.create(name="outer")
                with LazySavepoint(aliases) as inner:
                    with pytest.raises(DatabaseError):
                        _missing_table()
                    assert not inner.rollback()
                assert outer.rollback()
            Group.objects.create(name="after")
        assert _names() == ["after"]

    def test_an_inner_doom_without_a_savepoint_is_left_to_the_outer_one(self) -> None:
        with transaction.atomic():
            aliases = probe_atomic_aliases()
            with LazySavepoint(aliases) as outer:
                list(Group.objects.all())
                inner = LazySavepoint(aliases)
                with inner:
                    transaction.set_rollback(True)
                assert not inner.rollback()
                assert outer.rollback()
            assert not transaction.get_rollback()

    def test_an_inner_atomic_block_makes_the_failed_read_contained_there(self) -> None:
        with transaction.atomic():
            aliases = probe_atomic_aliases()
            with LazySavepoint(aliases):
                Group.objects.create(name="outer")
                with LazySavepoint(aliases) as inner:
                    with pytest.raises(DatabaseError), transaction.atomic():
                        _missing_table()
                    assert inner.rollback()
                assert not transaction.get_rollback()
                assert _names() == ["outer"]
            assert not transaction.get_rollback()
        assert _names() == ["outer"]

    def test_an_inner_write_makes_the_failed_read_contained_there(self) -> None:
        with transaction.atomic():
            aliases = probe_atomic_aliases()
            with LazySavepoint(aliases) as outer:
                Group.objects.create(name="outer")
                with LazySavepoint(aliases) as inner:
                    Group.objects.create(name="inner")
                    with pytest.raises(DatabaseError):
                        _missing_table()
                    assert inner.rollback()
                assert _names() == ["outer"]
                assert outer.rollback() is True
        assert _names() == []

    def test_a_failure_outside_a_transaction_breaks_nothing(self) -> None:
        with LazySavepoint() as point:
            with pytest.raises(DatabaseError):
                _missing_table()
            assert point.rollback()

    def test_an_inner_failed_read_after_an_autocommit_write_goes_outward(self) -> None:
        with LazySavepoint() as outer:
            Group.objects.create(name="outer")
            with LazySavepoint() as inner:
                with pytest.raises(DatabaseError):
                    _missing_table()
                assert not inner.rollback()
            assert outer.rollback()
        assert _names() == []
        assert connection.get_autocommit()

    def test_an_outermost_failed_read_rolls_back_to_its_own_savepoint(self) -> None:
        with transaction.atomic():
            with LazySavepoint(probe_atomic_aliases()) as point:
                with pytest.raises(DatabaseError):
                    _missing_table()
                assert point.rollback()
            Group.objects.create(name="after")
        assert _names() == ["after"]


@pytest.mark.django_db(transaction=True)
class TestUserBlocksInsideNestedSubtrees:
    """An inner subtree takes its savepoint before a user block's, never inside it."""

    def test_a_first_get_or_create_keeps_both_savepoints_valid(self) -> None:
        with transaction.atomic():
            aliases = probe_atomic_aliases()
            with _log() as log, LazySavepoint(aliases):
                list(Group.objects.all())
                with LazySavepoint(aliases):
                    Group.objects.get_or_create(name="inner")
        assert log == [
            "SAVEPOINT sp",
            "SELECT",
            "SELECT",
            "SAVEPOINT sp",
            "SAVEPOINT sp",
            "INSERT INTO auth_group",
            "RELEASE SAVEPOINT sp",
            "RELEASE SAVEPOINT sp",
            "RELEASE SAVEPOINT sp",
        ]
        assert _names() == ["inner"]

    def test_a_caught_user_rollback_leaves_the_subtree_savepoint(self) -> None:
        with transaction.atomic():
            aliases = probe_atomic_aliases()
            with LazySavepoint(aliases):
                Group.objects.create(name="outer")
                with LazySavepoint(aliases) as inner:
                    with pytest.raises(ValueError, match="view failed"):
                        _create_then_fail("caught")
                    Group.objects.create(name="inner")
                    assert inner.rollback()
                Group.objects.create(name="sibling")
        assert _names() == ["outer", "sibling"]

    def test_a_caught_integrity_error_leaves_the_subtree_savepoint(self) -> None:
        Group.objects.create(name="taken")
        with transaction.atomic():
            aliases = probe_atomic_aliases()
            with LazySavepoint(aliases):
                Group.objects.create(name="outer")
                with LazySavepoint(aliases) as inner:
                    with pytest.raises(IntegrityError), transaction.atomic():
                        Group.objects.create(name="taken")
                    Group.objects.create(name="inner")
                    assert inner.rollback()
        assert _names() == ["outer", "taken"]

    def test_a_user_block_in_autocommit_nests_after_the_inner_savepoint(self) -> None:
        with LazySavepoint() as outer:
            Group.objects.create(name="outer")
            with LazySavepoint(probe_atomic_aliases()) as inner:
                Group.objects.get_or_create(name="inner")
                assert inner.rollback()
            Group.objects.create(name="sibling")
            assert _names() == ["outer", "sibling"]
            assert outer.rollback()
        assert _names() == []


@pytest.mark.django_db(transaction=True)
class TestLazySavepointInAutocommit:
    """In autocommit `atomic` decides when a transaction opens around the subtree."""

    def test_a_query_free_subtree_issues_nothing(self) -> None:
        with _log() as log, LazySavepoint():
            pass
        assert log == []

    def test_reads_open_nothing_by_default(self) -> None:
        with _log() as log, LazySavepoint():
            list(Group.objects.all())
        assert log == ["SELECT"]

    def test_a_failure_between_two_writes_leaves_nothing(self) -> None:
        with _log() as log, LazySavepoint(atomic=WRITES) as point:
            list(Group.objects.all())
            Group.objects.create(name="one")
            Group.objects.create(name="two")
            point.rollback()
        assert log == [
            "SELECT",
            "BEGIN",
            "INSERT INTO auth_group",
            "INSERT INTO auth_group",
            "ROLLBACK",
        ]
        assert _names() == []
        assert connection.get_autocommit()

    def test_a_clean_exit_commits(self) -> None:
        with LazySavepoint():
            Group.objects.create(name="kept")
        assert _names() == ["kept"]
        assert not connection.in_atomic_block

    def test_a_base_exception_rolls_back_and_unwinds(self) -> None:
        with pytest.raises(_Abort):
            _abort_after_a_write()
        assert _names() == []
        assert connection.execute_wrappers == []
        assert connection.get_autocommit()

    def test_true_opens_before_the_first_read(self) -> None:
        with _log() as log, LazySavepoint(atomic=True) as point:
            list(Group.objects.all())
            Group.objects.create(name="failed")
            point.rollback()
        assert log[:2] == ["BEGIN", "SELECT"]
        assert _names() == []

    def test_false_leaves_autocommit_alone(self) -> None:
        with _log() as log, LazySavepoint(atomic=False) as point:
            Group.objects.create(name="one")
            Group.objects.create(name="two")
            point.rollback()
        assert log == ["INSERT INTO auth_group", "INSERT INTO auth_group"]
        assert _names() == ["one", "two"]
        assert connection.execute_wrappers == []

    def test_a_statement_that_refuses_a_transaction_opens_none(self) -> None:
        with _log() as log, LazySavepoint(atomic=True), connection.cursor() as cursor:
            cursor.execute("VACUUM")
        assert log == ["VACUUM"]

    def test_a_caught_error_on_the_first_write_leaves_autocommit_usable(self) -> None:
        Group.objects.create(name="taken")
        with _log() as log, LazySavepoint() as point:
            with pytest.raises(IntegrityError):
                Group.objects.create(name="taken")
            assert connection.get_autocommit()
            assert Group.objects.count() == 1
            Group.objects.create(name="after")
            point.rollback()
        assert log == [
            "BEGIN",
            "ROLLBACK",
            "INSERT INTO auth_group",
            "SELECT",
            "BEGIN",
            "INSERT INTO auth_group",
            "ROLLBACK",
        ]
        assert _names() == ["taken"]

    def test_a_caught_error_after_a_write_dooms_the_transaction(self) -> None:
        Group.objects.create(name="taken")
        with pytest.raises(TransactionManagementError):
            _write_then_catch()
        assert _names() == ["taken"]
        assert connection.get_autocommit()

    def test_a_user_block_opened_first_commits_on_its_own(self) -> None:
        with _log() as log, LazySavepoint() as point:
            with transaction.atomic():
                Group.objects.create(name="user")
            Group.objects.create(name="subtree")
            point.rollback()
        assert log == [
            "BEGIN",
            "INSERT INTO auth_group",
            "COMMIT",
            "BEGIN",
            "INSERT INTO auth_group",
            "ROLLBACK",
        ]
        assert _names() == ["user"]

    def test_a_user_block_inside_the_transaction_rolls_back_with_it(self) -> None:
        with LazySavepoint() as point:
            Group.objects.create(name="first")
            with transaction.atomic():
                Group.objects.create(name="user")
            point.rollback()
        assert _names() == []

    def test_a_block_django_opens_first_keeps_its_commit(self) -> None:
        with LazySavepoint() as point:
            Group.objects.get_or_create(name="created")
            point.rollback()
        assert _names() == ["created"]

    def test_true_takes_get_or_create_into_the_transaction(self) -> None:
        with _log() as log, LazySavepoint(atomic=True) as point:
            Group.objects.get_or_create(name="created")
            point.rollback()
        assert log == [
            "BEGIN",
            "SELECT",
            "SAVEPOINT sp",
            "INSERT INTO auth_group",
            "RELEASE SAVEPOINT sp",
            "ROLLBACK",
        ]
        assert _names() == []

    def test_true_leaves_a_first_update_or_create_to_its_own_block(self) -> None:
        with LazySavepoint(atomic=True) as point:
            Group.objects.update_or_create(name="created")
            point.rollback()
        assert _names() == ["created"]

    def test_a_failed_user_block_keeps_autocommit_usable(self) -> None:
        Group.objects.create(name="taken")
        with LazySavepoint():
            with pytest.raises(IntegrityError), transaction.atomic():
                Group.objects.create(name="taken")
            Group.objects.create(name="after")
        assert _names() == ["after", "taken"]

    def test_a_durable_block_first_keeps_its_commit(self) -> None:
        with LazySavepoint() as point:
            with transaction.atomic(durable=True):
                Group.objects.create(name="durable")
            Group.objects.create(name="failed")
            point.rollback()
        assert _names() == ["durable"]

    def test_a_durable_block_after_a_write_cannot_nest(self) -> None:
        with pytest.raises(RuntimeError, match="durable atomic block cannot be nested"):
            _write_then_durable()
        assert _names() == []

    def test_a_manual_transaction_is_left_to_its_owner(self, manual: None) -> None:
        with LazySavepoint() as point:
            with transaction.atomic():
                Group.objects.create(name="manual")
            point.rollback()
        transaction.commit()
        assert _names() == ["manual"]

    def test_nested_subtrees_share_the_transaction(self) -> None:
        with _log() as log, LazySavepoint():
            Group.objects.create(name="outer")
            with LazySavepoint() as inner:
                Group.objects.create(name="inner")
                inner.rollback()
            Group.objects.create(name="after")
        assert log == [
            "BEGIN",
            "INSERT INTO auth_group",
            "SAVEPOINT sp",
            "INSERT INTO auth_group",
            "ROLLBACK TO SAVEPOINT sp",
            "RELEASE SAVEPOINT sp",
            "INSERT INTO auth_group",
            "COMMIT",
        ]
        assert _names() == ["after", "outer"]

    def test_an_inner_true_subtree_opens_the_outer_transaction(self) -> None:
        with _log() as log, pytest.raises(ValueError, match="outer failed"):
            _read_write_then_fail()
        assert log == [
            "BEGIN",
            "SAVEPOINT sp",
            "SELECT",
            "INSERT INTO auth_group",
            "RELEASE SAVEPOINT sp",
            "ROLLBACK",
        ]
        assert _names() == []

    def test_an_inner_subtree_rolls_back_inside_an_outer_true_one(self) -> None:
        with _log() as log, LazySavepoint(atomic=True):
            with LazySavepoint() as inner:
                list(Group.objects.all())
                Group.objects.create(name="inner")
                inner.rollback()
            Group.objects.create(name="outer")
        assert log == [
            "BEGIN",
            "SAVEPOINT sp",
            "SELECT",
            "INSERT INTO auth_group",
            "ROLLBACK TO SAVEPOINT sp",
            "RELEASE SAVEPOINT sp",
            "INSERT INTO auth_group",
            "COMMIT",
        ]
        assert _names() == ["outer"]

    def test_a_failed_savepoint_while_opening_rolls_the_transaction_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with LazySavepoint(), LazySavepoint():
            monkeypatch.setattr(connections["default"], "savepoint", _refuse)
            with pytest.raises(DatabaseError, match="refused"):
                Group.objects.create(name="never")
            monkeypatch.undo()
            assert connection.get_autocommit()
            Group.objects.create(name="after")
        with transaction.atomic(durable=True):
            Group.objects.create(name="durable")
        assert _names() == ["after", "durable"]

    def test_an_inner_subtree_inside_a_user_block(self) -> None:
        with LazySavepoint(), transaction.atomic():
            with LazySavepoint() as inner:
                Group.objects.create(name="inner")
                inner.rollback()
            Group.objects.create(name="user")
        assert _names() == ["user"]


@pytest.mark.django_db(transaction=True)
class TestLazySavepointConnections:
    """The handles of one thread share one wrapper per connection and one receiver."""

    def test_a_connection_opened_inside_the_subtree_is_covered(self) -> None:
        with LazySavepoint() as point, _fresh_default():
            fresh = connections["default"]
            Group.objects.create(name="one")
            Group.objects.create(name="two")
            assert fresh.in_atomic_block
            point.rollback()
            assert fresh.execute_wrappers == []
        assert _names() == []
        assert connection.execute_wrappers == []

    def test_a_copy_of_a_connection_is_not_covered(self) -> None:
        with _fresh_default(), LazySavepoint():
            fresh = connections["default"]
            copy = fresh.copy()
            try:
                copy.ensure_connection()
                assert copy.execute_wrappers == []
            finally:
                _close(copy)
            fresh.ensure_connection()
            assert len(fresh.execute_wrappers) == 1

    def test_a_connection_another_thread_opens_is_not_covered(self) -> None:
        seen: list[object] = []

        def work() -> None:
            other = connections["default"]
            other.ensure_connection()
            seen.append(list(other.execute_wrappers))
            _close(other)

        with LazySavepoint():
            worker = threading.Thread(target=work)
            worker.start()
            worker.join()
        assert seen == [[]]

    def test_a_connection_opened_after_the_subtree_is_left_bare(self) -> None:
        with LazySavepoint():
            pass
        with _fresh_default():
            fresh = connections["default"]
            fresh.ensure_connection()
            assert fresh.execute_wrappers == []

    def test_a_connection_known_before_it_connects_keeps_one_wrapper(self) -> None:
        with _fresh_default():
            fresh = connections["default"]
            with LazySavepoint():
                fresh.ensure_connection()
                assert len(fresh.execute_wrappers) == 1

    def test_a_handle_that_watches_no_connection_ignores_new_ones(self) -> None:
        with LazySavepoint(atomic=False), _fresh_default():
            fresh = connections["default"]
            fresh.ensure_connection()
            assert fresh.execute_wrappers == []

    def test_nested_handles_share_one_wrapper(self) -> None:
        with LazySavepoint():
            with LazySavepoint(), LazySavepoint(atomic=True):
                assert len(connection.execute_wrappers) == 1
            assert len(connection.execute_wrappers) == 1
        assert connection.execute_wrappers == []

    def test_no_handle_connects_a_receiver(self) -> None:
        receivers = list(connection_created.receivers)
        with LazySavepoint(), LazySavepoint():
            assert connection_created.receivers == receivers
        assert connection_created.receivers == receivers

    def test_a_failed_enter_undoes_what_it_installed(self) -> None:
        point = LazySavepoint(("default", "missing"))
        with pytest.raises(ConnectionDoesNotExist):
            point.__enter__()
        assert connection.execute_wrappers == []
        with LazySavepoint():
            assert len(connection.execute_wrappers) == 1

    def test_closing_twice_is_harmless(self) -> None:
        point = LazySavepoint(atomic=False)
        with point:
            point.rollback()
        point.rollback()
        assert connection.execute_wrappers == []


@pytest.mark.django_db(transaction=True)
class TestKeptSavepoints:
    """A block keeps the guards on between the handles opened inside it."""

    def test_sibling_handles_share_the_guards(self) -> None:
        with keep_savepoints():
            with LazySavepoint():
                assert len(connection.execute_wrappers) == 1
            assert len(connection.execute_wrappers) == 1
            with LazySavepoint() as point:
                Group.objects.create(name="undone")
                point.rollback()
            Group.objects.create(name="kept")
        assert connection.execute_wrappers == []
        assert _names() == ["kept"]

    def test_a_block_without_a_handle_installs_nothing(self) -> None:
        with keep_savepoints():
            assert connection.execute_wrappers == []
        assert connection.execute_wrappers == []

    def test_nested_blocks_close_with_the_outermost(self) -> None:
        with keep_savepoints():
            with keep_savepoints(), LazySavepoint():
                pass
            assert len(connection.execute_wrappers) == 1
        assert connection.execute_wrappers == []

    def test_a_handle_left_open_closes_the_session_itself(self) -> None:
        point = LazySavepoint()
        with keep_savepoints():
            point.__enter__()
        assert len(connection.execute_wrappers) == 1
        point.__exit__(None, None, None)
        assert connection.execute_wrappers == []

    def test_a_connection_opened_between_handles_is_guarded(self) -> None:
        with keep_savepoints():
            with LazySavepoint():
                pass
            with _fresh_default():
                fresh = connections["default"]
                fresh.ensure_connection()
                assert len(fresh.execute_wrappers) == 1
                with LazySavepoint() as point:
                    Group.objects.create(name="undone")
                    point.rollback()
                assert _names() == []
        assert connection.execute_wrappers == []
