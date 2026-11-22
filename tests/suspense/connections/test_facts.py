from collections.abc import Callable

import pytest
from django.db import connections, transaction

from next.suspense.connections import (
    Outlived,
    States,
    capture_states,
    facts,
    forcing_pool,
    foreground_facts,
    pool_forced,
)
from next.suspense.markers import ForegroundFacts, Server
from tests.suspense.connections.shared import (
    Crum,
    Role,
    Tenant,
    defer,
    dotted,
    executor,
    query,
)


class TestForegroundFacts:
    """The view reads every rule input on its own thread."""

    def test_the_test_project_reads_as_one_in_memory_sqlite_alias(self, rf) -> None:
        assert foreground_facts(rf.get("/")) == ForegroundFacts(
            server=Server.WSGI_SYNC, sqlite=("default",), memory=("default",)
        )

    @pytest.mark.django_db()
    def test_an_open_atomic_block_is_named(self, rf) -> None:
        assert foreground_facts(rf.get("/")).atomic == ("default",)

    @pytest.mark.django_db(transaction=True)
    def test_autocommit_names_no_atomic_alias(self, rf) -> None:
        query()
        assert foreground_facts(rf.get("/")).atomic == ()

    def test_a_file_database_is_sqlite_but_not_in_memory(
        self, rf, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(connections["default"], "is_in_memory_db", lambda: False)
        found = foreground_facts(rf.get("/"))
        assert (found.sqlite, found.memory) == (("default",), ())

    def test_another_vendor_is_no_sqlite_alias(
        self, rf, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(connections["default"], "vendor", "postgresql")
        found = foreground_facts(rf.get("/"))
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
        self, rf, options: dict[str, object], sqlite_parallel, parallel
    ) -> None:
        with executor(**options):
            found = foreground_facts(rf.get("/"))
        assert (found.sqlite_parallel, found.parallel) == (sqlite_parallel, parallel)

    def test_a_scope_without_an_options_mapping_reads_the_defaults(self, rf) -> None:
        with defer(EXECUTOR={"BACKEND": "next.suspense.LaneExecutor"}):
            found = foreground_facts(rf.get("/"))
        assert (found.sqlite_parallel, found.parallel) == (False, True)

    def test_the_uncovered_sources_are_carried(
        self, rf, deployment: Callable[..., None]
    ) -> None:
        deployment(apps=("crum",), routers=("a.Router",))
        found = foreground_facts(rf.get("/"))
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
        rf,
        monkeypatch: pytest.MonkeyPatch,
        meta: dict[str, object],
        loop: object,
        server: Server,
    ) -> None:
        monkeypatch.setattr(facts, "server_loop", lambda: loop)
        assert foreground_facts(rf.get("/", **meta)).server is server

    def test_a_render_without_a_request_reads_as_a_threaded_worker(self) -> None:
        assert foreground_facts(None).server is Server.WSGI_THREADED

    @pytest.mark.django_db()
    def test_a_forced_pool_reports_no_atomic_block_and_no_sqlite(self, rf) -> None:
        with forcing_pool():
            assert pool_forced() is True
            found = foreground_facts(rf.get("/"))
        assert (found.atomic, found.sqlite, found.memory) == ((), (), ())
        assert pool_forced() is False


class TestOutlived:
    """A stream's jobs keep only the atomic blocks still open when they run."""

    @pytest.mark.django_db(transaction=True)
    def test_a_block_that_committed_pins_nothing(self, rf) -> None:
        with transaction.atomic():
            read = foreground_facts(rf.get("/"))
            outlived = Outlived(read.atomic)
            assert outlived.facts(read) is read
        assert read.atomic == ("default",)
        assert outlived.facts(read).atomic == ()

    @pytest.mark.django_db(transaction=True)
    def test_a_block_inside_one_still_open_keeps_it(self, rf) -> None:
        with transaction.atomic():
            with transaction.atomic():
                read = foreground_facts(rf.get("/"))
                outlived = Outlived(read.atomic)
            assert outlived.facts(read) is read

    @pytest.mark.django_db(transaction=True)
    def test_a_rolled_back_block_fails_closed(self, rf) -> None:
        with transaction.atomic():
            read = foreground_facts(rf.get("/"))
            outlived = Outlived(read.atomic)
            transaction.set_rollback(True)
        assert outlived.facts(read) is read


class TestCaptureStates:
    """Every backend captures in the view, `None` meaning nothing to replay."""

    def test_nothing_configured_captures_nothing(self, rf) -> None:
        assert capture_states(rf.get("/")) == States()

    def test_a_render_without_a_request_captures_nothing(self) -> None:
        with defer(THREAD_STATE=[dotted(Crum)]):
            assert capture_states(None) == States()

    def test_states_are_captured_per_backend_and_alias(self, rf) -> None:
        request = rf.get("/")
        request.tenant = "acme"
        request.crum = "user"
        with defer(
            THREAD_STATE=[dotted(Crum)], CONNECTION_STATE=[dotted(Tenant), dotted(Role)]
        ):
            captured = capture_states(request)
        assert [(type(i.source), i.state, i.alias) for i in captured.threads] == [
            (Crum, "user", None)
        ]
        assert [(type(i.source), i.state, i.alias) for i in captured.connections] == [
            (Tenant, "acme", "default")
        ]

    def test_a_none_capture_is_skipped(self, rf) -> None:
        with defer(THREAD_STATE=[dotted(Crum)], CONNECTION_STATE=[dotted(Tenant)]):
            assert capture_states(rf.get("/")) == States()
