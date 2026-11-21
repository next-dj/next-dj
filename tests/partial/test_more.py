from pathlib import Path

import pytest
from django.contrib.auth.models import Group
from django.core.paginator import Paginator
from django.template import Context, Engine, Template, TemplateSyntaxError
from django.test import RequestFactory, override_settings

from next.partial import KeysetPager, NumberPager, Window
from next.partial.more import next_page, page_href, previous_page
from next.partial.zone import ZoneNode, render_zone_body, render_zone_standalone
from next.seeding import COLLECTOR_KEY
from next.static import StaticCollector
from next.static.runtime import BUNDLE_NOTE
from next.testing import NextClient, envelope_of
from tests.support import routed, write_page
from tests.support.access import PAGE_HEAD
from tests.support.cases.more import COMPILE_ERROR_CASES, CompileErrorCase


DEFAULT_TAG = (
    '{% more zone="catalog-results" next=page_obj|next_page %}'
    "Show more{% empty %}End of results.{% endmore %}"
)
DEFAULT_LINK = (
    '<div data-next-zone="catalog-results-more"><a href="?page=2" '
    'data-next-key="catalog-results-more" '
    'data-next-target="catalog-results,catalog-results-more" '
    'data-next-merge="append" data-next-defer="visible">Show more</a></div>'
)
DEFAULT_END = (
    '<div data-next-zone="catalog-results-more">'
    '<div data-next-key="catalog-results-more">End of results.</div></div>'
)
PREVIOUS_TAG = (
    '{% more zone="catalog-results" previous=page_obj|previous_page %}'
    "Show earlier{% empty %}Start of results.{% endmore %}"
)
PREVIOUS_START = (
    '<div data-next-zone="catalog-results-less">'
    '<div data-next-key="catalog-results-less">Start of results.<a href="?" '
    'data-next-key="catalog-results-less" '
    'data-next-target="catalog-results,catalog-results-less" '
    'data-next-merge="prepend" hidden>Show earlier</a></div></div>'
)
WINDOW_TAG = (
    '{% more zone="feed" next=rows prune=40 url="replace" %}More'
    "{% empty %}End{% endmore %}"
)
FEED_HEAD = (
    '<div data-next-zone="feed-more"><a href="?{query}" data-next-key="feed-more" '
    'data-next-target="feed,feed-more" data-next-merge="append" '
    'data-next-defer="visible"'
)
ROWS = list(range(95))
NAMES = [f"g{index:03}" for index in range(95)]
NO_TOKEN_VALUES = (
    pytest.param(0, id="zero"),
    pytest.param(-1, id="negative"),
    pytest.param(True, id="bool"),
    pytest.param("0", id="zero-text"),
    pytest.param("a b", id="not-a-slug"),
    pytest.param("", id="empty"),
    pytest.param("W" * 513, id="too-long"),
    pytest.param("9" * 19, id="long-digits"),
    pytest.param(2.0, id="float"),
    pytest.param({"has_next": True, "page": 3}, id="mapping"),
)

factory = RequestFactory()


def _render(source: str, **context: object) -> str:
    return Template(source).render(Context(context))


def _page(number: int, *, rows: int = 50, per_page: int = 10):
    return Paginator(list(range(rows)), per_page).page(number)


def _zone(source: str, name: str) -> ZoneNode:
    [node] = [
        node
        for node in Template(source).nodelist.get_nodes_by_type(ZoneNode)
        if node.name == name
    ]
    return node


def _numbered(query: str) -> Window[int]:
    return NumberPager(per_page=10).window(ROWS, factory.get(f"/?{query}"))


class TestMoreMarkup:
    """The tag compiles to a zone holding a real link with the sentinel attributes."""

    def test_default_link(self) -> None:
        assert _render(DEFAULT_TAG, page_obj=_page(1)) == DEFAULT_LINK

    def test_every_option(self) -> None:
        source = (
            '{% more zone="feed" next=n mode="manual" merge="prepend" param="p" %}'
            "Older{% endmore %}"
        )
        assert _render(source, n=3) == (
            '<div data-next-zone="feed-more"><a href="?p=3" data-next-key="feed-more" '
            'data-next-target="feed,feed-more" data-next-merge="prepend" '
            'data-next-param="p">Older</a></div>'
        )

    def test_url_is_written_on_a_raw_link(self) -> None:
        source = '{% more zone="feed" next=n url="push" %}More{% endmore %}'
        assert _render(source, n="Wzx") == (
            FEED_HEAD.format(query="page=Wzx") + ' data-next-url="push">More</a></div>'
        )

    @pytest.mark.parametrize(
        ("option", "attribute"),
        [
            pytest.param('prune="50"', 'data-next-prune="50"', id="quoted-prune"),
            pytest.param("prune=007", 'data-next-prune="7"', id="leading-zeros"),
            pytest.param('url="push"', 'data-next-url="push"', id="push"),
        ],
    )
    def test_window_option_spellings(self, option: str, attribute: str) -> None:
        rows = Window(rows=[], param="page", next="2", back="1")
        out = _render(
            f'{{% more zone="z" next=rows {option} %}}x{{% endmore %}}', rows=rows
        )
        assert attribute in out

    def test_the_default_param_is_not_written(self) -> None:
        source = '{% more zone="feed" next=n param="page" %}More{% endmore %}'
        assert "data-next-param" not in _render(source, n=2)

    def test_margin(self) -> None:
        source = '{% more zone="feed" next=n margin="400px" %}More{% endmore %}'
        assert _render(source, n=2) == (
            FEED_HEAD.format(query="page=2")
            + ' data-next-margin="400px">More</a></div>'
        )

    @pytest.mark.parametrize(
        ("option", "attribute"),
        [
            pytest.param("margin=400", 'data-next-margin="400px"', id="bare-number"),
            pytest.param('margin="0400"', 'data-next-margin="400px"', id="quoted"),
            pytest.param(
                'margin=" 0px  0px 400px "',
                'data-next-margin="0px 0px 400px"',
                id="spaced",
            ),
            pytest.param('margin="12.5%"', 'data-next-margin="12.5%"', id="percent"),
            pytest.param('margin="-10px"', 'data-next-margin="-10px"', id="negative"),
            pytest.param(
                "margin=999999", 'data-next-margin="999999px"', id="six-digits"
            ),
        ],
    )
    def test_numeric_option_spellings(self, option: str, attribute: str) -> None:
        out = _render(f'{{% more zone="z" next=n {option} %}}x{{% endmore %}}', n=2)
        assert attribute in out

    def test_the_link_keeps_the_query_and_drops_the_restore_flag(self) -> None:
        request = factory.get("/list/?q=a+b%26c&page=5&restore=1&tag=x&tag=y")
        out = _render(DEFAULT_TAG, page_obj=_page(5, rows=100), request=request)
        assert 'href="?q=a+b%26c&amp;page=6&amp;tag=x&amp;tag=y"' in out

    def test_the_page_parameter_is_added_after_the_query(self) -> None:
        request = factory.get("/list/?q=%3Cb%3E")
        out = _render(
            '{% more zone="z" next=n param="p" %}x{% endmore %}', n=4, request=request
        )
        assert 'href="?q=%3Cb%3E&amp;p=4"' in out

    def test_the_label_renders_its_template(self) -> None:
        source = '{% more zone="z" next=n %}<b>{{ label }}</b>{% endmore %}'
        out = _render(source, n=2, label="<more>")
        assert "<b>&lt;more&gt;</b></a>" in out

    def test_page_href_without_a_token_names_the_first_window(self) -> None:
        request = factory.get("/?q=x&cursor=abc&restore=1")
        assert page_href(request, "cursor", None) == "?q=x"
        assert page_href(None, "page", None) == "?"


class TestNumberWindowLinks:
    """A number window writes the back token of the next page and restored backs."""

    def test_a_first_window(self) -> None:
        assert _render(WINDOW_TAG, rows=_numbered("")) == (
            FEED_HEAD.format(query="page=2")
            + ' data-next-prune="40" data-next-url="replace" data-next-back="1">'
            "More</a></div>"
        )

    def test_a_middle_window(self) -> None:
        request = factory.get("/?page=3")
        assert _render(WINDOW_TAG, rows=_numbered("page=3"), request=request) == (
            FEED_HEAD.format(query="page=4")
            + ' data-next-prune="40" data-next-url="replace" data-next-back="3">'
            "More</a></div>"
        )

    def test_a_restored_window(self) -> None:
        request = factory.get("/?page=4&restore=1")
        rows = _numbered("page=4&restore=1")
        assert _render(WINDOW_TAG, rows=rows, request=request) == (
            FEED_HEAD.format(query="page=5")
            + ' data-next-prune="40" data-next-url="replace" data-next-back="4" '
            'data-next-backs="1 2 3">More</a></div>'
        )

    def test_the_last_window(self) -> None:
        assert _render(WINDOW_TAG, rows=_numbered("page=10")) == (
            '<div data-next-zone="feed-more"><div data-next-key="feed-more">End'
            "</div></div>"
        )

    def test_a_tag_without_prune_or_url_writes_no_back(self) -> None:
        source = '{% more zone="feed" next=rows %}More{% endmore %}'
        out = _render(source, rows=_numbered("page=4&restore=1"))
        assert out == FEED_HEAD.format(query="page=5") + ">More</a></div>"


KEYSET = KeysetPager(("name",), per_page=10)


def _keyset(**query: object) -> Window[Group]:
    return KEYSET.window(Group.objects.all(), factory.get("/", query))


def _after(name: str) -> str:
    return KEYSET.encode((name,), reverse=False)


def _before(name: str) -> str:
    return KEYSET.encode((name,), reverse=True)


def _cursor_link(token: str, tail: str) -> str:
    return (
        FEED_HEAD.format(query=f"cursor={token}")
        + ' data-next-param="cursor" data-next-prune="40" data-next-url="replace"'
        + f"{tail}>More</a></div>"
    )


@pytest.mark.django_db()
class TestKeysetWindowLinks:
    """A keyset window writes its param and the opaque back tokens."""

    @pytest.fixture(autouse=True)
    def groups(self) -> None:
        Group.objects.bulk_create(Group(name=name) for name in NAMES)

    def test_a_first_window(self) -> None:
        out = _render(WINDOW_TAG, rows=_keyset())
        assert out == _cursor_link(
            _after("g009"), f' data-next-back="{_before("g010")}"'
        )

    def test_a_middle_window(self) -> None:
        rows = _keyset(cursor=_after("g019"))
        assert _names(rows) == NAMES[20:30]
        out = _render(WINDOW_TAG, rows=rows)
        assert out == _cursor_link(
            _after("g029"), f' data-next-back="{_before("g030")}"'
        )

    def test_a_restored_window(self) -> None:
        rows = _keyset(cursor=_after("g029"), restore=1)
        assert _names(rows) == NAMES[:40]
        backs = " ".join(_before(name) for name in ("g010", "g020", "g030"))
        out = _render(WINDOW_TAG, rows=rows)
        assert out == _cursor_link(
            _after("g039"),
            f' data-next-back="{_before("g040")}" data-next-backs="{backs}"',
        )

    def test_the_last_window(self) -> None:
        out = _render(WINDOW_TAG, rows=_keyset(cursor=_after("g089")))
        assert out == (
            '<div data-next-zone="feed-more"><div data-next-key="feed-more">End'
            "</div></div>"
        )

    def test_the_previous_tag_reads_the_reverse_token(self) -> None:
        source = '{% more zone="feed" previous=rows %}Earlier{% endmore %}'
        out = _render(source, rows=_keyset(cursor=_after("g019")))
        assert out == (
            f'<div data-next-zone="feed-less"><a href="?cursor={_before("g020")}" '
            'data-next-key="feed-less" data-next-target="feed,feed-less" '
            'data-next-merge="prepend" data-next-param="cursor">Earlier</a></div>'
        )

    def test_the_first_window_hides_the_top_link(self) -> None:
        source = (
            '{% more zone="feed" previous=rows %}Earlier{% empty %}Top{% endmore %}'
        )
        request = factory.get("/?cursor=x&restore=1&q=a")
        out = _render(source, rows=_keyset(), request=request)
        assert out == (
            '<div data-next-zone="feed-less"><div data-next-key="feed-less">Top'
            '<a href="?q=a" data-next-key="feed-less" '
            'data-next-target="feed,feed-less" data-next-merge="prepend" '
            'data-next-param="cursor" hidden>Earlier</a></div></div>'
        )


def _names(window: Window[Group]) -> list[str]:
    return [row.name for row in window]


class TestWindowValues:
    """A window wins over the tag param, and its tokens are escaped."""

    def test_a_contradicting_param_raises_under_debug(self) -> None:
        source = '{% more zone="z" next=rows param="p" %}x{% endmore %}'
        rows = Window(rows=[], param="cursor", next="t")
        with (
            override_settings(DEBUG=True),
            pytest.raises(TemplateSyntaxError, match="differs from the param"),
        ):
            _render(source, rows=rows)

    def test_the_window_param_wins_otherwise(self) -> None:
        source = '{% more zone="z" next=rows param="p" %}x{% endmore %}'
        out = _render(source, rows=Window(rows=[], param="cursor", next="t"))
        assert 'href="?cursor=t"' in out
        assert 'data-next-param="cursor"' in out

    def test_a_matching_param_is_quiet(self) -> None:
        source = '{% more zone="z" next=rows param="cursor" %}x{% endmore %}'
        with override_settings(DEBUG=True):
            out = _render(source, rows=Window(rows=[], param="cursor", next="t"))
        assert 'href="?cursor=t"' in out

    def test_tokens_of_a_foreign_window_are_escaped(self) -> None:
        rows = Window(rows=[], param="page", next="2", back='1"', backs=("<a>",))
        out = _render(WINDOW_TAG, rows=rows)
        assert 'data-next-back="1&quot;" data-next-backs="&lt;a&gt;"' in out


class TestPruneNeedsAWindow:
    """`prune=` with a raw token raises loudly and is dropped otherwise."""

    SOURCE = '{% more zone="z" next=n prune=40 url="push" %}x{% endmore %}'

    @pytest.mark.parametrize("value", [2, "Wzx"], ids=["number", "slug"])
    def test_it_raises_under_debug(self, value: object) -> None:
        with (
            override_settings(DEBUG=True),
            pytest.raises(TemplateSyntaxError, match="prune= needs a Window"),
        ):
            _render(self.SOURCE, n=value)

    def test_it_raises_under_strict_loading(self) -> None:
        with (
            override_settings(NEXT_FRAMEWORK={"STRICT_LOADING": True}),
            pytest.raises(TemplateSyntaxError, match="prune= needs a Window"),
        ):
            _render(self.SOURCE, n=2)

    def test_the_link_drops_prune_otherwise(self) -> None:
        out = _render(self.SOURCE, n=2)
        assert out.endswith('data-next-url="push">x</a></div>')
        assert "data-next-prune" not in out


class TestMoreEndBranch:
    """With no next batch the empty branch renders inside the same zone."""

    def test_last_page_renders_the_empty_branch(self) -> None:
        assert _render(DEFAULT_TAG, page_obj=_page(5)) == DEFAULT_END

    def test_missing_value_renders_the_empty_branch(self) -> None:
        assert _render(DEFAULT_TAG) == DEFAULT_END

    def test_no_empty_branch_keeps_the_keyed_marker(self) -> None:
        out = _render('{% more zone="z" next=n %}x{% endmore %}', n=None)
        assert (
            out
            == '<div data-next-zone="z-more"><div data-next-key="z-more"></div></div>'
        )

    @pytest.mark.parametrize("value", NO_TOKEN_VALUES)
    def test_a_value_that_names_no_batch_ends_the_list(self, value: object) -> None:
        out = _render('{% more zone="z" next=n %}x{% empty %}end{% endmore %}', n=value)
        assert '<div data-next-key="z-more">end</div>' in out

    @pytest.mark.parametrize("value", NO_TOKEN_VALUES)
    def test_a_value_that_names_no_batch_raises_under_debug(
        self, value: object
    ) -> None:
        source = '{% more zone="z" next=n %}x{% endmore %}'
        with (
            override_settings(DEBUG=True),
            pytest.raises(TemplateSyntaxError, match="next= names no window"),
        ):
            _render(source, n=value)

    def test_a_value_that_names_no_batch_raises_under_strict_loading(self) -> None:
        source = '{% more zone="z" previous=n %}x{% endmore %}'
        with (
            override_settings(NEXT_FRAMEWORK={"STRICT_LOADING": True}),
            pytest.raises(TemplateSyntaxError, match="previous_page filter"),
        ):
            _render(source, n="a b")

    @pytest.mark.parametrize("context", [{}, {"n": None}])
    def test_none_ends_the_list_quietly_under_debug(self, context: dict) -> None:
        source = '{% more zone="z" next=n %}x{% empty %}end{% endmore %}'
        with override_settings(DEBUG=True):
            assert '<div data-next-key="z-more">end</div>' in _render(source, **context)

    @pytest.mark.parametrize(
        ("value", "href"),
        [
            pytest.param("3", "?page=3", id="digits"),
            pytest.param("007", "?page=7", id="leading-zeros"),
            pytest.param(10**30, f"?page={10**30}", id="huge"),
            pytest.param("Wzx-_9", "?page=Wzx-_9", id="slug"),
        ],
    )
    def test_a_raw_token_links(self, value: object, href: str) -> None:
        out = _render('{% more zone="z" next=n %}x{% endmore %}', n=value)
        assert f'href="{href}"' in out

    def test_a_literal_page_number_links(self) -> None:
        out = _render('{% more zone="z" next=3 %}x{% endmore %}')
        assert 'href="?page=3"' in out


class TestMoreZones:
    """The sentinel zone is a zone node the registry walk finds."""

    @pytest.mark.parametrize(
        ("source", "name"),
        [
            pytest.param(DEFAULT_TAG, "catalog-results-more", id="next"),
            pytest.param(PREVIOUS_TAG, "catalog-results-less", id="previous"),
        ],
    )
    def test_one_zone_is_declared(self, source: str, name: str) -> None:
        nodes = Template(source).nodelist.get_nodes_by_type(ZoneNode)
        assert [node.name for node in nodes] == [name]

    def test_the_more_zone_renders_on_its_own(self) -> None:
        node = _zone(DEFAULT_TAG, "catalog-results-more")
        context = Context({"page_obj": _page(1)})
        out = render_zone_standalone(node.partial, node.name, node.options, context)
        assert out == DEFAULT_LINK

    def test_an_append_grafts_the_bare_link(self) -> None:
        node = _zone(DEFAULT_TAG, "catalog-results-more")
        context = Context({"page_obj": _page(4)})
        body, _wrapped = render_zone_body(
            node.partial, node.name, node.options, context
        )
        assert body.startswith('<a href="?page=5" data-next-key="catalog-results-more"')

    def test_a_failing_link_reports_the_tag_in_debug(self) -> None:
        engine = Engine(debug=True, builtins=["next.templatetags.partial"])
        template = Template('a {% more zone="z" next=n %}x{% endmore %}', engine=engine)
        with pytest.raises(AttributeError) as raised:
            template.render(Context({"n": 2, "request": object()}))
        debug = vars(raised.value)["template_debug"]
        assert debug["during"] == '{% more zone="z" next=n %}'


MORE_URL = "/static/next/next.more.min.js"
FEED_SOURCE = (
    PAGE_HEAD
    + "from next.partial import Window\n\n"
    + '@page.context("n")\ndef n() -> Window:\n'
    + '    return Window(rows=[], param="page", next="2", back="1")\n'
)


def _feed(options: str) -> str:
    return (
        '{% zone "rows" %}<p>r</p>{% endzone %}'
        f'{{% more zone="rows" next=n{options} %}}More{{% endmore %}}'
    )


def _collector() -> StaticCollector:
    return StaticCollector(bundle_url=lambda name: f"/static/next/next.{name}.min.js")


class TestMoreBundleNeed:
    """Only a link keeping its window with `prune=` or `url=` needs the bundle."""

    @pytest.mark.parametrize(
        ("options", "value", "needs"),
        [
            pytest.param(" prune=10", Window([], "page", "2"), [MORE_URL], id="prune"),
            pytest.param(' url="replace"', 2, [MORE_URL], id="url"),
            pytest.param(" prune=10", 2, [], id="raw-prune-dropped"),
            pytest.param("", Window([], "page", "2"), [], id="plain"),
        ],
    )
    def test_a_page_render_records_the_need(
        self, options: str, value: object, needs: list[str]
    ) -> None:
        collector = _collector()
        _render(_feed(options), n=value, **{COLLECTOR_KEY: collector})
        assert list(collector.notes(BUNDLE_NOTE)) == needs

    def test_the_end_of_the_list_needs_none(self) -> None:
        collector = _collector()
        _render(_feed(" prune=10"), n=Window([], "page"), **{COLLECTOR_KEY: collector})
        assert list(collector.notes(BUNDLE_NOTE)) == []

    @pytest.mark.django_db()
    @pytest.mark.parametrize(
        ("options", "listed"),
        [
            pytest.param(" prune=10", True, id="windowed"),
            pytest.param("", False, id="plain"),
        ],
    )
    def test_a_zone_get_lists_the_need(
        self, tmp_path: Path, options: str, *, listed: bool
    ) -> None:
        write_page(tmp_path, "feed", FEED_SOURCE, body=_feed(options))
        with routed(tmp_path):
            response = NextClient().get_zones("/feed/", "rows-more")
        urls = [asset["url"] for asset in envelope_of(response).assets]
        assert (MORE_URL in urls) is listed


class TestPreviousLink:
    """A `previous=` tag above the list renders a visible link that prepends."""

    def test_a_restored_window_links_the_page_before_it(self) -> None:
        request = factory.get("/list/?page=5&restore=1")
        with override_settings(NEXT_FRAMEWORK={"DEFER": {"MORE": {"MAX_RESTORE": 3}}}):
            rows = _numbered("page=5&restore=1")
        out = _render(
            PREVIOUS_TAG.replace("page_obj|previous_page", "rows"),
            rows=rows,
            request=request,
        )
        assert out == (
            '<div data-next-zone="catalog-results-less"><a href="?page=2" '
            'data-next-key="catalog-results-less" '
            'data-next-target="catalog-results,catalog-results-less" '
            'data-next-merge="prepend">Show earlier</a></div>'
        )

    def test_a_later_page_links_the_page_before_it(self) -> None:
        out = _render(PREVIOUS_TAG, page_obj=_page(3))
        assert '<a href="?page=2" data-next-key="catalog-results-less"' in out

    @pytest.mark.parametrize(
        "page_obj",
        [
            pytest.param(_page(1), id="first-page"),
            pytest.param({"has_previous": True, "page": 3}, id="mapping"),
            pytest.param(None, id="missing"),
        ],
    )
    def test_the_first_page_renders_the_empty_branch(self, page_obj) -> None:
        assert _render(PREVIOUS_TAG, page_obj=page_obj) == PREVIOUS_START

    def test_a_window_from_page_one_renders_the_empty_branch(self) -> None:
        source = PREVIOUS_TAG.replace("page_obj|previous_page", "rows")
        assert _render(source, rows=_numbered("page=3&restore=1")) == PREVIOUS_START

    def test_the_hidden_link_keeps_the_query_and_drops_the_restore_flag(self) -> None:
        request = factory.get("/list/?q=ban&page=3&restore=1")
        out = _render(PREVIOUS_TAG, page_obj=_page(1), request=request)
        assert '<a href="?q=ban" data-next-key="catalog-results-less"' in out
        assert 'data-next-merge="prepend" hidden>Show earlier</a></div></div>' in out

    def test_an_observed_link_carries_its_trigger(self) -> None:
        source = (
            '{% more zone="z" previous=p mode="visible" margin=300 %}x{% endmore %}'
        )
        assert _render(source, p=2) == (
            '<div data-next-zone="z-less"><a href="?page=2" data-next-key="z-less" '
            'data-next-target="z,z-less" data-next-merge="prepend" '
            'data-next-defer="visible" data-next-margin="300px">x</a></div>'
        )

    def test_a_prepending_list_appends_its_earlier_pages(self) -> None:
        source = '{% more zone="chat" previous=p merge="append" %}Newer{% endmore %}'
        assert 'data-next-merge="append">Newer</a>' in _render(source, p=4)

    def test_the_bottom_tag_renders_no_top_link(self) -> None:
        request = factory.get("/list/?page=5&restore=1")
        out = _render(WINDOW_TAG, rows=_numbered("page=5&restore=1"), request=request)
        assert "-less" not in out

    def test_the_less_zone_renders_on_its_own(self) -> None:
        source = PREVIOUS_TAG.replace("page_obj|previous_page", "rows")
        node = _zone(source, "catalog-results-less")
        rows = Window(rows=[], param="page", previous="5")
        context = Context({"rows": rows, "request": factory.get("/?page=8")})
        out = render_zone_standalone(node.partial, node.name, node.options, context)
        assert out.startswith(
            '<div data-next-zone="catalog-results-less"><a href="?page=5" '
        )


class TestMoreCompileErrors:
    """Unknown options and invalid values fail compilation."""

    @pytest.mark.parametrize("case", COMPILE_ERROR_CASES, ids=lambda c: c.id)
    def test_refused(self, case: CompileErrorCase) -> None:
        with pytest.raises(TemplateSyntaxError) as raised:
            Template(f"{case.tag}x{{% endmore %}}")
        message = str(raised.value)
        assert message.startswith("{% more %} ")
        assert case.message in message

    def test_an_unclosed_tag_is_refused(self) -> None:
        with pytest.raises(TemplateSyntaxError, match="endmore"):
            Template('{% more zone="z" next=n %}x')

    def test_an_unclosed_empty_branch_is_refused(self) -> None:
        with pytest.raises(TemplateSyntaxError, match="endmore"):
            Template('{% more zone="z" next=n %}x{% empty %}y')


class TestNextPage:
    """`next_page` adapts a Django page and answers `None` for anything else."""

    def test_a_page_with_a_next_one(self) -> None:
        assert next_page(_page(2)) == 3

    def test_the_last_page(self) -> None:
        assert next_page(_page(5)) is None

    @pytest.mark.parametrize(
        "value", [None, "", "x", 3, object(), {"has_next": True, "page": 3}]
    )
    def test_anything_else(self, value: object) -> None:
        assert next_page(value) is None

    def test_the_filter_is_a_builtin(self) -> None:
        assert _render("{{ p|next_page }}", p=_page(1)) == "2"


class TestPreviousPage:
    """`previous_page` adapts a Django page and answers `None` for anything else."""

    def test_a_page_with_a_previous_one(self) -> None:
        assert previous_page(_page(2)) == 1

    def test_the_first_page(self) -> None:
        assert previous_page(_page(1)) is None

    @pytest.mark.parametrize(
        "value", [None, "", "x", 3, object(), {"has_previous": True, "page": 3}]
    )
    def test_anything_else(self, value: object) -> None:
        assert previous_page(value) is None

    def test_the_filter_is_a_builtin(self) -> None:
        assert _render("{{ p|previous_page }}", p=_page(3)) == "2"
