import pytest
from django.http import Http404

from next.boundaries.failures import ErrorKind
from next.defer.assembly.inline import assemble, finish, prune
from next.defer.ledger import ResponseJobs
from next.defer.markers import Added, Captured, Delta, Job, Piece, Rendered
from next.defer.response import shell
from next.diagnostics import (
    degraded,
    degraded_status,
    reset_failure_logs,
    watch_degraded,
)
from next.static import StaticCollector
from next.static.assets import StaticAsset
from tests.support.boundaries import make_call, make_job, open_session, piece_at


def _ready(html: str, **kwargs: object) -> Rendered:
    return Rendered(html, Delta(), **kwargs)


def _job(
    session: ResponseJobs, rendered: Rendered, *, deadline: float | None = None
) -> Job:
    return make_job(
        session,
        deadline=deadline,
        run=lambda: rendered,
        error_ui=lambda kind: _ready(f"error:{kind}", kind=kind),
    )


def _wrapper(ident: str, tag: str | None = "div", loading: str = "L") -> str:
    return shell(ident, make_call(tag), loading)


class TestAssemble:
    """Each outcome replaces the loading UI of its wrapper, in document order."""

    def test_a_div_wrapper_settles(self) -> None:
        html = assemble(
            f"[{_wrapper('b-1-0')}]",
            [piece_at("b-1-0", _ready("<c>C</c>"))],
            StaticCollector(),
            response_id="1",
        )
        assert html == (
            '[<div id="b-1-0" data-next-boundary="b-1-0" data-next-boundary-key="k" '
            'data-next-state="ready"><c>C</c></div>]'
        )

    def test_an_adopted_root_is_replaced(self) -> None:
        html = assemble(
            f"<table>{_wrapper('b-1-0', None, '<tr><td>.</td></tr>')}</table>",
            [piece_at("b-1-0", _ready("<tr><td>R</td></tr>", kind=ErrorKind.ERROR))],
            None,
            response_id="1",
        )
        assert html == (
            '<table><tr id="b-1-0" data-next-boundary="b-1-0" '
            'data-next-boundary-key="k" data-next-state="error" '
            'data-next-error="error"><td>R</td></tr></table>'
        )

    def test_a_nested_outcome_lands_inside_its_parent_and_assets_keep_order(
        self,
    ) -> None:
        collector = StaticCollector()
        parent = Rendered(
            f"<o>{_wrapper('b-1-1')}</o>",
            Delta((Added(StaticAsset(url="/outer.css", kind="css")),)),
        )
        child = Rendered(
            "<i>I</i>", Delta((Added(StaticAsset(url="/in.css", kind="css")),))
        )
        html = assemble(
            _wrapper("b-1-0"),
            [piece_at("b-1-1", child), piece_at("b-1-0", parent)],
            collector,
            response_id="1",
        )
        assert '<o><div id="b-1-1"' in html
        assert "<i>I</i></div></o></div>" in html
        assert [asset.url for asset in collector.assets_in_slot("styles")] == [
            "/outer.css",
            "/in.css",
        ]

    def test_a_marker_of_no_queued_job_stays_text(self) -> None:
        text = _wrapper("b-1-0")
        assert assemble(text, [], None, response_id="1") == text

    def test_a_cut_wrapper_stays_text(self) -> None:
        cut = _wrapper("b-1-0").removesuffix("<!--/b-1-0-->")
        assert (
            assemble(cut, [piece_at("b-1-0", _ready("C"))], None, response_id="1")
            == cut
        )

    @pytest.mark.parametrize(
        "echo",
        [
            "<!--b-0-9:div-->x",
            "<!--b-2-0:div-->x<!--/b-2-0-->",
            "<!--b-1-5:div-->x<!--/b-1-5-->",
        ],
    )
    def test_an_echoed_marker_never_splices(self, echo: str) -> None:
        html = assemble(
            echo + _wrapper("b-1-0"),
            [piece_at("b-1-0", _ready("C"))],
            None,
            response_id="1",
        )
        assert html == echo + (
            '<div id="b-1-0" data-next-boundary="b-1-0" data-next-boundary-key="k" '
            'data-next-state="ready">C</div>'
        )

    def test_a_wrapper_inside_a_loading_ui_goes_with_it(self) -> None:
        outer = _wrapper("b-1-0", loading=_wrapper("b-1-1"))
        html = assemble(
            outer,
            [piece_at("b-1-0", _ready("C")), piece_at("b-1-1", _ready("I"))],
            None,
            response_id="1",
        )
        assert html == (
            '<div id="b-1-0" data-next-boundary="b-1-0" data-next-boundary-key="k" '
            'data-next-state="ready">C</div>'
        )

    def test_a_job_splices_once(self) -> None:
        twice = _wrapper("b-1-0") + _wrapper("b-1-0")
        html = assemble(twice, [piece_at("b-1-0", _ready("C"))], None, response_id="1")
        assert html.endswith(_wrapper("b-1-0"))
        assert html.count('data-next-state="ready"') == 1

    def test_a_wrapper_an_earlier_response_minted_is_reported(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        stale = _wrapper("b-0000beef-0")
        reset_failure_logs()
        with caplog.at_level("WARNING", logger="next.defer"):
            html = assemble(stale, [], None, response_id="1111beef")
            assemble(_wrapper("b-1111beef-0"), [], None, response_id="1111beef")
        assert html == stale
        [record] = caplog.records
        assert "{% cache %}, captured it" in record.getMessage()

    def test_a_failure_degrades(self) -> None:
        watch_degraded()
        assemble(
            _wrapper("b-1-0"),
            [piece_at("b-1-0", _ready("", degraded=True))],
            None,
            response_id="1",
        )
        assert degraded() is True

    def test_the_status_of_an_error_ui_reaches_the_response(self) -> None:
        watch_degraded()
        assemble(
            _wrapper("b-1-0"),
            [piece_at("b-1-0", _ready("", kind=ErrorKind.ERROR, status=500))],
            None,
            response_id="1",
        )
        assert degraded_status() == 500
        watch_degraded()

    def test_a_clean_page_stays_clean(self) -> None:
        watch_degraded()
        assemble(
            _wrapper("b-1-0"), [piece_at("b-1-0", _ready("C"))], None, response_id="1"
        )
        assert degraded() is False

    def test_the_first_intended_exception_raises(self) -> None:
        first, second = Http404("first"), Http404("second")
        with pytest.raises(Http404, match="first"):
            assemble(
                _wrapper("b-1-0") + _wrapper("b-1-1"),
                [
                    piece_at("b-1-0", _ready("", intended=first)),
                    piece_at("b-1-1", _ready("", intended=second)),
                ],
                None,
                response_id="1",
            )


class TestPrune:
    """A job whose wrapper the shell lost never runs."""

    def test_a_whole_shell_keeps_the_session(self) -> None:
        session = open_session()
        job = _job(session, _ready("C"))
        session.queue(job)
        assert prune(session, _wrapper(job.id)) is session

    def test_a_lost_wrapper_drops_its_job(self) -> None:
        session = open_session()
        kept, lost = _job(session, _ready("A")), _job(session, _ready("B"))
        session.queue(kept)
        session.queue(lost)
        session.threads = (Captured(object(), "state"),)
        pruned = prune(session, _wrapper(kept.id))
        assert pruned.jobs == (kept,)
        assert pruned.response_id == session.response_id
        assert pruned.deadline == session.deadline
        assert pruned.threads is session.threads


class TestFinish:
    """The view step prunes, runs and assembles in one call."""

    def test_the_page_is_assembled(self) -> None:
        session = open_session()
        job = _job(session, _ready("<c>C</c>"))
        lost = _job(session, _ready("never"))
        session.queue(job)
        session.queue(lost)
        ran: list[int] = []

        def run(live: ResponseJobs) -> list[list[Piece]]:
            ran.append(len(live.jobs))
            return [[Piece(each, each.run())] for each in live.jobs if each.run]

        html = finish(f"<p>{_wrapper(job.id)}</p>", session, None, run)
        assert ran == [1]
        assert html.startswith(f'<p><div id="{job.id}"')
        assert html.endswith('data-next-state="ready"><c>C</c></div></p>')
