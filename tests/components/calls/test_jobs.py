import re
import threading
from collections.abc import Callable, Iterator
from unittest.mock import Mock, patch

import pytest
from django.contrib.sessions.backends.signed_cookies import SessionStore
from django.http import Http404, HttpRequest
from django.template import Context
from django.test import RequestFactory, override_settings

from next.components.registry import DeferredComponents
from next.defer import Addressing, DeferMode, ErrorKind
from next.diagnostics import degraded, watch_degraded
from next.suspense import boundary_rendered, boundary_side_effect
from next.suspense.assembly import assembled
from next.suspense.components import Deferring, deferring
from next.testing import boundary_errors, capture_signals, parallel_boundaries
from next.testing.boundaries import gated, in_document_order, recorded_failures
from tests.components.sites import HEAD, ComponentSite, template_engine


class TestJob:
    """A job renders through the same boundary an inline render meets."""

    def test_a_failing_job_renders_its_error_ui_and_rolls_its_assets_back(
        self, site: ComponentSite
    ) -> None:
        site.add("leaf", "x")
        site.add(
            "bad",
            "{% component 'leaf' %}{{ boom }}",
            loading="L",
            error="E:{{ error.kind }}",
        )
        site.add("good", "{% component 'leaf' %}G", loading="L")
        watch_degraded()
        html = site.finished("{% component 'good' %}{% component 'bad' %}")
        assert 'data-next-state="error" data-next-error="error">E:error</div>' in html
        assert site.styles() == ["/good.css", "/bad.css", "/leaf.css"]
        assert site.collector._journal is None
        assert degraded() is True

    def test_a_debug_failure_shows_what_the_inline_boundary_shows(
        self, site: ComponentSite
    ) -> None:
        site.add("bad", "{{ boom }}", loading="L", error="E")
        with override_settings(DEBUG=True, BASE_DIR=site.root):
            html = site.finished("{% component 'bad' %}")
        assert 'data-next-state="error" data-next-error="error" ' in html
        assert "data-next-error-digest=" in html
        assert "<details open data-next-error-detail>" in html

    def test_a_render_meeting_a_deferred_call_marks_the_project_deferring(
        self, site: ComponentSite, fresh_deferred_components: DeferredComponents
    ) -> None:
        site.add("card", "C", loading="L")
        assert fresh_deferred_components.held is False
        site.render("{% component 'card' %}")
        assert fresh_deferred_components.held is True

    def test_a_failing_job_without_error_ui_reads_as_an_error(
        self, site: ComponentSite
    ) -> None:
        site.add("bad", "{{ boom }}", loading="L")
        html = site.finished("{% component 'bad' %}")
        assert html.endswith('data-next-state="error" data-next-error="error"></div>')

    def test_the_timeout_fallback_never_runs_the_component(
        self, site: ComponentSite
    ) -> None:
        site.add(
            "slow", "{{ boom }}", loading="L", error="{{ error.kind }}|{{ props.t }}"
        )
        _, session = site.deferred("{% component 'slow' t='T' %}")
        rendered = session.jobs[0].fallback(ErrorKind.TIMEOUT)
        assert rendered.html == "timeout|T"
        assert rendered.kind is ErrorKind.TIMEOUT

    def test_an_opaque_overload_reads_as_an_error(self, site: ComponentSite) -> None:
        site.add("slow", "C", loading="L", error="{{ error.kind }}")
        _, session = site.deferred("{% component 'slow' %}")
        with override_settings(
            NEXT_FRAMEWORK={"DEFER": {"ERRORS": {"KINDS": "opaque"}}}
        ):
            rendered = session.jobs[0].fallback(ErrorKind.OVERLOADED)
        assert rendered.html == "error"
        assert rendered.kind is ErrorKind.ERROR

    def test_the_error_default_stands_in(self, site: ComponentSite) -> None:
        site.add("slow", "C", loading="L")
        _, session = site.deferred("{% component 'slow' %}")
        template = Mock()
        template.render.return_value = "OOPS"
        with (
            override_settings(NEXT_FRAMEWORK={"DEFER": {"ERROR": "oops.html"}}),
            patch("next.components.facade.get_template", return_value=template),
        ):
            rendered = session.jobs[0].fallback(ErrorKind.TIMEOUT)
        assert rendered.html == "OOPS"


class TestNested:
    """A deferred call inside a job joins it, up to `MAX_DEPTH` levels."""

    def test_a_nested_call_is_recorded(self, site: ComponentSite) -> None:
        site.add("inner", "<i>{{ v }}</i>", loading="li")
        site.add("outer", "<o>{% component 'inner' v=1 %}</o>", loading="lo")
        _, session = site.deferred("{% component 'outer' %}")
        parent = session.jobs[0]
        rendered = parent.run()
        assert [(job.parent, job.depth) for job in rendered.nested] == [(parent.id, 1)]
        assert ">li</div>" in rendered.html

    def test_past_the_depth_a_nested_call_renders_in_place(
        self, site: ComponentSite, caplog: pytest.LogCaptureFixture
    ) -> None:
        site.add("inner", "<i>{{ v }}</i>", loading="li")
        site.add("outer", "<o>{% component 'inner' v=1 %}</o>", loading="lo")
        with override_settings(DEBUG=True, NEXT_FRAMEWORK={"DEFER": {"MAX_DEPTH": 1}}):
            _, session = site.deferred("{% component 'outer' %}")
            with caplog.at_level("DEBUG", logger="next.suspense"):
                rendered = session.jobs[0].run()
                session.jobs[0].run()
        assert rendered.nested == ()
        assert 'data-next-state="ready"><i>1</i></div>' in rendered.html
        assert len([r for r in caplog.records if "MAX_DEPTH" in r.getMessage()]) == 1

    def test_a_nested_outcome_lands_inside_its_parent(
        self, site: ComponentSite
    ) -> None:
        site.add("inner", "<i>{{ v }}</i>", loading="li")
        site.add("outer", "<o>{% component 'inner' v=1 %}</o>", loading="lo")
        html = site.finished("{% component 'outer' %}")
        assert html.count('data-next-state="ready"') == 2
        assert "<o><div id=" in html
        assert "<!--" not in html
        assert site.styles() == ["/outer.css", "/inner.css"]


@pytest.fixture()
def stalled() -> Iterator[Callable[[], str]]:
    """Return a context callable that blocks until the test ends."""
    release = threading.Event()

    def stall() -> str:
        release.wait(5)
        return ""

    yield stall
    release.set()


class _Meeting:
    """Lets each of three jobs through only once all three are in flight at once."""

    def __init__(self) -> None:
        self.barrier = threading.Barrier(3, timeout=5)

    def __call__(self) -> str:
        self.barrier.wait()
        return ""


def _missing() -> str:
    raise Http404


def _write_session(request: HttpRequest) -> str:
    request.session["seen"] = True
    return ""


class TestAssembled:
    """Every job a render queued runs in parallel and lands in its wrapper whole."""

    def _assembled(
        self,
        site: ComponentSite,
        source: str,
        request: HttpRequest | None = None,
        **values: object,
    ) -> str:
        if request is not None:
            values["request"] = request
        return assembled(
            lambda: site.render(source, **values),
            collector=site.collector,
            request=request,
        )

    def test_three_slow_jobs_run_side_by_side_in_document_order(
        self, site: ComponentSite
    ) -> None:
        site.add("card", "{{ meet }}<c>{{ n }}</c>", loading="L")
        source = "".join(f"{{% component 'card' n={n} %}}" for n in (1, 2, 3))
        meeting = _Meeting()
        with parallel_boundaries():
            html = self._assembled(site, source, meet=meeting)
        assert meeting.barrier.broken is False, "the three jobs never ran at once"
        assert html.count('data-next-state="ready"') == 3
        assert [html.index(f"<c>{n}</c>") for n in (1, 2, 3)] == sorted(
            html.index(f"<c>{n}</c>") for n in (1, 2, 3)
        )
        assert "<!--" not in html
        assert site.styles() == ["/card.css"]

    def test_a_failing_job_shows_its_error_ui_beside_the_others(
        self, site: ComponentSite
    ) -> None:
        site.add("card", "<c>{{ n }}</c>", loading="L")
        site.add("bad", "{{ boom }}", loading="L", error="E:{{ error.kind }}")
        with boundary_errors("render"), parallel_boundaries():
            html = self._assembled(
                site, "{% component 'card' n=1 %}{% component 'bad' %}"
            )
        assert 'data-next-state="ready"><c>1</c>' in html
        assert 'data-next-error="error">E:error</div>' in html

    def test_a_job_past_its_timeout_settles_as_one(
        self, site: ComponentSite, stalled: Callable[[], str]
    ) -> None:
        site.add("card", "{{ stall }}C", loading="L", error="E:{{ error.kind }}")
        with parallel_boundaries():
            html = self._assembled(
                site, "{% component 'card' timeout='20ms' %}", stall=stalled
            )
        assert 'data-next-error="timeout">E:timeout</div>' in html

    def test_an_intended_exception_in_a_job_raises_on_the_request_thread(
        self, site: ComponentSite
    ) -> None:
        site.add("card", "{{ missing }}", loading="L", error="E")
        with pytest.raises(Http404):
            self._assembled(site, "{% component 'card' %}", missing=_missing)

    def test_every_settled_job_is_announced(self, site: ComponentSite) -> None:
        site.add("card", "C", loading="L")
        with capture_signals(boundary_rendered) as recorder:
            self._assembled(site, "{% component 'card' %}{% component 'card' %}")
        events = recorder.events_for(boundary_rendered)
        assert [event.kwargs["name"] for event in events] == ["card", "card"]
        first = events[0].kwargs
        assert first["state"] == "ready"
        assert first["kind"] is None
        assert first["mode"] is DeferMode.STREAM
        assert first["addressing"] is Addressing.COMPONENT
        assert first["reason"] is not None
        assert first["run_ms"] >= 0

    @override_settings(
        MIDDLEWARE=["django.contrib.sessions.middleware.SessionMiddleware"]
    )
    def test_a_session_write_in_a_job_is_reported(
        self, site: ComponentSite, rf: RequestFactory
    ) -> None:
        site.add("card", "{{ write }}C", loading="L")
        request = rf.get("/")
        request.session = SessionStore()
        with capture_signals(boundary_side_effect) as recorder:
            self._assembled(
                site,
                "{% component 'card' %}",
                request=request,
                write=lambda: _write_session(request),
            )
        [event] = recorder.events_for(boundary_side_effect)
        assert event.kwargs["kind"] == "session"
        assert event.kwargs["request"] is request

    def test_a_failure_inside_a_job_is_re_raised_by_the_test_helpers(
        self, site: ComponentSite
    ) -> None:
        site.add("bad", "{{ boom }}", loading="L", error="E")
        with recorded_failures() as failures:
            self._assembled(site, "{% component 'bad' %}")
        assert [failure.name for failure in failures] == ["bad"]

    def test_a_gate_holds_a_job_until_the_test_releases_it(
        self, site: ComponentSite
    ) -> None:
        site.add("card", "<c>C</c>", loading="L")
        result: list[str] = []
        with parallel_boundaries(), gated("card") as gate:
            worker = threading.Thread(
                target=lambda: result.append(
                    self._assembled(site, "{% component 'card' %}")
                )
            )
            worker.start()
            assert gate.wait(5) is True
            assert result == []
            gate.release()
            worker.join(5)
        assert 'data-next-state="ready"><c>C</c>' in result[0]

    def test_a_render_inside_a_job_runs_as_it_is(self, site: ComponentSite) -> None:
        inner = Mock(return_value="as is")
        site.add("outer", "{{ inner }}", loading="L")
        html = self._assembled(
            site,
            "{% component 'outer' %}",
            inner=lambda: assembled(inner, collector=None, request=None),
        )
        assert "as is" in html
        inner.assert_called_once_with()

    def test_a_render_queueing_nothing_comes_back_as_it_is(
        self, site: ComponentSite
    ) -> None:
        site.add("plain", "<p>P</p>")
        assert self._assembled(site, "{% component 'plain' %}") == "<p>P</p>"


def _jobs(held: Deferring) -> int:
    return 0 if held.session is None else len(held.session.jobs)


@pytest.fixture()
def deferring_project(fresh_deferred_components: DeferredComponents) -> None:
    fresh_deferred_components.declare()


class TestStoredRender:
    """A call whose output a storing tag keeps renders inline, never as a skeleton."""

    def test_a_call_inside_cache_settles_in_place(self, site: ComponentSite) -> None:
        site.add("card", "<c>C</c>", loading="L")
        with deferring() as held:
            html = site.render(
                "{% load cache %}{% cache 0 stored %}{% component 'card' %}"
                "{% endcache %}"
            )
        assert _jobs(held) == 0
        assert html.endswith('data-next-state="ready"><c>C</c></div>')

    def test_a_component_inside_cache_keeps_its_nested_calls_inline(
        self, site: ComponentSite
    ) -> None:
        site.add("card", "<c>C</c>", loading="L")
        site.add("outer", "<o>{% component 'card' %}</o>")
        with deferring() as held:
            html = site.render(
                "{% load cache %}{% cache 0 nested %}{% component 'outer' %}"
                "{% endcache %}{% component 'card' %}"
            )
        assert _jobs(held) == 1
        assert '<o><div id="b-' in html
        assert "<!--" in html.split("</o>")[1]

    @pytest.mark.usefixtures("deferring_project")
    def test_an_include_inside_cache_settles_in_place(
        self, site: ComponentSite
    ) -> None:
        site.add("card", "<c>C</c>", loading="L")
        engine = template_engine(
            {
                "page.html": "{% load cache %}{% cache 0 inc %}"
                "{% include 'wrap.html' %}{% include 'wrap.html' %}{% endcache %}"
                "{% include 'free.html' %}",
                "wrap.html": "<w>{% include 'part.html' %}</w>",
                "part.html": "{% component 'card' %}",
                "free.html": "{% component 'card' %}",
            },
            cached=True,
        )
        template = engine.get_template("page.html")
        for _ in range(2):
            with deferring() as held:
                template.render(Context(site.context()))
            assert _jobs(held) == 1

    @pytest.mark.usefixtures("deferring_project")
    def test_an_include_outside_cache_still_defers(self, site: ComponentSite) -> None:
        site.add("card", "<c>C</c>", loading="L")
        engine = template_engine(
            {
                "page.html": "{% load cache %}{% cache 0 other %}x{% endcache %}"
                "{% include 'part.html' %}",
                "plain.html": "{% include 'part.html' %}",
                "part.html": "{% component 'card' %}",
            },
            cached=True,
        )
        for name in ("page.html", "plain.html"):
            with deferring() as held:
                engine.get_template(name).render(Context(site.context()))
            assert _jobs(held) == 1


class TestFailureIds:
    """A contained failure reports the id of the wrapper the document carries."""

    def _ids(self, html: str) -> list[str]:
        return re.findall(r' id="(b-[0-9a-f]{8}-\d+)"', html)

    @pytest.mark.parametrize(
        ("module", "loading"),
        [
            pytest.param(HEAD + 'component.defer("inline")\n', None, id="in-place"),
            pytest.param(None, "L", id="stream-job"),
        ],
    )
    def test_each_failure_names_its_wrapper(
        self, site: ComponentSite, module: str | None, loading: str | None, request_for
    ) -> None:
        site.add("ok", "<i>ok</i>", loading=loading, module=module)
        site.add("bad", "{{ boom }}", error="E", loading=loading, module=module)
        source = (
            "{% component 'ok' %}{% component 'bad' %}"
            "{% component 'ok' %}{% component 'bad' %}"
        )
        with boundary_errors("render"), recorded_failures() as failures:
            html = site.finished(source, request=request_for(None))
        recorded = [failure.id for failure in failures]
        assert len(recorded) == 2
        assert len(set(recorded)) == 2
        wrappers = self._ids(html)
        assert len(wrappers) == len(set(wrappers)) == 4
        assert recorded == [wrappers[1], wrappers[3]]
        assert in_document_order(failures[::-1], html) == tuple(failures)

    def test_a_nested_failure_keeps_its_own_id(
        self, site: ComponentSite, request_for
    ) -> None:
        site.add("inner", "{{ boom }}", error="I", loading="L")
        site.add("outer", "<o>{% component 'inner' %}</o>", error="O", loading="L")
        with boundary_errors("render"), recorded_failures() as failures:
            html = site.finished("{% component 'outer' %}", request=request_for(None))
        (failure,) = failures
        assert failure.name == "inner"
        assert self._ids(html) == [self._ids(html)[0], failure.id]
