from pathlib import Path

import pytest
from django.template import Context, Engine, TemplateSyntaxError
from django.template.base import Template
from django.template.defaulttags import ForNode, IfNode
from django.test import RequestFactory

from next.partial.zone import (
    DELAY_ATTR,
    POLL_ATTR,
    RELOAD_ATTR,
    ZONE_ATTR,
    ZoneNode,
    ZoneOptions,
    ZonePartial,
    render_zone_body,
    render_zone_standalone,
    zones_under,
)
from next.permissions.nodes import HasPermNode
from next.seeding import COLLECTOR_KEY, TEMPLATE_PATH_KEY
from next.static import StaticCollector
from next.static.runtime import BUNDLE_NOTE


def _render(source: str, **ctx) -> str:
    return Template(source).render(Context(ctx))


class TestZoneTagFullRender:
    """A zone renders inline inside its marker wrapper."""

    def test_default_wrapper_is_div(self) -> None:
        out = _render('a {% zone "hi" %}<p>{{ msg }}</p>{% endzone %} b', msg="x")
        assert out == f'a <div {ZONE_ATTR}="hi"><p>x</p></div> b'

    def test_tag_kwarg_changes_wrapper(self) -> None:
        out = _render('{% zone "box" tag="tbody" %}<td>{{ n }}</td>{% endzone %}', n=7)
        assert out == f'<tbody {ZONE_ATTR}="box"><td>7</td></tbody>'

    def test_a_loading_reload_is_written_on_the_wrapper(self) -> None:
        out = _render('{% zone "r" reload="loading" %}<p>{{ n }}</p>{% endzone %}', n=1)
        assert out == f'<div {ZONE_ATTR}="r" {RELOAD_ATTR}="loading"><p>1</p></div>'

    def test_the_default_reload_writes_nothing(self) -> None:
        out = _render('{% zone "r" reload="keep" %}body{% endzone %}')
        assert out == f'<div {ZONE_ATTR}="r">body</div>'

    @pytest.mark.parametrize(
        ("literal", "ms"),
        [
            pytest.param("400ms", "400", id="milliseconds"),
            pytest.param("1s", "1000", id="seconds"),
            pytest.param("0.25s", "250", id="fraction"),
            pytest.param("0ms", "0", id="zero"),
        ],
    )
    def test_a_delay_is_written_in_milliseconds(self, literal: str, ms: str) -> None:
        out = _render(
            '{% zone "r" reload="loading" delay="' + literal + '" %}b{% endzone %}'
        )
        assert out == (
            f'<div {ZONE_ATTR}="r" {RELOAD_ATTR}="loading" {DELAY_ATTR}="{ms}">b</div>'
        )

    def test_the_default_delay_writes_nothing(self) -> None:
        out = _render('{% zone "r" reload="loading" delay="150ms" %}b{% endzone %}')
        assert DELAY_ATTR not in out

    def test_poll_and_a_loading_reload_combine(self) -> None:
        out = _render('{% zone "t" poll="5s" reload="loading" %}b{% endzone %}')
        assert out == (
            f'<div {ZONE_ATTR}="t" {POLL_ATTR}="5000" {RELOAD_ATTR}="loading">b</div>'
        )

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

    def test_html_attributes_follow_the_framework_ones(self) -> None:
        out = _render(
            '{% zone "h" poll="5s" class="sticky top-0" aria-label="Cart" '
            'data-role="bar" id="cart" %}b{% endzone %}'
        )
        assert out == (
            f'<div {ZONE_ATTR}="h" {POLL_ATTR}="5000" class="sticky top-0" '
            'aria-label="Cart" data-role="bar" id="cart">b</div>'
        )

    def test_an_html_attribute_value_is_escaped(self) -> None:
        out = _render('{% zone "h" title="a&\\"b<" %}b{% endzone %}')
        assert out == f'<div {ZONE_ATTR}="h" title="a&amp;&quot;b&lt;">b</div>'

    def test_a_repeated_html_attribute_keeps_the_last_value(self) -> None:
        out = _render('{% zone "h" class="a" class="b" %}b{% endzone %}')
        assert out == f'<div {ZONE_ATTR}="h" class="b">b</div>'

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

    def test_lazy_is_an_unknown_option(self) -> None:
        with pytest.raises(TemplateSyntaxError) as raised:
            Template('{% zone "z" lazy="load" %}b{% endzone %}')
        assert str(raised.value) == (
            "{% zone %} got an unknown option 'lazy'. The valid options are tag, "
            "poll, reload, and delay, plus the HTML global, aria-* and data-* "
            "attributes of the wrapper."
        )

    def test_placeholder_is_an_unknown_tag(self) -> None:
        with pytest.raises(TemplateSyntaxError) as raised:
            Template('{% zone "z" %}b{% placeholder %}p{% endzone %}')
        assert str(raised.value) == (
            "Invalid block tag on line 1: 'placeholder', expected 'endzone'. "
            "Did you forget to register or load this tag?"
        )

    def test_unknown_reload_mode(self) -> None:
        with pytest.raises(TemplateSyntaxError, match="reload must be one of keep"):
            Template('{% zone "z" reload="skeleton" %}b{% endzone %}')

    @pytest.mark.parametrize("literal", ["300", "soon", "-1s", "2d", "86401s"])
    def test_a_malformed_delay_raises(self, literal: str) -> None:
        with pytest.raises(TemplateSyntaxError, match="delay= refuses its value"):
            Template(
                '{% zone "z" reload="loading" delay="' + literal + '" %}b{% endzone %}'
            )

    def test_a_delay_without_a_loading_reload_raises(self) -> None:
        with pytest.raises(TemplateSyntaxError, match='reload="loading" only'):
            Template('{% zone "z" delay="300ms" %}b{% endzone %}')

    def test_invalid_poll_literal_raises(self) -> None:
        with pytest.raises(TemplateSyntaxError):
            Template('{% zone "z" poll="soon" %}b{% endzone %}')

    # U+0665 is Arabic-Indic five, which int() accepts and the grammar must not.
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

    def test_empty_poll_raises(self) -> None:
        with pytest.raises(TemplateSyntaxError):
            Template('{% zone "z" poll="" %}b{% endzone %}')

    def test_stray_bit_without_equals_raises(self) -> None:
        with pytest.raises(TemplateSyntaxError, match="'garbage' is missing '='"):
            Template('{% zone "z" garbage %}body{% endzone %}')

    def test_spaces_around_equals_raise(self) -> None:
        # poll = "5s" splits into three bits and must fail loudly, not drop poll
        with pytest.raises(TemplateSyntaxError, match='key="value"'):
            Template('{% zone "z" poll = "5s" %}body{% endzone %}')

    def test_unknown_option_key_raises(self) -> None:
        with pytest.raises(TemplateSyntaxError, match="unknown option 'pol'"):
            Template('{% zone "z" pol="5s" %}body{% endzone %}')

    def test_unknown_option_error_lists_the_valid_keys(self) -> None:
        with pytest.raises(TemplateSyntaxError, match="tag, poll, reload, and delay"):
            Template('{% zone "z" lzay="load" %}body{% endzone %}')

    @pytest.mark.parametrize(
        "key",
        [
            "data-next-zone",
            "data-next-poll",
            "data-next-reload",
            "data-next-delay",
            "data-next-busy",
            "data-next-keep",
            "data-next-target",
            "data-next-transition",
            "data-next-boundary",
            "data-next-scrolled",
        ],
    )
    def test_a_runtime_attribute_is_refused(self, key: str) -> None:
        with pytest.raises(TemplateSyntaxError) as raised:
            Template('{% zone "z" ' + key + '="x" %}b{% endzone %}')
        assert str(raised.value) == (
            f"{{% zone %}} reserves the {key!r} attribute for the framework."
        )

    @pytest.mark.parametrize(
        "key",
        [
            "data-next-current",
            "data-next-focus",
            "data-next-match",
            "data-next-nav",
            "data-next-persist",
            "data-next-prefetch",
            "data-next-router",
            "data-next-scroll",
            "data-next-scroll-region",
            "data-next-toasts",
        ],
    )
    def test_an_author_attribute_reaches_the_wrapper(self, key: str) -> None:
        out = _render('{% zone "z" ' + key + '="on" %}b{% endzone %}')
        assert out == f'<div {ZONE_ATTR}="z" {key}="on">b</div>'

    def test_reserve_off_reaches_the_wrapper(self) -> None:
        out = _render(
            '{% zone "z" reload="loading" data-next-reserve="off" %}b{% endzone %}'
        )
        assert 'data-next-reload="loading" data-next-reserve="off">b</div>' in out

    @pytest.mark.parametrize("value", ["on", "", "OFF"])
    def test_reserve_takes_off_alone(self, value: str) -> None:
        with pytest.raises(TemplateSyntaxError) as raised:
            Template('{% zone "z" data-next-reserve="' + value + '" %}b{% endzone %}')
        assert str(raised.value) == (
            f"{{% zone %}} data-next-reserve must be off, got {value!r}."
        )

    @pytest.mark.parametrize("key", ["onclick", "Class", "data-", "aria-X", "nonce"])
    def test_a_name_outside_the_global_attributes_is_unknown(self, key: str) -> None:
        with pytest.raises(TemplateSyntaxError, match=f"unknown option {key!r}"):
            Template('{% zone "z" ' + key + '="x" %}b{% endzone %}')

    def test_an_unquoted_html_attribute_is_refused(self) -> None:
        with pytest.raises(TemplateSyntaxError, match="class= must be a quoted"):
            Template('{% zone "z" class=css %}b{% endzone %}')

    def test_poll_grammar_error_says_literals_only(self) -> None:
        with pytest.raises(TemplateSyntaxError, match="Template variables"):
            Template('{% zone "z" poll=interval %}b{% endzone %}')

    def test_a_variable_name_is_refused_rather_than_read_as_a_zone(self) -> None:
        with pytest.raises(TemplateSyntaxError, match="name must be a quoted literal"):
            Template("{% zone name %}body{% endzone %}")

    def test_a_half_quoted_name_is_refused(self) -> None:
        with pytest.raises(TemplateSyntaxError, match="must be a quoted literal"):
            Template("{% zone \"z' %}body{% endzone %}")

    def test_an_unquoted_option_value_is_refused(self) -> None:
        with pytest.raises(TemplateSyntaxError, match="reload= must be a quoted"):
            Template('{% zone "z" reload=loading %}b{% endzone %}')

    def test_an_escaped_quote_survives_the_name_literal(self) -> None:
        template = Template('{% zone "a\\"b" %}body{% endzone %}')
        assert template.nodelist.get_nodes_by_type(ZoneNode)[0].name == 'a"b'


_LOADING_ZONE = '{% zone "rows" reload="loading" %}<p>{{ n }}</p>{% endzone %}'
_LOADING_WRAPPER = f'<div {ZONE_ATTR}="rows" {RELOAD_ATTR}="loading"><p>1</p></div>'


def _page_with_skeleton(directory: Path, body: str = _LOADING_ZONE) -> Path:
    template = directory / "template.djx"
    template.write_text(body)
    (directory / "loading.rows.djx").write_text("<p>{{ who }} {{ n }}</p>")
    return template


class TestZoneSkeletonStash:
    """A zone reloading to its loading UI stashes its `loading.<zone>.djx` after it."""

    def test_the_skeleton_renders_with_the_call_site_context(
        self, tmp_path: Path
    ) -> None:
        template = _page_with_skeleton(tmp_path)
        out = _render(_LOADING_ZONE, n=1, who="<b>", **{TEMPLATE_PATH_KEY: template})
        assert out == (
            f"{_LOADING_WRAPPER}"
            '<template data-next-loading="rows"><p>&lt;b&gt; 1</p></template>'
        )

    def test_a_string_template_path_finds_the_skeleton(self, tmp_path: Path) -> None:
        template = _page_with_skeleton(tmp_path)
        out = _render(_LOADING_ZONE, n=1, **{TEMPLATE_PATH_KEY: str(template)})
        assert '<template data-next-loading="rows">' in out

    def test_a_keep_zone_stashes_nothing(self, tmp_path: Path) -> None:
        source = '{% zone "rows" %}<p>{{ n }}</p>{% endzone %}'
        template = _page_with_skeleton(tmp_path, source)
        out = _render(source, n=1, **{TEMPLATE_PATH_KEY: template})
        assert out == f'<div {ZONE_ATTR}="rows"><p>1</p></div>'

    def test_a_zone_without_a_skeleton_stays_busy_only(self, tmp_path: Path) -> None:
        template = tmp_path / "template.djx"
        template.write_text(_LOADING_ZONE)
        assert _render(_LOADING_ZONE, n=1, **{TEMPLATE_PATH_KEY: template}) == (
            _LOADING_WRAPPER
        )

    def test_a_render_without_a_template_path_stashes_nothing(self) -> None:
        assert _render(_LOADING_ZONE, n=1) == _LOADING_WRAPPER

    def test_a_skeleton_cannot_close_its_stash(self, tmp_path: Path) -> None:
        template = tmp_path / "template.djx"
        template.write_text(_LOADING_ZONE)
        (tmp_path / "loading.rows.djx").write_text(
            '<template id="row"><p>r</p></template>{{ who }}</template>'
        )
        out = _render(
            _LOADING_ZONE,
            n=1,
            who="</template><script>alert(1)</script>",
            **{TEMPLATE_PATH_KEY: template},
        )
        assert out == (
            f"{_LOADING_WRAPPER}"
            '<template data-next-loading="rows"><template id="row"><p>r</p></template>'
            "&lt;/template&gt;&lt;script&gt;alert(1)&lt;/script&gt;&lt;/template>"
            "</template>"
        )

    def test_a_stashed_zone_carries_its_skeleton_inside(self, tmp_path: Path) -> None:
        template = _page_with_skeleton(tmp_path)
        node = Template(_LOADING_ZONE).nodelist.get_nodes_by_type(ZoneNode)[0]
        context = Context({"n": 1, "who": "w", TEMPLATE_PATH_KEY: template})
        body, wrapped = render_zone_body(
            node.partial, node.name, node.options, context, stashed=True
        )
        assert body == "<p>1</p>"
        assert wrapped == (
            f'<div {ZONE_ATTR}="rows" {RELOAD_ATTR}="loading"><p>1</p>'
            '<template data-next-loading="rows"><p>w 1</p></template></div>'
        )

    def test_a_zone_body_alone_carries_no_skeleton(self, tmp_path: Path) -> None:
        template = _page_with_skeleton(tmp_path)
        node = Template(_LOADING_ZONE).nodelist.get_nodes_by_type(ZoneNode)[0]
        context = Context({"n": 1, TEMPLATE_PATH_KEY: template})
        _body, wrapped = render_zone_body(
            node.partial, node.name, node.options, context
        )
        assert wrapped == _LOADING_WRAPPER

    def test_one_stash_per_zone_name_and_render(self, tmp_path: Path) -> None:
        source = _LOADING_ZONE + _LOADING_ZONE
        template = _page_with_skeleton(tmp_path, source)
        out = _render(source, n=1, **{TEMPLATE_PATH_KEY: template})
        assert out.count("<template data-next-loading=") == 1


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
    """`ZoneOptions` derives the wrapper attributes from the mode options."""

    def test_plain_options_have_no_attrs(self) -> None:
        assert ZoneOptions().attrs == ""

    def test_poll_attr(self) -> None:
        assert ZoneOptions(poll=5000).attrs == f' {POLL_ATTR}="5000"'

    def test_reload_and_delay_attrs(self) -> None:
        options = ZoneOptions(reload="loading", delay=400)
        assert options.attrs == f' {RELOAD_ATTR}="loading" {DELAY_ATTR}="400"'

    def test_parsed_reload_options(self) -> None:
        template = Template('{% zone "p" reload="loading" delay="1s" %}b{% endzone %}')
        node = template.nodelist.get_nodes_by_type(ZoneNode)[0]
        assert node.options == ZoneOptions(reload="loading", delay=1000)

    def test_html_attributes_close_the_attrs(self) -> None:
        options = ZoneOptions(poll=5000, html=' class="c"')
        assert options.attrs == f' {POLL_ATTR}="5000" class="c"'

    def test_parsed_html_attributes(self) -> None:
        template = Template('{% zone "p" class="a b" %}b{% endzone %}')
        node = template.nodelist.get_nodes_by_type(ZoneNode)[0]
        assert node.options == ZoneOptions(html=' class="a b"')

    def test_parsed_node_carries_options(self) -> None:
        template = Template('{% zone "p" tag="ul" poll="5s" %}b{% endzone %}')
        node = template.nodelist.get_nodes_by_type(ZoneNode)[0]
        assert node.options == ZoneOptions(tag="ul", poll=5000)


class TestRenderZoneStandalone:
    """`render_zone_standalone` wraps the body for a delivered partial."""

    def test_wraps_body_in_the_declared_tag(self) -> None:
        template = Template('{% zone "lz" tag="section" %}<b>{{ v }}</b>{% endzone %}')
        node = template.nodelist.get_nodes_by_type(ZoneNode)[0]
        out = render_zone_standalone(
            node.partial, node.name, node.options, Context({"v": "x"})
        )
        assert out == f'<section {ZONE_ATTR}="lz"><b>x</b></section>'

    def test_standalone_render_keeps_the_reload_mode(self) -> None:
        template = Template('{% zone "r" reload="loading" %}b{% endzone %}')
        node = template.nodelist.get_nodes_by_type(ZoneNode)[0]
        out = render_zone_standalone(node.partial, node.name, node.options, Context())
        assert out == f'<div {ZONE_ATTR}="r" {RELOAD_ATTR}="loading">b</div>'

    def test_standalone_render_carries_poll_interval(self) -> None:
        # The client re-reads the interval from the live element, so the zone keeps it.
        template = Template('{% zone "p" poll="5s" %}<b>{{ v }}</b>{% endzone %}')
        node = template.nodelist.get_nodes_by_type(ZoneNode)[0]
        out = render_zone_standalone(
            node.partial, node.name, node.options, Context({"v": "x"})
        )
        assert out == f'<div {ZONE_ATTR}="p" {POLL_ATTR}="5000"><b>x</b></div>'

    def test_standalone_render_carries_the_html_attributes(self) -> None:
        template = Template('{% zone "c" tag="nav" class="flex" %}b{% endzone %}')
        node = template.nodelist.get_nodes_by_type(ZoneNode)[0]
        out = render_zone_standalone(node.partial, node.name, node.options, Context())
        assert out == f'<nav {ZONE_ATTR}="c" class="flex">b</nav>'

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


class TestNodeWalks:
    """The walkers the guard analysis and the zone checks read node trees with."""

    def test_an_ancestor_tuple_counts_each_class(self) -> None:
        template = Template(
            '{% if a %}{% zone "i" %}x{% endzone %}{% endif %}'
            '{% for x in y %}{% zone "f" %}x{% endzone %}{% endfor %}'
            '{% zone "open" %}x{% endzone %}'
        )
        assert list(zones_under(template.nodelist, (IfNode, ForNode))) == ["i", "f"]

    def test_zones_under_walks_zone_bodies_and_branches(self) -> None:
        template = Template(
            '{% zone "a" %}{% has_perm "x.y" %}{% zone "b" %}{% zone "c" %}'
            "{% endzone %}{% endzone %}{% endhas_perm %}{% endzone %}"
            '{% has_perm "x.y" %}plain{% else %}{% zone "d" %}{% endzone %}'
            "{% endhas_perm %}"
        )
        assert list(zones_under(template.nodelist, HasPermNode)) == ["b", "c", "d"]

    def test_zones_under_nothing_without_the_ancestor(self) -> None:
        template = Template('{% zone "a" %}{% zone "b" %}{% endzone %}{% endzone %}')
        assert list(zones_under(template.nodelist, HasPermNode)) == []


RELOAD_URL = "/static/next/next.reload.min.js"


class TestReloadBundleNeed:
    """Only a zone reloading to its loading UI needs the `reload` bundle."""

    @pytest.mark.parametrize(
        ("reload", "needs"),
        [
            pytest.param("loading", [RELOAD_URL], id="loading"),
            pytest.param("keep", [], id="keep"),
        ],
    )
    def test_a_page_render_records_the_need(
        self, reload: str, needs: list[str]
    ) -> None:
        collector = StaticCollector(
            bundle_url=lambda name: f"/static/next/next.{name}.min.js"
        )
        _render(
            '{% zone "r" reload="' + reload + '" %}b{% endzone %}',
            **{COLLECTOR_KEY: collector},
        )
        assert list(collector.notes(BUNDLE_NOTE)) == needs
