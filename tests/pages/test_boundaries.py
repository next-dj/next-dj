from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from urllib.parse import quote

import pytest
from django import template as django_template
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.template import Context, Template, TemplateDoesNotExist
from django.template.base import NodeList
from django.template.exceptions import TemplateSyntaxError
from django.template.loader_tags import BlockNode
from django.test import RequestFactory, override_settings

from next.boundaries import boundary_failed, contain
from next.components import (
    ComponentInfo,
    boundary as component_boundary,
    components_manager,
    registry as registry_module,
)
from next.components.info import BoundaryFiles
from next.components.loading import ModuleLoader
from next.components.registry import DeferredComponents, ErrorFiles
from next.components.trip import TripInput
from next.defer import DEFERRALS_KEY, BoundaryOptions, DeferMode, ErrorKind, stable_key
from next.diagnostics import degraded, degraded_status, watch_degraded
from next.pages.boundaries import (
    _NO_SCOPE,
    ADDRESSING,
    CAUGHT_KEY,
    FAILED_STATUS,
    FAILURE_KEY,
    SHELL_KEY,
    Caught,
    Shell,
    boundary_scope,
    boundary_template,
    catching,
    mark_failed,
    shown_source,
)
from next.pages.context import ContextResult
from next.pages.gating import GATE_KEY, Gate, plan_gating
from next.pages.graph import Blame, Phase, run_phase
from next.pages.loaders import (
    NO_BOUNDARIES,
    BoundaryChain,
    ErrorBoundary,
    PageBoundary,
    forget_layout_texts,
)
from next.pages.registry import PageContextRegistry
from next.ports import PortSlot, render_deferred
from next.seeding import (
    COLLECTOR_KEY,
    JS_CONTEXT_KEY,
    JS_SERIALIZERS_KEY,
    TEMPLATE_PATH_KEY,
)
from next.static import ScriptInjectionPolicy, StaticCollector
from next.static.runtime import BUNDLE_NOTE
from next.suspense import deferring
from next.templatetags import boundaries as boundary_tags, components as component_tags
from next.templatetags.boundaries import (
    PageBoundaryNode,
    PageJob,
    contained_by,
    enclosing,
)
from next.testing import capture_signals
from tests.support import rewrite_in_tick, touch_later


_BASE = Template("<b>{% block c %}{% endblock %}</b>")
_BROKEN_ERROR = '{% include "next-missing.djx" %}'


def _chain(*levels: int, root: Path = Path("/site")) -> BoundaryChain:
    return BoundaryChain.of(
        tuple(
            PageBoundary(level, level, root / str(level) / "error.djx", None)
            for level in levels
        )
    )


def _boom() -> str:
    msg = "body"
    raise ValueError(msg)


def _open(source: Path, wrapper: str = "div") -> str:
    return f'{{% next:boundary "{quote(str(source), safe="/")}" {wrapper} %}}'


def _tag(source: Path, body: str, wrapper: str = "div") -> str:
    return f"{_open(source, wrapper)}{body}{{% /next:boundary %}}"


def _shell(source: Path, **head: object) -> Shell:
    chain = BoundaryChain.of((PageBoundary(0, 0, source, None),))
    return Shell(head, {}, (), Blame(), chain)


def _shown(html: str) -> str:
    return html.split(">", 1)[1].rsplit("<", 1)[0]


@pytest.fixture()
def error_file(tmp_path: Path) -> Path:
    path = tmp_path / "error.djx"
    rewrite_in_tick(path, "{{ frame }}|{{ error.kind }}|{{ leaked|default:'clean' }}")
    forget_layout_texts()
    return path


class TestCatching:
    """The nearest boundary above the failed callable contains a failure."""

    def test_the_nearest_boundary_contains_a_failure_of_the_page(self) -> None:
        guard = catching(_chain(0, 2), ValueError("x"))

        assert guard == ErrorBoundary(0, 0, Path("/site/0/error.djx"))

    def test_a_callable_of_a_directory_skips_the_boundary_of_that_directory(
        self,
    ) -> None:
        guard = catching(_chain(1, 2), ValueError("x"), 1)

        assert guard is not None
        assert guard.level == 2

    def test_a_callable_above_every_boundary_is_not_contained(self) -> None:
        assert catching(_chain(1), ValueError("x"), 3) is None

    @pytest.mark.parametrize("error", [Http404("x"), PermissionDenied("x")])
    def test_an_intended_exception_passes_through(self, error: Exception) -> None:
        assert catching(_chain(0), error) is None

    def test_a_chain_without_an_error_file_contains_nothing(self) -> None:
        assert catching(NO_BOUNDARIES, ValueError("x")) is None

    def test_the_caught_failure_names_its_boundary(self) -> None:
        error = ValueError("x")
        guard = ErrorBoundary(0, 0, Path("/site/error.djx"))

        assert Caught(guard, error).boundary is guard
        assert Caught(guard, error).shell is None


class TestShell:
    """The shell holds the inherited groups at or above the cut and nothing below."""

    def _blame(self) -> Blame:
        blame = Blame()
        blame.settle(0, "nav", "NAV")
        blame.settle(0, None, {"nav": "lost", "site": "S"})
        blame.settle(1, "side", "SIDE")
        return blame

    def test_a_cut_keeps_the_groups_at_or_above_the_boundary(self) -> None:
        chain = _chain(1, 2)
        shell = Shell({"head": 1}, {"tail": 2}, (2, 1), self._blame(), chain)

        assert shell.at(2) == {
            "head": 1,
            "nav": "NAV",
            "site": "S",
            JS_CONTEXT_KEY: {},
            JS_SERIALIZERS_KEY: {},
            "tail": 2,
            SHELL_KEY: shell,
        }
        assert shell.at(1)["side"] == "SIDE"

    def test_a_source_reads_the_cut_of_its_boundary(self) -> None:
        chain = _chain(1, 2)
        shell = Shell({}, {}, (2, 1), self._blame(), chain)

        assert "side" not in shell.of(Path("/site/2/error.djx"))
        assert shell.of(Path("/site/1/error.djx"))["side"] == "SIDE"

    def test_an_unknown_source_gets_no_inherited_group(self) -> None:
        shell = Shell({"head": 1}, {}, (2,), self._blame(), _chain(2))

        context = shell.of(Path("/elsewhere/error.djx"))

        assert "nav" not in context
        assert context["head"] == 1

    def test_the_full_context_sets_the_results_before_the_tail(self) -> None:
        shell = Shell({"head": 1}, {"p": "P"}, (), Blame(), NO_BOUNDARIES)
        result = ContextResult({"own": 1, "p": "own"}, {"js": 1}, {})

        context = shell.around(result)

        assert context["own"] == 1
        assert context["p"] == "P"
        assert context[JS_CONTEXT_KEY] == {"js": 1}
        assert context[SHELL_KEY] is shell


class TestMarkFailed:
    """A settled page body answers the status of its kind, else 500."""

    @pytest.mark.parametrize(
        ("kind", "status"),
        [
            (ErrorKind.ERROR, 500),
            (ErrorKind.TIMEOUT, 500),
            (ErrorKind.OVERLOADED, 503),
            (ErrorKind.NOT_FOUND, 404),
            (ErrorKind.FORBIDDEN, 403),
            (ErrorKind.BAD_REQUEST, 400),
        ],
    )
    def test_each_kind_answers_its_status(self, kind: ErrorKind, status: int) -> None:
        watch_degraded()

        mark_failed(kind)

        assert degraded() is True
        assert degraded_status() == status
        watch_degraded()


class TestErrorTemplate:
    """The `error.djx` of a page boundary compiles once per text it reads."""

    def test_a_second_read_reuses_the_compiled_template(self, error_file) -> None:
        assert boundary_template(error_file) is boundary_template(error_file)

    def test_an_edited_file_compiles_again(self, error_file) -> None:
        first = boundary_template(error_file)
        touch_later(error_file, "edited")

        second = boundary_template(error_file)

        assert second is not first
        assert second.render(Context({})) == "edited"

    def test_a_missing_file_raises(self, tmp_path) -> None:
        with pytest.raises(TemplateDoesNotExist):
            boundary_template(tmp_path / "error.djx")


class TestShownSource:
    """DEBUG names the file that caught relative to the project where it can."""

    def test_a_file_inside_the_project_is_relative(self, tmp_path) -> None:
        with override_settings(BASE_DIR=tmp_path):
            assert shown_source(tmp_path / "pages" / "error.djx") == "pages/error.djx"

    def test_a_file_outside_the_project_stays_absolute(self, tmp_path) -> None:
        source = tmp_path / "error.djx"
        with override_settings(BASE_DIR=tmp_path / "project"):
            assert shown_source(source) == str(source)


class TestBoundaryScope:
    """A render opens the per-render state where the page or a component can catch."""

    def test_a_chain_without_an_error_file_opens_nothing(self) -> None:
        with mock.patch("next.pages.boundaries.error_files", ErrorFiles()):
            assert boundary_scope(NO_BOUNDARIES) is _NO_SCOPE

    def test_a_chain_with_an_error_file_opens_the_render_state(self) -> None:
        with mock.patch(
            "next.pages.boundaries.render_boundaries", return_value=_NO_SCOPE
        ) as opened:
            boundary_scope(_chain(0))

        opened.assert_called_once_with()

    def test_a_component_error_file_opens_the_render_state(self) -> None:
        held = ErrorFiles()
        held.held = True
        with (
            mock.patch("next.pages.boundaries.error_files", held),
            mock.patch(
                "next.pages.boundaries.render_boundaries", return_value=_NO_SCOPE
            ) as opened,
        ):
            boundary_scope(NO_BOUNDARIES)

        opened.assert_called_once_with()


class TestPageBoundaryNode:
    """The internal tag renders its content or the `error.djx` of its directory."""

    def test_a_working_content_renders_unwrapped(self, error_file) -> None:
        html = Template(_tag(error_file, "<p>{{ shell }}</p>")).render(
            Context({"shell": "S"})
        )

        assert html == "<p>S</p>"

    def test_a_failing_content_renders_the_error_file_in_its_wrapper(
        self, error_file
    ) -> None:
        watch_degraded()
        source = "{% firstof 'partial' as leaked %}{{ boom }}"

        html = Template(_tag(error_file, source)).render(
            Context({SHELL_KEY: _shell(error_file, frame="S"), "boom": _boom})
        )

        assert html.startswith('<div id="b-')
        assert html.endswith('data-next-error="error">S|error|clean</div>')
        assert degraded() is True
        assert degraded_status() == FAILED_STATUS
        watch_degraded()

    def test_the_error_file_never_reads_the_context_of_the_failed_content(
        self, error_file
    ) -> None:
        html = Template(_tag(error_file, "{{ boom }}")).render(
            Context({"frame": "S", "boom": _boom})
        )

        assert _shown(html) == "|error|clean"
        watch_degraded()

    def test_a_variable_the_content_sets_reaches_the_markup_after_it(
        self, error_file
    ) -> None:
        source = _tag(error_file, '{% firstof "SET" as t %}body') + "|{{ t }}"

        assert Template(source).render(Context({})) == "body|SET"

    def test_a_variable_a_failed_content_set_is_dropped(self, error_file) -> None:
        source = _tag(error_file, '{% firstof "SET" as t %}{{ boom }}')

        html = Template(source + "|{{ t }}").render(Context({"boom": _boom}))

        assert html.endswith(">|error|clean</div>|")
        watch_degraded()

    def test_the_error_file_keeps_the_escaping_of_the_render(self, tmp_path) -> None:
        source = tmp_path / "a&b" / "error.djx"
        source.parent.mkdir()
        source.write_text("{{ current_template_path }}")
        forget_layout_texts()
        markup = Template(_tag(source, "{{ boom }}"))

        escaped = markup.render(Context({"boom": _boom}))
        raw = markup.render(Context({"boom": _boom}, autoescape=False))

        assert _shown(escaped) == str(source).replace("&", "&amp;")
        assert _shown(raw) == str(source)
        watch_degraded()

    def test_the_error_file_keeps_the_localisation_of_the_render(
        self, error_file
    ) -> None:
        markup = Template(_tag(error_file, "{{ boom }}"))
        with mock.patch.object(
            django_template, "Context", wraps=django_template.Context
        ) as made:
            markup.render(Context({"boom": _boom}, use_l10n=False, use_tz=False))

        assert made.call_args.kwargs == {
            "autoescape": True,
            "use_l10n": False,
            "use_tz": False,
        }
        watch_degraded()

    def test_the_error_file_resolves_components_from_its_own_path(
        self, error_file
    ) -> None:
        error_file.write_text("{{ current_template_path }}")
        forget_layout_texts()

        html = Template(_tag(error_file, "{{ boom }}")).render(
            Context({TEMPLATE_PATH_KEY: "/page/template.djx", "boom": _boom})
        )

        assert _shown(html) == str(error_file)
        watch_degraded()

    def test_the_failure_reports_the_page_addressing(self, error_file) -> None:
        with capture_signals(boundary_failed) as recorder:
            Template(_tag(error_file, "{{ boom }}")).render(Context({"boom": _boom}))

        event = recorder.first_for(boundary_failed)
        assert event.sender is contain
        assert event.kwargs["addressing"] == ADDRESSING
        assert event.kwargs["name"] == str(error_file)
        watch_degraded()

    def test_debug_names_the_file_that_caught(self, error_file, tmp_path) -> None:
        with override_settings(DEBUG=True, BASE_DIR=tmp_path):
            html = Template(_tag(error_file, "{{ boom }}")).render(
                Context({"boom": _boom})
            )

        assert 'data-next-boundary-source="error.djx"' in html
        watch_degraded()

    def test_retry_is_on_by_default(self, error_file) -> None:
        error_file.write_text("{{ error.retry }}")
        forget_layout_texts()

        html = Template(_tag(error_file, "{{ boom }}")).render(Context({"boom": _boom}))

        assert _shown(html) == "True"
        watch_degraded()

    def test_retry_is_off_when_the_runtime_script_is_disabled(
        self, error_file, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        builder = SimpleNamespace(policy=ScriptInjectionPolicy.DISABLED)
        monkeypatch.setattr(
            "next.components.fallback.get_static_manager",
            lambda: SimpleNamespace(script_builder=lambda: builder),
        )
        error_file.write_text("{{ error.retry }}")
        forget_layout_texts()

        html = Template(_tag(error_file, "{{ boom }}")).render(Context({"boom": _boom}))

        assert _shown(html) == "False"
        watch_degraded()

    def test_a_failing_error_file_propagates(self, error_file) -> None:
        error_file.write_text(_BROKEN_ERROR)
        forget_layout_texts()

        with pytest.raises(TemplateDoesNotExist):
            Template(_tag(error_file, "{{ boom }}")).render(Context({"boom": _boom}))
        watch_degraded()

    def test_a_failure_a_job_met_for_it_renders_its_error_file(
        self, error_file
    ) -> None:
        caught = Caught(ErrorBoundary(0, 0, error_file), ValueError("job"))

        html = Template(_tag(error_file, "<p>body</p>")).render(
            Context({CAUGHT_KEY: caught, SHELL_KEY: _shell(error_file, frame="S")})
        )

        assert html.endswith('data-next-error="error">S|error|clean</div>')
        watch_degraded()

    def test_a_failure_held_for_another_boundary_renders_the_content(
        self, error_file, tmp_path
    ) -> None:
        caught = Caught(ErrorBoundary(1, 1, tmp_path / "other.djx"), ValueError("x"))

        html = Template(_tag(error_file, "<p>body</p>")).render(
            Context({CAUGHT_KEY: caught})
        )

        assert html == "<p>body</p>"

    def test_an_intended_exception_passes_through(self, error_file) -> None:
        def missing() -> str:
            raise Http404

        with pytest.raises(Http404):
            Template(_tag(error_file, "{{ missing }}")).render(
                Context({"missing": missing})
            )

    @pytest.mark.parametrize(
        "source",
        [
            "{% next:boundary %}{% /next:boundary %}",
            "{% next:boundary /x div %}{% /next:boundary %}",
            '{% next:boundary "/x" div %}',
        ],
    )
    def test_a_malformed_tag_is_refused(self, source: str) -> None:
        with pytest.raises(TemplateSyntaxError):
            Template(source)


class TestExtendsContent:
    """A content opening with `{% extends %}` takes the rest of the template."""

    def test_a_working_extends_renders_its_parent(self, error_file) -> None:
        source = _tag(error_file, "{% extends base %}{% block c %}hi{% endblock %}")

        assert Template(source).render(Context({"base": _BASE})) == "<b>hi</b>"

    def test_a_failing_block_renders_the_error_file(self, error_file) -> None:
        body = "{% extends base %}{% block c %}{{ boom }}{% endblock %}"

        html = Template(_tag(error_file, body)).render(
            Context({"base": _BASE, "boom": _boom})
        )

        assert _shown(html) == "|error|clean"
        watch_degraded()

    def test_text_and_comments_before_the_extends_are_looked_past(
        self, error_file
    ) -> None:
        body = "\n{# note #}{% extends base %}{% block c %}hi{% endblock %}"

        html = Template(_tag(error_file, body)).render(Context({"base": _BASE}))

        assert html == "\n<b>hi</b>"

    def test_nested_boundaries_each_take_the_rest(self, error_file, tmp_path) -> None:
        outer = tmp_path / "outer" / "error.djx"
        outer.parent.mkdir()
        outer.write_text("outer")
        forget_layout_texts()
        body = "{% extends base %}{% block c %}{{ boom }}{% endblock %}"
        source = _tag(outer, _tag(error_file, body))

        html = Template(source).render(Context({"base": _BASE, "boom": _boom}))

        assert _shown(html) == "|error|clean"
        watch_degraded()

    def test_a_lone_closing_tag_renders_nothing(self) -> None:
        assert Template("a{% /next:boundary %}b").render(Context({})) == "ab"


class TestEnclosing:
    """A zone body finds the page boundaries around it, innermost first."""

    def _template(self, inner: Path, outer: Path) -> Template:
        body = "{% with x=1 %}{% block z %}z{% endblock %}{% endwith %}"
        return Template(_tag(outer, f"<p>{_tag(inner, body)}</p>"))

    def test_the_boundaries_around_a_node_list_come_innermost_first(
        self, tmp_path
    ) -> None:
        markup = self._template(tmp_path / "inner.djx", tmp_path / "outer.djx")
        block = markup.nodelist.get_nodes_by_type(BlockNode)[0]

        nodes = enclosing(markup.nodelist, block.nodelist)

        assert nodes is not None
        assert [node.source.name for node in nodes] == ["inner.djx", "outer.djx"]

    def test_a_node_list_outside_the_template_is_not_found(self, tmp_path) -> None:
        markup = self._template(tmp_path / "inner.djx", tmp_path / "outer.djx")

        assert enclosing(markup.nodelist, Template("x").nodelist) is None

    def test_a_render_through_the_boundaries_falls_to_the_outer_one(
        self, tmp_path
    ) -> None:
        inner = tmp_path / "inner.djx"
        inner.write_text(_BROKEN_ERROR)
        outer = tmp_path / "outer.djx"
        outer.write_text("outer")
        forget_layout_texts()
        markup = self._template(inner, outer)
        block = markup.nodelist.get_nodes_by_type(BlockNode)[0]
        nodes = enclosing(markup.nodelist, block.nodelist)
        assert nodes is not None

        html = contained_by(nodes, Context({"boom": _boom}), _boom)

        assert _shown(html) == "outer"
        watch_degraded()


class TestFailedNode:
    """The tag that stands for a failed body raises the failure the context holds."""

    def test_the_held_failure_is_raised(self) -> None:
        error = ValueError("held")

        with pytest.raises(ValueError, match="held") as raised:
            Template("{% next:failed %}").render(Context({FAILURE_KEY: error}))

        assert raised.value is error

    def test_without_a_failure_it_renders_nothing(self) -> None:
        html = Template("<p>{% next:failed %}</p>").render(Context({}))

        assert html == "<p></p>"


def _deferred_tag(
    loading: Path, body: str, error: Path | None = None, wrapper: str = "div"
) -> str:
    shown = "" if error is None else quote(str(error), safe="/")
    source = quote(str(loading), safe="/")
    opening = f'{{% next:boundary "{shown}" {wrapper} "{source}" %}}'
    return f"{opening}{body}{{% /next:boundary %}}"


def _failing_body(
    loading: Path, context: Context, *, error: Path | None = None
) -> tuple[str, object]:
    """Run the body of a page job whose content raises, as a bound job renders it."""
    template = Template("{{ boom }}")
    node = PageBoundaryNode(error, "div", template.nodelist)
    context["boom"] = _boom
    with context.bind_template(template):
        return PageJob(node, loading).body(context)


def _node(template: Template) -> PageBoundaryNode:
    return next(
        node for node in template.nodelist if isinstance(node, PageBoundaryNode)
    )


@pytest.fixture()
def loading_file(tmp_path: Path) -> Path:
    path = tmp_path / "loading.djx"
    rewrite_in_tick(path, "<i>wait {{ frame|default:'' }}</i>")
    forget_layout_texts()
    return path


@pytest.fixture()
def fresh_flag(monkeypatch: pytest.MonkeyPatch) -> DeferredComponents:
    flag = DeferredComponents()
    monkeypatch.setattr(registry_module, "deferred_components", flag)
    return flag


_LEAF = Path("/deferred/leaf/page.py")
_SITE = Path("/deferred/page.py")


def _gate(loading: Path, error: Path | None, *, level: int = 0) -> Gate:
    registry = PageContextRegistry()
    registry.register_context(_SITE, "site", lambda: "S", inherit_context=True)
    registry.register_context(_LEAF, "word", lambda: "W")
    registry.register_context(_LEAF, "shown", lambda: 1, serialize=True)
    deferred = PageBoundary(level, level, None, loading)
    boundaries = (deferred,)
    if error is not None:
        boundaries = (deferred, PageBoundary(level + 1, level + 1, error, None))
    plan = plan_gating(
        registry.graph(_LEAF),
        registry.inherited_levels(_LEAF),
        (deferred,),
        frozenset(),
        _LEAF,
    )
    results = run_phase(
        plan.graph,
        _LEAF,
        Phase(plan.split.shell, {}),
        request=None,
        url_kwargs={},
        dep_cache={},
    )
    chain = BoundaryChain.of(boundaries)
    return Gate(plan, chain, _LEAF, None, {}, {}, results)


@pytest.mark.usefixtures("framework_providers")
class TestDeferredTag:
    """A slot with a `loading.djx` compiles to a deferred page boundary."""

    def test_the_tag_names_the_loading_file_and_raises_no_flag(
        self, loading_file, fresh_flag
    ) -> None:
        node = _node(Template(_deferred_tag(loading_file, "<p>b</p>")))

        assert node.loading == loading_file
        assert node.source is None
        assert node.key == stable_key(shown_source(loading_file), 0)
        assert fresh_flag.held is False

    def test_an_error_only_tag_raises_no_flag(self, error_file, fresh_flag) -> None:
        node = _node(Template(_tag(error_file, "<p>b</p>")))

        assert node.loading is None
        assert node.key == ""
        assert fresh_flag.held is False

    @pytest.mark.parametrize(
        "source",
        [
            '{% next:boundary "" div %}{% /next:boundary %}',
            '{% next:boundary "" div "" %}{% /next:boundary %}',
            '{% next:boundary "" div /x/loading.djx %}{% /next:boundary %}',
            '{% next:boundary "" div "/x" extra %}{% /next:boundary %}',
        ],
    )
    def test_a_malformed_deferred_tag_is_refused(self, source: str) -> None:
        with pytest.raises(TemplateSyntaxError):
            Template(source)

    def test_without_a_sink_the_content_renders_in_place(self, loading_file) -> None:
        template = Template(_deferred_tag(loading_file, "<p>{{ word }}</p>"))

        with mock.patch.object(
            component_boundary, "deferrals_slot", PortSlot("deferrals port")
        ):
            html = template.render(Context({"word": "w"}))

        assert html == "<p>w</p>"

    def test_outside_a_deferring_block_the_sink_settles_it_in_place(
        self, loading_file
    ) -> None:
        html = Template(_deferred_tag(loading_file, "<p>{{ word }}</p>")).render(
            Context({"word": "w"})
        )

        assert 'data-next-state="ready"' in html
        assert "<p>w</p>" in html

    def test_an_assembled_render_runs_the_job_and_splices_it(
        self, loading_file
    ) -> None:
        template = Template(_deferred_tag(loading_file, "<p>{{ word }}</p>"))

        html = render_deferred(
            lambda: template.render(Context({"word": "w"})),
            collector=None,
            request=None,
        )

        assert html.startswith('<div id="b-')
        assert 'data-next-state="ready"><p>w</p></div>' in html
        assert "<!--" not in html

    def test_a_node_built_by_hand_names_no_origin(self, loading_file) -> None:
        node = PageBoundaryNode(None, "div", NodeList(), loading_file)
        sink = mock.Mock(**{"queue.return_value": ""})

        node.render(Context({DEFERRALS_KEY: sink}))

        call = sink.queue.call_args.args[1]
        assert call.site.origin == ""
        assert call.site.name == shown_source(loading_file)
        assert call.options.mode is DeferMode.STREAM

    @override_settings(DEBUG=True)
    def test_debug_names_the_loading_file(self, loading_file) -> None:
        sink = mock.Mock(**{"queue.return_value": ""})

        Template(_deferred_tag(loading_file, "b")).render(
            Context({DEFERRALS_KEY: sink})
        )

        assert sink.queue.call_args.args[1].source == shown_source(loading_file)

    def test_the_page_declaration_supplies_the_options(self, loading_file) -> None:
        sink = mock.Mock(**{"queue.return_value": ""})
        declared = BoundaryOptions.parse(timeout=3)
        gate = _gate(loading_file, None)

        with mock.patch.object(boundary_tags, "page_defer", return_value=declared):
            Template(_deferred_tag(loading_file, "b")).render(
                Context({DEFERRALS_KEY: sink, GATE_KEY: gate})
            )

        options = sink.queue.call_args.args[1].options
        assert options.timeout == 3
        assert options.mode is DeferMode.STREAM

    def test_a_page_without_a_declaration_takes_the_default(self, loading_file) -> None:
        sink = mock.Mock(**{"queue.return_value": ""})
        gate = _gate(loading_file, None)

        with mock.patch.object(boundary_tags, "page_defer", return_value=None):
            Template(_deferred_tag(loading_file, "b")).render(
                Context({DEFERRALS_KEY: sink, GATE_KEY: gate})
            )

        assert sink.queue.call_args.args[1].options.timeout is None


@pytest.mark.usefixtures("framework_providers")
class TestPageJob:
    """A page job renders the loading UI, the gated content and the error UI."""

    def _job(self, loading: Path, error: Path | None = None) -> PageJob:
        node = PageBoundaryNode(error, "div", Template("<p>{{ word }}</p>").nodelist)
        return PageJob(node, loading)

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
            Context({"boom": _boom, SHELL_KEY: _shell(error_file, frame="S")})
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
            {GATE_KEY: _gate(loading_file, None), COLLECTOR_KEY: collector}
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
                GATE_KEY: _gate(loading_file, error_file),
                SHELL_KEY: _shell(error_file, frame="S"),
            }
        )

        html, failure = _failing_body(loading_file, context)

        assert html == "S|error|clean"
        assert failure is not None
        assert degraded_status() == FAILED_STATUS
        watch_degraded()

    def test_a_failure_without_an_error_file_propagates(self, loading_file) -> None:
        context = Context({GATE_KEY: _gate(loading_file, None)})

        with pytest.raises(ValueError, match="body"):
            _failing_body(loading_file, context)

    def test_a_gate_without_the_boundary_names_no_error_file(
        self, loading_file, error_file, tmp_path
    ) -> None:
        context = Context({GATE_KEY: _gate(loading_file, error_file)})

        with pytest.raises(ValueError, match="body"):
            _failing_body(tmp_path / "other.djx", context)

    def test_an_error_kind_renders_the_error_file(
        self, loading_file, error_file
    ) -> None:
        context = Context({SHELL_KEY: _shell(error_file, frame="S")})

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
        context = Context({SHELL_KEY: _shell(error_file, frame="S")})
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
    word: object = _boom,
) -> Gate:
    """Return the gate of a job at `level` whose own callable `word` runs in it."""
    registry = PageContextRegistry()
    registry.register_context(_SITE, "site", lambda: "S", inherit_context=True)
    registry.register_context(_LEAF, "word", word)
    deferred = PageBoundary(level, level, None, loading)
    plan = plan_gating(
        registry.graph(_LEAF),
        registry.inherited_levels(_LEAF),
        (deferred,),
        frozenset(),
        _LEAF,
    )
    results = run_phase(
        plan.graph,
        _LEAF,
        Phase(plan.split.shell, {}),
        request=None,
        url_kwargs={},
        dep_cache={},
    )
    chain = BoundaryChain.of(tuple(sorted((deferred, *boundaries), key=_level_of)))
    return Gate(plan, chain, _LEAF, None, {}, {}, results)


def _level_of(found: PageBoundary) -> int:
    return found.level


def _content(loading: Path, source: str, context: Context) -> str:
    template = Template(source)
    node = PageBoundaryNode(None, "div", template.nodelist, loading)
    with context.bind_template(template):
        return PageJob(node, loading).content(context)


@pytest.fixture()
def inner_error(tmp_path: Path) -> Path:
    path = tmp_path / "inner" / "error.djx"
    path.parent.mkdir()
    rewrite_in_tick(path, "{{ site|default:'-' }}|{{ error.kind }}")
    forget_layout_texts()
    return path


@pytest.mark.usefixtures("framework_providers")
class TestJobContainment:
    """A failing gated callable is held by the nearest boundary above its level."""

    def _shell(self, gate: Gate) -> Shell:
        return Shell({}, {}, (_SITE_LEVEL,), gate.blame(), gate.chain)

    def test_a_boundary_inside_the_content_renders_its_error_ui_in_place(
        self, loading_file, inner_error
    ) -> None:
        gate = _gated(loading_file, PageBoundary(0, 0, inner_error, None))
        context = Context({GATE_KEY: gate, SHELL_KEY: self._shell(gate)})
        watch_degraded()

        html = _content(
            loading_file,
            "<m>{{ site }}|" + _tag(inner_error, "{{ word }}") + "</m>",
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
        context = Context({GATE_KEY: gate, SHELL_KEY: self._shell(gate)})

        with pytest.raises(ValueError, match="body"):
            _content(loading_file, "{{ word }}", context)

    def test_a_chain_without_an_error_file_raises(self, loading_file) -> None:
        gate = _gated(loading_file)
        context = Context({GATE_KEY: gate, SHELL_KEY: self._shell(gate)})

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
        context = Context({GATE_KEY: gate, SHELL_KEY: self._shell(gate)})

        with (
            mock.patch.object(Gate, "advance", side_effect=KeyboardInterrupt),
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
        shell = Shell({}, {}, (_SITE_LEVEL,), Blame(), gate.chain)
        context = Context({GATE_KEY: gate, SHELL_KEY: shell, "boom": _boom})

        html = _content(loading_file, _tag(inner_error, "{{ boom }}"), context)

        assert html.endswith('data-next-error="error">S|error</div>')
        watch_degraded()


_TRIP = (
    'from next.components import component\n\ncomponent.defer("load", props=("x",))\n'
)


def _component(root: Path, name: str, body: str, module: str | None = None):
    """Write a component whose loading UI must never show, returning its info."""
    folder = root / "_components" / name
    folder.mkdir(parents=True)
    (folder / "component.djx").write_text(body)
    (folder / "loading.djx").write_text("never")
    module_path = None
    if module is not None:
        module_path = folder / "component.py"
        module_path.write_text(module)
        ModuleLoader().load(module_path)
    return ComponentInfo(
        name=name,
        scope_root=root,
        scope_relative="",
        template_path=folder / "component.djx",
        module_path=module_path,
        is_simple=False,
        files=BoundaryFiles(folder / "loading.djx", None),
    )


@pytest.mark.usefixtures("framework_providers")
class TestPageLoadingCalls:
    """A deferred component inside a page `loading.djx` renders in place."""

    def test_a_stream_and_a_client_trip_call_render_bare(self, tmp_path) -> None:
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
        request = RequestFactory().get("/")
        request.user = AnonymousUser()

        with (
            mock.patch.object(
                components_manager,
                "get_component",
                side_effect=lambda name, _path: infos.get(name),
            ),
            mock.patch(
                "next.templatetags.components.collect_component_assets",
                return_value=None,
            ),
            deferring(request=request) as held,
        ):
            html, failure = PageJob(node, loading).loading(
                Context({COLLECTOR_KEY: collector})
            )

        assert (html, failure) == ("<b>1</b><s>S</s>", None)
        assert not (held.session and held.session.jobs)
        assert collector.notes(BUNDLE_NOTE) == ()


class _Granted:
    is_authenticated = True

    def has_perm(self, perm: str, obj: object = None) -> bool:
        return True


@pytest.mark.usefixtures("framework_providers")
class TestPageJobGuards:
    """A page job renders under the granting branches open when it was queued."""

    def _render(self, tmp_path: Path, loading: Path, guard: str) -> str:
        infos = {"trip": _component(tmp_path, "trip", "<b>{{ x }}</b>", _TRIP)}
        body = _deferred_tag(loading, "{% component 'trip' x='1' %}")
        template = Template(
            f"{{% extends guard %}}{{% block body %}}{body}{{% endblock %}}"
        )
        parent = Template(
            f"{{% has_perm {guard} %}}{{% block body %}}{{% endblock %}}"
            "{% endhas_perm %}"
        )
        request = RequestFactory().get("/")
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
            mock.patch.object(
                components_manager,
                "get_component",
                side_effect=lambda name, _path: infos.get(name),
            ),
            mock.patch(
                "next.templatetags.components.collect_component_assets",
                return_value=None,
            ),
        ):
            return render_deferred(
                lambda: template.render(context), collector=None, request=request
            )

    def test_a_guard_of_a_parent_template_is_claimed_by_the_job(
        self, tmp_path, loading_file
    ) -> None:
        with mock.patch.object(component_tags, "TripInput", wraps=TripInput) as minted:
            html = self._render(tmp_path, loading_file, "'x.y'")

        assert "data-next-token=" in html
        assert [call.args[3] for call in minted.call_args_list] == [("x.y",)]

    def test_an_unnamed_guard_of_a_parent_template_refuses_the_job(
        self, tmp_path, loading_file
    ) -> None:
        with pytest.raises(TemplateSyntaxError, match="literal permission"):
            self._render(tmp_path, loading_file, "name")
