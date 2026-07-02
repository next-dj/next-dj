import pytest
from django.template import Context, Engine, TemplateSyntaxError
from django.template.base import Template
from django.test import RequestFactory

from next.partial.zone import (
    LAZY_ATTR,
    POLL_ATTR,
    ZONE_ATTR,
    ZoneNode,
    ZoneOptions,
    ZonePartial,
    render_zone_standalone,
)


def _render(source: str, **ctx: object) -> str:
    return Template(source).render(Context(ctx))


class TestZoneTagFullRender:
    """A non-lazy zone renders inline inside its marker wrapper."""

    def test_default_wrapper_is_div(self) -> None:
        out = _render('a {% zone "hi" %}<p>{{ msg }}</p>{% endzone %} b', msg="x")
        assert out == f'a <div {ZONE_ATTR}="hi"><p>x</p></div> b'

    def test_tag_kwarg_changes_wrapper(self) -> None:
        out = _render('{% zone "box" tag="tbody" %}<td>{{ n }}</td>{% endzone %}', n=7)
        assert out == f'<tbody {ZONE_ATTR}="box"><td>7</td></tbody>'

    def test_lazy_renders_only_placeholder(self) -> None:
        source = (
            '{% zone "lz" lazy="revealed" %}'
            "<table>{{ heavy }}</table>"
            "{% placeholder %}<div>loading</div>{% endzone %}"
        )
        out = _render(source, heavy="MUST-NOT-APPEAR")
        assert "MUST-NOT-APPEAR" not in out
        assert out == (
            f'<div {ZONE_ATTR}="lz" {LAZY_ATTR}="revealed"><div>loading</div></div>'
        )

    def test_lazy_load_trigger(self) -> None:
        source = '{% zone "lz" lazy="load" %}body{% placeholder %}ph{% endzone %}'
        out = _render(source)
        assert f'{LAZY_ATTR}="load"' in out

    def test_poll_seconds_renders_interval_inline(self) -> None:
        out = _render('{% zone "t" poll="5s" %}<p>{{ n }}</p>{% endzone %}', n=7)
        assert out == f'<div {ZONE_ATTR}="t" {POLL_ATTR}="5000"><p>7</p></div>'

    def test_poll_milliseconds_literal(self) -> None:
        out = _render('{% zone "t" poll="1500ms" %}body{% endzone %}')
        assert out == f'<div {ZONE_ATTR}="t" {POLL_ATTR}="1500">body</div>'

    def test_poll_bare_number_reads_as_milliseconds(self) -> None:
        out = _render('{% zone "t" poll="3000" %}body{% endzone %}')
        assert out == f'<div {ZONE_ATTR}="t" {POLL_ATTR}="3000">body</div>'

    def test_poll_at_the_ceiling_renders(self) -> None:
        out = _render('{% zone "t" poll="2147483647" %}body{% endzone %}')
        assert out == f'<div {ZONE_ATTR}="t" {POLL_ATTR}="2147483647">body</div>'

    def test_poll_literal_outer_whitespace_is_stripped(self) -> None:
        out = _render('{% zone "t" poll=" 5s " %}body{% endzone %}')
        assert out == f'<div {ZONE_ATTR}="t" {POLL_ATTR}="5000">body</div>'

    def test_full_render_is_byte_for_byte_outside_wrapper(self) -> None:
        plain = _render("before <p>{{ msg }}</p> after", msg="hi")
        zoned = _render(
            'before {% zone "z" %}<p>{{ msg }}</p>{% endzone %} after', msg="hi"
        )
        opener = f'<div {ZONE_ATTR}="z">'
        assert zoned == plain.replace("<p>hi</p>", f"{opener}<p>hi</p></div>")


class TestZoneTagSyntax:
    """The compile hook rejects malformed zone tags."""

    def test_missing_name(self) -> None:
        with pytest.raises(TemplateSyntaxError):
            Template("{% zone %}body{% endzone %}")

    def test_empty_name(self) -> None:
        with pytest.raises(TemplateSyntaxError):
            Template('{% zone "" %}body{% endzone %}')

    def test_unknown_lazy_trigger(self) -> None:
        with pytest.raises(TemplateSyntaxError):
            Template('{% zone "z" lazy="whenever" %}b{% placeholder %}p{% endzone %}')

    def test_invalid_poll_literal_raises(self) -> None:
        with pytest.raises(TemplateSyntaxError):
            Template('{% zone "z" poll="soon" %}b{% endzone %}')

    # U+0665 is the Arabic-Indic digit five, which int() accepts but the
    # strict ASCII-digit grammar must not.
    @pytest.mark.parametrize("literal", ["1_000", "+5s", "5 s", "\u0665s"])
    def test_malformed_poll_literal_raises_with_the_grammar_hint(
        self, literal: str
    ) -> None:
        with pytest.raises(TemplateSyntaxError, match="5s or 1500ms"):
            Template('{% zone "z" poll="' + literal + '" %}b{% endzone %}')

    def test_poll_below_floor_raises(self) -> None:
        with pytest.raises(TemplateSyntaxError):
            Template('{% zone "z" poll="100ms" %}b{% endzone %}')

    def test_poll_above_ceiling_raises(self) -> None:
        with pytest.raises(TemplateSyntaxError):
            Template('{% zone "z" poll="2592000s" %}b{% endzone %}')

    def test_placeholder_without_lazy_raises(self) -> None:
        with pytest.raises(TemplateSyntaxError):
            Template('{% zone "z" %}b{% placeholder %}p{% endzone %}')

    def test_placeholder_with_poll_raises(self) -> None:
        with pytest.raises(TemplateSyntaxError):
            Template('{% zone "z" poll="5s" %}b{% placeholder %}p{% endzone %}')

    def test_poll_and_lazy_are_exclusive(self) -> None:
        with pytest.raises(TemplateSyntaxError):
            Template('{% zone "z" lazy="load" poll="5s" %}b{% endzone %}')

    def test_empty_poll_raises(self) -> None:
        with pytest.raises(TemplateSyntaxError):
            Template('{% zone "z" poll="" %}b{% endzone %}')

    def test_stray_bits_ignored(self) -> None:
        out = _render('{% zone "z" garbage %}body{% endzone %}')
        assert out == f'<div {ZONE_ATTR}="z">body</div>'


class TestZonePartialStandalone:
    """The zone body partial renders alone with the full page context."""

    def test_standalone_render(self) -> None:
        source = '{% zone "z" %}<i>{{ value }}</i>{% endzone %}'
        template = Template(source)
        node = template.nodelist.get_nodes_by_type(ZoneNode)[0]
        partial = node.partial
        assert isinstance(partial, ZonePartial)
        out = partial.render(Context({"value": "deep"}))
        assert out == "<i>deep</i>"

    def test_partial_carries_origin_and_engine(self) -> None:
        template = Template('{% zone "z" %}body{% endzone %}')
        node = template.nodelist.get_nodes_by_type(ZoneNode)[0]
        assert node.partial.origin is template.origin
        assert node.partial.name == "z"
        assert node.partial.engine is not None


class TestZoneOptions:
    """`ZoneOptions` precomputes the wrapper attributes at parse time."""

    def test_plain_options_have_no_attrs(self) -> None:
        options = ZoneOptions()
        assert options.full_attrs == ""
        assert options.delivery_attrs == ""

    def test_poll_attr_appears_on_both_render_paths(self) -> None:
        options = ZoneOptions(poll=5000)
        assert options.full_attrs == f' {POLL_ATTR}="5000"'
        assert options.delivery_attrs == options.full_attrs

    def test_lazy_attr_is_dropped_from_delivery(self) -> None:
        options = ZoneOptions(lazy="load")
        assert options.full_attrs == f' {LAZY_ATTR}="load"'
        assert options.delivery_attrs == ""

    def test_parsed_node_carries_options(self) -> None:
        template = Template('{% zone "p" tag="ul" poll="5s" %}b{% endzone %}')
        node = template.nodelist.get_nodes_by_type(ZoneNode)[0]
        assert node.options == ZoneOptions(tag="ul", poll=5000)


class TestRenderZoneStandalone:
    """`render_zone_standalone` wraps the body for a delivered partial."""

    def test_wraps_body_without_lazy_hint(self) -> None:
        template = Template(
            '{% zone "lz" tag="section" lazy="load" %}'
            "<b>{{ v }}</b>{% placeholder %}ph{% endzone %}"
        )
        node = template.nodelist.get_nodes_by_type(ZoneNode)[0]
        out = render_zone_standalone(
            node.partial, node.name, node.options, Context({"v": "x"})
        )
        assert out == f'<section {ZONE_ATTR}="lz"><b>x</b></section>'
        assert LAZY_ATTR not in out

    def test_standalone_render_carries_poll_interval(self) -> None:
        # The client re-reads the interval from the live element after every
        # morph, so a delivered poll zone must keep the hint on its wrapper.
        template = Template('{% zone "p" poll="5s" %}<b>{{ v }}</b>{% endzone %}')
        node = template.nodelist.get_nodes_by_type(ZoneNode)[0]
        out = render_zone_standalone(
            node.partial, node.name, node.options, Context({"v": "x"})
        )
        assert out == f'<div {ZONE_ATTR}="p" {POLL_ATTR}="5000"><b>x</b></div>'

    def test_standalone_render_without_poll_stays_plain(self) -> None:
        template = Template('{% zone "p" %}<b>{{ v }}</b>{% endzone %}')
        node = template.nodelist.get_nodes_by_type(ZoneNode)[0]
        out = render_zone_standalone(
            node.partial, node.name, node.options, Context({"v": "x"})
        )
        assert out == f'<div {ZONE_ATTR}="p"><b>x</b></div>'
        assert POLL_ATTR not in out


class TestZoneDebugContract:
    """An exception in the zone body keeps an honest debug traceback."""

    def test_template_debug_is_populated(self) -> None:
        engine = Engine(debug=True, builtins=["next.templatetags.partial"])
        template = Template(
            'head {% zone "z" %}<p>{{ bad }}</p>{% endzone %} tail', engine=engine
        )

        class Boom:
            def __str__(self) -> str:
                msg = "boom-in-zone"
                raise ValueError(msg)

        with pytest.raises(ValueError, match="boom-in-zone") as exc_info:
            template.render(Context({"bad": Boom()}))
        debug = exc_info.value.template_debug  # type: ignore[attr-defined]
        assert debug["during"] == "{{ bad }}"

    def test_get_exception_info_without_page_template_is_empty(self) -> None:
        template = Template('{% zone "z" %}body{% endzone %}')
        node = template.nodelist.get_nodes_by_type(ZoneNode)[0]
        token = node.token
        assert node.partial.get_exception_info(ValueError("x"), token) == {}


class TestRequestFixture:
    """The request factory feeds the standalone render context."""

    def test_request_in_context(self) -> None:
        request = RequestFactory().get("/")
        source = '{% zone "z" %}{{ request.path }}{% endzone %}'
        template = Template(source)
        node = template.nodelist.get_nodes_by_type(ZoneNode)[0]
        out = node.partial.render(Context({"request": request}))
        assert out == "/"
