from dataclasses import dataclass
from unittest.mock import patch

import pytest
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.http import Http404
from django.template import Context, Engine, Template
from django.test import override_settings

from next.boundaries.calls import DeferredCall
from next.boundaries.failures import BoundaryFailure, ErrorKind
from next.defer import render as render_module
from next.defer.components import SiteCapture
from next.defer.markers import Added, Delta, Merged, Noted
from next.defer.render import Frame, Recording, current_frame, error_ui, run, unframed
from next.diagnostics import degraded_status, mark_degraded, watch_degraded
from next.seeding import COLLECTOR_KEY
from next.static import StaticCollector
from next.static.assets import StaticAsset
from next.static.runtime import BUNDLE_NOTE
from next.static.serializers import JsonJsContextSerializer
from tests.support.boundaries import make_call, open_session


def _asset(url: str) -> StaticAsset:
    return StaticAsset(url=url, kind="css")


@dataclass
class _Renderer:
    """A call renderer whose parts answer fixed markup or raise."""

    body_error: Exception | None = None
    error_error: Exception | None = None
    shown: BoundaryFailure | None = None
    write: bool = False
    error_markup: str = ""

    def loading(self, context: Context) -> tuple[str, BoundaryFailure | None]:
        return "L", None

    def body(self, context: Context) -> tuple[str, BoundaryFailure | None]:
        if self.body_error is not None:
            raise self.body_error
        if self.write:
            context["request"].session["k"] = 1
        frame = current_frame()
        depth = None if frame is None else frame.depth
        return f"{context['v']}:{depth}:{context.get('stamp')}", self.shown

    def error(self, context: Context, kind: ErrorKind) -> tuple[str, BoundaryFailure]:
        if self.error_error is not None:
            raise self.error_error
        markup = self.error_markup or f"E:{kind}"
        return markup, BoundaryFailure(kind, "d1", kind.status, retry=False)


def _call(renderer: _Renderer) -> DeferredCall:
    return make_call("div", "k", renderer=renderer)


def _capture(**data: object) -> SiteCapture:
    values = {"v": "V", **data}
    return SiteCapture(values, None, autoescape=True, use_l10n=None, use_tz=None)


def stamp(request: object) -> dict[str, str]:
    return {"stamp": "page"}


def engine_stamp(request: object) -> dict[str, str]:
    return {"stamp": "engine", "engine": "yes"}


def _framed() -> Frame:
    return Frame(open_session(), "b-x-0", 0)


class TestRecording:
    """The collector keeps the steps it took, a rollback dropping its own."""

    def test_every_accepted_change_is_a_step(self) -> None:
        collector = Recording()
        serializer = JsonJsContextSerializer()
        collector.add(_asset("/a.css"), prepend=True)
        collector.add(_asset("/a.css"))
        collector.note("k", 1)
        collector.add_js_context("j", 2, serializer=serializer)
        assert collector.delta() == Delta(
            (
                Added(_asset("/a.css"), prepend=True),
                Noted("k", 1),
                Merged("j", 2, serializer),
            )
        )

    def test_an_unserialisable_value_is_no_step(self) -> None:
        collector = Recording()
        with pytest.raises(TypeError):
            collector.add_js_context("j", object())
        assert collector.delta() == Delta()

    def test_a_rollback_drops_the_steps_since_its_mark(self) -> None:
        collector = Recording()
        collector.add(_asset("/kept.css"))
        outer = collector.mark()
        collector.add(_asset("/outer.css"))
        inner = collector.mark()
        collector.add(_asset("/inner.css"))
        collector.rollback(inner)
        collector.release()
        assert [step.asset.url for step in collector.delta().steps] == [
            "/kept.css",
            "/outer.css",
        ]
        assert outer == 0

    def test_a_rollback_without_a_mark_raises(self) -> None:
        collector = Recording()
        collector.add(_asset("/a.css"))
        with pytest.raises(RuntimeError, match="No collector mark"):
            collector.rollback(0)
        assert len(collector.delta().steps) == 1


class TestDelta:
    """A delta replays in order, the page collector's policies deciding."""

    def test_the_steps_replay_onto_the_page(self) -> None:
        page = StaticCollector()
        page.add(_asset("/page.css"))
        page.add_js_context("j", "page")
        Delta(
            (
                Added(_asset("/page.css")),
                Added(_asset("/job.css"), prepend=True),
                Noted("k", 1),
                Merged("j", "job"),
                Merged("other", "job"),
            )
        ).apply(page)
        assert [asset.url for asset in page.assets_in_slot("styles")] == [
            "/job.css",
            "/page.css",
        ]
        assert list(page.notes("k")) == [1]
        assert page.js_context() == {"j": "page", "other": "job"}


class TestRun:
    """A job renders from its capture inside its frame."""

    def test_the_body_renders_inside_the_frame(self) -> None:
        frame = _framed()
        rendered = run(_call(_Renderer()), _capture(), frame, frame.session.deadline)
        assert rendered.html == "V:0:None"
        assert rendered.kind is None
        assert rendered.failure is None
        assert rendered.degraded is False
        assert current_frame() is None

    def test_an_escaping_exception_returns_for_the_request_thread(self) -> None:
        error = Http404("gone")
        frame = _framed()
        rendered = run(_call(_Renderer(body_error=error)), _capture(), frame, 0.0)
        assert rendered.intended is error
        assert rendered.kind is ErrorKind.NOT_FOUND
        assert rendered.html == ""

    def test_a_failure_the_error_ui_showed_is_kept(self) -> None:
        shown = BoundaryFailure(ErrorKind.ERROR, "d0", None, retry=True)
        frame = _framed()
        rendered = run(
            _call(_Renderer(shown=shown)), _capture(), frame, frame.session.deadline
        )
        assert rendered.failure is shown
        assert rendered.kind is ErrorKind.ERROR

    def test_the_page_processors_rerun_after_the_engine_ones(self, rf) -> None:
        request = rf.get("/")
        template = Template(
            "", engine=Engine(context_processors=[f"{__name__}.engine_stamp"])
        )
        capture = SiteCapture(
            {"v": "V", "request": request},
            template,
            autoescape=True,
            use_l10n=None,
            use_tz=None,
        )
        frame = _framed()
        with patch("next.defer.render.context_processors", return_value=[stamp]):
            rendered = run(_call(_Renderer()), capture, frame, frame.session.deadline)
        assert rendered.html == "V:0:page"

    def test_the_call_site_values_sit_over_the_processors(self, rf) -> None:
        request = rf.get("/")
        capture = SiteCapture(
            {"v": "V", "request": request, "stamp": "call-site"},
            None,
            autoescape=True,
            use_l10n=None,
            use_tz=None,
        )
        frame = _framed()
        with patch("next.defer.render.context_processors", return_value=[stamp]):
            rendered = run(_call(_Renderer()), capture, frame, frame.session.deadline)
        assert rendered.html == "V:0:call-site"

    def test_the_processors_run_once_for_every_job_of_a_request(self, rf) -> None:
        request = rf.get("/")
        calls: list[object] = []

        def counted(seen: object) -> dict[str, str]:
            calls.append(seen)
            return {"stamp": "page"}

        renderer = _Renderer()
        frame = _framed()
        with patch("next.defer.render.context_processors", return_value=[counted]):
            htmls = [
                run(
                    _call(renderer),
                    _capture(request=request),
                    frame,
                    frame.session.deadline,
                ).html
                for _ in range(3)
            ]
            shown = error_ui(
                _call(renderer), _capture(request=request), ErrorKind.ERROR
            )
            other = run(
                _call(renderer),
                _capture(request=rf.get("/")),
                frame,
                frame.session.deadline,
            )
        assert htmls == ["V:0:page"] * 3
        assert shown.html == "E:error"
        assert other.html == "V:0:page"
        assert len(calls) == 2
        assert calls[0] is request

    @override_settings(
        MIDDLEWARE=["django.contrib.sessions.middleware.SessionMiddleware"]
    )
    def test_a_request_state_write_is_reported_on_the_result(self, rf) -> None:
        request = rf.get("/")
        request.session = SessionStore()
        frame = _framed()
        rendered = run(
            _call(_Renderer(write=True)),
            _capture(request=request),
            frame,
            frame.session.deadline,
        )
        assert rendered.effects.session_modified is True


class TestErrorUI:
    """The error UI renders without the component, a failure leaving it bare."""

    def test_the_error_ui_of_the_kind(self) -> None:
        rendered = error_ui(_call(_Renderer()), _capture(), ErrorKind.TIMEOUT)
        assert rendered.html == "E:timeout"
        assert rendered.kind is ErrorKind.TIMEOUT
        assert rendered.failure == BoundaryFailure(
            ErrorKind.TIMEOUT, "d1", None, retry=False
        )

    def test_a_failing_error_ui_leaves_the_bare_wrapper(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        renderer = _Renderer(error_error=ValueError("broken"))
        rendered = error_ui(_call(renderer), _capture(), ErrorKind.OVERLOADED)
        assert rendered.html == ""
        assert rendered.kind is ErrorKind.OVERLOADED
        assert "failed to render" in caplog.text


@dataclass
class _StatusRenderer(_Renderer):
    """A renderer whose body answers with a status, as a page error UI does."""

    def body(self, context: Context) -> tuple[str, BoundaryFailure | None]:
        mark_degraded(500)
        return "failed", BoundaryFailure(ErrorKind.ERROR, "d0", None, retry=True)


class TestStatus:
    """A status the job's error UI set comes back for the request thread to apply."""

    def test_a_job_without_a_status_carries_none(self) -> None:
        frame = _framed()

        rendered = run(_call(_Renderer()), _capture(), frame, frame.session.deadline)

        assert rendered.status is None

    def test_the_status_stays_off_the_request_until_applied(self) -> None:
        frame = _framed()
        watch_degraded()

        rendered = run(
            _call(_StatusRenderer()), _capture(), frame, frame.session.deadline
        )

        assert degraded_status() is None
        assert rendered.status == 500
        watch_degraded()


class TestUnframed:
    """A block outside every job renders a deferred call in place."""

    def test_the_frame_is_cleared_inside_and_restored_after(self, in_job) -> None:
        frame = in_job(open_session())
        with unframed():
            assert current_frame() is None
        assert current_frame() is frame


DEFER = StaticAsset(url="/static/next/next.boundaries.min.js", kind="js")
NEED = (Added(DEFER), Noted(BUNDLE_NOTE, DEFER.url))


def _page() -> StaticCollector:
    return StaticCollector(bundle_url=lambda name: f"/static/next/next.{name}.min.js")


class TestRetryNeedsTheDeferBundle:
    """An error UI a job settles with a retry puts the `defer` bundle in its delta."""

    def test_a_retry_control_in_the_error_ui_joins_the_delta(self) -> None:
        call = _call(_Renderer(error_markup="<b data-next-retry>again</b>"))
        capture = _capture(**{COLLECTOR_KEY: _page()})
        rendered = error_ui(call, capture, ErrorKind.TIMEOUT)
        assert rendered.delta.steps == NEED

    def test_an_error_ui_without_a_retry_adds_nothing(self) -> None:
        capture = _capture(**{COLLECTOR_KEY: _page()})
        rendered = error_ui(_call(_Renderer()), capture, ErrorKind.TIMEOUT)
        assert rendered.delta.steps == ()

    def test_a_retry_token_of_a_failed_job_joins_the_delta(self) -> None:
        shown = BoundaryFailure(ErrorKind.ERROR, "d0", None, retry=True)
        frame = _framed()
        capture = _capture(**{COLLECTOR_KEY: _page()})
        with patch.object(
            render_module, "retry_token", return_value=' data-next-token="t"'
        ):
            rendered = run(
                _call(_Renderer(shown=shown)), capture, frame, frame.session.deadline
            )
        assert rendered.retry == ' data-next-token="t"'
        assert rendered.delta.steps == NEED

    def test_a_job_without_a_page_collector_resolves_no_bundle(self) -> None:
        call = _call(_Renderer(error_markup="<b data-next-retry>again</b>"))
        rendered = error_ui(call, _capture(), ErrorKind.TIMEOUT)
        assert rendered.delta.steps == ()
