import re
from collections.abc import Callable, Collection, Iterator
from unittest import mock

import pytest
from django.test import override_settings
from django.test.utils import override_script_prefix

from next.paths import ANCESTOR_MARK, PAGE_MARK
from next.router import current
from next.router.current import (
    CONTAINERS,
    CurrentLinks,
    _Current,
    _opened,
    _Scan,
    current_links,
    origin_of,
)
from next.testing import override_next_settings
from tests.support import I18N_ROUTED
from tests.support.markup import HOSTILE, PARSER_VOID, flood


ORIGIN = "https://example.com"
HERE = "/docs/intro/"
NONE = ""
PAGE = PAGE_MARK
ANCESTOR = ANCESTOR_MARK


class UpperLinks(CurrentLinks):
    """A project backend the settings name instead of the shipped one."""


def mark_current(
    html: str,
    path: str,
    query: str,
    *,
    containers: tuple[str, ...],
    origin: str = "",
    homes: Callable[[], Collection[str]] | None = None,
) -> str:
    """Mark `html` for an explicit path and query, as `CurrentLinks.mark` does."""
    opened = _opened(html, containers)
    if opened is None:
        return html
    plan, low = opened
    return _Scan(html, low, plan, _Current(path, query, origin, homes)).run()


def marked(
    html: str,
    path: str = HERE,
    query: str = "",
    *,
    containers: tuple[str, ...] = CONTAINERS,
    origin: str = ORIGIN,
) -> str:
    return mark_current(html, path, query, containers=containers, origin=origin)


MARK_ATTRIBUTE = re.compile(
    r"""([\t\n\f\r ]*)(?:aria-current|data-next-current)"""
    r"""(?:[\t\n\f\r ]*=[\t\n\f\r ]*(?:"[^"]*"|'[^']*'|[^\t\n\f\r >]*))?(?=(.?))""",
    re.IGNORECASE | re.DOTALL,
)


def strip(html: str) -> str:
    """Drop every mark attribute the way the pass drops one, keeping all else."""

    def drop(match: re.Match[str]) -> str:
        after = match.group(2)
        return "" if not after or after in "\t\n\f\r >" else match.group(1)

    return MARK_ATTRIBUTE.sub(drop, html)


def link_mark(
    href: str,
    path: str = HERE,
    query: str = "",
    *,
    origin: str = ORIGIN,
    attrs: str = "",
) -> str:
    html = f'<nav><a href="{href}"{attrs}>x</a></nav>'
    out = marked(html, path, query, origin=origin)
    for mark in (NONE, PAGE, ANCESTOR):
        if out == html.replace(">x</a>", f"{mark}>x</a>"):
            return mark
    return pytest.fail(out)


class TestMatching:
    """Each link target resolves against the current URL before the paths compare."""

    @pytest.mark.parametrize(
        ("href", "path", "origin", "expected"),
        [
            ("/docs/intro/", HERE, ORIGIN, PAGE),
            ("/docs/intro", HERE, ORIGIN, PAGE),
            ("/docs", HERE, ORIGIN, ANCESTOR),
            ("/docs/", HERE, ORIGIN, ANCESTOR),
            ("/", HERE, ORIGIN, NONE),
            ("/blog", HERE, ORIGIN, NONE),
            ("/docs/intro/more", HERE, ORIGIN, NONE),
            ("/doc", HERE, ORIGIN, NONE),
            ("/docs/intr", HERE, ORIGIN, NONE),
            ("intro", HERE, ORIGIN, NONE),
            ("./", HERE, ORIGIN, PAGE),
            ("../", HERE, ORIGIN, ANCESTOR),
            ("../../", HERE, ORIGIN, NONE),
            ("../../../..", HERE, ORIGIN, NONE),
            ("./more/../", HERE, ORIGIN, PAGE),
            ("?", HERE, ORIGIN, PAGE),
            ("#top", HERE, ORIGIN, NONE),
            ("", HERE, ORIGIN, NONE),
            ("  /docs/intro  ", HERE, ORIGIN, PAGE),
            ("/do\ncs/intro", HERE, ORIGIN, PAGE),
            ("/docs/%69ntro", HERE, ORIGIN, PAGE),
            ("/docs/%2e/intro", HERE, ORIGIN, PAGE),
            ("/docs/%2E%2E/docs/intro", HERE, ORIGIN, PAGE),
            ("/Docs/intro", HERE, ORIGIN, NONE),
            ("/docs/intro?tab=a#f", HERE, ORIGIN, PAGE),
            ("/docs/intro#f", HERE, ORIGIN, PAGE),
            ("https://example.com/docs/intro", HERE, ORIGIN, PAGE),
            ("HTTPS://Example.COM/docs", HERE, ORIGIN, ANCESTOR),
            ("https://example.com", HERE, ORIGIN, NONE),
            ("https://other.com/docs/intro", HERE, ORIGIN, NONE),
            ("http://example.com/docs/intro", HERE, ORIGIN, NONE),
            ("//example.com/docs/", HERE, ORIGIN, ANCESTOR),
            ("//other.com/docs/intro", HERE, ORIGIN, NONE),
            ("\\\\example.com\\docs", HERE, ORIGIN, ANCESTOR),
            ("https:/docs/intro", HERE, ORIGIN, NONE),
            ("mailto:someone@example.com", HERE, ORIGIN, NONE),
            ("javascript:void(0)", HERE, ORIGIN, NONE),
            ("https://example.com/docs/intro", HERE, "", NONE),
            ("//example.com/docs", HERE, "", NONE),
            ("intro", "/docs/intro", ORIGIN, PAGE),
            ("./", "/docs/intro", ORIGIN, ANCESTOR),
            ("other", "/docs/intro", ORIGIN, NONE),
            ("/", "/", ORIGIN, PAGE),
            ("", "/", ORIGIN, NONE),
            ("/docs", "/", ORIGIN, NONE),
            ("?", "/", ORIGIN, PAGE),
            ("/docs", "docs/intro", ORIGIN, ANCESTOR),
            ("/docs", "", ORIGIN, NONE),
            ("/", "", ORIGIN, PAGE),
            ("/%D0%BF%D1%80%D0%B8%D0%B2%D0%B5%D1%82", "/привет/мир/", ORIGIN, ANCESTOR),
            (
                "/%d0%bf%d1%80%d0%b8%d0%b2%d0%b5%d1%82/%D0%BC%D0%B8%D1%80",
                "/привет/мир/",
                ORIGIN,
                PAGE,
            ),
            ("/a b", "/a%20b/", ORIGIN, PAGE),
            ("/files/%ff", "/files/%FF/", ORIGIN, PAGE),
        ],
    )
    def test_the_rule(self, href, path, origin, expected) -> None:
        assert link_mark(href, path, origin=origin) == expected

    def test_references_in_the_href_are_decoded(self) -> None:
        assert link_mark("&#47;docs&#x2F;intro") == PAGE

    def test_the_query_is_ignored_without_the_query_mode(self) -> None:
        assert link_mark("/docs/intro?tab=b", query="tab=a") == PAGE

    @pytest.mark.parametrize(
        ("href", "query", "expected"),
        [
            ("?page=2", "page=2", PAGE),
            ("?page=2", "page=3", NONE),
            ("?page=2", "", NONE),
            ("?", "page=3", PAGE),
        ],
    )
    def test_a_query_only_link_compares_its_parameters_in_every_mode(
        self, href, query, expected
    ) -> None:
        assert link_mark(href, query=query) == expected


class TestModes:
    """`data-next-match` narrows the rule on a link or on its whole container."""

    @pytest.mark.parametrize(
        ("href", "query", "expected"),
        [
            ("/docs/intro?tab=a", "tab=a", PAGE),
            ("/docs/intro?tab=a", "tab=b", NONE),
            ("/docs/intro?tab=a", "", NONE),
            ("/docs/intro?tab=a", "tab=a&x=1", PAGE),
            ("/docs/intro?tab=a&tab=b", "tab=b&tab=a", PAGE),
            ("/docs/intro?tab=a&tab=b", "tab=a", NONE),
            ("/docs/intro", "tab=a", PAGE),
            ("/docs/intro?a=1&amp;b=2", "b=2&a=1", PAGE),
            ("/docs/intro?flag", "flag=", PAGE),
            ("/docs?tab=a", "tab=a", ANCESTOR),
            ("/docs?tab=a", "tab=b", NONE),
        ],
    )
    def test_query_mode(self, href, query, expected) -> None:
        attrs = ' data-next-match="query"'
        assert link_mark(href, query=query, attrs=attrs) == expected

    def test_each_query_link_of_a_pass_compares_against_the_current_query(self) -> None:
        html = (
            '<nav data-next-match="query"><a href="/docs/intro?tab=b">b</a>'
            '<a href="/docs/intro?tab=a">a</a></nav>'
        )
        out = marked(html, query="tab=a")
        assert out == html.replace(
            '"/docs/intro?tab=a">', f'"/docs/intro?tab=a"{PAGE}>'
        )

    def test_exact_mode_drops_the_ancestor_mark(self) -> None:
        attrs = ' data-next-match="exact"'
        assert link_mark("/docs", attrs=attrs) == NONE
        assert link_mark("/docs/intro", attrs=attrs) == PAGE

    def test_modes_combine(self) -> None:
        attrs = ' data-next-match="EXACT query"'
        assert link_mark("/docs/intro?tab=a", query="tab=a", attrs=attrs) == PAGE
        assert link_mark("/docs?tab=a", query="tab=a", attrs=attrs) == NONE

    def test_a_container_mode_reaches_its_links(self) -> None:
        html = '<nav data-next-match="exact"><a href="/docs">x</a></nav>'
        assert marked(html) == html

    def test_a_nested_container_inherits_the_mode(self) -> None:
        html = '<nav data-next-match="exact"><div data-next-nav><a href="/docs"></div></nav>'
        assert marked(html) == html

    def test_the_link_mode_wins_over_its_container(self) -> None:
        html = '<nav data-next-match="exact"><a data-next-match="" href="/docs"></nav>'
        assert marked(html) == html.replace('"/docs">', f'"/docs"{ANCESTOR}>')


class TestOptOut:
    """`data-next-current` on a link or a container keeps the pass away from it."""

    @pytest.mark.parametrize(
        "html",
        [
            '<nav><a data-next-current="off" href="/docs/intro">x</a></nav>',
            '<nav data-next-current="off"><a href="/docs/intro">x</a></nav>',
            '<nav data-next-current="off"><div data-next-nav><a href="/docs/intro"></div></nav>',
            '<nav><a data-next-current="off" aria-current="page" href="/blog">x</a></nav>',
            '<nav data-next-current="off"><a aria-current="step" href="/blog">x</a></nav>',
        ],
    )
    def test_opted_out(self, html) -> None:
        assert marked(html) is html

    def test_a_later_container_is_marked_again(self) -> None:
        html = '<nav data-next-current="off"></nav><nav><a href="/docs/intro"></nav>'
        out = marked(html)
        assert out.count(PAGE_MARK) == 1
        assert strip(out) == strip(html)


class TestExistingMarks:
    """The rule replaces the marks a link carries, from its author or an earlier pass."""

    @pytest.mark.parametrize(
        ("html", "expected"),
        [
            (
                '<nav><a href="/blog" aria-current="page">x</a></nav>',
                '<nav><a href="/blog">x</a></nav>',
            ),
            (
                '<nav><a href="/docs/intro" aria-current="true">x</a></nav>',
                f'<nav><a href="/docs/intro"{PAGE}>x</a></nav>',
            ),
            (
                '<nav><a href="/docs/intro" data-next-current="ancestor">x</a></nav>',
                f'<nav><a href="/docs/intro"{PAGE}>x</a></nav>',
            ),
            (
                '<nav><a href="/docs" data-next-current="page" aria-current="page">x</a></nav>',
                f'<nav><a href="/docs"{ANCESTOR}>x</a></nav>',
            ),
            (
                '<nav><a href="/blog" data-next-current="ancestor">x</a></nav>',
                '<nav><a href="/blog">x</a></nav>',
            ),
            (
                '<nav><a aria-current="page" aria-current="page" href="/docs/intro">x</a></nav>',
                f'<nav><a href="/docs/intro"{PAGE}>x</a></nav>',
            ),
            (
                '<nav><a href="/docs/intro" ARIA-CURRENT="true">x</a></nav>',
                f'<nav><a href="/docs/intro"{PAGE}>x</a></nav>',
            ),
            ('<nav><a aria-current="step">x</a></nav>', "<nav><a>x</a></nav>"),
            (
                '<nav><a x=1 aria-current="p"title=t href=/blog>x</a></nav>',
                "<nav><a x=1 title=t href=/blog>x</a></nav>",
            ),
            (
                '<nav><a href=/blog aria-current="p"/>x</a></nav>',
                "<nav><a href=/blog />x</a></nav>",
            ),
            (
                "<nav><a href=/blog aria-current=p>x</a></nav>",
                "<nav><a href=/blog>x</a></nav>",
            ),
            (
                '<nav><a\n  aria-current="page"\n  href="/blog"\n>x</a></nav>',
                '<nav><a\n  href="/blog"\n>x</a></nav>',
            ),
            (
                "<nav><a href=/docs/intro/>x</a></nav>",
                f"<nav><a href=/docs/intro/{PAGE}>x</a></nav>",
            ),
        ],
    )
    def test_rewritten(self, html, expected) -> None:
        out = marked(html)
        assert out == expected
        assert strip(out) == strip(html)
        assert marked(out) == out

    @pytest.mark.parametrize(
        "html",
        [
            f'<nav><a href="/docs/intro"{PAGE}>x</a></nav>',
            '<nav><a data-next-current="page" href="/docs/intro" aria-current="page">',
            '<nav><a href="/docs" data-next-current="ancestor">x</a></nav>',
            '<main><a href="/blog" aria-current="page">x</a></main>',
        ],
    )
    def test_settled_marks_stay_as_written(self, html) -> None:
        assert marked(html) is html

    def test_idempotent(self) -> None:
        html = (
            '<nav><a href="/">h</a><a href="/docs">d</a>'
            '<a href="/docs/intro/">i</a><a href="/blog">b</a></nav>'
        )
        once = marked(html)
        assert once.count(PAGE_MARK) == 1
        assert once.count(ANCESTOR_MARK) == 1
        assert marked(once) == once


class TestContainers:
    """Only anchors inside the configured containers are marked."""

    @pytest.mark.parametrize("name", PARSER_VOID)
    def test_a_void_element_opens_no_container(self, name: str) -> None:
        html = f'<p><{name} data-next-nav><a href="/docs/intro/">x</a></p>'
        assert marked(html) is html

    @pytest.mark.parametrize(
        "html",
        [
            '<ul role="navigation"><li><a href="/docs/intro"></ul>',
            '<ul ROLE="navigation"><li><a href="/docs/intro"></ul>',
            '<div data-next-nav><a href="/docs/intro"></div>',
            '<NAV><A HREF="/docs/intro">x</A></NAV>',
            '<nav><a href="/docs/intro">a</a><p><a href="/docs/intro/">b</a>',
            '<nav><span></span><a href="/docs/intro"></nav>',
            '<nav><div data-next-nav></div><div></div><a href="/docs/intro"></nav>',
        ],
    )
    def test_marked(self, html) -> None:
        out = marked(html)
        assert out.count(PAGE_MARK) == html.count("/docs/intro")
        assert strip(out) == strip(html)

    @pytest.mark.parametrize(
        "html",
        [
            '<ul role="menu"><a href="/docs/intro"></ul>',
            '<ul role="NAVIGATION"><a href="/docs/intro"></ul>',
            '<ul data-x="role"><a href="/docs/intro"></ul>',
            '<nav-bar><a href="/docs/intro"></nav-bar>',
            '<hr data-next-nav><a href="/docs/intro">',
            '<nav></nav><main><a href="/docs/intro"></main>',
        ],
    )
    def test_not_marked(self, html) -> None:
        assert marked(html) is html

    def test_links_outside_any_container_are_content(self) -> None:
        html = '<main><a href="/docs/intro">x</a></main>'
        assert marked(html) is html

    def test_nested_same_name_elements_keep_the_container_open(self) -> None:
        html = (
            '<div data-next-nav><div><span></span></div><a href="/docs/intro">in</a>'
            '</div><a href="/docs/intro">out</a>'
        )
        out = marked(html)
        assert out.count(PAGE_MARK) == 1
        assert out.index(PAGE_MARK) < out.index("in</a>")

    def test_closing_an_outer_container_closes_the_inner(self) -> None:
        html = '<div data-next-nav><nav><a href="/x"></div><a href="/docs/intro">'
        assert marked(html) is html

    @pytest.mark.parametrize(
        ("containers", "count"),
        [
            (("header",), 1),
            (("HEADER",), 1),
            (("[data-x=y]",), 1),
            (("[DATA-X=y]",), 1),
            (('[data-x="y"]',), 1),
            (("[data-x='y']",), 1),
            (("[data-x]",), 2),
        ],
    )
    def test_custom_containers(self, containers, count) -> None:
        html = (
            '<header data-x="y"><a href="/docs/intro"></header>'
            '<p data-x="z"><a href="/docs/intro"></p><nav><a href="/docs/intro"></nav>'
        )
        out = marked(html, containers=containers)
        assert out.count(PAGE_MARK) == count
        assert strip(out) == strip(html)

    def test_malformed_selectors_are_dropped(self) -> None:
        html = '<nav><div data-x="1"><a href="/docs/intro"></div></nav>'
        containers = ("div > a", "[", "[1x]", "[=x]", "", " nav", ".x", "#x", "div.x")
        assert marked(html, containers=containers) is html

    def test_no_containers_marks_nothing(self) -> None:
        html = '<nav><a href="/docs/intro"></nav>'
        assert marked(html, containers=()) is html


UNMARKED = (
    "<nav><script>document.write('<a href=\"/docs/intro\">')</script></nav>",
    '<nav><style>a[href="/docs/intro"]::after{content:"<a href=/docs/intro>"}</style></nav>',
    '<nav><textarea><a href="/docs/intro"></textarea></nav>',
    '<nav><title><a href="/docs/intro"></title></nav>',
    '<nav><template><a href="/docs/intro"></template></nav>',
    '<nav><noscript><a href="/docs/intro"></noscript></nav>',
    '<nav><!-- <a href="/docs/intro"> --></nav>',
    '<nav><![CDATA[<a href="/docs/intro">]]></nav>',
    "<nav><div title=\"<a href='/docs/intro'>\"></div></nav>",
    "<nav><div title='x>\"<a href=/docs/intro>'></div></nav>",
    '<script><nav><a href="/docs/intro"></script>',
    '<!-- <nav> --><a href="/docs/intro">',
    '<div title="<nav>"><a href="/docs/intro"></div>',
    '<nav><script><a href="/docs/intro">',
    '<nav><!-- <a href="/docs/intro">',
    '<nav><a href="/docs/intro',
    '<nav><plaintext><a href="/docs/intro">',
    '<nav><script></scriptx><a href="/docs/intro"></script>',
    '<nav><?php <a href="/docs/intro"> ?>',
    '<nav>< a href="/docs/intro"></nav>',
    '<nav><a href="/x" href="/docs/intro"></nav>',
    "<nav><a>x</a><a href>y</a></nav>",
    "<nav><script>x</script",
    "<nav><div",
    "<nav></div",
    "<nav><!doctype html",
    '<nav><![CDATA[<a href="/docs/intro">',
    '<nav></ x <a href="/docs/intro">',
    "<nav><?x",
    '<NAV><SCRIPT><A HREF="/docs/intro"></SCRIPT></NAV>',
)

MARKED = (
    '<nav><!--> <a href="/docs/intro">',
    '<nav><!---> <a href="/docs/intro">',
    '<nav><!-- x --!><a href="/docs/intro">',
    '<nav><scriptx><a href="/docs/intro">',
    '<nav><script></script ><a href="/docs/intro">',
    '<nav><script></SCRIPT><a href="/docs/intro">',
    '<nav><!doctype html><a href="/docs/intro">',
    '<nav></ x><a href="/docs/intro">',
    '<nav></><a href="/docs/intro">',
    '<nav></div title=">"><a href="/docs/intro">',
    '</div title=">"><nav><a href="/docs/intro">',
    '<nav><a href="/docs/intro" href="/x">',
    '<p>İK</p><nav><a href="/docs/intro">',
    "<nav><a\nhref='/docs/intro'\n>",
    "<nav><a href=/docs/intro/>",
    "<nav><a/href=/docs/intro>",
    '<nav><a title="a>b" href="/docs/intro">',
    "<nav><a =x href=/docs/intro>",
    '<nav><?php x ?><a href="/docs/intro">',
    '<nav><![CDATA[x]]><a href="/docs/intro">',
    '<svg><title>t</title></svg><nav><a href="/docs/intro">',
    '<nav><a href="/docs/intro" title=\'it"s\'>',
    '<nav> a < b <a href="/docs/intro">',
    '<nav>\n<a\thref="/docs/intro">',
    '<div title="x"></div><div title="y"></div><nav><a href="/docs/intro">',
)


class TestMarkup:
    """Only the mark attributes of anchors inside containers change, nothing else."""

    @pytest.mark.parametrize("html", UNMARKED)
    def test_hidden_or_broken_anchors_stay_untouched(self, html) -> None:
        assert marked(html) is html

    @pytest.mark.parametrize("html", MARKED)
    def test_real_anchors_gain_only_the_mark(self, html) -> None:
        out = marked(html)
        assert out.count(PAGE_MARK) == 1
        assert strip(out) == strip(html)
        assert len(out) == len(html) + len(PAGE_MARK)

    @pytest.mark.parametrize("html", UNMARKED + MARKED)
    def test_a_second_pass_changes_nothing(self, html) -> None:
        once = marked(html)
        assert marked(once) == once

    def test_a_document_without_a_container_comes_back_as_is(self) -> None:
        html = "<main>" + "<p>text</p>" * 100 + "</main>"
        assert marked(html) is html

    def test_a_container_without_a_current_link_comes_back_as_is(self) -> None:
        html = '<nav><a href="/blog">b</a></nav>' + "<p>x</p>" * 10 + "<script>"
        assert marked(html) is html


SOUP = (
    "<",
    ">",
    "nav",
    "<nav>",
    "</nav>",
    "<a href=/docs>",
    "<a href='/docs/intro'>",
    "</a>",
    '"',
    "'",
    "=",
    " ",
    "/",
    "<!--",
    "-->",
    "<script>",
    "</script>",
    "x",
    "role=navigation",
    "data-next-nav",
    "#",
    "?",
    "<![CDATA[",
    "]]>",
    "&amp;",
    "İ",
    "K",
    "<A HREF=..>",
    "<div data-next-nav>",
    "</div>",
    "\x00",
    "<a",
    "<ul role=navigation>",
    "data-next-current=off",
    "data-next-current=ancestor",
    'aria-current="page"',
    "ARIA-CURRENT",
    '<a aria-current="page" href=/docs/intro>',
    "<a data-next-current=page href=/blog>",
)


def _draws(seed: int) -> Iterator[int]:
    """Yield a seeded linear congruential stream, so a failing seed replays exactly."""
    state = seed
    while True:
        state = (state * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        yield state >> 33


class TestRobustness:
    """Random markup never raises and keeps every byte that is not a mark attribute."""

    def test_random_soup(self) -> None:
        draws = _draws(20261117)
        for _ in range(3000):
            size = next(draws) % 61
            html = "".join(SOUP[next(draws) % len(SOUP)] for _ in range(size))
            out = marked(html)
            assert strip(out) == strip(html)
            assert marked(out) == out


def _steps(html: str) -> int:
    """Count the markup pieces the walk consumes and the closers it searches for."""
    with (
        mock.patch.object(_Scan, "step", autospec=True, side_effect=_Scan.step) as step,
        mock.patch.object(
            _Scan, "raw_end", autospec=True, side_effect=_Scan.raw_end
        ) as raw,
        mock.patch.object(current, "_after", wraps=current._after) as after,
    ):
        marked(html)
    return step.call_count + raw.call_count + after.call_count


class TestLinearWork:
    """Twice the input takes at most twice the walk steps, a count that times nothing."""

    @pytest.mark.parametrize("unit", list(HOSTILE.values()), ids=list(HOSTILE))
    def test_hostile_input(self, unit) -> None:
        html = flood(unit, 50_000)
        assert _steps(html * 2) <= 2 * _steps(html) + 1


class TestHomes:
    """A home path is exact only like `/`, read once a link could be an ancestor."""

    @pytest.mark.parametrize(
        ("href", "path", "expected"),
        [
            ("/de/", "/de/blog/", NONE),
            ("/de", "/de/blog/", NONE),
            ("/de/", "/de/", PAGE),
            ("/de/blog/", "/de/blog/post/", ANCESTOR),
            ("/en/", "/de/blog/", NONE),
            ("/app/", "/app/docs/", NONE),
            ("/app/docs/", "/app/docs/intro/", ANCESTOR),
        ],
    )
    def test_the_rule(self, href, path, expected) -> None:
        html = f'<nav><a href="{href}">x</a></nav>'
        out = mark_current(
            html,
            path,
            "",
            containers=CONTAINERS,
            homes=lambda: frozenset({"/", "/de", "/en", "/app"}),
        )
        assert out == html.replace(">x</a>", f"{expected}>x</a>")

    def test_the_homes_are_read_once_and_only_for_an_ancestor(self) -> None:
        reads: list[int] = []

        def homes() -> frozenset[str]:
            reads.append(1)
            return frozenset({"/"})

        page = '<nav><a href="/docs/intro/">x</a><a href="/blog/">y</a></nav>'
        mark_current(page, HERE, "", containers=CONTAINERS, homes=homes)
        assert reads == []
        two = '<nav><a href="/docs/">x</a><a href="/docs/">y</a></nav>'
        out = mark_current(two, HERE, "", containers=CONTAINERS, homes=homes)
        assert out.count(ANCESTOR_MARK) == 2
        assert reads == [1]

    @override_settings(**I18N_ROUTED)
    def test_the_backend_reads_the_language_roots(self) -> None:
        html = '<nav><a href="/de/">x</a><a href="/de/blog/">y</a></nav>'
        out = CurrentLinks().mark(html, lambda: "/de/blog/post/")
        assert out == html.replace('"/de/blog/">', f'"/de/blog/"{ANCESTOR_MARK}>')

    def test_the_backend_reads_the_script_prefix(self) -> None:
        html = '<nav><a href="/app/">x</a><a href="/app/docs/">y</a></nav>'
        with override_script_prefix("/app/"):
            out = CurrentLinks().mark(html, lambda: "/app/docs/intro/")
        assert out == html.replace('"/app/docs/">', f'"/app/docs/"{ANCESTOR_MARK}>')


class TestCurrentLinks:
    """The backend splits the request path from its query before the pass."""

    def test_the_query_and_fragment_are_split_off(self) -> None:
        html = '<nav><a data-next-match="query" href="/a?x=1">x</a></nav>'
        assert PAGE_MARK in CurrentLinks().mark(html, lambda: "/a?x=1#top")
        assert CurrentLinks().mark(html, lambda: "/a?x=2") is html

    def test_the_origin_reaches_the_rule(self) -> None:
        html = '<nav><a href="https://example.com/a">x</a></nav>'
        assert PAGE_MARK in CurrentLinks().mark(html, lambda: "/a", lambda: ORIGIN)
        assert CurrentLinks().mark(html, lambda: "/a") is html

    def test_containers_come_from_the_constructor(self) -> None:
        html = '<header><a href="/a"></header><nav><a href="/a"></nav>'
        out = CurrentLinks(("header",)).mark(html, lambda: "/a")
        assert out.index(PAGE_MARK) < out.index("<nav>")
        assert out.count(PAGE_MARK) == 1

    @pytest.mark.parametrize(
        ("html", "held"),
        [
            ('<nav><a href="/a">x</a></nav>', True),
            ('<NAV><a href="/a">x</a></NAV>', True),
            ('<ul ROLE="navigation"></ul>', True),
            ("<div data-next-nav></div>", True),
            ('<p><a href="/a">x</a></p>', False),
            ("", False),
        ],
    )
    def test_holds_tells_html_with_a_container_opener(
        self, html: str, *, held: bool
    ) -> None:
        assert CurrentLinks().holds(html) is held

    def test_holds_reads_the_containers_of_the_backend(self) -> None:
        links = CurrentLinks(("header",))
        assert links.holds("<header></header>") is True
        assert links.holds("<nav></nav>") is False


class TestCurrentLinksSetting:
    """`current_links` builds the named backend once per settings state."""

    def test_the_default_backend(self) -> None:
        links = current_links()
        assert type(links) is CurrentLinks
        assert links.containers == CONTAINERS
        assert current_links() is links

    @pytest.mark.parametrize("current", [False, {"ENABLED": False}])
    def test_off(self, current: object) -> None:
        with override_next_settings(ROUTER={"CURRENT": current}):
            assert current_links() is None

    @pytest.mark.parametrize(
        "current",
        [
            None,
            {"ENABLED": 0},
            {"BACKEND": ""},
            {"CONTAINERS": ["nav", 3]},
            {"CONTAINERS": ["header", ".menu"]},
            {"CONTAINERS": "nav"},
        ],
        ids=repr,
    )
    def test_a_misfit_reads_as_the_shipped_backend(self, current: object) -> None:
        with override_next_settings(ROUTER={"CURRENT": current}):
            links = current_links()
            assert type(links) is CurrentLinks
            assert links.containers == CONTAINERS

    def test_a_named_backend(self) -> None:
        backend = f"{UpperLinks.__module__}.UpperLinks"
        current = {"BACKEND": backend, "CONTAINERS": ["header"]}
        with override_next_settings(ROUTER={"CURRENT": current}):
            links = current_links()
            assert type(links) is UpperLinks
            assert links.containers == ("header",)


class TestOriginOf:
    """The origin names the scheme and the validated host of the request."""

    @override_settings(ALLOWED_HOSTS=["example.com"])
    def test_an_allowed_host(self, rf) -> None:
        request = rf.get("/", HTTP_HOST="example.com", secure=True)
        assert origin_of(request) == ORIGIN

    @override_settings(ALLOWED_HOSTS=["example.com"])
    def test_a_disallowed_host(self, rf) -> None:
        request = rf.get("/", HTTP_HOST="evil.com")
        assert origin_of(request) == ""


def _forget() -> None:
    current._walked.clear()
    current._known_link.cache_clear()


def _fresh(html: str, path: str = HERE, query: str = "") -> str:
    """Mark `html` with nothing remembered, as the first pass of a process does."""
    _forget()
    return marked(html, path, query)


SIDEBAR = (
    '<header><a href="/docs/">out</a></header><nav class="side" data-next-match>'
    '<a href="/docs/">Docs</a><ul><li><a class="x" href="/docs/intro/">Intro</a></li>'
    '<li><a href="/docs/setup/?tab=a" data-next-match="query">Setup</a></li>'
    '<li><a href="more">More</a><!-- <a href="/docs/intro"> --></li></ul>'
    '<div data-next-nav data-next-match="exact"><a href="/docs">Exact</a></div>'
    '<script>var a = "<a href=/docs/intro>";</script></nav>'
    '<main><a href="/docs/intro/">body</a></main>'
    '<footer role="navigation"><a href="/docs/intro" aria-current="page">F</a></footer>'
)


class TestWalkedContainers:
    """A container walked once replays for every later pass, bounded in what it keeps."""

    @pytest.mark.parametrize(
        ("path", "query"),
        [
            ("/docs/intro/", ""),
            ("/docs/setup/", "tab=a"),
            ("/docs/setup/", "tab=b"),
            ("/docs/", ""),
            ("/docs/intro/more/", ""),
            ("/blog/", ""),
        ],
    )
    def test_a_replay_marks_what_a_fresh_walk_marks(self, path, query) -> None:
        fresh = _fresh(SIDEBAR, path, query)
        _fresh(SIDEBAR, "/elsewhere/")
        assert marked(SIDEBAR, path, query) == fresh
        assert marked(fresh, path, query) == fresh

    def test_a_replay_walks_no_link_again(self) -> None:
        _fresh(SIDEBAR)
        read = current._known_link.cache_info()
        marked(SIDEBAR, "/docs/setup/")
        assert current._known_link.cache_info() == read

    def test_random_soup_replays_as_it_walked(self) -> None:
        draws = _draws(20261120)
        for _ in range(1500):
            size = next(draws) % 61
            html = "".join(SOUP[next(draws) % len(SOUP)] for _ in range(size))
            fresh = _fresh(html)
            assert marked(html) == fresh
            other = _fresh(html, "/docs/")
            _fresh(html)
            assert marked(html, "/docs/") == other

    def test_a_changed_container_is_walked_again(self) -> None:
        before = '<nav><a href="/docs/intro">a</a></nav>'
        after = '<nav><a href="/docs/intro">a</a><a href="/docs/intro/">b</a></nav>'
        assert marked(before).count(PAGE_MARK) == 1
        assert marked(after).count(PAGE_MARK) == 2
        assert marked(before).count(PAGE_MARK) == 1

    def test_the_containers_of_one_opener_stay_bounded(self) -> None:
        for index in range(current._REGION_VARIANTS + 3):
            marked(f'<nav><a href="/docs/intro">{index}</a></nav>')
        held = current._regions(current._plan(CONTAINERS).selectors, "<nav>")
        assert len(held) == current._REGION_VARIANTS
        assert held[0][0] == (
            f'<nav><a href="/docs/intro">{current._REGION_VARIANTS + 2}</a></nav>'
        )

    def test_the_openers_stay_bounded(self) -> None:
        for index in range(current._REGION_MEMO + 8):
            marked(f'<nav id="n{index}"><a href="/docs/intro">x</a></nav>')
        assert len(current._walked) == current._REGION_MEMO

    def test_a_container_past_the_limit_is_walked_every_time(self) -> None:
        links = '<a href="/docs/intro">x</a>' * (current._REGION_LIMIT // 20)
        html = f"<nav>{links}</nav>"
        once = marked(html)
        assert once.count(PAGE_MARK) == html.count("<a ")
        assert marked(html) == once
        assert not current._regions(current._plan(CONTAINERS).selectors, "<nav>")

    def test_an_opener_past_the_limit_is_never_remembered(self) -> None:
        html = f'<nav title="{"t" * current._LINK_LIMIT}"><a href="/docs/intro"></nav>'
        once = marked(html)
        assert once.count(PAGE_MARK) == 1
        assert marked(html) == once
        assert len(current._walked) == 0

    def test_an_unclosed_container_is_never_remembered(self) -> None:
        html = '<nav><a href="/docs/intro">'
        assert marked(html).count(PAGE_MARK) == 1
        assert not current._regions(current._plan(CONTAINERS).selectors, "<nav>")
        assert marked(html + "</nav><nav>").count(PAGE_MARK) == 1

    def test_one_selector_list_never_replays_for_another(self) -> None:
        html = '<nav data-side><a href="/docs/intro">x</a></nav>'
        assert marked(html).count(PAGE_MARK) == 1
        assert marked(html, containers=("[data-other]",)) is html


class TestLinkMemo:
    """An anchor tag is parsed once for every request, within a bound."""

    def test_a_repeated_link_is_parsed_once(self) -> None:
        marked('<nav><a href="/docs/intro">a</a><a href="/docs/intro">b</a></nav>')
        info = current._known_link.cache_info()
        assert (info.misses, info.hits) == (1, 1)

    def test_the_parsed_links_stay_bounded(self) -> None:
        links = "".join(
            f'<a href="/docs/{index}">x</a>' for index in range(current._LINK_MEMO + 10)
        )
        marked(f"<nav>{links}</nav>")
        assert current._known_link.cache_info().currsize == current._LINK_MEMO

    def test_a_link_past_the_limit_is_parsed_but_never_kept(self) -> None:
        html = f'<nav><a title="{"t" * current._LINK_LIMIT}" href="/docs/intro"></nav>'
        assert marked(html).count(PAGE_MARK) == 1
        assert current._known_link.cache_info().currsize == 0


class TestInsideContainers:
    """Inside a container only anchors and the elements that close it stop the walk."""

    @pytest.mark.parametrize(
        ("html", "count"),
        [
            (
                (
                    '<div data-next-nav><div><a href="/docs/intro"></div>'
                    '<a href="/docs/intro"></div><a href="/docs/intro">'
                ),
                2,
            ),
            ('<div data-next-nav><div></div></div><a href="/docs/intro">', 0),
            (
                (
                    '<a data-next-nav href="/docs/intro"><a href="/docs/intro"></a>'
                    '<a href="/docs/intro"></a></a><a href="/docs/intro">'
                ),
                2,
            ),
            (
                (
                    '<nav><a role=navigation href="/docs/intro"><b></a>'
                    '<a href="/docs/intro"></nav><a href="/docs/intro">'
                ),
                2,
            ),
            (
                (
                    '<nav><p>a < b</p><a href="/docs/intro"><? x ?>'
                    '<a href="/docs/intro"></nav>'
                ),
                2,
            ),
            ('<nav></nav x="\'"><a href="/docs/intro">', 0),
            ('<nav></navx><a href="/docs/intro"></nav><a href="/docs/intro">', 1),
        ],
    )
    def test_marked(self, html, count) -> None:
        out = _fresh(html)
        assert out.count(PAGE_MARK) == count
        assert strip(out) == strip(html)
        assert marked(html) == out

    def test_the_patterns_of_open_elements_stay_bounded(self) -> None:
        assert current._inside.cache_info().maxsize == current._INSIDE_MEMO
        plan = current._plan(CONTAINERS)
        assert current._inside(plan.selectors, ("nav",)) is current._inside(
            plan.selectors, ("nav",)
        )
