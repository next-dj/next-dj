import asyncio
import contextvars
import logging
import threading
from collections.abc import Callable
from contextlib import suppress
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Group
from django.core.exceptions import (
    BadRequest,
    ImproperlyConfigured,
    PermissionDenied,
    RequestAborted,
    SuspiciousOperation,
)
from django.db import connection, connections, transaction
from django.db.backends.signals import connection_created
from django.http import Http404
from django.http.multipartparser import MultiPartParserError
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from next import boundaries, signals as framework_signals, transactions
from next.boundaries import (
    Boundary,
    Trace,
    adopt,
    boundary_failed,
    contain,
    contained,
    debug_detail,
    failed,
    identity,
    kind_of,
    minting,
    misrooted,
    note_retry,
    protect,
    render_boundaries,
    shown_kind,
    wrapped,
    wrapper_tag,
)
from next.defer import BoundaryError, DebugBoundaryError, ErrorKind
from next.diagnostics import QUIET_PERIOD, degraded
from next.static import StaticCollector
from next.static.assets import StaticAsset
from next.static.runtime import BUNDLE_NOTE
from next.testing import capture_signals, override_next_settings


REPORTED: list[tuple[Exception, dict[str, object]]] = []
NOT_CALLABLE = "a string"
LOUD = "loud"
STAMP = 'id="b" data-next-boundary="b" data-next-state="error" data-next-error="error"'


def report(exc: Exception, **kwargs: object) -> str:
    REPORTED.append((exc, kwargs))
    return "custom-digest"


def report_raising(exc: Exception, **kwargs: object) -> str:
    raise RuntimeError(LOUD)


def report_none(exc: Exception, **kwargs: object) -> None:
    return None


def report_empty(exc: Exception, **kwargs: object) -> str:
    return ""


class _Fallback:
    """A fallback that records what it was asked and renders a fixed body."""

    def __init__(
        self,
        body: str = "<p>sorry</p>",
        *,
        tag: str | None = "div",
        retry: bool = True,
        render: Callable[[BoundaryError, ErrorKind], str] | None = None,
        token: str = "",
    ) -> None:
        self.body = body
        self.token = token
        self._tag = tag
        self._retry = retry
        self._render = render
        self.calls: list[tuple[BoundaryError, ErrorKind]] = []

    def retry(self) -> bool:
        return self._retry

    def retry_token(self, error: BoundaryError) -> str:
        return self.token

    def tag(self) -> str | None:
        return self._tag

    def render(self, error: BoundaryError, kind: ErrorKind) -> str:
        self.calls.append((error, kind))
        if self._render is not None:
            return self._render(error, kind)
        return self.body


def _raise(exc: BaseException) -> Callable[[], str]:
    def render() -> str:
        raise exc

    return render


def _errors(**values: object):
    return override_next_settings(DEFER={"ERRORS": values})


@pytest.fixture(autouse=True)
def _reported():
    REPORTED.clear()
    yield
    REPORTED.clear()


class TestCatchRules:
    """Every exception is contained except the ones Django answers and disconnects."""

    @pytest.mark.parametrize(
        ("exc", "before", "after"),
        [
            (ValueError("x"), True, True),
            (KeyError("x"), True, True),
            (Http404(), False, True),
            (PermissionDenied(), False, True),
            (SuspiciousOperation(), False, True),
            (BadRequest(), False, True),
            (MultiPartParserError(), False, True),
            (RequestAborted(), False, False),
            (KeyboardInterrupt(), False, False),
            (GeneratorExit(), False, False),
            (asyncio.CancelledError(), False, False),
        ],
        ids=lambda value: (
            type(value).__name__ if isinstance(value, BaseException) else None
        ),
    )
    def test_contained_before_and_after_the_first_byte(
        self, exc: BaseException, *, before: bool, after: bool
    ) -> None:
        assert contained(exc) is before
        assert contained(exc, started=True) is after

    @pytest.mark.parametrize(
        ("exc", "kind"),
        [
            (Http404(), ErrorKind.NOT_FOUND),
            (PermissionDenied(), ErrorKind.FORBIDDEN),
            (BadRequest(), ErrorKind.BAD_REQUEST),
            (SuspiciousOperation(), ErrorKind.BAD_REQUEST),
            (MultiPartParserError(), ErrorKind.BAD_REQUEST),
            (ValueError(), ErrorKind.ERROR),
        ],
    )
    def test_kind_of(self, exc: Exception, kind: ErrorKind) -> None:
        assert kind_of(exc) is kind

    @pytest.mark.parametrize(
        ("kind", "opaque", "shown"),
        [
            (ErrorKind.NOT_FOUND, True, ErrorKind.ERROR),
            (ErrorKind.FORBIDDEN, True, ErrorKind.ERROR),
            (ErrorKind.BAD_REQUEST, True, ErrorKind.ERROR),
            (ErrorKind.OVERLOADED, True, ErrorKind.ERROR),
            (ErrorKind.TIMEOUT, True, ErrorKind.TIMEOUT),
            (ErrorKind.NOT_FOUND, False, ErrorKind.NOT_FOUND),
        ],
    )
    def test_shown_kind(
        self, kind: ErrorKind, shown: ErrorKind, *, opaque: bool
    ) -> None:
        assert shown_kind(kind, opaque=opaque) is shown


class TestWrapperTag:
    """No wrapper element is offered where the markup before the site forbids one."""

    @pytest.mark.parametrize(
        ("preceding", "tag"),
        [
            ("", "div"),
            ("<section><p>", "div"),
            ("<table>", None),
            ("<table><tbody>", None),
            ("<table><tr><td>", "div"),
            ("<table></table>", "div"),
            ("<ul>", None),
            ("<ul><li>", "div"),
            ("<select><optgroup>", None),
            ("<svg><g>", None),
            ("<svg></svg>", "div"),
            ("<!-- <table> -->", "div"),
            ("<table><br>", None),
            ("<table><col/>", None),
            ("<div/><ol>", None),
            ("</p><div>", "div"),
            ("<TABLE><TR>", None),
            ("<table><tr><td></td></tr></table><p>", "div"),
            ("<table><script>if (a<b && c>d) {}</script>", None),
            ("<script>var t = '<table>';</script>", "div"),
            ("<div title='<table>'>", "div"),
            ('<div title="a>b"><ul>', None),
            ("<svg><foreignObject><div>", "div"),
            ("<svg><foreignObject></foreignObject><g>", None),
            ("<table><tr><td><svg></svg></td>", None),
            ("<ul><path />", None),
        ],
    )
    def test_wrapper_tag(self, preceding: str, tag: str | None) -> None:
        assert wrapper_tag(preceding) == tag

    def test_the_tag_given_is_returned(self) -> None:
        assert wrapper_tag("<p>", "span") == "span"


class TestMarkup:
    """The wrapper and the DEBUG detail escape everything they print."""

    def test_the_trace_formats_the_line_and_the_traceback(self) -> None:
        try:
            _raise(ValueError("x"))()
        except ValueError as exc:
            trace = Trace.of(exc)
        assert trace.line == "ValueError: x"
        assert trace.text.startswith("Traceback (most recent call last):")
        assert trace.text.endswith("ValueError: x\n")

    def test_wrapped_carries_the_state_and_the_kind(self) -> None:
        html = wrapped("div", "b-1-1", ErrorKind.ERROR, "<p>x</p>")
        assert html == (
            '<div id="b-1-1" data-next-boundary="b-1-1" data-next-state="error" '
            'data-next-error="error"><p>x</p></div>'
        )

    def test_wrapped_prints_the_digest_when_given(self) -> None:
        html = wrapped("div", "b", ErrorKind.ERROR, "", '"x"')
        assert 'data-next-error-digest="&quot;x&quot;"' in html

    def test_wrapped_names_the_source_when_given(self) -> None:
        html = wrapped("div", "b", ErrorKind.ERROR, "", source="pages/<x>/error.djx")
        assert 'data-next-boundary-source="pages/&lt;x&gt;/error.djx"' in html

    def test_a_contained_failure_names_its_source_under_debug(self) -> None:
        boundary = Boundary("a", "page", source="pages/error.djx")
        with override_settings(DEBUG=True):
            html = contain(boundary, _raise(ValueError()), _Fallback())
        assert 'data-next-boundary-source="pages/error.djx"' in html

    def test_a_contained_failure_hides_its_source_outside_debug(self) -> None:
        boundary = Boundary("a", "page", source="pages/error.djx")
        with override_settings(DEBUG=False):
            html = contain(boundary, _raise(ValueError()), _Fallback())
        assert "data-next-boundary-source" not in html

    def test_debug_detail_escapes_the_exception_and_the_traceback(self) -> None:
        try:
            _raise(ValueError("<script>alert(1)</script>"))()
        except ValueError as exc:
            detail = debug_detail(Trace.of(exc))
        assert detail.startswith("<details open data-next-error-detail><summary>")
        assert "<script>" not in detail
        assert "ValueError: &lt;script&gt;alert(1)&lt;/script&gt;" in detail
        assert "<pre>Traceback (most recent call last):" in detail


class TestContain:
    """A guarded subtree renders, or its failure is contained behind the error UI."""

    def test_a_success_returns_the_render(self) -> None:
        fallback = _Fallback()
        assert contain(
            Boundary("card", "component"), lambda: "<b>ok</b>", fallback
        ) == ("<b>ok</b>")
        assert fallback.calls == []
        assert degraded() is False

    def test_a_failure_renders_the_error_ui_in_its_wrapper(self) -> None:
        fallback = _Fallback()
        html = contain(Boundary("card", "component"), _raise(ValueError("x")), fallback)
        ((error, kind),) = fallback.calls
        assert kind is ErrorKind.ERROR
        assert type(error) is BoundaryError
        assert error.kind is ErrorKind.ERROR
        assert error.status is None
        assert error.retry is True
        assert len(error.digest) == 12
        assert int(error.digest, 16) >= 0
        assert html.startswith('<div id="b-')
        assert (
            'data-next-state="error" data-next-error="error"><p>sorry</p></div>' in html
        )
        assert "data-next-error-digest" not in html
        assert degraded() is True

    def test_retry_follows_the_fallback(self) -> None:
        fallback = _Fallback(retry=False)
        contain(Boundary("card", "component"), _raise(ValueError()), fallback)
        assert fallback.calls[0][0].retry is False

    @pytest.mark.parametrize(
        "exc",
        [Http404(), PermissionDenied(), BadRequest(), RequestAborted()],
        ids=lambda exc: type(exc).__name__,
    )
    def test_an_intended_exception_passes_before_the_first_byte(
        self, exc: Exception
    ) -> None:
        fallback = _Fallback()
        with pytest.raises(type(exc)):
            contain(Boundary("card", "component"), _raise(exc), fallback)
        assert fallback.calls == []
        assert degraded() is False

    def test_a_base_exception_passes_and_undoes_the_journal(self) -> None:
        collector = StaticCollector()

        def render() -> str:
            collector.add(StaticAsset(url="/failed.css", kind="css"))
            raise KeyboardInterrupt

        with pytest.raises(KeyboardInterrupt):
            contain(
                Boundary("card", "component", collector=collector), render, _Fallback()
            )
        assert list(collector.assets_in_slot("styles")) == []
        assert collector._journal is None

    def test_an_intended_exception_after_the_first_byte_maps_to_its_kind(self) -> None:
        fallback = _Fallback()
        boundary = Boundary("card", "component", started=True)
        html = contain(boundary, _raise(Http404()), fallback)
        error, kind = fallback.calls[0]
        assert kind is ErrorKind.NOT_FOUND
        assert error.kind is ErrorKind.NOT_FOUND
        assert error.status == 404
        assert 'data-next-error="not_found"' in html

    def test_opaque_kinds_hide_the_kind_but_the_fallback_reads_it(self) -> None:
        fallback = _Fallback()
        with _errors(KINDS="opaque"):
            html = contain(
                Boundary("card", "component", started=True),
                _raise(PermissionDenied()),
                fallback,
            )
        error, kind = fallback.calls[0]
        assert kind is ErrorKind.FORBIDDEN
        assert error.kind is ErrorKind.ERROR
        assert error.status is None
        assert 'data-next-error="error"' in html

    @pytest.mark.parametrize(
        "exc", [SuspiciousOperation("odd"), MultiPartParserError("odd")]
    )
    def test_a_suspicious_operation_after_the_first_byte_is_logged_as_django_does(
        self, exc: Exception, caplog: pytest.LogCaptureFixture
    ) -> None:
        name = f"django.security.{type(exc).__name__}"
        with caplog.at_level(logging.ERROR, name):
            contain(
                Boundary("card", "component", started=True), _raise(exc), _Fallback()
            )
        records = [record for record in caplog.records if record.name == name]
        assert [record.getMessage() for record in records] == ["odd"]
        assert records[0].status_code == 400

    def test_a_bad_request_is_not_a_security_event(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.ERROR):
            contain(
                Boundary("card", "component", started=True),
                _raise(BadRequest("odd")),
                _Fallback(),
            )
        assert not any(r.name.startswith("django.security") for r in caplog.records)

    def test_no_tag_stamps_the_root_of_the_error_ui(self) -> None:
        html = contain(
            Boundary("row", "component"),
            _raise(ValueError()),
            _Fallback("<tr><td>x</td></tr>", tag=None),
        )
        assert html.startswith('<tr id="b-')
        assert html.endswith('data-next-error="error"><td>x</td></tr>')


class TestNesting:
    """The innermost boundary catches first, and the render shares one state."""

    def test_the_innermost_boundary_catches_and_the_outer_one_renders(self) -> None:
        outer, middle, inner = _Fallback("O"), _Fallback("M"), _Fallback("I")

        leaf = _raise(ValueError("leaf"))

        def middle_render() -> str:
            return "[" + contain(Boundary("inner", "component"), leaf, inner) + "]"

        def outer_render() -> str:
            return (
                "("
                + contain(Boundary("middle", "component"), middle_render, middle)
                + ")"
            )

        html = contain(Boundary("outer", "component"), outer_render, outer)
        assert html.startswith('([<div id="b-')
        assert html.endswith(">I</div>])")
        assert len(inner.calls) == 1
        assert middle.calls == []
        assert outer.calls == []

    def test_a_failing_error_ui_reaches_the_next_boundary_up(self) -> None:
        outer = _Fallback("O")
        inner = _Fallback(render=lambda _error, _kind: _raise(KeyError("ui"))())

        def outer_render() -> str:
            return contain(Boundary("inner", "component"), _raise(ValueError()), inner)

        html = contain(Boundary("outer", "component"), outer_render, outer)
        assert html.endswith(">O</div>")
        assert len(outer.calls) == 1
        assert len(inner.calls) == 1

    def test_the_same_name_is_not_caught_by_itself_inside_its_error_ui(self) -> None:
        def own_error_ui(error: BoundaryError, kind: ErrorKind) -> str:
            return contain(
                Boundary("card", "component"), _raise(KeyError("again")), own
            )

        own = _Fallback(render=own_error_ui)
        with pytest.raises(KeyError, match="again"):
            contain(Boundary("card", "component"), _raise(ValueError()), own)
        assert len(own.calls) == 1

    def test_another_name_inside_an_error_ui_still_catches(self) -> None:
        def error_ui(error: BoundaryError, kind: ErrorKind) -> str:
            return contain(Boundary("other", "component"), _raise(KeyError()), other)

        other = _Fallback("X")
        html = contain(
            Boundary("card", "component"),
            _raise(ValueError()),
            _Fallback(render=error_ui),
        )
        assert ">X</div></div>" in html

    def test_one_render_probes_the_aliases_once_and_numbers_its_boundaries(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        probes: list[int] = []

        def probe() -> tuple[str, ...]:
            probes.append(1)
            return ()

        monkeypatch.setattr(boundaries, "probe_atomic_aliases", probe)

        def outer_render() -> str:
            first = contain(
                Boundary("a", "component"), _raise(ValueError()), _Fallback()
            )
            second = contain(
                Boundary("b", "component"), _raise(ValueError()), _Fallback()
            )
            return first + second

        html = contain(Boundary("outer", "component"), outer_render, _Fallback())
        ids = [part.split('"')[0] for part in html.split('data-next-boundary="')[1:]]
        assert len(probes) == 1
        assert [identifier.rsplit("-", 1)[1] for identifier in ids] == ["1", "2"]
        assert ids[0].rsplit("-", 1)[0] == ids[1].rsplit("-", 1)[0]


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

        def error_ui(error: BoundaryError, kind: ErrorKind) -> str:
            collector.add(StaticAsset(url="/error.css", kind="css"))
            return "x"

        contain(
            Boundary("card", "component", collector=collector),
            render,
            _Fallback(render=error_ui),
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

        contain(Boundary("card", "component", collector=collector), render, _Fallback())
        assert [a.url for a in collector.assets_in_slot("styles")] == ["/kept.css"]
        assert collector._journal is None

    def test_an_inner_failure_keeps_the_outer_assets(self) -> None:
        collector = StaticCollector()

        def outer_render() -> str:
            collector.add(StaticAsset(url="/outer.css", kind="css"))
            return contain(
                Boundary("inner", "component", collector=collector),
                _raise(ValueError()),
                _Fallback(),
            )

        contain(
            Boundary("outer", "component", collector=collector),
            outer_render,
            _Fallback(),
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


def _error_ui_writes(name: str) -> _Fallback:
    def render(error: BoundaryError, kind: ErrorKind) -> str:
        Group.objects.create(name=name)
        return str(Group.objects.count())

    return _Fallback(render=render)


@pytest.mark.django_db()
class TestSavepointInsideAnAtomicBlock:
    """Inside a request transaction the failed subtree rolls back to its savepoint."""

    def test_the_failed_writes_roll_back_and_the_error_ui_writes_stay(self) -> None:
        with transaction.atomic():
            Group.objects.create(name="before")
            html = contain(
                Boundary("card", "component"),
                _write_then_fail("one", "two"),
                _error_ui_writes("error-ui"),
            )
            names = set(Group.objects.values_list("name", flat=True))
        assert names == {"before", "error-ui"}
        assert ">2</div>" in html

    def test_a_query_free_subtree_issues_no_statement(self) -> None:
        with transaction.atomic(), CaptureQueriesContext(connection) as queries:
            contain(Boundary("card", "component"), _raise(ValueError()), _Fallback())
            contain(Boundary("card", "component"), lambda: "ok", _Fallback())
        assert queries.captured_queries == []

    def test_a_kept_subtree_keeps_its_writes(self) -> None:
        with transaction.atomic():
            contain(Boundary("card", "component"), _write("kept"), _Fallback())
            assert Group.objects.filter(name="kept").exists()

    def test_reading_rows_inside_a_boundary_share_its_one_savepoint(self) -> None:
        def row() -> str:
            list(Group.objects.all())
            return "r"

        def page() -> str:
            return "".join(
                contain(Boundary("row", "component"), row, _Fallback())
                for _ in range(100)
            )

        with transaction.atomic(), CaptureQueriesContext(connection) as queries:
            contain(Boundary("page", "page"), page, _Fallback())
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
            return contain(Boundary("row", "component"), broken_row, _Fallback("row"))

        with transaction.atomic():
            html = contain(Boundary("page", "page"), page, _Fallback("<p>page</p>"))
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
        html = contain(Boundary("card", "component"), card, _Fallback("<p>card</p>"))
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
            html = contain(Boundary("page", "page"), render, _Fallback("<p>page</p>"))
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
            return contain(Boundary("card", "component"), card, _Fallback())

        with CaptureQueriesContext(connection) as queries:
            contain(Boundary("page", "page"), page, _Fallback())
        assert _savepoints(queries) == []
        assert len(queries.captured_queries) == 2


def test_a_render_without_queries_opens_no_connection() -> None:
    held: list[list[str]] = []

    def render() -> None:
        with render_boundaries():
            contain(
                Boundary("page", "page"),
                lambda: contain(Boundary("row", "component"), lambda: "r", _Fallback()),
                _Fallback(),
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
            Boundary("card", "component"),
            _write_then_fail("one", "two"),
            _error_ui_writes("error-ui"),
        )
        assert list(Group.objects.values_list("name", flat=True)) == ["error-ui"]

    def test_atomic_false_reads_a_block_opened_inside_the_render(self) -> None:
        def nested() -> str:
            with transaction.atomic():
                return contain(
                    Boundary("inner", "component"),
                    _write_then_fail("inner"),
                    _Fallback(),
                )

        with _errors(ATOMIC=False):
            contain(Boundary("outer", "component"), nested, _Fallback())
        assert list(Group.objects.values_list("name", flat=True)) == []

    def test_atomic_false_leaves_the_writes_of_autocommit(self) -> None:
        with _errors(ATOMIC=False):
            contain(Boundary("card", "component"), _write_then_fail("one"), _Fallback())
        assert list(Group.objects.values_list("name", flat=True)) == ["one"]

    def test_a_read_only_subtree_opens_no_transaction(self) -> None:
        def render() -> str:
            list(Group.objects.all())
            raise ValueError

        with CaptureQueriesContext(connection) as queries:
            contain(Boundary("card", "component"), render, _Fallback())
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
        with _errors(ATOMIC=False):
            protection = protect()
            Group.objects.create(name="one")
            protection.undo()
        assert list(Group.objects.values_list("name", flat=True)) == ["one"]


class TestProtectProbes:
    """A protection inside a render shares the probe the boundaries of it share."""

    @pytest.fixture()
    def probes(self, monkeypatch: pytest.MonkeyPatch) -> list[int]:
        found: list[int] = []
        monkeypatch.setattr(
            boundaries, "probe_atomic_aliases", lambda: found.append(1) or ()
        )
        return found

    def test_outside_a_render_it_probes_for_itself(self, probes: list[int]) -> None:
        protect().keep()
        assert probes == [1]
        assert transactions._local.session is None

    def test_inside_a_render_it_reuses_the_probe(self, probes: list[int]) -> None:
        with render_boundaries():
            protect().keep()
            protect().undo()
        assert probes == [1]

    def test_atomic_false_probes_again_for_each(self, probes: list[int]) -> None:
        with _errors(ATOMIC=False), render_boundaries():
            protect().keep()
            protect().keep()
        assert probes == [1, 1, 1]


class TestSettings:
    """`DEFER["ERRORS"]` switches raising, reporting and the DEBUG detail."""

    def test_raise_reraises_after_the_rollback(self) -> None:
        collector = StaticCollector()

        def render() -> str:
            collector.add(StaticAsset(url="/failed.css", kind="css"))
            raise ValueError(LOUD)

        fallback = _Fallback()
        with _errors(RAISE=True), pytest.raises(ValueError, match="loud"):
            contain(
                Boundary("card", "component", collector=collector), render, fallback
            )
        assert fallback.calls == []
        assert list(collector.assets_in_slot("styles")) == []

    def test_a_misfit_value_reads_as_its_default(self) -> None:
        fallback = _Fallback()
        with _errors(RAISE="yes"):
            contain(Boundary("card", "component"), _raise(ValueError()), fallback)
        assert len(fallback.calls) == 1

    def test_errors_that_are_no_mapping_read_as_the_defaults(self) -> None:
        fallback = _Fallback()
        with override_next_settings(DEFER={"ERRORS": "loud"}):
            contain(Boundary("card", "component"), _raise(ValueError()), fallback)
            contain(Boundary("card", "component"), _raise(ValueError()), fallback)
        assert len(fallback.calls) == 2

    def test_the_reporter_replaces_the_log_and_names_the_digest(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        fallback = _Fallback()
        with (
            _errors(REPORTER=f"{__name__}.report"),
            caplog.at_level(logging.ERROR, "next.boundaries"),
        ):
            for _ in range(2):
                contain(
                    Boundary("card", "component"), _raise(ValueError("r")), fallback
                )
        assert [error.digest for error, _kind in fallback.calls] == [
            "custom-digest",
            "custom-digest",
        ]
        exc, kwargs = REPORTED[0]
        assert str(exc) == "r"
        assert kwargs == {
            "name": "card",
            "addressing": "component",
            "kind": ErrorKind.ERROR,
            "request": None,
        }
        assert not [r for r in caplog.records if r.name == "next.boundaries"]

    @pytest.mark.parametrize(
        ("dotted", "logged"),
        [
            (f"{__name__}.missing", ImproperlyConfigured),
            (f"{__name__}.NOT_CALLABLE", ImproperlyConfigured),
            (f"{__name__}.report_raising", RuntimeError),
        ],
    )
    def test_a_failing_reporter_falls_back_to_the_default_digest_and_log(
        self, dotted: str, logged: type[Exception], caplog: pytest.LogCaptureFixture
    ) -> None:
        fallback = _Fallback()
        with (
            _errors(REPORTER=dotted),
            caplog.at_level(logging.ERROR, "next.boundaries"),
        ):
            contain(Boundary("card", "component"), _raise(ValueError("x")), fallback)
        reporter_record, failure_record = [
            r for r in caplog.records if r.name == "next.boundaries"
        ]
        assert reporter_record.exc_info is not None
        assert reporter_record.exc_info[0] is logged
        assert "default digest and logging stand in" in reporter_record.getMessage()
        digest = fallback.calls[0][0].digest
        assert failure_record.digest == digest
        assert len(digest) == 12

    def test_a_reporter_answering_a_misfit_digest_is_logged_and_falls_back(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        fallback = _Fallback()
        with (
            _errors(REPORTER=f"{__name__}.report_empty"),
            caplog.at_level(logging.ERROR, "next.boundaries"),
        ):
            contain(Boundary("card", "component"), _raise(ValueError()), fallback)
        answer_record, failure_record = [
            r for r in caplog.records if r.name == "next.boundaries"
        ]
        assert "rather than a digest string" in answer_record.getMessage()
        assert failure_record.digest == fallback.calls[0][0].digest
        assert len(fallback.calls[0][0].digest) == 12

    def test_a_reporter_answering_none_has_no_reference_and_the_default_logs(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        fallback = _Fallback()
        with (
            _errors(REPORTER=f"{__name__}.report_none"),
            caplog.at_level(logging.ERROR, "next.boundaries"),
        ):
            contain(Boundary("card", "component"), _raise(ValueError()), fallback)
        (failure_record,) = [r for r in caplog.records if r.name == "next.boundaries"]
        assert "failed and rendered its error.djx" in failure_record.getMessage()
        assert failure_record.exc_info is not None
        assert failure_record.digest == fallback.calls[0][0].digest
        assert len(fallback.calls[0][0].digest) == 12

    def test_debug_adds_the_message_the_traceback_and_the_detail(self) -> None:
        fallback = _Fallback()
        with override_settings(DEBUG=True):
            html = contain(
                Boundary("card", "component"),
                _raise(ValueError("<script>x</script>")),
                fallback,
            )
        error = fallback.calls[0][0]
        assert isinstance(error, DebugBoundaryError)
        assert error.message == "ValueError: <script>x</script>"
        assert "Traceback" in error.traceback
        assert f'data-next-error-digest="{error.digest}"' in html
        assert html.endswith("</pre></details></div>")
        assert "<script>x" not in html
        assert html.count("Traceback (most recent call last)") == 1

    def test_debug_detail_off_keeps_only_the_author_markup(self) -> None:
        with override_settings(DEBUG=True), _errors(DEBUG_DETAIL=False):
            html = contain(
                Boundary("card", "component"), _raise(ValueError()), _Fallback()
            )
        assert "data-next-error-detail" not in html
        assert html.endswith("<p>sorry</p></div>")


class TestReporting:
    """A contained failure is logged at a bounded rate and announced to receivers."""

    def test_the_failure_is_logged_once_per_quiet_period_with_its_digest(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        fallback = _Fallback()
        with caplog.at_level(logging.ERROR, "next.boundaries"):
            for _ in range(2):
                contain(Boundary("card", "component"), _raise(ValueError()), fallback)
        (record,) = [r for r in caplog.records if r.name == "next.boundaries"]
        assert record.digest == fallback.calls[0][0].digest
        assert record.exc_info is not None
        assert record.exc_info[0] is ValueError
        assert "component 'card'" in record.getMessage()

    def test_every_digest_shown_is_logged_the_quiet_ones_at_debug(
        self, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        now = [1000.0]
        monkeypatch.setattr("next.diagnostics.monotonic", lambda: now[0])
        fallback = _Fallback()
        with caplog.at_level(logging.DEBUG, "next.boundaries"):
            for _ in range(3):
                contain(Boundary("card", "component"), _raise(ValueError()), fallback)
            now[0] += QUIET_PERIOD
            contain(Boundary("card", "component"), _raise(ValueError()), fallback)
        records = [r for r in caplog.records if r.name == "next.boundaries"]
        assert [r.digest for r in records] == [e.digest for e, _k in fallback.calls]
        assert [r.levelno for r in records] == [
            logging.ERROR,
            logging.DEBUG,
            logging.DEBUG,
            logging.ERROR,
        ]
        assert (
            records[3]
            .getMessage()
            .endswith(
                "The same failure occurred 2 more times since it was last logged."
            )
        )

    def test_the_signal_carries_the_failure(self) -> None:
        request = object()
        with capture_signals(boundary_failed) as recorder:
            html = contain(
                Boundary("card", "component", request=request),
                _raise(ValueError("x")),
                _Fallback(),
            )
        event = recorder.first_for(boundary_failed)
        assert event.sender is contain
        assert event.kwargs["name"] == "card"
        assert event.kwargs["addressing"] == "component"
        assert event.kwargs["kind"] is ErrorKind.ERROR
        assert event.kwargs["request"] is request
        assert str(event.kwargs["exception"]) == "x"
        assert f'data-next-boundary="{event.kwargs["id"]}"' in html
        assert len(event.kwargs["digest"]) == 12

    def test_no_listener_sends_nothing(self) -> None:
        with (
            patch.object(boundary_failed, "has_listeners", return_value=False),
            patch.object(boundary_failed, "send_robust") as send,
        ):
            contain(Boundary("card", "component"), _raise(ValueError()), _Fallback())
        send.assert_not_called()

    def test_a_raising_receiver_never_escapes_the_boundary(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        def receiver(**kwargs: object) -> None:
            raise KeyError(LOUD)

        collector = StaticCollector()
        boundary_failed.connect(receiver, sender=contain, weak=False)
        try:
            with caplog.at_level(logging.ERROR, "django.dispatch"):
                html = contain(
                    Boundary("card", "component", collector=collector),
                    _raise(ValueError()),
                    _Fallback(),
                )
        finally:
            boundary_failed.disconnect(receiver, sender=contain)
        assert html.endswith("<p>sorry</p></div>")
        assert collector._journal is None
        assert transactions._local.session is None
        assert any(r.exc_info and r.exc_info[0] is KeyError for r in caplog.records)


def test_the_aggregate_re_exports_the_signal() -> None:
    assert framework_signals.boundary_failed is boundary_failed


class TestAdoptedRoot:
    """Without a wrapper the boundary stamps the one root element of its error UI."""

    @pytest.mark.parametrize(
        ("body", "stamped"),
        [
            ("<tr><td>x</td></tr>", f"<tr {STAMP}><td>x</td></tr>"),
            ("  <tr class='a>b'></tr>\n", f"  <tr {STAMP} class='a>b'></tr>\n"),
            ("<!-- note --><li>x</li>", f"<!-- note --><li {STAMP}>x</li>"),
            ("<col span=2>", f"<col {STAMP} span=2>"),
            ("<rect/>", f"<rect {STAMP}/>"),
            ("<script>a<b</script>", f"<script {STAMP}>a<b</script>"),
            ("<li><br><img src=x></li>", f"<li {STAMP}><br><img src=x></li>"),
            ("<tr><script>a<b</script></tr>", f"<tr {STAMP}><script>a<b</script></tr>"),
        ],
    )
    def test_the_single_root_carries_the_wrapper_attributes(
        self, body: str, stamped: str
    ) -> None:
        assert wrapped(None, "b", ErrorKind.ERROR, body) == stamped

    @pytest.mark.parametrize(
        ("body", "found"),
        [
            ("", "0 root elements"),
            ("<!-- only a note -->", "0 root elements"),
            ("<tr></tr><tr></tr>", "2 root elements"),
            ("text", "loose text"),
            ("<tr></tr>tail", "loose text"),
            ("lead<tr></tr>", "loose text"),
            ("</div><tr></tr>", "loose text"),
        ],
    )
    def test_any_other_shape_leaves_an_empty_placeholder_and_a_warning(
        self, body: str, found: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING, "next.boundaries"):
            html = wrapped(None, "b", ErrorKind.ERROR, body)
        assert html == f"<template {STAMP}></template>"
        (record,) = [r for r in caplog.records if r.name == "next.boundaries"]
        assert found in record.getMessage()
        assert "a boundary" in record.getMessage()

    def test_debug_names_the_site_in_the_placeholder(self) -> None:
        with override_settings(DEBUG=True):
            html = contain(
                Boundary("row", "component", site="the call in page.djx, line 3"),
                _raise(ValueError("x")),
                _Fallback("<tr></tr><tr></tr>", tag=None),
            )
        assert html.startswith('<template id="b-')
        assert "data-next-error-digest=" in html
        assert (
            "The error UI of the call in page.djx, line 3 renders 2 root elements"
        ) in html
        assert "ValueError: x" in html.split("data-next-error-detail=")[1]

    def test_debug_without_the_detail_keeps_the_site_message(self) -> None:
        with override_settings(DEBUG=True), _errors(DEBUG_DETAIL=False):
            html = contain(
                Boundary("row", "component"),
                _raise(ValueError("x")),
                _Fallback("", tag=None),
            )
        detail = html.split('data-next-error-detail="')[1]
        assert detail.startswith("The error UI of the component &#x27;row&#x27;")
        assert "Traceback" not in detail

    def test_debug_puts_the_detail_on_the_adopted_root(self) -> None:
        with override_settings(DEBUG=True):
            html = contain(
                Boundary("row", "component"),
                _raise(ValueError("<b>")),
                _Fallback("<tr><td>E</td></tr>", tag=None),
            )
        assert html.startswith('<tr id="b-')
        assert html.endswith("><td>E</td></tr>")
        assert "<details" not in html
        assert "ValueError: &lt;b&gt;" in html

    def test_production_leaves_the_placeholder_without_a_message(self) -> None:
        html = contain(
            Boundary("row", "component"), _raise(ValueError()), _Fallback("", tag=None)
        )
        assert html.startswith('<template id="b-')
        assert "data-next-error-detail" not in html


class TestDetailPlacement:
    """The DEBUG detail is an element only inside a wrapper that keeps it in place."""

    @pytest.mark.parametrize(("tag", "element"), [("div", True), ("li", True)])
    def test_a_flow_wrapper_holds_the_element(self, tag: str, *, element: bool) -> None:
        with override_settings(DEBUG=True):
            html = contain(
                Boundary("card", "component"), _raise(ValueError()), _Fallback(tag=tag)
            )
        assert ("<details open data-next-error-detail>" in html) is element

    @pytest.mark.parametrize("tag", ["tr", "span", "tbody"])
    def test_another_wrapper_carries_the_detail_as_an_attribute(self, tag: str) -> None:
        with override_settings(DEBUG=True):
            html = contain(
                Boundary("card", "component"),
                _raise(ValueError("x")),
                _Fallback("<td>E</td>", tag=tag),
            )
        assert "<details" not in html
        assert html.startswith(f'<{tag} id="b-')
        assert 'data-next-error-detail="Traceback' in html
        assert html.endswith(f"><td>E</td></{tag}>")


class TestSharedMarkup:
    """The helpers a deferred wrapper builds its markup with, as an inline one does."""

    def test_identity_carries_the_id_under_both_names(self) -> None:
        assert identity('b"1') == ' id="b&quot;1" data-next-boundary="b&quot;1"'

    def test_adopt_stamps_the_one_root(self) -> None:
        assert adopt(' id="b"', "<tr></tr>", site="s") == '<tr id="b"></tr>'

    @pytest.mark.parametrize(
        ("body", "found"),
        [
            ("<li>a</li>", None),
            ("<li>a</li><li>b</li>", "2 root elements"),
            ("", "0 root elements"),
            ("text", "loose text"),
        ],
    )
    def test_misrooted_names_how_a_body_misses_one_root(
        self, body: str, found: str | None
    ) -> None:
        assert misrooted(body) == found

    def test_failed_outside_debug_shows_the_kind_alone(self) -> None:
        error = BoundaryError(ErrorKind.NOT_FOUND, "d1", 404, retry=False)
        assert failed("div", ' id="b"', error, "E", site="s") == (
            '<div id="b" data-next-state="error" data-next-error="not_found">E</div>'
        )

    @override_settings(DEBUG=True)
    def test_failed_under_debug_shows_the_digest_and_the_detail(self) -> None:
        error = DebugBoundaryError(ErrorKind.ERROR, "d1", None, False, "line", "text")
        assert failed("span", ' id="b"', error, "E", site="s") == (
            '<span id="b" data-next-state="error" data-next-error="error" '
            'data-next-error-digest="d1" data-next-error-detail="text">E</span>'
        )

    @override_settings(DEBUG=True)
    def test_the_detail_setting_hides_the_detail(self) -> None:
        error = DebugBoundaryError(ErrorKind.ERROR, "d1", None, False, "line", "text")
        with _errors(DEBUG_DETAIL=False):
            html = failed("span", "", error, "E", site="s")
        assert "data-next-error-digest" in html
        assert "data-next-error-detail" not in html


class TestMinting:
    """A bound minter names the error wrappers, the render counter standing in."""

    def test_a_minted_id_names_the_wrapper(self) -> None:
        with minting(lambda: "b-seal-7"):
            html = contain(
                Boundary("a", "component"), _raise(ValueError()), _Fallback()
            )
        assert html.startswith('<div id="b-seal-7" data-next-boundary="b-seal-7"')

    def test_a_minter_without_an_id_leaves_the_render_counter(self) -> None:
        with minting(lambda: None):
            html = contain(
                Boundary("a", "component"), _raise(ValueError()), _Fallback()
            )
        assert html.startswith('<div id="b-')
        assert '-1" data-next-boundary="b-' in html


class TestRenderBoundaries:
    """One top-level render shares its state, its settings and its alias probe."""

    def test_siblings_share_one_probe_and_one_numbering(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        probes: list[int] = []

        def probe() -> tuple[str, ...]:
            probes.append(1)
            return ()

        monkeypatch.setattr(boundaries, "probe_atomic_aliases", probe)
        with render_boundaries():
            html = "".join(
                contain(Boundary(name, "component"), _raise(ValueError()), _Fallback())
                for name in ("a", "b", "c")
            )
        ids = [part.split('"')[0] for part in html.split('data-next-boundary="')[1:]]
        assert len(probes) == 1
        assert [identifier.rsplit("-", 1)[1] for identifier in ids] == ["1", "2", "3"]

    def test_a_render_without_a_boundary_opens_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        probes: list[int] = []
        monkeypatch.setattr(
            boundaries, "probe_atomic_aliases", lambda: probes.append(1) or ()
        )
        with render_boundaries():
            pass
        assert probes == []
        assert transactions._local.session is None

    def test_the_wrappers_stay_for_the_render_and_go_with_it(self) -> None:
        with render_boundaries():
            contain(Boundary("a", "component"), lambda: "ok", _Fallback())
            assert transactions._local.session is not None
            contain(Boundary("b", "component"), lambda: "ok", _Fallback())
        assert transactions._local.session is None
        assert connection.execute_wrappers == []

    def test_a_nested_entry_joins_the_open_render(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        probes: list[int] = []
        monkeypatch.setattr(
            boundaries, "probe_atomic_aliases", lambda: probes.append(1) or ()
        )
        with render_boundaries():
            contain(Boundary("a", "component"), lambda: "ok", _Fallback())
            with render_boundaries():
                contain(Boundary("b", "component"), lambda: "ok", _Fallback())
            assert transactions._local.session is not None
        assert len(probes) == 1

    def test_another_thread_opens_a_render_of_its_own(self) -> None:
        seen: list[str] = []

        def work() -> None:
            seen.append(
                contain(Boundary("t", "component"), _raise(ValueError()), _Fallback())
            )

        with render_boundaries():
            contain(Boundary("a", "component"), lambda: "ok", _Fallback())
            thread = threading.Thread(
                target=contextvars.copy_context().run, args=(work,)
            )
            thread.start()
            thread.join()
            assert transactions._local.session is not None
        assert seen[0].startswith('<div id="b-')


class TestOwners:
    """A boundary rendering its own error UI is keyed by owner, not by name alone."""

    def test_the_same_owner_inside_its_error_ui_is_not_caught_again(self) -> None:
        owner = object()

        def own_error_ui(error: BoundaryError, kind: ErrorKind) -> str:
            return contain(
                Boundary("card", "component", owner=owner), _raise(KeyError()), own
            )

        own = _Fallback(render=own_error_ui)
        with pytest.raises(KeyError):
            contain(
                Boundary("card", "component", owner=owner), _raise(ValueError()), own
            )

    def test_a_same_named_component_of_another_scope_is_still_caught(self) -> None:
        other = _Fallback("X")

        def error_ui(error: BoundaryError, kind: ErrorKind) -> str:
            return contain(
                Boundary("card", "component", owner="scoped"), _raise(KeyError()), other
            )

        html = contain(
            Boundary("card", "component", owner="shared"),
            _raise(ValueError()),
            _Fallback(render=error_ui),
        )
        assert ">X</div></div>" in html


DEFER_URL = "/static/next/next.defer.min.js"


def _bundled() -> StaticCollector:
    return StaticCollector(bundle_url=lambda name: f"/static/next/next.{name}.min.js")


class TestRetryBundle:
    """An error UI offering a retry needs the `defer` bundle, a failed subtree's not."""

    def test_a_retry_control_in_the_error_ui_needs_it(self) -> None:
        collector = _bundled()
        html = contain(
            Boundary("card", "component", collector=collector),
            _raise(ValueError()),
            _Fallback("<b data-next-retry>again</b>"),
        )
        assert "data-next-retry" in html
        assert collector.notes(BUNDLE_NOTE) == [DEFER_URL]

    def test_a_retry_token_on_the_wrapper_needs_it(self) -> None:
        collector = _bundled()
        html = contain(
            Boundary("card", "component", collector=collector),
            _raise(ValueError()),
            _Fallback(token=' data-next-token="t"'),
        )
        assert ' data-next-token="t"' in html
        assert collector.notes(BUNDLE_NOTE) == [DEFER_URL]

    def test_an_error_ui_without_a_retry_needs_nothing(self) -> None:
        collector = _bundled()
        contain(
            Boundary("card", "component", collector=collector),
            _raise(ValueError()),
            _Fallback(),
        )
        assert collector.notes(BUNDLE_NOTE) == ()

    def test_the_need_of_a_failed_subtree_rolls_back(self) -> None:
        collector = _bundled()

        def render() -> str:
            collector.require("defer", urgent=True)
            raise ValueError

        contain(Boundary("card", "component", collector=collector), render, _Fallback())
        assert collector.notes(BUNDLE_NOTE) == ()
        assert collector.assets_in_slot("scripts") == ()

    def test_an_outer_failure_takes_back_the_inner_retry(self) -> None:
        collector = _bundled()

        def outer() -> str:
            contain(
                Boundary("inner", "component", collector=collector),
                _raise(ValueError()),
                _Fallback("<b data-next-retry>again</b>"),
            )
            raise ValueError

        contain(Boundary("outer", "component", collector=collector), outer, _Fallback())
        assert collector.notes(BUNDLE_NOTE) == ()

    @pytest.mark.parametrize(
        ("body", "needed"),
        [
            pytest.param("<button data-next-retry>again</button>", True, id="bare"),
            pytest.param('<a title="a>b" data-next-retry="1">x</a>', True, id="after"),
            pytest.param("<B class=x DATA-NEXT-RETRY/>", True, id="upper-case"),
            pytest.param("<p>Add data-next-retry to a button.</p>", False, id="prose"),
            pytest.param("<p title='data-next-retry'>x</p>", False, id="in-a-value"),
            pytest.param("<b data-next-retry-count>x</b>", False, id="longer-name"),
        ],
    )
    def test_only_a_start_tag_attribute_offers_a_retry(
        self, body: str, *, needed: bool
    ) -> None:
        collector = _bundled()
        note_retry(collector, body, "")
        assert bool(collector.notes(BUNDLE_NOTE)) is needed

    def test_a_render_without_a_collector_records_nothing(self) -> None:
        note_retry(None, "<b data-next-retry>again</b>", ' data-next-token="t"')
