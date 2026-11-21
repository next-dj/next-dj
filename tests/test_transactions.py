import functools
import re
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

import pytest
from django.conf import settings
from django.contrib.auth.models import Group
from django.core.exceptions import ImproperlyConfigured
from django.core.handlers.base import BaseHandler
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
from django.db.utils import ConnectionHandler
from django.http import HttpRequest, HttpResponse
from django.test import RequestFactory, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils.connection import ConnectionDoesNotExist
from django.utils.module_loading import import_string

from next import transactions
from next.transactions import (
    SAFE_METHODS,
    WRITES,
    AtomicOverride,
    LazySavepoint,
    StatementKind,
    ViewKind,
    atomic_aliases,
    keep_savepoints,
    lazy_savepoint,
    override_of,
    override_problem,
    probe_atomic_aliases,
    request_atomic,
    safe_aliases,
    skip_atomic_requests,
    statement_kind,
)


RULE_PATH = "tests.test_transactions.rule_by_kind"
TWO_ALIASES = ("default", "replica")
SQLITE = "django.db.backends.sqlite3"

not_callable = 42


def rule_by_kind(request: HttpRequest, kind: ViewKind) -> tuple[str, ...]:
    return ("default",) if kind is ViewKind.ACTION else ()


def rule_one_name(request: HttpRequest, kind: ViewKind) -> str:
    return "default"


def rule_with_unknown(request: HttpRequest, kind: ViewKind) -> list[str]:
    return ["replica", "default", "default"]


def _request(method: str) -> HttpRequest:
    return RequestFactory().generic(method, "/")


def _transactions(**scope: object) -> override_settings:
    return override_settings(NEXT_FRAMEWORK={"TRANSACTIONS": scope})


def _connections(
    monkeypatch: pytest.MonkeyPatch,
    aliases: tuple[str, ...] = TWO_ALIASES,
    *,
    atomic_requests: bool = False,
) -> None:
    """Give Django's connection handler these aliases, as a second database would."""
    declared = {
        alias: {"ENGINE": SQLITE, "ATOMIC_REQUESTS": atomic_requests}
        for alias in aliases
    }
    monkeypatch.setattr(connections, "settings", declared)


def _create_then_fail(name: str) -> None:
    with request_atomic(("default",)):
        Group.objects.create(name=name)
        msg = "view failed"
        raise ValueError(msg)


def _hook_then_fail(committed: list[str]) -> None:
    with request_atomic(("default",)):
        transaction.on_commit(lambda: committed.append("hook"))
        msg = "view failed"
        raise ValueError(msg)


class TestViewKind:
    def test_every_view_names_itself(self) -> None:
        assert [kind.value for kind in ViewKind] == [
            "page",
            "action",
            "zone",
            "boundary",
            "navigation",
        ]


class TestMethodTable:
    """Safe methods follow `SAFE`, every other method follows `UNSAFE`."""

    def test_safe_methods(self) -> None:
        assert frozenset({"GET", "HEAD", "OPTIONS", "TRACE"}) == SAFE_METHODS

    @pytest.mark.parametrize("method", sorted(SAFE_METHODS))
    @pytest.mark.parametrize("kind", list(ViewKind))
    def test_a_safe_method_opens_nothing_by_default(
        self, method: str, kind: ViewKind
    ) -> None:
        assert atomic_aliases(_request(method), kind) == ()

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    @pytest.mark.parametrize("kind", list(ViewKind))
    def test_an_unsafe_method_wraps_the_default_alias(
        self, method: str, kind: ViewKind
    ) -> None:
        assert atomic_aliases(_request(method), kind) == ("default",)

    def test_safe_turned_on_wraps_a_get(self) -> None:
        with _transactions(SAFE=True):
            assert atomic_aliases(_request("GET"), ViewKind.PAGE) == ("default",)

    def test_unsafe_turned_off_leaves_a_post_bare(self) -> None:
        with _transactions(UNSAFE=False):
            assert atomic_aliases(_request("POST"), ViewKind.ACTION) == ()

    @pytest.mark.parametrize(("key", "value"), [("SAFE", "yes"), ("UNSAFE", 0)])
    def test_a_non_bool_flag_keeps_its_default(self, key: str, value: object) -> None:
        with _transactions(**{key: value}):
            assert atomic_aliases(_request("GET"), ViewKind.PAGE) == ()
            assert atomic_aliases(_request("POST"), ViewKind.PAGE) == ("default",)


class TestAliases:
    """`ALIASES` names the databases to wrap, matched against Django's connections."""

    def test_all_names_every_alias_in_order(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _connections(monkeypatch)
        with _transactions(ALIASES="all"):
            assert atomic_aliases(_request("POST"), ViewKind.PAGE) == TWO_ALIASES

    def test_a_list_keeps_its_order_without_repeats(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _connections(monkeypatch)
        with _transactions(ALIASES=["replica", "default", "replica"]):
            assert atomic_aliases(_request("POST"), ViewKind.PAGE) == (
                "replica",
                "default",
            )

    def test_an_alias_without_a_connection_is_skipped(self) -> None:
        with _transactions(ALIASES=["replica", "default"]):
            assert atomic_aliases(_request("POST"), ViewKind.PAGE) == ("default",)

    @pytest.mark.parametrize("value", ["default", ["default", 1], 42])
    def test_an_unusable_value_keeps_the_default(self, value: object) -> None:
        with _transactions(ALIASES=value):
            assert atomic_aliases(_request("POST"), ViewKind.PAGE) == ("default",)

    def test_no_connection_means_no_transaction(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _connections(monkeypatch, ())
        assert atomic_aliases(_request("POST"), ViewKind.ACTION) == ()

    def test_a_project_without_databases_opens_no_transaction(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(transactions, "connections", ConnectionHandler({}))
        assert atomic_aliases(_request("POST"), ViewKind.ACTION) == ()
        with _transactions(ALIASES="all"):
            assert atomic_aliases(_request("POST"), ViewKind.PAGE) == ()

    def test_a_dummy_alias_is_skipped_beside_a_real_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        declared = {
            "default": {"ENGINE": SQLITE},
            "replica": {"ENGINE": "django.db.backends.dummy"},
        }
        monkeypatch.setattr(connections, "settings", declared)
        with _transactions(ALIASES="all"):
            assert atomic_aliases(_request("POST"), ViewKind.PAGE) == ("default",)

    def test_new_connection_settings_are_read_afresh(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        with _transactions(ALIASES="all"):
            assert atomic_aliases(_request("POST"), ViewKind.PAGE) == ("default",)
            _connections(monkeypatch)
            assert atomic_aliases(_request("POST"), ViewKind.PAGE) == TWO_ALIASES

    def test_databases_the_connections_never_read_are_ignored(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "DATABASES", {"default": {}, "replica": {}})
        with _transactions(ALIASES="all"):
            assert atomic_aliases(_request("POST"), ViewKind.PAGE) == ("default",)


class TestRule:
    """`RULE` replaces `SAFE`, `UNSAFE` and `ALIASES` when set."""

    def test_the_rule_answers_for_every_method(self) -> None:
        with _transactions(RULE=RULE_PATH, SAFE=True):
            assert atomic_aliases(_request("GET"), ViewKind.ACTION) == ("default",)
            assert atomic_aliases(_request("POST"), ViewKind.PAGE) == ()

    def test_one_name_is_one_alias(self) -> None:
        with _transactions(RULE="tests.test_transactions.rule_one_name"):
            assert atomic_aliases(_request("POST"), ViewKind.PAGE) == ("default",)

    def test_an_answer_is_matched_against_the_connections(self) -> None:
        with _transactions(RULE="tests.test_transactions.rule_with_unknown"):
            assert atomic_aliases(_request("POST"), ViewKind.PAGE) == ("default",)

    def test_the_rule_is_imported_once_per_settings(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        imported: list[str] = []

        def counting(dotted: str) -> object:
            imported.append(dotted)
            return import_string(dotted)

        monkeypatch.setattr(transactions, "import_string", counting)
        with _transactions(RULE=RULE_PATH):
            atomic_aliases(_request("POST"), ViewKind.ACTION)
            atomic_aliases(_request("GET"), ViewKind.PAGE)
        assert imported == [RULE_PATH]

    def test_a_settings_reload_drops_the_rule(self) -> None:
        with _transactions(RULE=RULE_PATH):
            assert atomic_aliases(_request("POST"), ViewKind.PAGE) == ()
        assert atomic_aliases(_request("POST"), ViewKind.PAGE) == ("default",)

    def test_a_path_that_does_not_import_raises(self) -> None:
        with (
            _transactions(RULE="tests.missing.rule"),
            pytest.raises(ImproperlyConfigured, match="could not be imported"),
        ):
            atomic_aliases(_request("POST"), ViewKind.PAGE)

    def test_a_path_to_no_callable_raises(self) -> None:
        with (
            _transactions(RULE="tests.test_transactions.not_callable"),
            pytest.raises(ImproperlyConfigured, match="names no callable"),
        ):
            atomic_aliases(_request("POST"), ViewKind.PAGE)

    def test_a_rule_that_is_no_path_keeps_the_method_table(self) -> None:
        with _transactions(RULE=42):
            assert atomic_aliases(_request("POST"), ViewKind.PAGE) == ("default",)


class TestSafeAliases:
    """What every safe request of a view opens is known before any request."""

    def test_the_default_table_opens_nothing(self) -> None:
        assert safe_aliases() == ()

    def test_safe_turned_on_names_the_default_alias(self) -> None:
        with _transactions(SAFE=True):
            assert safe_aliases() == ("default",)

    def test_a_rule_answers_per_request_so_nothing_is_held(self) -> None:
        with _transactions(RULE=RULE_PATH):
            assert safe_aliases() is None

    def test_an_override_is_known_ahead_even_under_a_rule(self) -> None:
        with _transactions(RULE=RULE_PATH):
            assert safe_aliases(AtomicOverride(atomic=True)) == ("default",)
            assert safe_aliases(AtomicOverride(atomic=False)) == ()


class TestOverrideShape:
    @pytest.mark.parametrize(
        ("atomic", "aliases", "expected"),
        [
            (True, None, AtomicOverride(atomic=True)),
            (False, "all", AtomicOverride(atomic=False, aliases="all")),
            (True, ["a", "b"], AtomicOverride(atomic=True, aliases=("a", "b"))),
            (True, ("a",), AtomicOverride(atomic=True, aliases=("a",))),
        ],
    )
    def test_a_usable_pair_makes_an_override(
        self, atomic: object, aliases: object, expected: AtomicOverride
    ) -> None:
        assert override_problem(atomic, aliases) is None
        assert override_of(atomic, aliases) == expected

    @pytest.mark.parametrize(
        ("atomic", "aliases", "fragment"),
        [
            ("yes", None, "atomic must be True or False, got 'str'"),
            (1, None, "got 'int'"),
            (True, "default", "aliases must be None, 'all'"),
            (True, ["default", 1], "aliases must be None, 'all'"),
            (True, 42, "got 42"),
        ],
    )
    def test_an_unusable_pair_makes_none(
        self, atomic: object, aliases: object, fragment: str
    ) -> None:
        problem = override_problem(atomic, aliases)
        assert problem is not None
        assert fragment in problem
        assert override_of(atomic, aliases) is None


class TestOverride:
    """A per-view `AtomicOverride` wins over the method table and the rule."""

    def test_off_leaves_an_unsafe_method_bare(self) -> None:
        override = AtomicOverride(atomic=False)
        assert atomic_aliases(_request("POST"), ViewKind.ACTION, override) == ()

    def test_on_wraps_a_safe_method_in_the_configured_aliases(self) -> None:
        override = AtomicOverride(atomic=True)
        assert atomic_aliases(_request("GET"), ViewKind.PAGE, override) == ("default",)

    def test_own_aliases_are_matched_against_the_connections(self) -> None:
        override = AtomicOverride(atomic=True, aliases=("replica", "default"))
        assert atomic_aliases(_request("GET"), ViewKind.PAGE, override) == ("default",)

    def test_all_names_every_alias(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _connections(monkeypatch)
        override = AtomicOverride(atomic=True, aliases="all")
        assert atomic_aliases(_request("GET"), ViewKind.PAGE, override) == TWO_ALIASES

    def test_the_override_wins_over_the_rule(self) -> None:
        override = AtomicOverride(atomic=True)
        with _transactions(RULE=RULE_PATH):
            assert atomic_aliases(_request("POST"), ViewKind.PAGE, override) == (
                "default",
            )


def _recorder(events: list[str], refused: str | None = None) -> Callable[..., object]:
    @contextmanager
    def atomic(using: str | None = None) -> Iterator[None]:
        if using == refused:
            msg = f"cannot open {using}"
            raise RuntimeError(msg)
        events.append(f"enter {using}")
        try:
            yield
        finally:
            events.append(f"exit {using}")

    return atomic


class TestRequestAtomicOrder:
    """The blocks nest as `ATOMIC_REQUESTS` nests them, the last alias outermost."""

    def test_the_last_alias_is_outermost(self, monkeypatch: pytest.MonkeyPatch) -> None:
        events: list[str] = []
        monkeypatch.setattr(transaction, "atomic", _recorder(events))
        with request_atomic(TWO_ALIASES):
            events.append("view")
        assert events == [
            "enter replica",
            "enter default",
            "view",
            "exit default",
            "exit replica",
        ]

    def test_the_order_matches_atomic_requests(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _connections(monkeypatch, atomic_requests=True)
        wrapped: list[str] = []
        monkeypatch.setattr(transaction, "atomic", _recorder(wrapped))

        def view(request: HttpRequest) -> HttpResponse:
            wrapped.append("view")
            return HttpResponse()

        BaseHandler().make_view_atomic(view)(_request("POST"))
        opened: list[str] = []
        monkeypatch.setattr(transaction, "atomic", _recorder(opened))
        with request_atomic(tuple(connections)):
            opened.append("view")
        assert opened == wrapped
        assert opened[0] == "enter replica"

    def test_a_block_that_fails_to_open_closes_the_ones_before_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        events: list[str] = []
        monkeypatch.setattr(transaction, "atomic", _recorder(events, "default"))
        with (
            pytest.raises(RuntimeError, match="cannot open default"),
            request_atomic(TWO_ALIASES),
        ):
            events.append("view")
        assert events == ["enter replica", "exit replica"]

    def test_no_alias_touches_no_connection(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        opened: list[object] = []
        touched: list[object] = []
        monkeypatch.setattr(
            transaction, "atomic", lambda *args, **kwargs: opened.append(kwargs)
        )

        def touch(handler: ConnectionHandler, alias: str) -> None:
            touched.append(alias)

        monkeypatch.setattr(ConnectionHandler, "__getitem__", touch)
        with request_atomic(()):
            pass
        assert opened == []
        assert touched == []
        assert request_atomic(()) is request_atomic(())


@pytest.mark.django_db(transaction=True)
class TestRequestAtomicDatabase:
    """The blocks commit on a normal exit and roll back on an exception."""

    def test_a_normal_exit_commits(self) -> None:
        with request_atomic(("default",)):
            Group.objects.create(name="committed")
        assert Group.objects.filter(name="committed").exists()

    def test_an_exception_rolls_back(self) -> None:
        with pytest.raises(ValueError, match="view failed"):
            _create_then_fail("rolled back")
        assert not Group.objects.filter(name="rolled back").exists()

    def test_the_block_opens_before_the_first_query(self) -> None:
        committed: list[str] = []
        with request_atomic(("default",)):
            assert connection.in_atomic_block
            transaction.on_commit(lambda: committed.append("hook"))
            assert committed == []
            Group.objects.create(name="eager")
            assert committed == []
        assert committed == ["hook"]

    def test_a_rollback_drops_the_on_commit_hook(self) -> None:
        committed: list[str] = []
        with pytest.raises(ValueError, match="view failed"):
            _hook_then_fail(committed)
        assert committed == []


class TestSkipAtomicRequests:
    """A marked view stays outside Django's `ATOMIC_REQUESTS` wrapping on every alias."""

    def test_an_unmarked_view_is_wrapped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _connections(monkeypatch, atomic_requests=True)

        def view(request: HttpRequest) -> HttpResponse:
            return HttpResponse()

        assert BaseHandler().make_view_atomic(view) is not view

    def test_a_marked_view_is_left_alone_on_both_aliases(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _connections(monkeypatch, atomic_requests=True)

        def view(request: HttpRequest) -> HttpResponse:
            return HttpResponse()

        marked = skip_atomic_requests(view)
        assert marked is view
        assert BaseHandler().make_view_atomic(marked) is view

    def test_an_alias_connected_after_the_mark_is_skipped_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def view(request: HttpRequest) -> HttpResponse:
            return HttpResponse()

        skip_atomic_requests(view)
        _connections(monkeypatch, atomic_requests=True)
        assert BaseHandler().make_view_atomic(view) is view

    def test_a_wraps_wrapper_is_marked_and_its_inner_view_kept(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _connections(monkeypatch, atomic_requests=True)

        def view(request: HttpRequest) -> HttpResponse:
            return HttpResponse()

        inner = transaction.non_atomic_requests(using="default")(view)

        @functools.wraps(inner)
        def outer(request: HttpRequest) -> HttpResponse:
            return inner(request)

        skip_atomic_requests(outer)
        assert BaseHandler().make_view_atomic(outer) is outer
        assert inner._non_atomic_requests == {"default"}
        assert "replica" not in inner._non_atomic_requests

    def test_django_marking_afterwards_keeps_every_alias(self) -> None:
        def view(request: HttpRequest) -> HttpResponse:
            return HttpResponse()

        skip_atomic_requests(view)
        transaction.non_atomic_requests(using="replica")(view)
        assert "replica" in set(view._non_atomic_requests)
        assert "any alias" in view._non_atomic_requests


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


def _create_and_raise(name: str) -> None:
    with lazy_savepoint(("default",)):
        Group.objects.create(name=name)
        msg = "subtree failed"
        raise ValueError(msg)


def _write_then_durable() -> None:
    with lazy_savepoint():
        Group.objects.create(name="first")
        with transaction.atomic(durable=True):
            Group.objects.create(name="durable")


def _read_write_then_fail() -> None:
    with lazy_savepoint():
        with lazy_savepoint(atomic=True):
            list(Group.objects.all())
            Group.objects.create(name="inner")
        msg = "outer failed"
        raise ValueError(msg)


def _create_then_refuse(monkeypatch: pytest.MonkeyPatch, method: str) -> None:
    with lazy_savepoint(("default",)):
        Group.objects.create(name="doomed")
        monkeypatch.setattr(type(connections["default"]), method, _refuse)


def _create_with_savepoints_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    with transaction.atomic(), lazy_savepoint(probe_atomic_aliases()):
        monkeypatch.setattr(connections["default"], "savepoint", _refuse)
        Group.objects.create(name="never")


def _write_then_catch() -> None:
    with lazy_savepoint():
        Group.objects.create(name="first")
        with pytest.raises(IntegrityError):
            Group.objects.create(name="taken")
        Group.objects.count()


def _abort_after_a_write() -> None:
    with lazy_savepoint():
        Group.objects.create(name="aborted")
        raise _Abort


class TestStatementKind:
    """The leading keyword classifies a statement, an unknown one counts as a write."""

    @pytest.mark.parametrize(
        ("sql", "kind"),
        [
            ("BEGIN", StatementKind.CONTROL),
            ("BEGIN;", StatementKind.CONTROL),
            ("BEGIN IMMEDIATE", StatementKind.CONTROL),
            ("BEGIN TRANSACTION ISOLATION LEVEL SERIALIZABLE", StatementKind.CONTROL),
            ("START TRANSACTION", StatementKind.CONTROL),
            ("COMMIT", StatementKind.CONTROL),
            ("COMMIT PREPARED 'x'", StatementKind.CONTROL),
            ("END", StatementKind.CONTROL),
            ("ABORT", StatementKind.CONTROL),
            ("ROLLBACK", StatementKind.CONTROL),
            ('ROLLBACK TO SAVEPOINT "s1_x1"', StatementKind.CONTROL),
            ('RELEASE SAVEPOINT "s1_x1"', StatementKind.CONTROL),
            ('SAVEPOINT "s1_x1"', StatementKind.SESSION),
            ("SET CONSTRAINTS ALL IMMEDIATE", StatementKind.SESSION),
            ("RESET ALL", StatementKind.SESSION),
            ("PRAGMA foreign_keys = ON", StatementKind.SESSION),
            ("DISCARD TEMP", StatementKind.SESSION),
            ('SELECT "auth_group"."name" FROM "auth_group"', StatementKind.READ),
            ("  (SELECT 1) UNION (SELECT 2)", StatementKind.READ),
            ("/* app:view */ SELECT 1", StatementKind.READ),
            ("-- a note\nSELECT 1", StatementKind.READ),
            ("select updated_at from t", StatementKind.READ),
            ("select update from t", StatementKind.READ),
            ('SELECT "blog_post"."share" FROM "blog_post"', StatementKind.READ),
            ("SELECT `update` FROM t", StatementKind.READ),
            ("SELECT * FROM t WHERE note = 'please UPDATE me'", StatementKind.READ),
            ("SELECT * FROM t WHERE note = 'it''s FOR UPDATE'", StatementKind.READ),
            ("SELECT $$ FOR UPDATE $$", StatementKind.READ),
            ("SELECT 1 /* FOR UPDATE */", StatementKind.READ),
            ("SHOW search_path", StatementKind.READ),
            ("EXPLAIN SELECT 1", StatementKind.READ),
            ("WITH x AS (SELECT 1) SELECT * FROM x", StatementKind.READ),
            ("VALUES (1), (2)", StatementKind.READ),
            ("TABLE t", StatementKind.READ),
            ("DESCRIBE t", StatementKind.READ),
            ("DESC t", StatementKind.READ),
            (b"SELECT 1", StatementKind.READ),
            ('INSERT INTO "auth_group" ("name") VALUES (%s)', StatementKind.WRITE),
            ('UPDATE "auth_group" SET "name" = %s', StatementKind.WRITE),
            ('DELETE FROM "auth_group"', StatementKind.WRITE),
            ("CREATE TABLE t (id int)", StatementKind.WRITE),
            ("CALL refresh()", StatementKind.WRITE),
            ("SELECT * FROM t FOR UPDATE", StatementKind.WRITE),
            ("SELECT * FROM t FOR NO KEY UPDATE SKIP LOCKED", StatementKind.WRITE),
            ("SELECT * FROM t FOR SHARE", StatementKind.WRITE),
            ("SELECT * FROM t FOR KEY SHARE", StatementKind.WRITE),
            ("SELECT * FROM t LOCK IN SHARE MODE", StatementKind.WRITE),
            ("SELECT * INTO copy FROM t", StatementKind.WRITE),
            ("WITH gone AS (DELETE FROM t RETURNING *) SELECT 1", StatementKind.WRITE),
            (
                "WITH u AS (UPDATE t SET a = 1 RETURNING *) SELECT 1",
                StatementKind.WRITE,
            ),
            ("EXPLAIN ANALYZE UPDATE t SET a = 1", StatementKind.WRITE),
            ("SELECT 'never closed FOR UPDATE", StatementKind.WRITE),
            ("BEGIN INSERT INTO t VALUES (1); END;", StatementKind.WRITE),
            ("DECLARE x NUMBER; BEGIN NULL; END;", StatementKind.WRITE),
            ("BEGIN 'never closed", StatementKind.WRITE),
            ("", StatementKind.WRITE),
            ("/* never closed SELECT 1", StatementKind.WRITE),
            ("-- SELECT 1", StatementKind.WRITE),
            (b"\xff", StatementKind.WRITE),
            (object(), StatementKind.WRITE),
            ("VACUUM", StatementKind.STANDALONE),
            ("VACUUM INTO 'copy.db'", StatementKind.STANDALONE),
            ("ATTACH DATABASE 'x.db' AS x", StatementKind.STANDALONE),
            ("DETACH DATABASE x", StatementKind.STANDALONE),
            ("CREATE DATABASE app", StatementKind.STANDALONE),
            ("DROP DATABASE app", StatementKind.STANDALONE),
            ("CREATE TABLESPACE fast LOCATION '/ssd'", StatementKind.STANDALONE),
            ("CREATE INDEX CONCURRENTLY i ON t (a)", StatementKind.STANDALONE),
            ("CREATE UNIQUE INDEX CONCURRENTLY i ON t (a)", StatementKind.STANDALONE),
            ("DROP INDEX CONCURRENTLY i", StatementKind.STANDALONE),
            ("REINDEX TABLE CONCURRENTLY t", StatementKind.STANDALONE),
            ("REINDEX DATABASE app", StatementKind.STANDALONE),
            ("REINDEX (VERBOSE) SYSTEM app", StatementKind.STANDALONE),
            ("REINDEX TABLE t", StatementKind.WRITE),
            ("ALTER SYSTEM SET work_mem = '64MB'", StatementKind.STANDALONE),
            ("ALTER TABLE t ADD COLUMN a int", StatementKind.WRITE),
            ("CLUSTER", StatementKind.STANDALONE),
            ("CLUSTER VERBOSE", StatementKind.STANDALONE),
            ("CLUSTER t USING i", StatementKind.WRITE),
            ("DISCARD ALL", StatementKind.STANDALONE),
        ],
    )
    def test_kind(self, sql: object, kind: StatementKind) -> None:
        assert statement_kind(sql) is kind


class TestProbeAtomicAliases:
    """The probe names the aliases inside an atomic block and opens no connection."""

    @pytest.mark.django_db(transaction=True)
    def test_outside_a_block_names_none(self) -> None:
        assert probe_atomic_aliases() == ()

    @pytest.mark.django_db(transaction=True)
    def test_inside_a_block_names_the_alias(self) -> None:
        with transaction.atomic():
            assert probe_atomic_aliases() == ("default",)

    def test_a_thread_without_connections_gets_none_created(self) -> None:
        seen: list[object] = []

        def probe() -> None:
            seen.append(probe_atomic_aliases())
            seen.append(connections.all(initialized_only=True))

        worker = threading.Thread(target=probe)
        worker.start()
        worker.join()
        assert seen == [(), []]


@pytest.mark.django_db(transaction=True)
class TestLazySavepointInsideATransaction:
    """Inside a transaction the savepoint waits for the subtree's first statement."""

    def test_a_query_free_subtree_issues_nothing(self) -> None:
        with (
            transaction.atomic(),
            _log() as log,
            lazy_savepoint(probe_atomic_aliases()),
        ):
            pass
        assert log == []
        assert connection.execute_wrappers == []

    def test_one_query_is_wrapped_in_one_savepoint(self) -> None:
        with (
            transaction.atomic(),
            _log() as log,
            lazy_savepoint(probe_atomic_aliases()),
        ):
            list(Group.objects.all())
        assert log == ["SAVEPOINT sp", "SELECT", "RELEASE SAVEPOINT sp"]

    def test_a_failure_rolls_back_only_the_subtree(self) -> None:
        with transaction.atomic():
            Group.objects.create(name="before")
            with _log() as log, lazy_savepoint(probe_atomic_aliases()) as point:
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
            with lazy_savepoint(probe_atomic_aliases()) as point:
                Group.objects.create(name="failed")
                transaction.on_commit(lambda: committed.append("hook"))
                point.rollback()
            assert committed == []
        assert committed == ["hook"]
        assert _names() == []

    def test_a_database_error_leaves_the_outer_block_usable(self) -> None:
        Group.objects.create(name="taken")
        with transaction.atomic():
            with lazy_savepoint(probe_atomic_aliases()) as point:
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
            with lazy_savepoint(("default",)):
                Group.objects.create(name="doomed")
                transaction.set_rollback(True)
            Group.objects.create(name="after")
        assert _names() == ["after"]

    def test_a_broken_transaction_takes_no_savepoint(self) -> None:
        with transaction.atomic():
            transaction.set_rollback(True)
            with _log() as log, lazy_savepoint(("default",)) as point:
                with pytest.raises(TransactionManagementError):
                    list(Group.objects.all())
                point.rollback()
            assert log == ["SELECT"]
            assert transaction.get_rollback()

    def test_a_savepoint_whose_transaction_ended_is_left_alone(self) -> None:
        point = lazy_savepoint(("default",))
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
            with lazy_savepoint(("default",)) as point:
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
            with lazy_savepoint(aliases) as outer:
                Group.objects.create(name="outer")
                with lazy_savepoint(aliases) as inner:
                    Group.objects.create(name="inner")
                    inner.rollback()
                with lazy_savepoint(aliases):
                    Group.objects.create(name="sibling")
                assert _names() == ["outer", "sibling"]
                outer.rollback()
            Group.objects.create(name="after")
        assert _names() == ["after"]

    def test_an_inner_first_query_takes_the_outer_savepoint_first(self) -> None:
        with transaction.atomic():
            aliases = probe_atomic_aliases()
            with _log() as log, lazy_savepoint(aliases):
                with lazy_savepoint(aliases) as inner:
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
            lazy_savepoint(probe_atomic_aliases()) as point,
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
            with lazy_savepoint(probe_atomic_aliases()) as point:
                with transaction.atomic(savepoint=False):
                    Group.objects.create(name="inside")
                Group.objects.create(name="after")
                point.rollback()
            Group.objects.create(name="kept")
        assert _names() == ["kept"]

    def test_false_still_protects_a_transaction(self) -> None:
        with (
            transaction.atomic(),
            lazy_savepoint(probe_atomic_aliases(), atomic=False) as point,
        ):
            Group.objects.create(name="failed")
            point.rollback()
        assert _names() == []

    def test_the_handle_belongs_to_its_thread(self) -> None:
        refused: list[BaseException] = []
        with transaction.atomic(), lazy_savepoint(("default",)) as point:
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


def _missing_table() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT * FROM next_missing_table")


@pytest.mark.django_db(transaction=True)
class TestNestedReadsShareTheOuterSavepoint:
    """Inside a transaction an inner subtree takes a savepoint before its first write."""

    def test_inner_reads_issue_one_savepoint_pair(self) -> None:
        with transaction.atomic():
            aliases = probe_atomic_aliases()
            with _log() as log, lazy_savepoint(aliases):
                list(Group.objects.all())
                for _ in range(3):
                    with lazy_savepoint(aliases):
                        list(Group.objects.all())
        assert log == ["SAVEPOINT sp", *["SELECT"] * 4, "RELEASE SAVEPOINT sp"]

    def test_an_inner_first_read_takes_only_the_outer_savepoint(self) -> None:
        with transaction.atomic():
            aliases = probe_atomic_aliases()
            with (
                _log() as log,
                lazy_savepoint(aliases),
                lazy_savepoint(aliases) as inner,
            ):
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
            with lazy_savepoint(aliases) as outer:
                Group.objects.create(name="outer")
                with lazy_savepoint(aliases) as inner:
                    with pytest.raises(DatabaseError):
                        _missing_table()
                    assert not inner.rollback()
                assert outer.rollback()
            Group.objects.create(name="after")
        assert _names() == ["after"]

    def test_an_inner_doom_without_a_savepoint_is_left_to_the_outer_one(self) -> None:
        with transaction.atomic():
            aliases = probe_atomic_aliases()
            with lazy_savepoint(aliases) as outer:
                list(Group.objects.all())
                inner = lazy_savepoint(aliases)
                with inner:
                    transaction.set_rollback(True)
                assert not inner.rollback()
                assert outer.rollback()
            assert not transaction.get_rollback()

    def test_an_inner_atomic_block_makes_the_failed_read_contained_there(self) -> None:
        with transaction.atomic():
            aliases = probe_atomic_aliases()
            with lazy_savepoint(aliases):
                Group.objects.create(name="outer")
                with lazy_savepoint(aliases) as inner:
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
            with lazy_savepoint(aliases) as outer:
                Group.objects.create(name="outer")
                with lazy_savepoint(aliases) as inner:
                    Group.objects.create(name="inner")
                    with pytest.raises(DatabaseError):
                        _missing_table()
                    assert inner.rollback()
                assert _names() == ["outer"]
                assert outer.rollback() is True
        assert _names() == []

    def test_a_failure_outside_a_transaction_breaks_nothing(self) -> None:
        with lazy_savepoint() as point:
            with pytest.raises(DatabaseError):
                _missing_table()
            assert point.rollback()

    def test_an_inner_failed_read_after_an_autocommit_write_goes_outward(self) -> None:
        with lazy_savepoint() as outer:
            Group.objects.create(name="outer")
            with lazy_savepoint() as inner:
                with pytest.raises(DatabaseError):
                    _missing_table()
                assert not inner.rollback()
            assert outer.rollback()
        assert _names() == []
        assert connection.get_autocommit()

    def test_an_outermost_failed_read_rolls_back_to_its_own_savepoint(self) -> None:
        with transaction.atomic():
            with lazy_savepoint(probe_atomic_aliases()) as point:
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
            with _log() as log, lazy_savepoint(aliases):
                list(Group.objects.all())
                with lazy_savepoint(aliases):
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

    def test_a_failed_inner_subtree_undoes_its_get_or_create_only(self) -> None:
        with transaction.atomic():
            aliases = probe_atomic_aliases()
            with lazy_savepoint(aliases):
                Group.objects.create(name="outer")
                with lazy_savepoint(aliases) as inner:
                    Group.objects.get_or_create(name="inner")
                    assert inner.rollback()
        assert _names() == ["outer"]

    def test_a_caught_user_rollback_leaves_the_subtree_savepoint(self) -> None:
        with transaction.atomic():
            aliases = probe_atomic_aliases()
            with lazy_savepoint(aliases):
                Group.objects.create(name="outer")
                with lazy_savepoint(aliases) as inner:
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
            with lazy_savepoint(aliases):
                Group.objects.create(name="outer")
                with lazy_savepoint(aliases) as inner:
                    with pytest.raises(IntegrityError), transaction.atomic():
                        Group.objects.create(name="taken")
                    Group.objects.create(name="inner")
                    assert inner.rollback()
        assert _names() == ["outer", "taken"]

    def test_an_outer_failure_undoes_the_inner_get_or_create(self) -> None:
        with transaction.atomic():
            aliases = probe_atomic_aliases()
            with lazy_savepoint(aliases) as outer:
                list(Group.objects.all())
                with lazy_savepoint(aliases):
                    Group.objects.get_or_create(name="inner")
                assert outer.rollback()
            Group.objects.create(name="after")
        assert _names() == ["after"]

    def test_a_user_block_in_autocommit_nests_after_the_inner_savepoint(self) -> None:
        with lazy_savepoint() as outer:
            Group.objects.create(name="outer")
            with lazy_savepoint(probe_atomic_aliases()) as inner:
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
        with _log() as log, lazy_savepoint():
            pass
        assert log == []

    def test_reads_open_nothing_by_default(self) -> None:
        with _log() as log, lazy_savepoint():
            list(Group.objects.all())
        assert log == ["SELECT"]

    def test_a_failure_between_two_writes_leaves_nothing(self) -> None:
        with _log() as log, lazy_savepoint(atomic=WRITES) as point:
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
        with lazy_savepoint():
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
        with _log() as log, lazy_savepoint(atomic=True) as point:
            list(Group.objects.all())
            Group.objects.create(name="failed")
            point.rollback()
        assert log[:2] == ["BEGIN", "SELECT"]
        assert _names() == []

    def test_false_leaves_autocommit_alone(self) -> None:
        with _log() as log, lazy_savepoint(atomic=False) as point:
            Group.objects.create(name="one")
            Group.objects.create(name="two")
            point.rollback()
        assert log == ["INSERT INTO auth_group", "INSERT INTO auth_group"]
        assert _names() == ["one", "two"]
        assert connection.execute_wrappers == []

    def test_a_statement_that_refuses_a_transaction_opens_none(self) -> None:
        with _log() as log, lazy_savepoint(atomic=True), connection.cursor() as cursor:
            cursor.execute("VACUUM")
        assert log == ["VACUUM"]

    def test_a_caught_error_on_the_first_write_leaves_autocommit_usable(self) -> None:
        Group.objects.create(name="taken")
        with _log() as log, lazy_savepoint() as point:
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
        with _log() as log, lazy_savepoint() as point:
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
        with lazy_savepoint() as point:
            Group.objects.create(name="first")
            with transaction.atomic():
                Group.objects.create(name="user")
            point.rollback()
        assert _names() == []

    def test_a_block_django_opens_first_keeps_its_commit(self) -> None:
        with lazy_savepoint() as point:
            Group.objects.get_or_create(name="created")
            point.rollback()
        assert _names() == ["created"]

    def test_true_takes_get_or_create_into_the_transaction(self) -> None:
        with _log() as log, lazy_savepoint(atomic=True) as point:
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
        with lazy_savepoint(atomic=True) as point:
            Group.objects.update_or_create(name="created")
            point.rollback()
        assert _names() == ["created"]

    def test_a_failed_user_block_keeps_autocommit_usable(self) -> None:
        Group.objects.create(name="taken")
        with lazy_savepoint():
            with pytest.raises(IntegrityError), transaction.atomic():
                Group.objects.create(name="taken")
            Group.objects.create(name="after")
        assert _names() == ["after", "taken"]

    def test_a_durable_block_first_keeps_its_commit(self) -> None:
        with lazy_savepoint() as point:
            with transaction.atomic(durable=True):
                Group.objects.create(name="durable")
            Group.objects.create(name="failed")
            point.rollback()
        assert _names() == ["durable"]

    def test_a_durable_block_after_a_write_cannot_nest(self) -> None:
        with pytest.raises(RuntimeError, match="durable atomic block cannot be nested"):
            _write_then_durable()
        assert _names() == []

    def test_a_manual_transaction_is_left_to_its_owner(self) -> None:
        transaction.set_autocommit(False)
        try:
            with lazy_savepoint() as point:
                with transaction.atomic():
                    Group.objects.create(name="manual")
                point.rollback()
            transaction.commit()
        finally:
            transaction.set_autocommit(True)
        assert _names() == ["manual"]

    def test_nested_subtrees_share_the_transaction(self) -> None:
        with _log() as log, lazy_savepoint():
            Group.objects.create(name="outer")
            with lazy_savepoint() as inner:
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

    def test_an_outer_failure_undoes_a_finished_inner_subtree(self) -> None:
        with lazy_savepoint() as outer:
            with lazy_savepoint():
                Group.objects.create(name="inner")
            Group.objects.create(name="outer")
            outer.rollback()
        assert _names() == []

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
        with _log() as log, lazy_savepoint(atomic=True):
            with lazy_savepoint() as inner:
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
        with lazy_savepoint(), lazy_savepoint():
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
        with lazy_savepoint(), transaction.atomic():
            with lazy_savepoint() as inner:
                Group.objects.create(name="inner")
                inner.rollback()
            Group.objects.create(name="user")
        assert _names() == ["user"]


@pytest.mark.django_db(transaction=True)
class TestLazySavepointConnections:
    """The handles of one thread share one wrapper per connection and one receiver."""

    def test_a_connection_opened_inside_the_subtree_is_covered(self) -> None:
        with lazy_savepoint() as point, _fresh_default():
            fresh = connections["default"]
            Group.objects.create(name="one")
            Group.objects.create(name="two")
            assert fresh.in_atomic_block
            point.rollback()
            assert fresh.execute_wrappers == []
        assert _names() == []
        assert connection.execute_wrappers == []

    def test_a_copy_of_a_connection_is_not_covered(self) -> None:
        with _fresh_default(), lazy_savepoint():
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

        with lazy_savepoint():
            worker = threading.Thread(target=work)
            worker.start()
            worker.join()
        assert seen == [[]]

    def test_a_connection_opened_after_the_subtree_is_left_bare(self) -> None:
        with lazy_savepoint():
            pass
        with _fresh_default():
            fresh = connections["default"]
            fresh.ensure_connection()
            assert fresh.execute_wrappers == []

    def test_a_connection_known_before_it_connects_keeps_one_wrapper(self) -> None:
        with _fresh_default():
            fresh = connections["default"]
            with lazy_savepoint():
                fresh.ensure_connection()
                assert len(fresh.execute_wrappers) == 1

    def test_a_handle_that_watches_no_connection_ignores_new_ones(self) -> None:
        with lazy_savepoint(atomic=False), _fresh_default():
            fresh = connections["default"]
            fresh.ensure_connection()
            assert fresh.execute_wrappers == []

    def test_nested_handles_share_one_wrapper(self) -> None:
        with lazy_savepoint():
            with lazy_savepoint(), lazy_savepoint(atomic=True):
                assert len(connection.execute_wrappers) == 1
            assert len(connection.execute_wrappers) == 1
        assert connection.execute_wrappers == []

    def test_no_handle_connects_a_receiver(self) -> None:
        receivers = list(connection_created.receivers)
        with lazy_savepoint(), lazy_savepoint():
            assert connection_created.receivers == receivers
        assert connection_created.receivers == receivers

    def test_a_failed_enter_undoes_what_it_installed(self) -> None:
        point = lazy_savepoint(("default", "missing"))
        with pytest.raises(ConnectionDoesNotExist):
            point.__enter__()
        assert connection.execute_wrappers == []
        with lazy_savepoint():
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
            with lazy_savepoint():
                assert len(connection.execute_wrappers) == 1
            assert len(connection.execute_wrappers) == 1
            with lazy_savepoint() as point:
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
            with keep_savepoints(), lazy_savepoint():
                pass
            assert len(connection.execute_wrappers) == 1
        assert connection.execute_wrappers == []

    def test_a_handle_left_open_closes_the_session_itself(self) -> None:
        point = lazy_savepoint()
        with keep_savepoints():
            point.__enter__()
        assert len(connection.execute_wrappers) == 1
        point.__exit__(None, None, None)
        assert connection.execute_wrappers == []

    def test_a_connection_opened_between_handles_is_guarded(self) -> None:
        with keep_savepoints():
            with lazy_savepoint():
                pass
            with _fresh_default():
                fresh = connections["default"]
                fresh.ensure_connection()
                assert len(fresh.execute_wrappers) == 1
                with lazy_savepoint() as point:
                    Group.objects.create(name="undone")
                    point.rollback()
                assert _names() == []
        assert connection.execute_wrappers == []
