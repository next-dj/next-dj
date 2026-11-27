import threading
from collections.abc import Callable
from contextlib import suppress

import pytest
from django.contrib.auth.models import Group
from django.db import connection, connections, transaction
from django.db.backends.signals import connection_created
from django.test.utils import CaptureQueriesContext

from next.boundaries import (
    contain,
    protection as protection_module,
    scope as scope_module,
)
from next.boundaries.calls import Addressing
from next.boundaries.failures import Boundary, BoundaryFailure, ErrorKind
from next.boundaries.protection import protect
from next.boundaries.scope import render_boundaries
from next.static import StaticCollector
from next.static.assets import StaticAsset
from next.testing import capture_signals
from next.transactions import savepoints
from tests.support.boundaries import RecordingErrorUI, defer_errors, raising


class TestJournal:
    """The failed subtree takes back its assets, notes and JS context."""

    def test_the_failed_subtree_rolls_back_and_the_error_ui_keeps(self) -> None:
        collector = StaticCollector()
        collector.add(StaticAsset(url="/before.css", kind="css"))

        def render() -> str:
            collector.add(StaticAsset(url="/failed.css", kind="css"))
            collector.note("zones", "failed")
            collector.add_js_context("failed", 1)
            raise ValueError

        def error_ui(error: BoundaryFailure, kind: ErrorKind) -> str:
            collector.add(StaticAsset(url="/error.css", kind="css"))
            return "x"

        contain(
            Boundary("card", Addressing.COMPONENT, collector=collector),
            render,
            RecordingErrorUI(render=error_ui),
        )
        urls = [asset.url for asset in collector.assets_in_slot("styles")]
        assert urls == ["/before.css", "/error.css"]
        assert list(collector.notes("zones")) == []
        assert "failed" not in collector.js_context()
        assert collector._journal is None

    def test_a_success_keeps_the_assets_and_drops_the_journal(self) -> None:
        collector = StaticCollector()

        def render() -> str:
            collector.add(StaticAsset(url="/kept.css", kind="css"))
            return "ok"

        contain(
            Boundary("card", Addressing.COMPONENT, collector=collector),
            render,
            RecordingErrorUI(),
        )
        assert [a.url for a in collector.assets_in_slot("styles")] == ["/kept.css"]
        assert collector._journal is None

    def test_an_inner_failure_keeps_the_outer_assets(self) -> None:
        collector = StaticCollector()

        def outer_render() -> str:
            collector.add(StaticAsset(url="/outer.css", kind="css"))
            return contain(
                Boundary("inner", Addressing.COMPONENT, collector=collector),
                raising(ValueError()),
                RecordingErrorUI(),
            )

        contain(
            Boundary("outer", Addressing.COMPONENT, collector=collector),
            outer_render,
            RecordingErrorUI(),
        )
        assert [a.url for a in collector.assets_in_slot("styles")] == ["/outer.css"]


def _write(name: str) -> Callable[[], str]:
    def render() -> str:
        Group.objects.create(name=name)
        return ""

    return render


def _write_then_fail(*names: str) -> Callable[[], str]:
    def render() -> str:
        for name in names:
            Group.objects.create(name=name)
        raise ValueError(names)

    return render


def _error_ui_writes(name: str) -> RecordingErrorUI:
    def render(error: BoundaryFailure, kind: ErrorKind) -> str:
        Group.objects.create(name=name)
        return str(Group.objects.count())

    return RecordingErrorUI(render=render)


@pytest.mark.django_db()
class TestSavepointInsideAnAtomicBlock:
    """Inside a request transaction the failed subtree rolls back to its savepoint."""

    def test_the_failed_writes_roll_back_and_the_error_ui_writes_stay(self) -> None:
        with transaction.atomic():
            Group.objects.create(name="before")
            html = contain(
                Boundary("card", Addressing.COMPONENT),
                _write_then_fail("one", "two"),
                _error_ui_writes("error-ui"),
            )
            names = set(Group.objects.values_list("name", flat=True))
        assert names == {"before", "error-ui"}
        assert ">2</div>" in html

    def test_a_query_free_subtree_issues_no_statement(self) -> None:
        with transaction.atomic(), CaptureQueriesContext(connection) as queries:
            contain(
                Boundary("card", Addressing.COMPONENT),
                raising(ValueError()),
                RecordingErrorUI(),
            )
            contain(
                Boundary("card", Addressing.COMPONENT), lambda: "ok", RecordingErrorUI()
            )
        assert queries.captured_queries == []

    def test_a_kept_subtree_keeps_its_writes(self) -> None:
        with transaction.atomic():
            contain(
                Boundary("card", Addressing.COMPONENT),
                _write("kept"),
                RecordingErrorUI(),
            )
            assert Group.objects.filter(name="kept").exists()

    def test_reading_rows_inside_a_boundary_share_its_one_savepoint(self) -> None:
        def row() -> str:
            list(Group.objects.all())
            return "r"

        def page() -> str:
            return "".join(
                contain(Boundary("row", Addressing.COMPONENT), row, RecordingErrorUI())
                for _ in range(100)
            )

        with transaction.atomic(), CaptureQueriesContext(connection) as queries:
            contain(Boundary("page", Addressing.PAGE), page, RecordingErrorUI())
        leads = [q["sql"].split()[0] for q in queries.captured_queries]
        assert leads == ["SAVEPOINT", *["SELECT"] * 100, "RELEASE"]

    def test_a_failed_read_without_a_savepoint_is_held_by_the_boundary_above(
        self,
    ) -> None:
        def broken_row() -> str:
            with connection.cursor() as cursor:
                cursor.execute("SELECT * FROM next_missing_table")
            return "r"

        def page() -> str:
            Group.objects.create(name="page")
            return contain(
                Boundary("row", Addressing.COMPONENT),
                broken_row,
                RecordingErrorUI("row"),
            )

        with transaction.atomic():
            html = contain(
                Boundary("page", Addressing.PAGE), page, RecordingErrorUI("<p>page</p>")
            )
            Group.objects.create(name="after")
        assert "<p>page</p>" in html
        assert list(Group.objects.values_list("name", flat=True)) == ["after"]


def _savepoints(queries: CaptureQueriesContext) -> list[str]:
    """Name each savepoint statement's target by the order it was created in."""
    order: dict[str, int] = {}
    named: list[str] = []
    for query in queries.captured_queries:
        words = query["sql"].split()
        if words[0] in {"SAVEPOINT", "RELEASE", "ROLLBACK"}:
            number = order.setdefault(words[-1], len(order) + 1)
            named.append(f"{words[0]} {number}")
    return named


def _caught_then_fail() -> str:
    with suppress(LookupError), transaction.atomic():
        Group.objects.create(name="caught")
        raise LookupError
    Group.objects.create(name="card")
    raise ValueError


def _page(card: Callable[[], str], *, fails: bool = False) -> Callable[[], str]:
    def render() -> str:
        Group.objects.create(name="page")
        html = contain(
            Boundary("card", Addressing.COMPONENT),
            card,
            RecordingErrorUI("<p>card</p>"),
        )
        if fails:
            raise ValueError
        return html

    return render


def _get_or_create() -> str:
    Group.objects.get_or_create(name="card")
    return "card"


@pytest.mark.django_db(transaction=True)
class TestApplicationBlocksInsideNestedBoundaries:
    """An inner boundary's savepoint is never nested inside an application block."""

    def _render(self, render: Callable[[], str]) -> tuple[str, list[str]]:
        with transaction.atomic(), CaptureQueriesContext(connection) as queries:
            html = contain(
                Boundary("page", Addressing.PAGE),
                render,
                RecordingErrorUI("<p>page</p>"),
            )
        return html, _savepoints(queries)

    def test_a_first_get_or_create_keeps_every_savepoint_valid(self) -> None:
        html, savepoints = self._render(_page(_get_or_create))
        assert html == "card"
        assert savepoints == [
            "SAVEPOINT 1",
            "SAVEPOINT 2",
            "SAVEPOINT 3",
            "RELEASE 3",
            "RELEASE 2",
            "RELEASE 1",
        ]
        assert set(Group.objects.values_list("name", flat=True)) == {"page", "card"}

    def test_a_failing_card_after_a_caught_block_undoes_only_its_writes(self) -> None:
        html, savepoints = self._render(_page(_caught_then_fail))
        assert "<p>card</p>" in html
        assert savepoints == [
            "SAVEPOINT 1",
            "SAVEPOINT 2",
            "SAVEPOINT 3",
            "ROLLBACK 3",
            "RELEASE 3",
            "ROLLBACK 2",
            "RELEASE 2",
            "RELEASE 1",
        ]
        assert list(Group.objects.values_list("name", flat=True)) == ["page"]

    def test_a_failing_page_undoes_the_get_or_create_of_its_card(self) -> None:
        html, savepoints = self._render(_page(_get_or_create, fails=True))
        assert "<p>page</p>" in html
        assert savepoints == [
            "SAVEPOINT 1",
            "SAVEPOINT 2",
            "SAVEPOINT 3",
            "RELEASE 3",
            "RELEASE 2",
            "ROLLBACK 1",
            "RELEASE 1",
        ]
        assert list(Group.objects.values_list("name", flat=True)) == []

    def test_a_read_only_page_in_autocommit_takes_no_savepoint(self) -> None:
        def card() -> str:
            list(Group.objects.all())
            return "card"

        def page() -> str:
            list(Group.objects.all())
            return contain(
                Boundary("card", Addressing.COMPONENT), card, RecordingErrorUI()
            )

        with CaptureQueriesContext(connection) as queries:
            contain(Boundary("page", Addressing.PAGE), page, RecordingErrorUI())
        assert _savepoints(queries) == []
        assert len(queries.captured_queries) == 2


def test_a_render_without_queries_opens_no_connection() -> None:
    held: list[list[str]] = []

    def render() -> None:
        with render_boundaries():
            contain(
                Boundary("page", Addressing.PAGE),
                lambda: contain(
                    Boundary("row", Addressing.COMPONENT),
                    lambda: "r",
                    RecordingErrorUI(),
                ),
                RecordingErrorUI(),
            )
        held.append([c.alias for c in connections.all(initialized_only=True)])

    with capture_signals(connection_created) as created:
        worker = threading.Thread(target=render)
        worker.start()
        worker.join()
    assert held == [[]]
    assert created.events == []


@pytest.mark.django_db(transaction=True)
class TestWritesInAutocommit:
    """`ATOMIC="writes"` opens a transaction before the first write of the subtree."""

    def test_two_writes_and_a_failure_leave_nothing(self) -> None:
        contain(
            Boundary("card", Addressing.COMPONENT),
            _write_then_fail("one", "two"),
            _error_ui_writes("error-ui"),
        )
        assert list(Group.objects.values_list("name", flat=True)) == ["error-ui"]

    def test_atomic_false_reads_a_block_opened_inside_the_render(self) -> None:
        def nested() -> str:
            with transaction.atomic():
                return contain(
                    Boundary("inner", Addressing.COMPONENT),
                    _write_then_fail("inner"),
                    RecordingErrorUI(),
                )

        with defer_errors(ATOMIC=False):
            contain(Boundary("outer", Addressing.COMPONENT), nested, RecordingErrorUI())
        assert list(Group.objects.values_list("name", flat=True)) == []

    def test_atomic_false_leaves_the_writes_of_autocommit(self) -> None:
        with defer_errors(ATOMIC=False):
            contain(
                Boundary("card", Addressing.COMPONENT),
                _write_then_fail("one"),
                RecordingErrorUI(),
            )
        assert list(Group.objects.values_list("name", flat=True)) == ["one"]

    def test_a_read_only_subtree_opens_no_transaction(self) -> None:
        def render() -> str:
            list(Group.objects.all())
            raise ValueError

        with CaptureQueriesContext(connection) as queries:
            contain(Boundary("card", Addressing.COMPONENT), render, RecordingErrorUI())
        assert [q["sql"].split()[0] for q in queries.captured_queries] == ["SELECT"]


@pytest.mark.django_db(transaction=True)
class TestProtectInAutocommit:
    """Work outside a template undoes its writes as a guarded subtree does."""

    def test_an_undone_protection_rolls_the_writes_back(self) -> None:
        protection = protect()
        Group.objects.create(name="one")
        protection.undo()
        assert list(Group.objects.values_list("name", flat=True)) == []

    def test_a_kept_protection_keeps_the_writes(self) -> None:
        protection = protect()
        Group.objects.create(name="kept")
        protection.keep()
        assert list(Group.objects.values_list("name", flat=True)) == ["kept"]

    def test_atomic_false_leaves_the_writes_of_autocommit(self) -> None:
        with defer_errors(ATOMIC=False):
            protection = protect()
            Group.objects.create(name="one")
            protection.undo()
        assert list(Group.objects.values_list("name", flat=True)) == ["one"]


class TestProtectProbes:
    """A protection inside a render shares the probe the boundaries of it share."""

    @pytest.fixture()
    def probes(self, monkeypatch: pytest.MonkeyPatch) -> list[int]:
        found: list[int] = []
        for module in (protection_module, scope_module):
            monkeypatch.setattr(
                module, "probe_atomic_aliases", lambda: found.append(1) or ()
            )
        return found

    def test_outside_a_render_it_probes_for_itself(self, probes: list[int]) -> None:
        protect().keep()
        assert probes == [1]
        assert savepoints._local.session is None

    def test_inside_a_render_it_reuses_the_probe(self, probes: list[int]) -> None:
        with render_boundaries():
            protect().keep()
            protect().undo()
        assert probes == [1]

    def test_atomic_false_probes_again_for_each(self, probes: list[int]) -> None:
        with defer_errors(ATOMIC=False), render_boundaries():
            protect().keep()
            protect().keep()
        assert probes == [1, 1, 1]
