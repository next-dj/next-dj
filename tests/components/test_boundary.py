from collections.abc import Callable, Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.template import Context, Engine, Template, TemplateSyntaxError
from django.template.base import NodeList
from django.test import Client, override_settings

from next.components import ComponentInfo, components_manager
from next.components.boundary import Attempt, current_attempt
from next.components.facade import render_component_error
from next.components.info import BoundaryFiles
from next.components.loading import ModuleLoader
from next.components.renderers import (
    CachedComponentTemplateLoader,
    ComponentTemplateLoader,
    _granted,
)
from next.defer import BoundaryOptions, ErrorKind, WrapperTag
from next.diagnostics import degraded
from next.seeding import COLLECTOR_KEY
from next.static import ScriptInjectionPolicy, StaticCollector
from next.static.assets import StaticAsset
from next.templatetags import components as component_tags
from next.templatetags.components import ComponentNode
from tests.pages.checks.metadata.trees import (
    COMPONENTS,
    component_template,
    layout_root,
    templated_page,
)
from tests.support import routed


BOOM = "boom"
_RULE = SimpleNamespace(requirements=("auth.view_user",))
_NO_RULE = SimpleNamespace(requirements=())


def _boom() -> str:
    raise ValueError(BOOM)


def _info(
    root: Path,
    name: str,
    body: str,
    error: str | None = None,
    *,
    loading: str | None = None,
    module_path: Path | None = None,
) -> ComponentInfo:
    folder = root / name
    folder.mkdir()
    template_path = folder / "component.djx"
    template_path.write_text(body)
    error_path = loading_path = None
    if error is not None:
        error_path = folder / "error.djx"
        error_path.write_text(error)
    if loading is not None:
        loading_path = folder / "loading.djx"
        loading_path.write_text(loading)
    files = None
    if error_path is not None or loading_path is not None:
        files = BoundaryFiles(loading_path, error_path)
    return ComponentInfo(
        name=name,
        scope_root=root,
        scope_relative="",
        template_path=template_path,
        module_path=module_path,
        is_simple=False,
        files=files,
    )


class _Site:
    """Components resolved by name, rendered from a page template string."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.infos: dict[str, ComponentInfo] = {}
        self.collector = StaticCollector()

    def add(self, name: str, body: str, error: str | None = None, **kwargs) -> None:
        self.infos[name] = _info(self.root, name, body, error, **kwargs)

    def asset(self, url: str) -> Callable[[], str]:
        def add() -> str:
            self.collector.add(StaticAsset(url=url, kind="css"))
            return ""

        return add

    def context(self, **values: object) -> dict[str, object]:
        return {
            "current_template_path": str(self.root / "page.djx"),
            COLLECTOR_KEY: self.collector,
            "boom": _boom,
            **values,
        }

    def resolving(self):
        return patch.object(
            components_manager,
            "get_component",
            side_effect=lambda name, _path: self.infos.get(name),
        )

    def render(self, source: str, **values: object) -> str:
        with self.resolving():
            return Template(source).render(Context(self.context(**values)))

    def styles(self) -> list[str]:
        return [asset.url for asset in self.collector.assets_in_slot("styles")]


@pytest.fixture()
def site(tmp_path: Path) -> _Site:
    return _Site(tmp_path)


def _error_body(html: str) -> str:
    """Return the markup inside the one error wrapper of `html`."""
    _, _, rest = html.partition("<div id=")
    assert rest
    inner = rest.split(">", 1)[1]
    return inner.rsplit("</div>", 1)[0]


class TestPropagation:
    """The nearest enclosing component with an `error.djx` catches."""

    def test_the_innermost_of_three_levels_catches(self, site: _Site) -> None:
        site.add("outer", "<o>{% component 'middle' %}</o>", "O")
        site.add("middle", "<m>{% component 'inner' %}</m>", "M")
        site.add("inner", "<i>{{ boom }}</i>", "I")
        html = site.render("{% component 'outer' %}")
        assert html.startswith("<o><m><div id=")
        assert html.endswith('data-next-error="error">I</div></m></o>')
        assert degraded() is True

    def test_a_failure_rises_through_levels_without_an_error_file(
        self, site: _Site
    ) -> None:
        site.add("outer", "<o>{% component 'middle' %}</o>", "O")
        site.add("middle", "<m>{% component 'inner' %}</m>")
        site.add("inner", "<i>{{ boom }}</i>")
        html = site.render("[{% component 'outer' %}]")
        assert html.startswith("[<div id=")
        assert html.endswith(">O</div>]")

    def test_without_any_boundary_the_failure_propagates(self, site: _Site) -> None:
        site.add("inner", "<i>{{ boom }}</i>")
        with pytest.raises(ValueError, match=BOOM):
            site.render("{% component 'inner' %}")

    def test_children_fail_inside_their_owner(self, site: _Site) -> None:
        site.add("card", "<c>{{ children }}</c>", "[{{ props.title }}]")
        html = site.render(
            "{% #component 'card' title='T' %}{{ boom }}{% /component %}", props="leak"
        )
        assert _error_body(html) == "[T]"

    def test_a_slot_body_fails_inside_its_owner(self, site: _Site) -> None:
        site.add("card", "<c>{% set_slot 'title' %}</c>", "E")
        html = site.render(
            "{% #component 'card' %}{% #slot 'title' %}{{ boom }}{% /slot %}"
            "{% /component %}"
        )
        assert _error_body(html) == "E"

    def test_a_guarded_component_fills_its_slots(self, site: _Site) -> None:
        site.add("card", "<c>{% set_slot 'title' %}</c>", "E")
        html = site.render(
            "{% #component 'card' %}{% #slot 'title' %}T{% /slot %}{% /component %}"
        )
        assert html == "<c>T</c>"

    def test_a_failing_error_ui_reaches_the_next_boundary_up(self, site: _Site) -> None:
        site.add("outer", "<o>{% component 'inner' %}</o>", "O")
        site.add("inner", "{{ boom }}", "{{ boom }}")
        assert _error_body(site.render("{% component 'outer' %}")) == "O"

    def test_a_component_is_not_caught_by_itself_inside_its_error_ui(
        self, site: _Site
    ) -> None:
        site.add("card", "{{ boom }}", "{% component 'card' %}")
        with pytest.raises(ValueError, match=BOOM):
            site.render("{% component 'card' %}")

    def test_a_loading_file_alone_isolates_the_deferred_render(
        self, site: _Site
    ) -> None:
        site.add("card", "{{ boom }}", loading="...")
        html = site.render("{% component 'card' %}")
        assert html.startswith('<div id="b-')
        assert html.endswith('data-next-state="error" data-next-error="error"></div>')

    def test_a_successful_error_boundary_renders_without_a_wrapper(
        self, site: _Site
    ) -> None:
        site.add("card", "<c>{{ title }}</c>", "E")
        assert site.render("{% component 'card' title='T' %}") == "<c>T</c>"
        assert degraded() is False


class TestErrorTemplateContext:
    """`error.djx` reads the call-site context, `error` and the props."""

    def test_the_call_site_context_the_error_and_the_props(self, site: _Site) -> None:
        site.add(
            "card",
            "{{ boom }}",
            "{{ page_var }}|{{ error.kind }}|{{ error.digest|length }}|"
            "{{ props.title }}|{{ title }}|{{ error.message }}",
        )
        html = site.render("{% component 'card' title='T' %}", page_var="V")
        assert _error_body(html) == "V|error|12|T||"

    def test_retry_is_off_when_the_runtime_script_is_disabled(
        self, site: _Site, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        builder = SimpleNamespace(policy=ScriptInjectionPolicy.DISABLED)
        monkeypatch.setattr(
            "next.components.fallback.get_static_manager",
            lambda: SimpleNamespace(script_builder=lambda: builder),
        )
        site.add("card", "{{ boom }}", "{{ error.retry }}")
        assert _error_body(site.render("{% component 'card' %}")) == "False"

    def test_retry_is_on_by_default(self, site: _Site) -> None:
        site.add("card", "{{ boom }}", "{{ error.retry }}")
        assert _error_body(site.render("{% component 'card' %}")) == "True"

    def test_an_unreadable_error_file_renders_an_empty_error_ui(
        self, site: _Site
    ) -> None:
        site.add("card", "{{ boom }}", "E")
        files = site.infos["card"].files
        assert files is not None
        assert files.error is not None
        files.error.unlink()
        html = site.render("{% component 'card' %}")
        assert html.endswith('data-next-error="error"></div>')


class TestBoundarySource:
    """Under `DEBUG` the error wrapper names the `error.djx` that caught."""

    def test_debug_names_the_file_under_base_dir(self, site: _Site) -> None:
        site.add("card", "{{ boom }}", "E")
        with override_settings(DEBUG=True, BASE_DIR=site.root):
            html = site.render("{% component 'card' %}")
        assert 'data-next-boundary-source="card/error.djx"' in html

    def test_debug_names_a_file_outside_base_dir_in_full(
        self, site: _Site, tmp_path_factory: pytest.TempPathFactory
    ) -> None:
        site.add("card", "{{ boom }}", "E")
        elsewhere = tmp_path_factory.mktemp("elsewhere")
        with override_settings(DEBUG=True, BASE_DIR=elsewhere):
            html = site.render("{% component 'card' %}")
        source = site.root / "card" / "error.djx"
        assert f'data-next-boundary-source="{source}"' in html

    def test_production_names_no_file(self, site: _Site) -> None:
        site.add("card", "{{ boom }}", "E")
        with override_settings(DEBUG=False, BASE_DIR=site.root):
            html = site.render("{% component 'card' %}")
        assert "data-next-boundary=" in html
        assert "data-next-boundary-source" not in html


class TestJournal:
    """Assets of the failed subtree are taken back, those of the error UI stay."""

    def test_assets_of_the_failed_subtree_are_dropped(self, site: _Site) -> None:
        site.add(
            "card",
            "{{ add_failed }}{% component 'leaf' %}{{ boom }}",
            "{{ add_error }}E",
        )
        site.add("leaf", "{{ add_leaf }}")
        site.render(
            "{{ add_page }}{% component 'card' %}",
            add_page=site.asset("/page.css"),
            add_failed=site.asset("/failed.css"),
            add_leaf=site.asset("/leaf.css"),
            add_error=site.asset("/error.css"),
        )
        assert site.styles() == ["/page.css", "/error.css"]
        assert site.collector._journal is None


class TestWrapperContext:
    """Where no element may wrap, the error UI carries the boundary on its root."""

    @pytest.mark.parametrize(
        ("source", "wrapped"),
        [
            ("<table>{% component 'row' %}</table>", False),
            ("<table>{% if True %}{% component 'row' %}{% endif %}</table>", False),
            (
                "<ul>{% for x in xs %}<li></li>{% endfor %}{% component 'row' %}</ul>",
                False,
            ),
            (
                "{% for x in xs %}<table></table>{% endfor %}<p>{% component 'row' %}</p>",
                True,
            ),
            ("<svg><g>{% component 'row' %}</g></svg>", False),
            ("<ul><li>{% component 'row' %}</li></ul>", True),
        ],
    )
    def test_the_markup_before_the_site_decides(
        self, site: _Site, source: str, *, wrapped: bool
    ) -> None:
        site.add("row", "{{ boom }}", "<tr><td>E</td></tr>")
        html = site.render(source, xs=[1])
        assert ("<div id=" in html) is wrapped
        assert ("<tr id=" in html) is not wrapped
        assert "><td>E</td></tr>" in html

    def test_a_node_rendered_outside_a_template_takes_a_div(self, site: _Site) -> None:
        site.add("row", "{{ boom }}", "E")
        node = Template("{% component 'row' %}").nodelist[0]
        assert isinstance(node, ComponentNode)
        with patch.object(
            components_manager, "get_component", return_value=site.infos["row"]
        ):
            html = node.render(Context(site.context()))
        assert html.startswith("<div id=")

    def test_a_node_its_template_does_not_hold_takes_a_div(self, site: _Site) -> None:
        site.add("row", "{{ boom }}", "E")
        node = Template("{% component 'row' %}").nodelist[0]
        context = Context(site.context())
        context.render_context.template = Template("<table></table>")
        with patch.object(
            components_manager, "get_component", return_value=site.infos["row"]
        ):
            html = node.render(context)
        assert html.startswith("<div id=")


class TestAttempt:
    """Props reach the error UI only after the access rule passed."""

    def test_no_props_resolved_discloses_none(self, tmp_path: Path) -> None:
        assert Attempt(_info(tmp_path, "c", "")).disclosed(ErrorKind.ERROR) is None

    @pytest.mark.parametrize("kind", [ErrorKind.FORBIDDEN, ErrorKind.NOT_FOUND])
    def test_a_refusal_hides_the_props(self, tmp_path: Path, kind: ErrorKind) -> None:
        attempt = Attempt(_info(tmp_path, "c", ""))
        attempt.props = {"a": 1}
        assert attempt.disclosed(kind) is None

    def test_a_ruleless_component_discloses_a_copy(self, tmp_path: Path) -> None:
        attempt = Attempt(_info(tmp_path, "c", ""))
        attempt.props = {"a": 1}
        disclosed = attempt.disclosed(ErrorKind.ERROR)
        assert disclosed == {"a": 1}
        assert disclosed is not attempt.props

    @pytest.mark.parametrize(("granted", "shown"), [(False, False), (True, True)])
    def test_a_rule_hides_the_props_until_it_passed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        *,
        granted: bool,
        shown: bool,
    ) -> None:
        monkeypatch.setattr("next.components.boundary.declared", lambda *_: _RULE)
        attempt = Attempt(
            _info(tmp_path, "c", "", module_path=tmp_path / "component.py")
        )
        attempt.props = {"a": 1}
        attempt.granted = granted
        assert (attempt.disclosed(ErrorKind.ERROR) is not None) is shown

    def test_a_module_without_a_rule_discloses(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("next.components.boundary.declared", lambda *_: _NO_RULE)
        attempt = Attempt(
            _info(tmp_path, "c", "", module_path=tmp_path / "component.py")
        )
        attempt.props = {"a": 1}
        assert attempt.disclosed(ErrorKind.ERROR) == {"a": 1}

    @pytest.mark.parametrize(
        ("declared", "disclosed"), [(("a", "z"), {"a": 1}), ((), {"a": 1, "b": 2})]
    )
    def test_declared_props_narrow_what_is_disclosed(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        declared: tuple[str, ...],
        disclosed: dict[str, int],
    ) -> None:
        store = SimpleNamespace(options=lambda *_: BoundaryOptions(props=declared))
        monkeypatch.setattr("next.components.boundary.defer_declarations", store)
        attempt = Attempt(_info(tmp_path, "c", ""))
        attempt.props = {"a": 1, "b": 2}
        assert attempt.disclosed(ErrorKind.ERROR) == disclosed


class TestGrant:
    """A passed access rule is noted on the attempt rendering that component."""

    @pytest.fixture()
    def ruled(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ComponentInfo:
        monkeypatch.setattr("next.components.renderers.declared", lambda *_: _RULE)
        return _info(tmp_path, "c", "", module_path=tmp_path / "component.py")

    @pytest.fixture()
    def passing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("next.components.renderers.check", lambda *_: None)

    @pytest.mark.parametrize(("answer", "granted"), [(None, True), ("denied", False)])
    def test_the_check_result_is_noted(
        self,
        ruled: ComponentInfo,
        monkeypatch: pytest.MonkeyPatch,
        answer: object,
        *,
        granted: bool,
    ) -> None:
        monkeypatch.setattr("next.components.renderers.check", lambda *_: answer)
        attempt = Attempt(ruled)
        token = current_attempt.set(attempt)
        try:
            assert _granted(ruled, {}, None) is granted
        finally:
            current_attempt.reset(token)
        assert attempt.granted is granted

    @pytest.mark.usefixtures("passing")
    def test_a_grant_outside_any_attempt_is_ignored(self, ruled: ComponentInfo) -> None:
        assert _granted(ruled, {}, None) is True

    @pytest.mark.usefixtures("passing")
    def test_a_grant_of_another_component_is_ignored(
        self, ruled: ComponentInfo, tmp_path: Path
    ) -> None:
        attempt = Attempt(_info(tmp_path, "other", ""))
        token = current_attempt.set(attempt)
        try:
            assert _granted(ruled, {}, None) is True
        finally:
            current_attempt.reset(token)
        assert attempt.granted is False


class TestErrorTemplates:
    """`error.djx` compiles once and is reused until its file changes."""

    def test_the_plain_loader_reads_and_compiles(self, tmp_path: Path) -> None:
        path = tmp_path / "error.djx"
        path.write_text("E{{ x }}")
        loader = ComponentTemplateLoader(ModuleLoader())
        template = loader.load_file(path)
        assert template is not None
        assert template.render(Context({"x": 1})) == "E1"

    @pytest.mark.parametrize(
        "content", [None, b"\xff\xfe\x00"], ids=["missing", "undecodable"]
    )
    def test_an_unreadable_file_compiles_to_nothing(
        self, tmp_path: Path, content: bytes | None
    ) -> None:
        path = tmp_path / "error.djx"
        if content is not None:
            path.write_bytes(content)
        assert ComponentTemplateLoader(ModuleLoader()).load_file(path) is None
        assert CachedComponentTemplateLoader(ModuleLoader()).load_file(path) is None

    def test_the_cached_loader_reuses_the_compiled_template(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "error.djx"
        path.write_text("E")
        loader = CachedComponentTemplateLoader(ModuleLoader())
        assert loader.load_file(path) is loader.load_file(path)

    def test_a_stale_entry_is_compiled_again(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "error.djx"
        path.write_text("E")
        loader = CachedComponentTemplateLoader(ModuleLoader())
        first = loader.load_file(path)
        monkeypatch.setattr(loader, "_is_fresh", lambda *_: False)
        assert loader.load_file(path) is not first

    def test_a_file_without_a_stamp_is_not_kept(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "error.djx"
        path.write_text("E")
        monkeypatch.setattr("next.components.renderers.snapshot", lambda *_: None)
        loader = CachedComponentTemplateLoader(ModuleLoader())
        first = loader.load_file(path)
        assert first is not None
        assert loader.load_file(path) is not first

    def test_a_component_without_an_error_file_renders_no_error_ui(
        self, tmp_path: Path
    ) -> None:
        bare = _info(tmp_path, "bare", "")
        loading_only = _info(tmp_path, "loading", "", loading="...")
        assert render_component_error(bare, {}) == ""
        assert render_component_error(loading_only, {}) == ""


_FAILING_MODULE = """
from next.components import component


@component.context("value")
def value():
    raise RuntimeError("widget down")
"""


@pytest.fixture()
def widget_pages(tmp_path: Path) -> Iterator[Path]:
    pages = layout_root(tmp_path)
    component_template(pages, "widget", "<b>{{ value }}</b>")
    folder = pages / "_components" / "widget"
    (folder / "component.py").write_text(_FAILING_MODULE)
    (folder / "error.djx").write_text(
        '<p class="widget-error">{{ error.kind }} {{ props.label }}</p>'
    )
    templated_page(
        pages / "home", "cache = 60\n", '[{% component "widget" label="L" %}]'
    )
    with routed(pages, COMPONENT_BACKENDS=COMPONENTS):
        yield pages


@pytest.mark.django_db()
@pytest.mark.usefixtures("widget_pages")
class TestPage:
    """A contained failure keeps the page up and every shared cache away."""

    def test_the_page_renders_the_error_ui_and_goes_private(self) -> None:
        response = Client().get("/home/")
        body = response.content.decode()
        assert response.status_code == 200
        assert '[<div id="b-' in body
        assert '<p class="widget-error">error L</p></div>]' in body
        assert response["Cache-Control"] == "private, no-store"


_ENGINE_TEMPLATES = {
    "base.html": "<table><tbody>{% block rows %}{% endblock %}</tbody></table>",
    "middle.html": "{% extends 'base.html' %}",
    "child.html": (
        "{% extends 'base.html' %}{% block rows %}{% component 'row' %}{% endblock %}"
    ),
    "grandchild.html": (
        "{% extends 'middle.html' %}{% block rows %}{% component 'row' %}{% endblock %}"
    ),
    "cell.html": (
        "{% extends 'base.html' %}{% block rows %}<tr><td>{% component 'row' %}"
        "</td></tr>{% endblock %}"
    ),
    "nested.html": (
        "{% extends 'base.html' %}{% block rows %}{% block inner %}"
        "{% component 'row' %}{% endblock %}{% endblock %}"
    ),
    "inc.html": "{% component 'row' %}",
    "outer.html": "<table>{% include 'inc.html' %}</table>",
    "split.html": "<table>{% block a %}{% endblock %}</table><p>{% block c %}"
    "{% endblock %}</p>",
    "inner.html": (
        "{% extends 'split.html' %}{% block a %}{% block inner %}"
        "{% component 'row' %}{% endblock %}{% endblock %}"
    ),
    "moved.html": (
        "{% extends 'inner.html' %}{% block a %}{% endblock %}{% block c %}"
        "{% block inner %}{{ block.super }}{% endblock %}{% endblock %}"
    ),
}


@pytest.fixture()
def engine() -> Engine:
    return Engine(
        loaders=[("django.template.loaders.locmem.Loader", _ENGINE_TEMPLATES)],
        builtins=["next.templatetags.components"],
    )


class TestInheritedSites:
    """The nearest site the static text knows decides, through the block it fills."""

    @pytest.mark.parametrize(
        ("name", "adopted"),
        [
            ("child.html", True),
            ("grandchild.html", True),
            ("cell.html", False),
            ("nested.html", True),
            ("outer.html", False),
        ],
    )
    def test_the_parent_template_places_a_child_block(
        self, site: _Site, engine: Engine, name: str, *, adopted: bool
    ) -> None:
        site.add("row", "{{ boom }}", "<tr><td>E</td></tr>")
        with site.resolving():
            html = engine.get_template(name).render(Context(site.context()))
        assert ("<tr id=" in html) is adopted
        assert ("<div id=" in html) is not adopted

    def test_one_call_placed_by_other_blocks_reads_them_again(
        self, site: _Site, engine: Engine
    ) -> None:
        site.add("row", "{{ boom }}", "<tr><td>E</td></tr>")
        with site.resolving():
            placed = [
                engine.get_template(name).render(Context(site.context()))
                for name in ("inner.html", "moved.html", "inner.html")
            ]
        assert ["<tr id=" in html for html in placed] == [True, False, True]

    def test_a_block_the_rendered_template_does_not_place_reads_itself(
        self, site: _Site
    ) -> None:
        site.add("row", "{{ boom }}", "<tr><td>E</td></tr>")
        compiled = Template("{% block x %}<table>{% component 'row' %}{% endblock %}")
        (node,) = compiled.nodelist.get_nodes_by_type(ComponentNode)
        context = Context(site.context(block=compiled.nodelist[0]))
        context.render_context.template = Template("<p></p>")
        with site.resolving():
            html = node.render(context)
        assert html.startswith("<tr id=")

    def test_the_markup_before_a_looped_call_is_read_once(self, site: _Site) -> None:
        site.add("row", "{{ boom }}", "<tr><td>E</td></tr>")
        compiled = Template(
            "<table>{% for x in xs %}{% component 'row' %}{% endfor %}</table>"
        )
        with (
            site.resolving(),
            patch.object(
                component_tags._Placement, "of", wraps=component_tags._Placement.of
            ) as read,
        ):
            for _ in range(2):
                html = compiled.render(Context(site.context(xs=[1, 2, 3])))
                assert html.count("<tr id=") == 3
        assert read.call_count == 1

    def test_a_block_outside_any_extends_reads_its_own_template(
        self, site: _Site
    ) -> None:
        site.add("row", "{{ boom }}", "<tr><td>E</td></tr>")
        html = site.render("<table>{% block b %}{% component 'row' %}{% endblock %}")
        assert "<tr id=" in html

    def test_a_node_in_a_block_it_does_not_belong_to_takes_a_div(
        self, site: _Site
    ) -> None:
        site.add("row", "{{ boom }}", "E")
        node = Template("{% component 'row' %}").nodelist[0]
        assert isinstance(node, ComponentNode)
        holder = Template("<table>{% block b %}{% endblock %}")
        context = Context(site.context(block=holder.nodelist[1]))
        with site.resolving():
            html = node.render(context)
        assert html.startswith("<div id=")


class TestDeclaredTag:
    """A declared tag overrides the site, an undeclared one leaves the site to decide."""

    @pytest.fixture()
    def declared(self, monkeypatch: pytest.MonkeyPatch):
        options: dict[Path, BoundaryOptions] = {}
        monkeypatch.setattr(
            "next.templatetags.components.defer_declarations",
            SimpleNamespace(options=options.get),
        )
        return options

    @pytest.mark.parametrize(
        ("tag", "source", "opening"),
        [
            ("tbody", "<table>{% component 'row' %}</table>", "<tbody id="),
            (None, "<p>{% component 'row' %}</p>", "<tr id="),
            ("div", "<table>{% component 'row' %}</table>", "<div id="),
            ("div", "<p>{% component 'row' %}</p>", "<div id="),
            (WrapperTag.SITE, "<table>{% component 'row' %}</table>", "<tr id="),
            (WrapperTag.SITE, "<p>{% component 'row' %}</p>", "<div id="),
        ],
    )
    def test_the_declaration_decides(
        self,
        site: _Site,
        declared: dict[Path, BoundaryOptions],
        tag: str | WrapperTag | None,
        source: str,
        opening: str,
    ) -> None:
        module = site.root / "row" / "component.py"
        site.add("row", "{{ boom }}", "<tr><td>E</td></tr>", module_path=module)
        module.write_text("")
        declared[module] = BoundaryOptions.parse(None, tag=tag)
        assert opening in site.render(source)


class TestSiteNames:
    """Messages name the template and the line of the call."""

    def test_a_compiled_call_names_its_template_and_line(self) -> None:
        node = Template("<p>\n{% component 'row' %}", name="page.djx").nodelist[1]
        assert isinstance(node, ComponentNode)
        assert node.site == "the call of component 'row' in <unknown source>, line 2"
        assert node.site is node.site

    def test_a_node_built_by_hand_names_no_template(self) -> None:
        node = ComponentNode("row", {}, NodeList())
        assert node.site == "the call of component 'row' in an unnamed template"

    def test_a_component_template_names_its_file(self, tmp_path: Path) -> None:
        path = tmp_path / "card" / "component.djx"
        path.parent.mkdir()
        path.write_text("{% component 'row' %}")
        info = ComponentInfo("card", tmp_path, "", path, None, is_simple=False)
        template = ComponentTemplateLoader(ModuleLoader()).load_template(info)
        assert template is not None
        node = template.nodelist[0]
        assert isinstance(node, ComponentNode)
        assert node.site == f"the call of component 'row' in {path}, line 1"

    def test_the_debug_placeholder_names_the_site(self, site: _Site) -> None:
        site.add("row", "{{ boom }}", "<tr></tr><tr></tr>")
        with override_settings(DEBUG=True):
            html = site.render("<table>\n\n{% component 'row' %}</table>")
        assert html.startswith("<table>\n\n<template id=")
        assert "component &#x27;row&#x27; in &lt;unknown source&gt;, line 3" in html


class TestBrokenErrorTemplate:
    """A syntax error in `error.djx` is parsed once and reaches the next boundary."""

    def test_the_failure_is_kept_until_the_file_changes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "error.djx"
        path.write_text("{% if %}")
        loader = CachedComponentTemplateLoader(ModuleLoader())
        compiles: list[str] = []
        real = Template.__init__

        def counting(self: Template, *args: object, **kwargs: object) -> None:
            compiles.append(str(args[0]))
            real(self, *args, **kwargs)

        monkeypatch.setattr(Template, "__init__", counting)
        for _ in range(3):
            with pytest.raises(TemplateSyntaxError):
                loader.load_file(path)
        assert len(compiles) == 1
        monkeypatch.setattr(loader, "_is_fresh", lambda *_: False)
        path.write_text("fixed")
        template = loader.load_file(path)
        assert template is not None
        assert template.render(Context()) == "fixed"

    def test_a_failure_without_a_stamp_is_not_kept(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "error.djx"
        path.write_text("{% if %}")
        monkeypatch.setattr("next.components.renderers.snapshot", lambda *_: None)
        loader = CachedComponentTemplateLoader(ModuleLoader())
        with pytest.raises(TemplateSyntaxError):
            loader.load_file(path)
        assert loader._compiled.get((path, None)) is None

    def test_the_next_boundary_up_catches_it(self, site: _Site) -> None:
        site.add("outer", "<o>{% component 'inner' %}</o>", "O")
        site.add("inner", "{{ boom }}", "{% if %}")
        assert _error_body(site.render("{% component 'outer' %}")) == "O"


class TestOwnAssets:
    """The assets of a failed component go with its subtree, its error UI's stay."""

    def test_the_failed_component_takes_back_its_own_assets(
        self, site: _Site, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def own(info: ComponentInfo, collector: StaticCollector | None) -> None:
            assert collector is not None
            collector.add(StaticAsset(url=f"/{info.name}.css", kind="css"))

        monkeypatch.setattr(
            "next.templatetags.components.collect_component_assets", own
        )
        site.add("card", "{{ boom }}", "E")
        site.add("plain", "<p></p>")
        site.render("{% component 'plain' %}{% component 'card' %}")
        assert site.styles() == ["/plain.css"]
