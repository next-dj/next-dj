from pathlib import Path
from unittest import mock

import pytest
from django.contrib.auth.models import AnonymousUser
from django.template import Context, Template
from django.template.base import NodeList
from django.template.exceptions import TemplateSyntaxError
from django.test import RequestFactory

from next.boundaries.failures import ErrorKind
from next.components import tags as component_tags
from next.components.trip import TripInput
from next.defer.components import deferring
from next.diagnostics import degraded_status, watch_degraded
from next.pages.boundaries import FAILED_STATUS, SHELL_KEY, ErrorContext
from next.pages.gating import GATE_KEY, ShellGate, plan_gating
from next.pages.graph import Blame, Phase, merged, run_phase
from next.pages.jobs import PageJob
from next.pages.loaders import BoundaryChain, PageBoundary, forget_layout_texts
from next.pages.nodes import PageBoundaryNode
from next.pages.registry import PageContextRegistry
from next.ports import render_deferred
from next.seeding import COLLECTOR_KEY, TEMPLATE_PATH_KEY
from next.static import StaticCollector
from next.static.runtime import BUNDLE_NOTE
from tests.components.sites import info_in, resolving
from tests.pages.shells import (
    LEAF,
    SITE,
    boom,
    boundary_tag,
    deferred_tag,
    error_shell,
    shell_gate,
)
from tests.support import rewrite_in_tick


pytestmark = pytest.mark.usefixtures("framework_providers")


_BASE = Template("<b>{% block c %}{% endblock %}</b>")
_BROKEN_ERROR = '{% include "next-missing.djx" %}'


def _failing_body(
    loading: Path, context: Context, *, error: Path | None = None
) -> tuple[str, object]:
    """Run the body of a page job whose content raises, as a bound job renders it."""
    template = Template("{{ boom }}")
    node = PageBoundaryNode(error, "div", template.nodelist)
    context["boom"] = boom
    with context.bind_template(template):
        return PageJob(
            node.source, node.tag, node.nodelist, loading, node.loading_shown
        ).body(context)


class TestPageJob:
    """A page job renders the loading UI, the gated content and the error UI."""

    def _job(self, loading: Path, error: Path | None = None) -> PageJob:
        node = PageBoundaryNode(error, "div", Template("<p>{{ word }}</p>").nodelist)
        return PageJob(
            node.source, node.tag, node.nodelist, loading, node.loading_shown
        )

    def test_the_loading_file_renders_over_the_shell(self, loading_file) -> None:
        html, failure = self._job(loading_file).loading(Context({"frame": "F"}))

        assert html == "<i>wait F</i>"
        assert failure is None

    def test_a_loading_file_its_own_error_file_guards(
        self, loading_file, error_file
    ) -> None:
        html, failure = self._job(loading_file, error_file).loading(Context({}))

        assert html == "<i>wait </i>"
        assert failure is None

    def test_a_failing_loading_file_settles_its_own_error_file(
        self, tmp_path, error_file
    ) -> None:
        broken = tmp_path / "broken.djx"
        broken.write_text("{{ boom }}")
        forget_layout_texts()

        html, failure = self._job(broken, error_file).loading(
            Context({"boom": boom, SHELL_KEY: error_shell(error_file, frame="S")})
        )

        assert html == "S|error|clean"
        assert failure is not None
        assert failure.kind is ErrorKind.ERROR
        watch_degraded()

    def test_without_a_gate_the_content_renders_as_is(self, loading_file) -> None:
        html, failure = self._job(loading_file).body(Context({"word": "w"}))

        assert (html, failure) == ("<p>w</p>", None)

    def test_the_gate_runs_the_gated_callables_first(self, loading_file) -> None:
        collector = StaticCollector()
        context = Context(
            {GATE_KEY: shell_gate(loading_file, None), COLLECTOR_KEY: collector}
        )

        html, failure = self._job(loading_file).body(context)

        assert (html, failure) == ("<p>W</p>", None)
        assert collector.js_context_wire() == {"shown": 1}
        assert "word" not in context

    def test_a_failure_settles_the_error_file_above_the_slot(
        self, loading_file, error_file
    ) -> None:
        context = Context(
            {
                GATE_KEY: shell_gate(loading_file, error_file),
                SHELL_KEY: error_shell(error_file, frame="S"),
            }
        )

        html, failure = _failing_body(loading_file, context)

        assert html == "S|error|clean"
        assert failure is not None
        assert degraded_status() == FAILED_STATUS
        watch_degraded()

    def test_a_failure_without_an_error_file_propagates(self, loading_file) -> None:
        context = Context({GATE_KEY: shell_gate(loading_file, None)})

        with pytest.raises(ValueError, match="body"):
            _failing_body(loading_file, context)

    def test_a_gate_without_the_boundary_names_no_error_file(
        self, loading_file, error_file, tmp_path
    ) -> None:
        context = Context({GATE_KEY: shell_gate(loading_file, error_file)})

        with pytest.raises(ValueError, match="body"):
            _failing_body(tmp_path / "other.djx", context)

    def test_an_error_kind_renders_the_error_file(
        self, loading_file, error_file
    ) -> None:
        context = Context({SHELL_KEY: error_shell(error_file, frame="S")})

        html, failure = self._job(loading_file, error_file).error(
            context, ErrorKind.TIMEOUT
        )

        assert html == "S|timeout|clean"
        assert failure.kind is ErrorKind.TIMEOUT
        watch_degraded()

    def test_an_error_kind_without_an_error_file_renders_nothing(
        self, loading_file
    ) -> None:
        html, failure = self._job(loading_file).error(Context({}), ErrorKind.TIMEOUT)

        assert html == ""
        assert failure.kind is ErrorKind.TIMEOUT
        watch_degraded()

    @pytest.mark.parametrize(
        ("kind", "status"),
        [
            (ErrorKind.TIMEOUT, 500),
            (ErrorKind.OVERLOADED, 503),
            (ErrorKind.NOT_FOUND, 404),
            (ErrorKind.FORBIDDEN, 403),
            (ErrorKind.BAD_REQUEST, 400),
        ],
    )
    def test_an_error_kind_answers_its_status_with_or_without_an_error_file(
        self, loading_file, error_file, kind: ErrorKind, status: int
    ) -> None:
        context = Context({SHELL_KEY: error_shell(error_file, frame="S")})
        for error in (None, error_file):
            watch_degraded()

            self._job(loading_file, error).error(context, kind)

            assert degraded_status() == status
        watch_degraded()

    def test_the_contain_of_a_loading_only_node_renders_plainly(
        self, loading_file
    ) -> None:
        node = PageBoundaryNode(None, "div", NodeList(), loading_file)

        assert node.contain(Context({}), lambda: "plain") == "plain"


_SITE_LEVEL = 1


def _gated(
    loading: Path,
    *boundaries: PageBoundary,
    level: int = _SITE_LEVEL,
    word: object = boom,
) -> ShellGate:
    """Return the gate of a job at `level` whose own callable `word` runs in it."""
    registry = PageContextRegistry()
    registry.register_context(SITE, "site", lambda: "S", inherit_context=True)
    registry.register_context(LEAF, "word", word)
    deferred = PageBoundary(level, level, None, loading)
    plan = plan_gating(
        registry.graph(LEAF),
        registry.inherited_levels(LEAF),
        (deferred,),
        frozenset(),
        LEAF,
    )
    results = run_phase(
        plan.graph,
        LEAF,
        Phase(plan.split.shell, {}),
        request=None,
        url_kwargs={},
        dep_cache={},
    )
    chain = BoundaryChain.of(tuple(sorted((deferred, *boundaries), key=_level_of)))
    taken = merged(plan.graph, LEAF, results)
    return ShellGate(plan, chain, LEAF, None, {}, {}, results, taken, frozenset())


def _level_of(found: PageBoundary) -> int:
    return found.level


def _content(loading: Path, source: str, context: Context) -> str:
    template = Template(source)
    node = PageBoundaryNode(None, "div", template.nodelist, loading)
    with context.bind_template(template):
        return PageJob(
            node.source, node.tag, node.nodelist, loading, node.loading_shown
        ).content(context)


@pytest.fixture()
def inner_error(tmp_path: Path) -> Path:
    path = tmp_path / "inner" / "error.djx"
    path.parent.mkdir()
    rewrite_in_tick(path, "{{ site|default:'-' }}|{{ error.kind }}")
    forget_layout_texts()
    return path


class TestJobContainment:
    """A failing gated callable is held by the nearest boundary above its level."""

    def error_shell(self, gate: ShellGate) -> ErrorContext:
        return ErrorContext({}, {}, (_SITE_LEVEL,), gate.blame(), gate.chain)

    def test_a_boundary_inside_the_content_renders_its_error_ui_in_place(
        self, loading_file, inner_error
    ) -> None:
        gate = _gated(loading_file, PageBoundary(0, 0, inner_error, None))
        context = Context({GATE_KEY: gate, SHELL_KEY: self.error_shell(gate)})
        watch_degraded()

        html = _content(
            loading_file,
            "<m>{{ site }}|" + boundary_tag(inner_error, "{{ word }}") + "</m>",
            context,
        )

        assert html.startswith("<m>S|<div id=")
        assert html.endswith('data-next-error="error">S|error</div></m>')
        assert degraded_status() == FAILED_STATUS
        watch_degraded()

    def test_a_boundary_of_the_job_directory_leaves_the_failure_to_the_body(
        self, loading_file, inner_error
    ) -> None:
        gate = _gated(loading_file, PageBoundary(_SITE_LEVEL, 0, inner_error, None))
        context = Context({GATE_KEY: gate, SHELL_KEY: self.error_shell(gate)})

        with pytest.raises(ValueError, match="body"):
            _content(loading_file, "{{ word }}", context)

    def test_a_chain_without_an_error_file_raises(self, loading_file) -> None:
        gate = _gated(loading_file)
        context = Context({GATE_KEY: gate, SHELL_KEY: self.error_shell(gate)})

        with pytest.raises(ValueError, match="body"):
            _content(loading_file, "{{ word }}", context)

    def test_a_context_without_a_shell_raises(self, loading_file, inner_error) -> None:
        gate = _gated(loading_file, PageBoundary(0, 0, inner_error, None))

        with pytest.raises(ValueError, match="body"):
            _content(loading_file, "{{ word }}", Context({GATE_KEY: gate}))

    def test_an_interruption_undoes_the_job_and_propagates(
        self, loading_file, inner_error
    ) -> None:
        gate = _gated(loading_file, PageBoundary(0, 0, inner_error, None))
        context = Context({GATE_KEY: gate, SHELL_KEY: self.error_shell(gate)})

        with (
            mock.patch.object(ShellGate, "advance", side_effect=KeyboardInterrupt),
            pytest.raises(KeyboardInterrupt),
        ):
            _content(loading_file, "{{ word }}", context)

    def test_an_error_ui_inside_the_content_reads_what_the_job_took(
        self, loading_file, inner_error
    ) -> None:
        gate = _gated(
            loading_file,
            PageBoundary(0, 0, inner_error, None),
            level=_SITE_LEVEL + 1,
            word=lambda: "W",
        )
        shell = ErrorContext({}, {}, (_SITE_LEVEL,), Blame(), gate.chain)
        context = Context({GATE_KEY: gate, SHELL_KEY: shell, "boom": boom})

        html = _content(loading_file, boundary_tag(inner_error, "{{ boom }}"), context)

        assert html.endswith('data-next-error="error">S|error</div>')
        watch_degraded()


_TRIP = (
    'from next.components import component\n\ncomponent.defer("load", props=("x",))\n'
)


def _component(root: Path, name: str, body: str, module: str | None = None):
    """Write a component whose loading UI must never show, returning its info."""
    return info_in(root / "_components", name, body, loading="never", module=module)


class TestPageLoadingCalls:
    """A deferred component inside a page `loading.djx` renders in place."""

    def test_a_stream_and_a_client_trip_call_render_bare(
        self, tmp_path, rf: RequestFactory
    ) -> None:
        infos = {
            "trip": _component(tmp_path, "trip", "<b>{{ x }}</b>", _TRIP),
            "slow": _component(tmp_path, "slow", "<s>S</s>"),
        }
        loading = tmp_path / "shop" / "loading.djx"
        loading.parent.mkdir()
        loading.write_text(
            "{% component 'trip' x=1 %}{% component 'slow' defer='stream' %}"
        )
        forget_layout_texts()
        collector = StaticCollector(bundle_url=lambda name: f"/s/next.{name}.min.js")
        node = PageBoundaryNode(None, "div", NodeList(), loading)
        request = rf.get("/")
        request.user = AnonymousUser()

        with (
            resolving(infos),
            mock.patch("next.static.ports.collect_component_assets", return_value=None),
            deferring(request=request) as held,
        ):
            html, failure = PageJob(
                node.source, node.tag, node.nodelist, loading, node.loading_shown
            ).loading(Context({COLLECTOR_KEY: collector}))

        assert (html, failure) == ("<b>1</b><s>S</s>", None)
        assert not (held.session and held.session.jobs)
        assert collector.notes(BUNDLE_NOTE) == ()


class _Granted:
    pk = 1
    is_authenticated = True

    def has_perm(self, perm: str, obj: object = None) -> bool:
        return True


class TestPageJobGuards:
    """A page job renders under the granting branches open when it was queued."""

    def _render(
        self, rf: RequestFactory, tmp_path: Path, loading: Path, guard: str
    ) -> str:
        infos = {"trip": _component(tmp_path, "trip", "<b>{{ x }}</b>", _TRIP)}
        body = deferred_tag(loading, "{% component 'trip' x='1' %}")
        template = Template(
            f"{{% extends guard %}}{{% block body %}}{body}{{% endblock %}}"
        )
        parent = Template(
            f"{{% has_perm {guard} %}}{{% block body %}}{{% endblock %}}"
            "{% endhas_perm %}"
        )
        request = rf.get("/")
        request.user = _Granted()
        context = Context(
            {
                "guard": parent,
                "name": "x.y",
                "request": request,
                TEMPLATE_PATH_KEY: str(tmp_path / "template.djx"),
            }
        )
        with (
            resolving(infos),
            mock.patch("next.static.ports.collect_component_assets", return_value=None),
        ):
            return render_deferred(
                lambda: template.render(context), collector=None, request=request
            )

    def test_a_guard_of_a_parent_template_is_claimed_by_the_job(
        self, rf: RequestFactory, tmp_path, loading_file
    ) -> None:
        with mock.patch.object(component_tags, "TripInput", wraps=TripInput) as minted:
            html = self._render(rf, tmp_path, loading_file, "'x.y'")

        assert "data-next-token=" in html
        assert [call.args[3] for call in minted.call_args_list] == [("x.y",)]

    def test_an_unnamed_guard_of_a_parent_template_refuses_the_job(
        self, rf: RequestFactory, tmp_path, loading_file
    ) -> None:
        with pytest.raises(TemplateSyntaxError, match="literal permission"):
            self._render(rf, tmp_path, loading_file, "name")
