from pathlib import Path
from unittest.mock import patch

import pytest
from django.template import Context, Template
from django.test import RequestFactory

from next.components import ComponentInfo, components_manager
from next.marks import ContainerNode, FixedText, fragment_marks, marked
from next.router.links import TemplateLinks
from next.router.navigation import fragment_marking
from next.seeding import REQUEST_KEY
from next.views.containers import compile_containers


LINKS = TemplateLinks()
MARK = 'aria-current="page"'
LOADS = "{% load components next_static %}"


def _compiled(source: str) -> tuple[Template, bool]:
    template = Template(source)
    return template, compile_containers(template, LINKS)


def _wrapped(template: Template) -> list[str]:
    return [
        "".join(getattr(node, "s", "?") for node in wrapper.nodelist)
        for wrapper in template.nodelist.get_nodes_by_type(ContainerNode)
    ]


def _render(template: Template, path: str = "/docs/", **values: object) -> str:
    request = RequestFactory().get(path)
    with fragment_marking(request, compile_containers):
        return template.render(Context({REQUEST_KEY: request, **values}))


class TestWrapping:
    """Each container the source spells becomes one node, the rest stays as it was."""

    def test_a_literal_nav_is_wrapped_alone(self) -> None:
        template, whole = _compiled(
            '<header><p>x</p><nav><a href="/docs/">D</a></nav><i>y</i></header>'
        )
        assert whole
        assert _wrapped(template) == ['<nav><a href="/docs/">D</a></nav>']
        assert MARK in _render(template)

    def test_the_metadata_of_the_head_leaves_it_whole(self) -> None:
        template, whole = _compiled(
            '<head>{% metadata %}</head><nav><a href="/docs/">D</a></nav>'
        )
        assert whole
        assert _wrapped(template) == ['<nav><a href="/docs/">D</a></nav>']

    def test_a_template_without_a_container_wraps_nothing(self) -> None:
        template, whole = _compiled('<p><a href="/docs/">D</a></p>')
        assert whole
        assert _wrapped(template) == []
        assert MARK not in _render(template)

    def test_a_loop_of_links_inside_the_container(self) -> None:
        template, whole = _compiled(
            "<nav>{% for url in urls %}"
            '<a href="{{ url }}">{{ url }}</a>{% endfor %}</nav><p>tail</p>'
        )
        assert whole
        html = _render(template, urls=["/a/", "/docs/"])
        assert html.count(MARK) == 1
        assert html.endswith("</nav><p>tail</p>")

    def test_text_between_variables_reads_as_text(self) -> None:
        template, whole = _compiled("<nav>{{ a }} and {{ b }}</nav>")
        assert whole
        assert len(_wrapped(template)) == 1

    def test_a_container_inside_a_branch(self) -> None:
        template, whole = _compiled(
            '{% if on %}<nav><a href="/docs/">D</a></nav>{% else %}<p>no</p>{% endif %}'
        )
        assert whole
        assert MARK in _render(template, on=True)
        assert _render(template, on=False) == "<p>no</p>"

    def test_a_nested_container_of_its_own_name(self) -> None:
        template, whole = _compiled(
            '<nav><nav><a href="/x/">X</a></nav><a href="/docs/">D</a></nav><p>t</p>'
        )
        assert whole
        assert len(_wrapped(template)) == 1
        assert MARK in _render(template)

    def test_an_attribute_selector_and_a_split_start_tag(self) -> None:
        template, whole = _compiled(
            '<div class="{{ c }}" role="navigation"><a href="/docs/">D</a></div>'
        )
        assert whole
        assert len(template.nodelist.get_nodes_by_type(ContainerNode)) == 1
        assert MARK in _render(template, c="menu")

    def test_a_start_tag_a_branch_writes_into_counts_as_a_container(self) -> None:
        template, whole = _compiled(
            '<div {% if on %}data-next-nav{% endif %}><a href="/docs/">D</a></div>'
        )
        assert whole
        assert MARK in _render(template, on=True)
        assert MARK not in _render(template, on=False)

    def test_a_tag_with_a_value_it_cannot_read_counts_as_a_container(self) -> None:
        template, whole = _compiled(
            '<div role="{{ r }}"><a href="/docs/">D</a></div><p>t</p>'
        )
        assert whole
        assert MARK in _render(template, r="navigation")
        assert MARK not in _render(template, r="main")

    def test_raw_text_and_comments_hide_their_markup(self) -> None:
        template, whole = _compiled(
            '<script>let s = "<nav>";</script><!-- <nav> --><style>a{}</style>'
            '<title>t</title><nav><a href="/docs/">D</a></nav>'
        )
        assert whole
        assert _wrapped(template) == ['<nav><a href="/docs/">D</a></nav>']

    def test_a_declaration_and_a_stray_bracket_pass(self) -> None:
        template, whole = _compiled("<!DOCTYPE html><p>1 < 2</p><nav></nav>")
        assert whole
        assert _wrapped(template) == ["<nav></nav>"]

    def test_a_void_element_a_selector_names_closes_at_once(self) -> None:
        template, whole = _compiled('<img data-next-nav src="/x.png"><p>t</p>')
        assert whole
        assert _wrapped(template) == ['<img data-next-nav src="/x.png">']

    def test_a_verbatim_without_a_container_reads_as_text(self) -> None:
        template, whole = _compiled(
            "{% verbatim %}<p>{{ x }}</p>{% endverbatim %}<nav></nav>"
        )
        assert whole
        assert _wrapped(template) == ["<nav></nav>"]

    def test_neutral_tags_inside_a_start_tag_make_it_dynamic(self) -> None:
        template, whole = _compiled(
            LOADS + '<div {% collect_styles %}><a href="/docs/">D</a></div>'
        )
        assert whole
        assert len(template.nodelist.get_nodes_by_type(ContainerNode)) == 1

    def test_a_component_beside_a_container(self) -> None:
        template, whole = _compiled(LOADS + '<nav>{% component "x" %}</nav>')
        assert whole
        assert len(template.nodelist.get_nodes_by_type(ContainerNode)) == 1


class TestBlindSpots:
    """A template whose containers the source cannot show keeps the document pass."""

    @pytest.mark.parametrize(
        "source",
        [
            pytest.param('{% include "x.html" %}', id="include"),
            pytest.param(
                "<nav>{% if on %}</nav>{% endif %}", id="branch-closes-the-container"
            ),
            pytest.param(
                "{% if on %}<p>{% else %}<p x='{% endif %}", id="branch-state"
            ),
            pytest.param("<nav>", id="unclosed"),
            pytest.param("{% if on %}<script>{% endif %}", id="branch-opens-raw-text"),
            pytest.param('<div class="{% component "x" %}">', id="component-in-tag"),
            pytest.param(
                "{% verbatim %}<nav></nav>{% endverbatim %}", id="verbatim-container"
            ),
            pytest.param('<a href="x', id="open-start-tag"),
        ],
    )
    def test_a_blind_source_changes_nothing(self, source: str) -> None:
        template, whole = _compiled(LOADS + source)
        assert not whole
        assert template.nodelist.get_nodes_by_type(ContainerNode) == []

    def test_a_zone_whose_wrapper_is_a_container(self) -> None:
        links = TemplateLinks(("[data-next-zone]",))
        template = Template('{% load partial %}{% zone "list" %}<p>x</p>{% endzone %}')
        assert not compile_containers(template, links)

    def test_a_zone_wrapping_a_container(self) -> None:
        _, whole = _compiled(
            '{% load partial %}{% zone "list" %}<nav></nav>{% endzone %}'
        )
        assert whole

    def test_the_result_is_held_for_the_containers_it_was_taken_for(self) -> None:
        template, whole = _compiled("<nav></nav>")
        assert whole
        assert compile_containers(template, LINKS)
        assert not compile_containers(template, TemplateLinks(("main",)))


class TestOpens:
    def test_a_tag_without_a_name_opens_nothing(self) -> None:
        assert not LINKS.opens("<>")
        assert LINKS.opens('<div role="navigation">')


class TestFragments:
    """The outermost fragment alone marks, and nothing marks while no pass is bound."""

    def test_no_bound_pass_leaves_the_html(self) -> None:
        template, _ = _compiled('<nav><a href="/docs/">D</a></nav>')
        request = RequestFactory().get("/docs/")
        html = template.render(Context({REQUEST_KEY: request}))
        assert MARK not in html

    def test_an_inner_fragment_leaves_the_marks_to_the_outer(self) -> None:
        seen: list[str] = []

        def marks(html: str) -> str:
            seen.append(html)
            return html

        token = fragment_marks.set(marks)
        try:
            out = marked(lambda: "<a>" + marked(lambda: "<b>") + "</a>")
        finally:
            fragment_marks.reset(token)
        assert (out, seen) == ("<a><b></a>", ["<a><b></a>"])

    def test_a_container_of_text_alone_is_read_at_compile_time(self) -> None:
        template, _ = _compiled(
            '<nav><a href="/docs/">D</a></nav><aside>{{ x }}<a href="/">H</a></aside>'
            '<div class="x"><nav><a href="/">{{ y }}</a></nav></div>'
        )
        held = [
            node.fixed is not None
            for node in template.nodelist.get_nodes_by_type(ContainerNode)
        ]
        assert held == [True, False]
        assert _render(template).count(MARK) == 1

    def test_a_fixed_container_inside_a_fragment_leaves_the_marks_to_it(self) -> None:
        template, _ = _compiled('<nav><a href="/docs/">D</a></nav>')
        recorder = _Recorder()
        token = fragment_marks.set(recorder)
        try:
            out = marked(lambda: "<p>" + template.render(Context()) + "</p>")
        finally:
            fragment_marks.reset(token)
        assert recorder.seen == [out] == ['<p><nav><a href="/docs/">D</a></nav></p>']


class _Recorder:
    """A bound pass that records each output it marks and compiles like the page."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    def __call__(self, html: str) -> str:
        self.seen.append(html)
        return html

    def compiled(self, template: Template) -> bool:
        return compile_containers(template, LINKS)

    def fixed(self, text: FixedText) -> str:
        self.seen.append(text.html)
        return text.html


def _component(tmp_path: Path, source: str) -> ComponentInfo:
    (tmp_path / "c.djx").write_text(source)
    return ComponentInfo(
        name="c",
        scope_root=tmp_path,
        scope_relative="",
        template_path=tmp_path / "c.djx",
        module_path=None,
        is_simple=True,
    )


def _call(tmp_path: Path, source: str, page: str) -> tuple[str, list[str]]:
    info = _component(tmp_path, source)
    recorder = _Recorder()
    token = fragment_marks.set(recorder)
    try:
        with patch.object(components_manager, "get_component", return_value=info):
            html = Template(LOADS + page).render(
                Context({"current_template_path": str(tmp_path / "page.djx")})
            )
    finally:
        fragment_marks.reset(token)
    return html, recorder.seen


class TestComponentFragments:
    """A call with no body leaves its links to a template that marks its own."""

    def test_a_template_without_a_container_runs_no_pass(self, tmp_path: Path) -> None:
        html, seen = _call(
            tmp_path, "<p>{{ title }}</p>", '{% component "c" title="T" %}'
        )
        assert (html, seen) == ("<p>T</p>", [])

    def test_a_container_of_the_template_marks_alone(self, tmp_path: Path) -> None:
        html, seen = _call(
            tmp_path, '<p>x</p><nav><a href="/">H</a></nav>', '{% component "c" %}'
        )
        assert seen == ['<nav><a href="/">H</a></nav>']
        assert html == '<p>x</p><nav><a href="/">H</a></nav>'

    def test_a_blind_template_is_marked_whole(self, tmp_path: Path) -> None:
        html, seen = _call(
            tmp_path, "<p>{% firstof x 'y' %}</p>", '{% component "c" %}'
        )
        assert seen == [html] == ["<p>y</p>"]

    def test_a_call_with_children_is_marked_whole(self, tmp_path: Path) -> None:
        html, seen = _call(
            tmp_path,
            "<div>{{ children }}</div>",
            '{% #component "c" %}<nav><a href="/">H</a></nav>{% /component %}',
        )
        assert seen == [html] == ['<div><nav><a href="/">H</a></nav></div>']

    def test_a_link_in_the_template_is_marked_for_the_request(
        self, tmp_path: Path
    ) -> None:
        info = _component(tmp_path, '<nav><a href="/docs/">D</a></nav>')
        with patch.object(components_manager, "get_component", return_value=info):
            html = _render(
                Template(LOADS + '{% component "c" %}'),
                current_template_path=str(tmp_path / "page.djx"),
            )
        assert MARK in html
