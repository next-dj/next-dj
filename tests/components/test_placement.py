import pytest
from django.template import Context, Engine, Template
from django.template.base import TextNode
from django.template.loader_tags import BlockNode, IncludeNode

from next.components.nodes import ComponentTagNode
from next.components.placement import (
    Known,
    Placement,
    block_named,
    captured_includes,
    finds,
    included,
    nodelists,
    rendering_blocks,
    same,
    significant,
)
from tests.components.sites import ComponentSite, template_engine


def _is_call(node: object) -> bool:
    return isinstance(node, ComponentTagNode)


def _compiled(source: str) -> Template:
    return template_engine({}).from_string(source)


def _blocks(template: Template) -> list[BlockNode]:
    return list(template.nodelist.get_nodes_by_type(BlockNode))


class TestSignificant:
    """Whitespace between the tags of a component is no content."""

    @pytest.mark.parametrize(
        ("source", "meant"),
        [
            pytest.param("  \n", False, id="whitespace"),
            pytest.param("text", True, id="text"),
            pytest.param("{{ x }}", True, id="variable"),
        ],
    )
    def test_only_blank_text_is_skipped(self, source: str, *, meant: bool) -> None:
        (node,) = _compiled(source).nodelist
        assert significant(node) is meant


class TestFinds:
    """A node is found at any depth, through every nodelist a tag holds."""

    @pytest.mark.parametrize(
        "source",
        [
            "{% component 'c' %}",
            "{% if a %}{% component 'c' %}{% endif %}",
            "{% if a %}x{% else %}{% component 'c' %}{% endif %}",
            "{% for i in xs %}{% for j in xs %}{% component 'c' %}{% endfor %}{% endfor %}",
            "{% block b %}{% component 'c' %}{% endblock %}",
        ],
    )
    def test_a_call_is_found_at_any_depth(self, source: str) -> None:
        assert finds(_compiled(source).nodelist, _is_call) is True

    def test_a_template_without_a_call_finds_none(self) -> None:
        assert (
            finds(_compiled("{% if a %}x{% endif %}{{ y }}").nodelist, _is_call)
            is False
        )

    def test_a_block_is_picked_by_its_name(self) -> None:
        template = _compiled("{% block a %}A{% endblock %}{% block b %}B{% endblock %}")
        assert finds(template.nodelist, block_named("b")) is True
        assert finds(template.nodelist, block_named("c")) is False

    def test_a_node_of_another_kind_is_never_a_block(self) -> None:
        (text,) = (
            node for node in _compiled("x").nodelist if isinstance(node, TextNode)
        )
        assert block_named("x")(text) is False


class TestBlocks:
    """The blocks rendering around a node are read from the render context."""

    def test_only_the_block_layers_count_outermost_first(self) -> None:
        outer, inner = _blocks(
            _compiled("{% block a %}{% endblock %}{% block b %}{% endblock %}")
        )
        context = Context({"x": 1})
        context.push(block=outer)
        context.push(other=1)
        context.push(block="not a block")
        context.push(block=inner)
        assert rendering_blocks(context) == (outer, inner)

    def test_no_block_rendering_gives_none(self) -> None:
        assert rendering_blocks(Context({"x": 1})) == ()

    def test_the_nodelists_of_the_blocks_are_kept_by_identity(self) -> None:
        first, second = _blocks(
            _compiled("{% block a %}A{% endblock %}{% block b %}B{% endblock %}")
        )
        held = nodelists((first, second))
        assert held == (first.nodelist, second.nodelist)
        assert same(held, (first.nodelist, second.nodelist)) is True

    def test_a_copy_of_a_nodelist_is_not_the_same(self) -> None:
        (block,) = _blocks(_compiled("{% block a %}A{% endblock %}"))
        (twin,) = _blocks(_compiled("{% block a %}A{% endblock %}"))
        assert same((block.nodelist,), (twin.nodelist,)) is False

    def test_a_different_depth_is_not_the_same(self) -> None:
        (block,) = _blocks(_compiled("{% block a %}A{% endblock %}"))
        assert same((block.nodelist,), ()) is False
        assert same((), ()) is True


class TestCapturedIncludes:
    """Only the includes inside a `{% cache %}` body count as captured."""

    def test_the_includes_in_a_cache_body_are_held(self) -> None:
        template = _compiled(
            "{% load cache %}{% include 'free.html' %}"
            "{% cache 0 k %}{% if a %}{% include 'one.html' %}{% endif %}"
            "{% include 'two.html' %}{% endcache %}"
        )
        held = captured_includes(template)
        assert len(held) == 2
        assert all(isinstance(node, IncludeNode) for node in held)

    def test_a_template_without_a_cache_holds_none(self) -> None:
        assert captured_includes(_compiled("{% include 'free.html' %}")) == ()

    def test_the_answer_is_read_once_per_template(self) -> None:
        template = _compiled(
            "{% load cache %}{% cache 0 k %}{% include 'a' %}{% endcache %}"
        )
        assert captured_includes(template) is captured_includes(template)


class TestIncluded:
    """A node is included when a template an include rendered holds it, however deep."""

    @pytest.fixture()
    def engine(self) -> Engine:
        return template_engine(
            {
                "page.html": "{% include 'wrap.html' %}",
                "wrap.html": "<w>{% include 'part.html' %}</w>",
                "part.html": "{% component 'card' %}",
                "other.html": "{% component 'card' %}",
                "plain.html": "<p>{% component 'card' %}</p>",
            },
            cached=True,
        )

    def _rendered(
        self, site: ComponentSite, engine: Engine, name: str
    ) -> tuple[Context, tuple[IncludeNode, ...]]:
        site.add("card", "C")
        context = Context(site.context())
        template = engine.get_template(name)
        template.render(context)
        includes = tuple(template.nodelist.get_nodes_by_type(IncludeNode))
        return context, includes

    def test_a_call_in_a_nested_include_is_held(
        self, site: ComponentSite, engine: Engine
    ) -> None:
        context, includes = self._rendered(site, engine, "page.html")
        (call,) = engine.get_template("part.html").nodelist.get_nodes_by_type(
            ComponentTagNode
        )
        seen = context.render_context.dicts[0]
        assert included(call, includes, seen) is True

    def test_a_call_of_a_template_nothing_included_is_not_held(
        self, site: ComponentSite, engine: Engine
    ) -> None:
        context, includes = self._rendered(site, engine, "page.html")
        (call,) = engine.get_template("other.html").nodelist.get_nodes_by_type(
            ComponentTagNode
        )
        assert included(call, includes, context.render_context.dicts[0]) is False

    def test_an_include_that_rendered_nothing_holds_none(self, engine: Engine) -> None:
        template = engine.get_template("page.html")
        (call,) = engine.get_template("part.html").nodelist.get_nodes_by_type(
            ComponentTagNode
        )
        includes = tuple(template.nodelist.get_nodes_by_type(IncludeNode))
        assert included(call, includes, {}) is False


class TestPlacement:
    """The markup before a call decides its wrapper and the key of the row around it."""

    @pytest.mark.parametrize(
        ("source", "tag"),
        [
            pytest.param("{% component 'c' %}", "div", id="bare"),
            pytest.param("<p>{% component 'c' %}</p>", "div", id="paragraph"),
            pytest.param("<table>{% component 'c' %}</table>", None, id="table"),
        ],
    )
    def test_the_open_element_decides_the_wrapper(
        self, source: str, tag: str | None
    ) -> None:
        template = _compiled(source)
        assert Placement.of(((template.nodelist, _is_call),)).tag == tag

    def test_a_call_outside_a_loop_has_no_row_key(self) -> None:
        template = _compiled("<li data-next-key='x'>{% component 'c' %}")
        assert Placement.of(((template.nodelist, _is_call),)).recipe is None

    def test_a_loop_row_key_is_its_literal_and_variable_pieces(self) -> None:
        template = _compiled(
            "{% for p in xs %}<li data-next-key='a-{{ p }}-z'>{% component 'c' %}"
            "</li>{% endfor %}"
        )
        recipe = Placement.of(((template.nodelist, _is_call),)).recipe
        assert recipe is not None
        first, middle, last = recipe
        assert (first, last) == ("a-", "-z")
        assert middle.var.var == "p"

    def test_a_constant_row_key_is_one_literal_piece(self) -> None:
        template = _compiled(
            "{% for p in xs %}<li data-next-key='const'>{% component 'c' %}"
            "</li>{% endfor %}"
        )
        assert Placement.of(((template.nodelist, _is_call),)).recipe == ("const",)

    @pytest.mark.parametrize(
        "before",
        ["<title-bar></title-bar>", "<script-loader></script-loader>", "<!-->"],
    )
    def test_the_row_key_reads_past_markup_that_hides_nothing(
        self, before: str
    ) -> None:
        template = _compiled(
            f"{{% for p in xs %}}{before}<tr data-next-key='const'>"
            "{% component 'c' %}</tr>{% endfor %}"
        )
        assert Placement.of(((template.nodelist, _is_call),)).recipe == ("const",)

    def test_a_loop_without_a_keyed_row_has_no_key(self) -> None:
        template = _compiled(
            "{% for p in xs %}<li>{% component 'c' %}</li>{% endfor %}"
        )
        assert Placement.of(((template.nodelist, _is_call),)).recipe is None

    def test_a_path_through_a_block_reads_the_text_before_each_step(self) -> None:
        template = _compiled("<table>{% block rows %}{% component 'c' %}{% endblock %}")
        (block,) = _blocks(template)
        placement = Placement.of(
            ((template.nodelist, block_named("rows")), (block.nodelist, _is_call))
        )
        assert placement.tag is None


class TestKnown:
    """What a call works out once starts empty and holds a placement per template."""

    def test_a_call_starts_with_nothing_known(self) -> None:
        known = Known()
        assert (known.site, known.key) == ("", "")
        assert len(known.placed) == 0
