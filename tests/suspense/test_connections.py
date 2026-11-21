import re
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.middleware import AuthenticationMiddleware
from django.contrib.auth.models import Group
from django.contrib.messages.middleware import MessageMiddleware
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.middleware import SessionMiddleware
from django.core.exceptions import ImproperlyConfigured
from django.core.paginator import Paginator
from django.db import DatabaseError, connection, connections, transaction
from django.db.backends.base.base import BaseDatabaseWrapper
from django.db.backends.signals import connection_created
from django.db.transaction import TransactionManagementError
from django.http import HttpRequest, HttpResponse
from django.middleware.csrf import (
    CsrfViewMiddleware,
    _unmask_cipher_token,
    get_token,
    rotate_token,
)
from django.template import Context, Template
from django.test import RequestFactory, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils.functional import empty

from next.suspense import LaneExecutor
from next.suspense.connections import (
    DEADLINE_SQL,
    ConnectionState,
    Outlived,
    Seams,
    Settled,
    States,
    ThreadState,
    capture_states,
    check_connection_sources,
    check_pool_budget,
    check_pool_timeout,
    check_thread_sources,
    connection_sources,
    deadline_statement,
    detection,
    effects,
    facts,
    forcing_pool,
    foreground_facts,
    forget_detection,
    fresh_querysets,
    guard,
    job_guard,
    pool_forced,
    request_state,
    seams,
    settle,
    settle_request,
    thread_sources,
    timeout_ms,
)
from next.suspense.connections.checks import Pool, pools
from next.suspense.markers import Captured, Effects, ForegroundFacts, Server
from next.tasks import StealableTask
from next.testing import override_next_settings


TENANT = "SELECT 'tenant acme'"
_SAVEPOINT_NAME = re.compile(r'"s\d+_x\d+"')

MIDDLEWARE_ALL = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]


class Tenant(ConnectionState):
    covers = frozenset({"routers"})

    def capture(self, request, alias):
        return getattr(request, "tenant", None)

    def apply(self, state, connection):
        with connection.cursor() as cursor:
            cursor.execute("SELECT %s", [f"tenant {state}"])


class Role(ConnectionState):
    covers = frozenset({f"routers:{__name__}.StatefulRouter"})

    def capture(self, request, alias):
        return None

    def apply(self, state, connection):
        msg = "a state that was never captured is never applied"
        raise AssertionError(msg)


class Refusing(ConnectionState):
    def capture(self, request, alias):
        return "refused"

    def apply(self, state, connection):
        msg = "apply failed"
        raise DatabaseError(msg)


class Crum(ThreadState):
    covers = frozenset({"django-crum"})

    def capture(self, request):
        return getattr(request, "crum", None)

    def apply(self, state):
        return state

    def reset(self, token):
        return None


class Recorder(ThreadState):
    def __init__(self, name: str, events: list[str]) -> None:
        """Record under `name` into the shared `events`."""
        self.name = name
        self.events = events

    def capture(self, request):
        return self.name

    def apply(self, state):
        self.events.append(f"apply {state}")
        return f"token {state}"

    def reset(self, token):
        self.events.append(f"reset {token}")


class Broken(Recorder):
    def apply(self, state):
        msg = "thread state failed"
        raise RuntimeError(msg)


class Unresettable(Recorder):
    def reset(self, token):
        msg = "thread state stuck"
        raise RuntimeError(msg)


class StatelessRouter:
    next_stateless = True


class StatefulRouter:
    pass


class ProjectSessions(SessionMiddleware):
    pass


def not_a_class(get_response):
    return get_response


def _defer(**scope: object) -> override_settings:
    return override_settings(NEXT_FRAMEWORK={"DEFER": scope})


def _executor(**options: object) -> override_settings:
    return _defer(EXECUTOR={"OPTIONS": options})


@pytest.fixture(autouse=True)
def _forget() -> Iterator[None]:
    forget_detection()
    yield
    forget_detection()


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
                _statement(query["sql"]) for query in queries.captured_queries
            )


def _query(sql: str = "SELECT 1") -> None:
    with connection.cursor() as cursor:
        cursor.execute(sql)


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
        _query("SELECT 1")
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


def _deployment(
    monkeypatch: pytest.MonkeyPatch,
    *,
    apps: tuple[object, ...] = (),
    middleware: tuple[object, ...] = (),
    routers: tuple[object, ...] = (),
) -> None:
    fake = SimpleNamespace(
        INSTALLED_APPS=list(apps),
        MIDDLEWARE=list(middleware),
        DATABASE_ROUTERS=list(routers),
    )
    monkeypatch.setattr(detection, "settings", fake)


class TestBackends:
    """The `THREAD_STATE` and `CONNECTION_STATE` paths load once per process."""

    def test_no_backend_is_configured_by_default(self) -> None:
        assert seams() == Seams()
        assert seams().covers == frozenset()

    def test_listed_backends_load_in_order_with_their_covers(self) -> None:
        with _defer(
            THREAD_STATE=[f"{__name__}.Crum"],
            CONNECTION_STATE=[f"{__name__}.Tenant", f"{__name__}.Role"],
        ):
            held = seams()
            assert [type(backend) for backend in held.threads] == [Crum]
            assert [type(backend) for backend in held.connections] == [Tenant, Role]
            assert held.covers == {
                "django-crum",
                "routers",
                f"routers:{__name__}.StatefulRouter",
            }
            assert seams() is held

    def test_a_settings_change_reads_the_backends_again(self) -> None:
        with _defer(THREAD_STATE=[f"{__name__}.Crum"]):
            assert len(seams().threads) == 1
        assert seams().threads == ()

    def test_forgetting_drops_the_loaded_backends(self) -> None:
        held = seams()
        forget_detection(setting="NEXT_FRAMEWORK")
        assert seams() is not held

    def test_a_path_that_does_not_import_raises(self) -> None:
        with (
            _defer(THREAD_STATE=["missing.module.State"]),
            pytest.raises(ImproperlyConfigured, match="could not be imported"),
        ):
            seams()

    @pytest.mark.parametrize(
        ("key", "path", "base"),
        [
            ("THREAD_STATE", f"{__name__}.Tenant", "ThreadState"),
            ("CONNECTION_STATE", f"{__name__}.Crum", "ConnectionState"),
            ("CONNECTION_STATE", f"{__name__}.not_a_class", "ConnectionState"),
        ],
        ids=["wrong_kind_thread", "wrong_kind_connection", "function"],
    )
    def test_a_path_to_no_subclass_raises(self, key, path, base) -> None:
        with (
            _defer(**{key: [path]}),
            pytest.raises(ImproperlyConfigured, match=f"names no {base} subclass"),
        ):
            seams()

    def test_a_misfit_list_reads_as_the_default(self) -> None:
        with _defer(THREAD_STATE="not.a.list"):
            assert seams().threads == ()


class TestConnectionDetection:
    """Per-connection state comes from receivers, routers and tenant middleware."""

    def test_a_connection_created_receiver_is_no_source(self) -> None:
        def receiver(**kwargs: object) -> None:
            return None

        connection_created.connect(receiver, weak=False)
        try:
            assert connection_sources() == ()
        finally:
            connection_created.disconnect(receiver)

    def test_routers_without_the_stateless_flag_are_sources(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        stateless = StatelessRouter()
        stateful = StatefulRouter()
        _deployment(
            monkeypatch,
            routers=(
                f"{__name__}.StatelessRouter",
                f"{__name__}.StatefulRouter",
                "missing.module.Router",
                stateless,
                stateful,
            ),
        )
        assert connection_sources() == (
            f"routers:{__name__}.StatefulRouter",
            "routers:missing.module.Router",
        )

    def test_a_truthy_flag_other_than_true_keeps_the_router(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        router = StatefulRouter()
        router.next_stateless = 1
        _deployment(monkeypatch, routers=(router,))
        assert connection_sources() == (f"routers:{__name__}.StatefulRouter",)

    @pytest.mark.parametrize(
        ("path", "found"),
        [
            ("django_tenants.middleware.main.TenantMainMiddleware", True),
            ("django_tenants.middleware.TenantSubfolderMiddleware", True),
            ("tenant_schemas.middleware.TenantMiddleware", True),
            ("django_tenants.utils.Helper", False),
            ("django_tenants_extra.middleware.Other", False),
        ],
    )
    def test_known_tenant_middleware_is_a_source(
        self, monkeypatch: pytest.MonkeyPatch, path: str, found
    ) -> None:
        _deployment(monkeypatch, middleware=(path,))
        expected = (f"tenant-middleware:{path}",) if found else ()
        assert connection_sources() == expected

    def test_a_backend_covers_a_kind_or_a_full_name(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _deployment(
            monkeypatch,
            routers=(f"{__name__}.StatefulRouter",),
            middleware=("tenant_schemas.middleware.TenantMiddleware",),
        )
        tenant = "tenant-middleware:tenant_schemas.middleware.TenantMiddleware"
        for backend in ("Tenant", "Role"):
            with _defer(CONNECTION_STATE=[f"{__name__}.{backend}"]):
                assert connection_sources() == (tenant,)

    def test_the_answer_is_cached_until_forgotten(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _deployment(monkeypatch)
        assert connection_sources() == ()
        monkeypatch.setattr(detection.settings, "DATABASE_ROUTERS", ["a.Router"])
        assert connection_sources() == ()
        forget_detection()
        assert connection_sources() == ("routers:a.Router",)


class TestThreadDetection:
    """Thread-local scoping comes from known libraries and the project's own list."""

    @pytest.mark.parametrize(
        ("where", "entry", "source"),
        [
            ("apps", "crum", "django-crum:crum"),
            (
                "middleware",
                "crum.CurrentRequestUserMiddleware",
                "django-crum:crum.CurrentRequestUserMiddleware",
            ),
            ("apps", "django_multitenant", "django-multitenant:django_multitenant"),
            ("apps", "django_scopes", "django-scopes:django_scopes"),
            (
                "apps",
                "tenant_users.tenants",
                "django-tenant-users:tenant_users.tenants",
            ),
            (
                "middleware",
                "threadlocals.middleware.ThreadLocalMiddleware",
                "django-threadlocals:threadlocals.middleware.ThreadLocalMiddleware",
            ),
            (
                "middleware",
                "django_currentuser.middleware.ThreadLocalUserMiddleware",
                (
                    "django-currentuser:"
                    "django_currentuser.middleware.ThreadLocalUserMiddleware"
                ),
            ),
            ("apps", "crumbs", None),
            ("apps", "django.contrib.auth", None),
        ],
    )
    def test_listed_libraries_are_sources(
        self,
        monkeypatch: pytest.MonkeyPatch,
        where: str,
        entry: str,
        source: str | None,
    ) -> None:
        _deployment(monkeypatch, **{where: (entry,)})
        assert thread_sources() == (() if source is None else (source,))

    def test_project_middleware_is_named_by_its_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _deployment(monkeypatch)
        with _executor(THREAD_LOCAL_MIDDLEWARE=["project.middleware.Tenant"]):
            assert thread_sources() == (
                "thread-local-middleware:project.middleware.Tenant",
            )

    def test_a_thread_state_covers_its_library(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _deployment(
            monkeypatch,
            apps=("crum", "django_scopes"),
            middleware=("crum.CurrentRequestUserMiddleware",),
        )
        with _defer(THREAD_STATE=[f"{__name__}.Crum"]):
            assert thread_sources() == ("django-scopes:django_scopes",)

    def test_the_test_project_has_no_source(self) -> None:
        assert thread_sources() == ()


class TestChecks:
    """W155 and W171 name each uncovered source."""

    def test_each_uncovered_source_warns(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _deployment(
            monkeypatch,
            apps=("crum",),
            routers=("a.Router",),
            middleware=("tenant_schemas.middleware.TenantMiddleware",),
        )
        connection_found = check_connection_sources()
        thread_found = check_thread_sources()
        assert [message.id for message in connection_found] == ["next.W155"] * 2
        assert "'routers:a.Router'" in connection_found[0].msg
        assert [message.id for message in thread_found] == ["next.W171"]
        assert "'django-crum:crum'" in thread_found[0].msg

    def test_nothing_uncovered_warns_nothing(self) -> None:
        assert check_connection_sources() == []
        assert check_thread_sources() == []


def _request(**meta: object) -> HttpRequest:
    return RequestFactory().get("/", **meta)


class TestForegroundFacts:
    """The view reads every rule input on its own thread."""

    def test_the_test_project_reads_as_one_in_memory_sqlite_alias(self) -> None:
        assert foreground_facts(_request()) == ForegroundFacts(
            server=Server.WSGI_SYNC, sqlite=("default",), memory=("default",)
        )

    @pytest.mark.django_db()
    def test_an_open_atomic_block_is_named(self) -> None:
        assert foreground_facts(_request()).atomic == ("default",)

    @pytest.mark.django_db(transaction=True)
    def test_autocommit_names_no_atomic_alias(self) -> None:
        _query()
        assert foreground_facts(_request()).atomic == ()

    def test_a_file_database_is_sqlite_but_not_in_memory(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(connections["default"], "is_in_memory_db", lambda: False)
        found = foreground_facts(_request())
        assert (found.sqlite, found.memory) == (("default",), ())

    def test_another_vendor_is_no_sqlite_alias(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(connections["default"], "vendor", "postgresql")
        found = foreground_facts(_request())
        assert (found.sqlite, found.memory) == ((), ())

    @pytest.mark.parametrize(
        ("options", "sqlite_parallel", "parallel"),
        [
            ({"SQLITE": "parallel"}, True, True),
            ({"SQLITE": "foreground"}, False, True),
            ({"SQLITE": "bogus"}, False, True),
            ({"PARALLEL": False}, False, False),
            ({"PARALLEL": "no"}, False, True),
        ],
    )
    def test_the_executor_options_are_read(
        self, options: dict[str, object], sqlite_parallel, parallel
    ) -> None:
        with _executor(**options):
            found = foreground_facts(_request())
        assert (found.sqlite_parallel, found.parallel) == (sqlite_parallel, parallel)

    def test_a_scope_without_an_options_mapping_reads_the_defaults(self) -> None:
        with _defer(EXECUTOR={"BACKEND": "next.suspense.LaneExecutor"}):
            found = foreground_facts(_request())
        assert (found.sqlite_parallel, found.parallel) == (False, True)

    def test_the_uncovered_sources_are_carried(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _deployment(monkeypatch, apps=("crum",), routers=("a.Router",))
        found = foreground_facts(_request())
        assert found.connection_sources == ("routers:a.Router",)
        assert found.thread_sources == ("django-crum:crum",)

    @pytest.mark.parametrize(
        ("meta", "loop", "server"),
        [
            ({}, None, Server.WSGI_SYNC),
            ({"wsgi.multithread": True}, None, Server.WSGI_THREADED),
            ({"wsgi.multithread": False}, None, Server.WSGI_SYNC),
            ({"wsgi.multithread": True}, object(), Server.ASGI),
        ],
        ids=["sync", "threaded", "single_thread", "asgi"],
    )
    def test_the_server_kind(
        self,
        monkeypatch: pytest.MonkeyPatch,
        meta: dict[str, object],
        loop: object,
        server: Server,
    ) -> None:
        monkeypatch.setattr(facts, "server_loop", lambda: loop)
        assert foreground_facts(_request(**meta)).server is server

    def test_a_render_without_a_request_reads_as_a_threaded_worker(self) -> None:
        assert foreground_facts(None).server is Server.WSGI_THREADED

    @pytest.mark.django_db()
    def test_a_forced_pool_reports_no_atomic_block_and_no_sqlite(self) -> None:
        with forcing_pool():
            assert pool_forced() is True
            found = foreground_facts(_request())
        assert (found.atomic, found.sqlite, found.memory) == ((), (), ())
        assert pool_forced() is False


class TestOutlived:
    """A stream's jobs keep only the atomic blocks still open when they run."""

    @pytest.mark.django_db(transaction=True)
    def test_a_block_that_committed_pins_nothing(self) -> None:
        with transaction.atomic():
            read = foreground_facts(_request())
            outlived = Outlived(read.atomic)
            assert outlived.facts(read) is read
        assert read.atomic == ("default",)
        assert outlived.facts(read).atomic == ()

    @pytest.mark.django_db(transaction=True)
    def test_a_block_inside_one_still_open_keeps_it(self) -> None:
        with transaction.atomic():
            with transaction.atomic():
                read = foreground_facts(_request())
                outlived = Outlived(read.atomic)
            assert outlived.facts(read) is read

    @pytest.mark.django_db(transaction=True)
    def test_a_rolled_back_block_fails_closed(self) -> None:
        with transaction.atomic():
            read = foreground_facts(_request())
            outlived = Outlived(read.atomic)
            transaction.set_rollback(True)
        assert outlived.facts(read) is read


class TestCaptureStates:
    """Every backend captures in the view, `None` meaning nothing to replay."""

    def test_nothing_configured_captures_nothing(self) -> None:
        assert capture_states(_request()) == States()

    def test_a_render_without_a_request_captures_nothing(self) -> None:
        with _defer(THREAD_STATE=[f"{__name__}.Crum"]):
            assert capture_states(None) == States()

    def test_states_are_captured_per_backend_and_alias(self) -> None:
        request = _request()
        request.tenant = "acme"
        request.crum = "user"
        with _defer(
            THREAD_STATE=[f"{__name__}.Crum"],
            CONNECTION_STATE=[f"{__name__}.Tenant", f"{__name__}.Role"],
        ):
            captured = capture_states(request)
        assert [(type(i.source), i.state, i.alias) for i in captured.threads] == [
            (Crum, "user", None)
        ]
        assert [(type(i.source), i.state, i.alias) for i in captured.connections] == [
            (Tenant, "acme", "default")
        ]

    def test_a_none_capture_is_skipped(self) -> None:
        with _defer(
            THREAD_STATE=[f"{__name__}.Crum"], CONNECTION_STATE=[f"{__name__}.Tenant"]
        ):
            assert capture_states(_request()) == States()


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
            _query("SELECT 1")
            assert connection.in_atomic_block
            _query("SELECT 2")

        assert _guarded(body) == ["BEGIN", TENANT, "SELECT 1", "SELECT 2", "COMMIT"]
        assert connection.execute_wrappers == []
        assert not connection.in_atomic_block

    def test_transaction_control_alone_never_applies(self) -> None:
        def body() -> None:
            with transaction.atomic():
                pass

        assert _guarded(body) == ["BEGIN", "COMMIT"]

    def test_a_session_statement_alone_never_applies(self) -> None:
        assert _guarded(lambda: _query("PRAGMA foreign_keys")) == [
            "PRAGMA foreign_keys"
        ]

    def test_a_user_block_first_takes_the_state_and_it_returns_after(self) -> None:
        def body() -> None:
            with transaction.atomic():
                _query("SELECT 1")
            _query("SELECT 2")

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
            _query("SELECT 2")

        log = _guarded(body)
        assert log[:2] == ["BEGIN", TENANT]
        assert log[2].startswith('INSERT INTO "auth_group"')
        assert log[3:] == ["COMMIT", "BEGIN", TENANT, "SELECT 2", "COMMIT"]
        assert Group.objects.filter(name="durable").exists()

    def test_back_to_back_user_blocks_each_take_the_state(self) -> None:
        def body() -> None:
            with transaction.atomic():
                _query("SELECT 1")
            with transaction.atomic():
                _query("SELECT 2")

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
                _query("SELECT 1")
                with transaction.atomic():
                    _query("SELECT 2")
                _query("SELECT 3")
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
                _query("SELECT 2")

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
                _query("SELECT 1")
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
                _query("SELECT 1")
                _query("SELECT 2")
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
                _query("SELECT 1")
            _query("PRAGMA foreign_keys")
            with transaction.atomic():
                _query("SELECT 2")

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
                _query("SELECT 1")
            with transaction.atomic():
                _query("SELECT 2")

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
                _query("SELECT 2")

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
                    _query("SELECT 1")

        assert _guarded(body) == ["BEGIN", "SELECT 1", "ROLLBACK"]

    def test_a_failed_first_statement_rolls_back_and_the_next_reapplies(self) -> None:
        def body() -> None:
            with pytest.raises(DatabaseError):
                _query("SELECT * FROM missing_table")
            _query("SELECT 1")

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
            _query("SELECT 1")
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
        log = _guarded(_query, deadline=time.monotonic() - 1)
        assert log == ["BEGIN", self._setting("50"), TENANT, "SELECT 1", "COMMIT"]
        assert calls == [
            ("statement_timeout", "50", 1),
            ("idle_in_transaction_session_timeout", "50", 1),
        ]

    def test_the_floor_setting_is_read(self, calls: list[tuple[object, ...]]) -> None:
        with _defer(DB_TIMEOUT_FLOOR="2s"):
            log = _guarded(_query, deadline=time.monotonic() - 1)
        assert log[1] == self._setting("2000")

    def test_state_mode_without_a_state_opens_nothing(
        self, calls: list[tuple[object, ...]]
    ) -> None:
        assert _guarded(_query, States()) == ["SELECT 1"]
        assert calls == []

    def test_always_opens_the_transaction_for_the_deadline_alone(
        self, calls: list[tuple[object, ...]]
    ) -> None:
        with _defer(DEADLINE_TO_DB="always"):
            log = _guarded(_query, States(), deadline=time.monotonic() - 1)
        assert log == ["BEGIN", self._setting("50"), "SELECT 1", "COMMIT"]

    def test_always_leaves_a_query_free_job_bare(
        self, calls: list[tuple[object, ...]]
    ) -> None:
        with _defer(DEADLINE_TO_DB="always"):
            assert _guarded(lambda: None, States()) == []

    def test_false_sends_no_deadline(self, calls: list[tuple[object, ...]]) -> None:
        with _defer(DEADLINE_TO_DB=False):
            log = _guarded(_query)
        assert log == ["BEGIN", TENANT, "SELECT 1", "COMMIT"]
        assert calls == []

    def test_a_misfit_mode_reads_as_state(
        self, calls: list[tuple[object, ...]]
    ) -> None:
        with _defer(DEADLINE_TO_DB="sometimes"):
            log = _guarded(_query, deadline=time.monotonic() - 1)
        assert log[1] == self._setting("50")


@pytest.mark.django_db(transaction=True)
class TestAlwaysOnOtherVendors:
    """`always` touches PostgreSQL aliases only."""

    def test_a_sqlite_alias_gets_no_deadline(self) -> None:
        with _defer(DEADLINE_TO_DB="always"):
            assert _guarded(_query, States()) == ["SELECT 1"]
            assert _guarded(_query) == ["BEGIN", TENANT, "SELECT 1", "COMMIT"]


def _through(request: HttpRequest) -> HttpRequest:
    def response(request: HttpRequest) -> HttpResponse:
        return HttpResponse()

    SessionMiddleware(response).process_request(request)
    AuthenticationMiddleware(response).process_request(request)
    MessageMiddleware(response).process_request(request)
    return request


def _stored_session() -> str:
    store = SessionStore()
    store["seen"] = True
    store.save()
    return str(store.session_key)


def _post() -> HttpRequest:
    return RequestFactory().post("/", {"field": "value"})


@pytest.mark.django_db()
class TestSettleRequest:
    """Lazy request state is forced before the first job, each where it is installed."""

    def _request(self, method: str = "get") -> HttpRequest:
        request = _through(getattr(RequestFactory(), method)("/", {"a": "1"}))
        request.session = SessionStore(_stored_session())
        return request

    def test_every_installed_piece_is_forced(self) -> None:
        request = self._request()
        messages.info(request, "saved")
        settled = settle_request(request, stream=False)
        assert request.user._wrapped is not empty
        assert hasattr(request.session, "_session_cache")
        storage = messages.get_messages(request)
        assert hasattr(storage, "_loaded_data")
        assert storage.used is False
        assert "CSRF_COOKIE_NEEDS_UPDATE" not in request.META
        assert settled == Settled(
            request.session.session_key, False, False, request.META["CSRF_COOKIE"]
        )

    def test_a_stream_also_mints_the_csrf_token(self) -> None:
        request = self._request()
        settle_request(request, stream=True)
        assert "CSRF_COOKIE" in request.META
        assert request.META["CSRF_COOKIE_NEEDS_UPDATE"] is True

    def test_parallel_jobs_share_one_seeded_csrf_secret(self) -> None:
        request = self._request()
        settle_request(request, stream=False)
        template = Template("{% csrf_token %}")
        tokens: list[str] = []

        def job() -> None:
            html = template.render(Context({"csrf_token": get_token(request)}))
            tokens.append(re.findall(r'value="([^"]+)"', html)[0])

        threads = [threading.Thread(target=job) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        secret = request.META["CSRF_COOKIE"]
        assert [_unmask_cipher_token(token) for token in tokens] == [secret, secret]
        assert request.META["CSRF_COOKIE_NEEDS_UPDATE"] is True

    def test_jobs_that_render_no_token_leave_the_response_shared(self) -> None:
        request = self._request()
        settle_request(request, stream=False)
        response = CsrfViewMiddleware(lambda _: HttpResponse()).process_response(
            request, HttpResponse()
        )
        assert settings.CSRF_COOKIE_NAME not in response.cookies
        assert not response.has_header("Vary")

    def test_a_request_carrying_its_secret_is_left_alone(self) -> None:
        request = self._request()
        request.META["CSRF_COOKIE"] = "held"
        settle_request(request, stream=False)
        assert request.META["CSRF_COOKIE"] == "held"
        assert "CSRF_COOKIE_NEEDS_UPDATE" not in request.META

    def test_an_unsafe_method_parses_the_body(self) -> None:
        request = _through(_post())
        settle_request(request, stream=False)
        assert vars(request)["_post"]["field"] == "value"
        assert "_files" in vars(request)

    def test_a_safe_method_leaves_the_body(self) -> None:
        request = self._request()
        settle_request(request, stream=False)
        assert "_post" not in vars(request)

    @pytest.mark.parametrize(
        ("installed", "user", "session", "messages_loaded", "csrf"),
        [
            ([], False, False, False, False),
            ([MIDDLEWARE_ALL[0]], False, True, False, False),
            ([MIDDLEWARE_ALL[0], MIDDLEWARE_ALL[2]], True, True, False, False),
            ([MIDDLEWARE_ALL[0], MIDDLEWARE_ALL[3]], False, True, True, False),
            ([MIDDLEWARE_ALL[1]], False, False, False, True),
            ([f"{__name__}.ProjectSessions"], False, True, False, False),
        ],
        ids=["none", "sessions", "auth", "messages", "csrf", "subclass"],
    )
    def test_each_step_follows_its_middleware(
        self, installed: list[str], user, session, messages_loaded, csrf
    ) -> None:
        request = self._request()
        with override_settings(MIDDLEWARE=installed):
            settle_request(request, stream=True)
        assert (request.user._wrapped is not empty) is user
        assert hasattr(request.session, "_session_cache") is session
        assert (
            hasattr(messages.get_messages(request), "_loaded_data") is messages_loaded
        )
        assert ("CSRF_COOKIE" in request.META) is csrf

    def test_a_request_the_middleware_never_saw_is_tolerated(self) -> None:
        request = _post()
        settled = settle_request(request, stream=True)
        assert settled == Settled(csrf_secret=request.META.get("CSRF_COOKIE"))

    def test_a_missing_session_cookie_creates_no_session(self) -> None:
        request = _through(RequestFactory().get("/"))
        settled = settle_request(request, stream=False)
        assert settled.session_key is None
        assert request.session.session_key is None

    def test_a_shared_page_loads_the_session_unmarked(self) -> None:
        request = self._request()
        settled = settle_request(request, stream=False, shared=True)
        assert hasattr(request.session, "_session_cache")
        assert request.session.accessed is False
        assert not hasattr(messages.get_messages(request), "_loaded_data")
        assert settled.session_key == request.session.session_key

    def test_a_shared_page_user_marks_the_session_read_when_read(self) -> None:
        request = self._request()
        settle_request(request, stream=False, shared=True)
        assert request.session.accessed is False
        assert request.user.is_authenticated is False
        assert request.session.accessed is True

    def test_a_shared_page_whose_shell_read_the_session_resolves_as_usual(self) -> None:
        request = self._request()
        request.session.get("seen")
        settle_request(request, stream=False, shared=True)
        assert request.user._wrapped is not empty
        assert request.session.accessed is True

    @override_settings(MIDDLEWARE=[MIDDLEWARE_ALL[0]])
    def test_a_shared_page_without_authentication_loads_the_session_alone(self) -> None:
        request = self._request()
        settle_request(request, stream=False, shared=True)
        assert request.user._wrapped is empty
        assert request.session.accessed is False

    def test_a_shared_page_without_a_session_settles_the_csrf_secret_alone(
        self,
    ) -> None:
        request = _post()
        settled = settle_request(request, stream=False, shared=True)
        assert settled == Settled(csrf_secret=request.META["CSRF_COOKIE"])

    def test_the_state_reads_load_nothing(self) -> None:
        request = self._request()
        assert request_state(request) == Settled(request.session.session_key)
        assert request.session.accessed is False


@pytest.mark.django_db()
class TestEffects:
    """A job's writes to request state show against the settled record."""

    def _settled(self) -> tuple[HttpRequest, Settled]:
        request = _through(RequestFactory().get("/"))
        request.session = SessionStore(_stored_session())
        messages.info(request, "saved")
        return request, settle_request(request, stream=False)

    def test_reads_alone_are_no_effect(self) -> None:
        request, before = self._settled()
        request.session.get("seen")
        len(messages.get_messages(request))
        assert effects(request, before) == Effects()
        assert not effects(request, before)

    def test_each_write_is_named(self) -> None:
        request, before = self._settled()
        request.session["written"] = True
        request.session.cycle_key()
        list(messages.get_messages(request))
        assert effects(request, before) == Effects(
            session_modified=True, session_key_changed=True, messages_used=True
        )

    def test_a_write_before_the_settle_is_not_the_job(self) -> None:
        request = _through(RequestFactory().get("/"))
        request.session["early"] = True
        list(messages.get_messages(request))
        before = settle_request(request, stream=False)
        assert effects(request, before) == Effects()

    def test_a_rotated_csrf_secret_is_named(self) -> None:
        request, _before = self._settled()
        get_token(request)
        before = request_state(request)
        rotate_token(request)
        assert effects(request, before) == Effects(csrf_changed=True)

    def test_a_first_csrf_secret_or_a_reread_is_no_effect(self) -> None:
        request, before = self._settled()
        get_token(request)
        assert effects(request, before) == Effects()
        minted = request_state(request)
        get_token(request)
        assert effects(request, minted) == Effects()

    def test_no_session_and_no_messages_is_no_effect(self) -> None:
        request = _post()
        assert effects(request, settle_request(request, stream=False)) == Effects()


@pytest.mark.django_db()
class TestFreshQuerysets:
    """Each job gets lazy clones of the shell's querysets, at no query."""

    def test_unevaluated_querysets_and_pages_are_cloned_without_a_query(self) -> None:
        Group.objects.create(name="one")
        evaluated = Group.objects.all()
        list(evaluated)
        lazy = Group.objects.filter(name="one")
        page = Paginator(Group.objects.order_by("pk"), 10).page(1)
        done = Paginator(Group.objects.order_by("pk"), 10).page(1)
        list(done)
        data = {
            "lazy": lazy,
            "evaluated": evaluated,
            "page": page,
            "done": done,
            "n": 1,
        }
        with CaptureQueriesContext(connection) as queries:
            fresh = fresh_querysets(data)
        assert queries.captured_queries == []
        assert fresh["lazy"] is not lazy
        assert str(fresh["lazy"].query) == str(lazy.query)
        assert fresh["evaluated"] is evaluated
        assert fresh["page"] is not page
        assert fresh["page"].object_list is not page.object_list
        assert fresh["page"].number == page.number
        assert fresh["page"].paginator is page.paginator
        assert fresh["done"] is done
        assert fresh["n"] == 1
        assert data["lazy"] is lazy
        assert [group.name for group in fresh["page"]] == ["one"]

    def test_a_page_of_a_list_is_kept(self) -> None:
        page = Paginator(["a", "b"], 1).page(1)
        assert fresh_querysets({"page": page})["page"] is page


_POSTGRES = "django.db.backends.postgresql"


def _pooled(pool: object, engine: str = _POSTGRES) -> dict[str, object]:
    return {"ENGINE": engine, "NAME": "db", "OPTIONS": {"pool": pool}}


class TestPools:
    """The pool of an alias is read from its settings with the psycopg_pool defaults."""

    @pytest.mark.parametrize(
        ("pool", "size", "timeout"),
        [
            pytest.param(True, 4, 30.0, id="defaults"),
            pytest.param({"min_size": 2}, 2, 30.0, id="min-size-only"),
            pytest.param({"min_size": 2, "max_size": 20}, 20, 30.0, id="max-size"),
            pytest.param({"max_size": None, "timeout": 5}, 4, 5.0, id="max-size-none"),
            pytest.param({"max_size": "many", "timeout": True}, None, None, id="bad"),
            pytest.param({"min_size": 2.5}, None, 30.0, id="fractional"),
        ],
    )
    def test_the_size_and_the_wait_are_read(
        self,
        monkeypatch: pytest.MonkeyPatch,
        pool: object,
        size: int | None,
        timeout: float | None,
    ) -> None:
        monkeypatch.setattr(settings, "DATABASES", {"default": _pooled(pool)})
        assert list(pools()) == [Pool("default", size, timeout)]

    @pytest.mark.parametrize(
        "config",
        [
            pytest.param(_pooled(True, "django.db.backends.sqlite3"), id="sqlite"),
            pytest.param(_pooled(False), id="off"),
            pytest.param({"ENGINE": _POSTGRES, "OPTIONS": "x"}, id="odd-options"),
            pytest.param({"ENGINE": _POSTGRES}, id="no-options"),
            pytest.param("not a mapping", id="odd-alias"),
        ],
    )
    def test_an_alias_without_a_pool_is_skipped(
        self, monkeypatch: pytest.MonkeyPatch, config: object
    ) -> None:
        monkeypatch.setattr(settings, "DATABASES", {"default": config})
        assert list(pools()) == []

    def test_postgis_pools_too(self, monkeypatch: pytest.MonkeyPatch) -> None:
        gis = "django.contrib.gis.db.backends.postgis"
        monkeypatch.setattr(settings, "DATABASES", {"geo": _pooled(True, gis)})
        assert [pool.alias for pool in pools()] == ["geo"]


class TestPoolChecks:
    """next.W162 and next.W172 compare each pool with the budget and the deadline."""

    def test_a_default_pool_is_below_the_default_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "DATABASES", {"default": _pooled(True)})
        [message] = check_pool_budget()
        assert message.id == "next.W162"
        assert "holds at most 4 connections, below the 17" in message.msg
        assert "at least 17" in (message.hint or "")

    def test_a_pool_as_large_as_the_budget_is_quiet(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pool = {"max_size": 3, "timeout": 2}
        monkeypatch.setattr(settings, "DATABASES", {"default": _pooled(pool)})
        defer = {"REQUEST_CONCURRENCY": 1, "EXECUTOR": {"OPTIONS": {"MAX_WORKERS": 2}}}
        with override_next_settings(DEFER=defer):
            assert check_pool_budget() == []
            assert check_pool_timeout() == []

    def test_an_unreadable_size_is_left_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        pool = {"max_size": "many", "timeout": "soon"}
        monkeypatch.setattr(settings, "DATABASES", {"default": _pooled(pool)})
        assert check_pool_budget() == []
        assert check_pool_timeout() == []

    def test_the_default_wait_outlasts_the_deadline(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "DATABASES", {"default": _pooled(True)})
        [message] = check_pool_timeout()
        assert message.id == "next.W172"
        assert "waits up to 30 seconds" in message.msg
        assert "DEADLINE'] of 10 seconds" in message.msg


class TestListedMiddleware:
    """The request state follows the middleware list, asked again once it moves."""

    def test_a_moved_list_is_asked_again(self) -> None:
        request = _through(_request())
        request.session = SessionStore()
        assert settle._session(request) is request.session
        with override_settings(MIDDLEWARE=[]):
            assert settle._session(request) is None
        assert settle._session(request) is request.session
