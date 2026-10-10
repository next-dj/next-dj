from collections.abc import Callable
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from django.conf import settings
from django.template import Context, TemplateSyntaxError
from django.template.base import Template
from django.test import Client, RequestFactory, override_settings
from django.utils import translation
from django.utils.functional import lazy

from next.components import ComponentInfo, components_manager
from next.diagnostics import degraded, watch_degraded
from next.pages.metadata import Breadcrumb
from next.pages.metadata.chain import MetadataThunk
from next.pages.metadata.nodes import BreadcrumbsNode, MetadataNode, render_breadcrumbs
from next.pages.metadata.registry import PageMetadataRegistry
from next.pages.metadata.resolve import published_metadata
from next.seeding import METADATA_KEY, TEMPLATE_PATH_KEY
from next.testing import override_next_settings
from next.testing.metadata import head_tags
from tests.support import (
    BASE,
    I18N,
    I18N_PREFIXED_URLCONF,
    WITH_BASE,
    routed,
    write_page,
    write_page_chain,
)


_RAISING = Mock(render=Mock(side_effect=ValueError("broken")))


def _render(source: str, **ctx) -> str:
    return Template(source).render(Context(ctx))


def _thunk(
    tmp_path: Path, source: str, calls: list[int] | None = None
) -> MetadataThunk:
    """Return a thunk over a fresh registry, counting the callable's runs in `calls`."""
    (leaf,) = write_page_chain(tmp_path, [("leaf", source)])
    registry = PageMetadataRegistry()
    if calls is not None:

        def meta() -> dict[str, object]:
            calls.append(1)
            return {"title": lazy(translation.get_language, str)()}

        registry.register(leaf, meta)
    return MetadataThunk(registry, leaf, RequestFactory().get("/leaf/"), {}, {})


def _thunk_over(root: Path, meta: Callable[..., object]) -> MetadataThunk:
    """Return a request-free thunk over a fresh registry whose page registers `meta`."""
    root.mkdir(exist_ok=True)
    (leaf,) = write_page_chain(root, [("leaf", "x = 1\n")])
    registry = PageMetadataRegistry()
    registry.register(leaf, meta)
    return MetadataThunk(registry, leaf, None, {}, {})


class TestMetadataNode:
    """The node renders the resolve of the page whose thunk the context carries."""

    def test_no_thunk_renders_nothing(self) -> None:
        assert _render("a{% metadata %}b") == "ab"

    def test_a_foreign_value_under_the_key_renders_nothing(self) -> None:
        assert _render("a{% metadata %}b", **{METADATA_KEY: {"title": "T"}}) == "ab"

    def test_a_thunk_renders_the_head_lines(self, tmp_path: Path) -> None:
        thunk = _thunk(tmp_path, 'metadata = {"title": "T", "description": "D"}\n')
        assert _render("{% metadata %}", **{METADATA_KEY: thunk}) == (
            '<title>T</title>\n<meta name="description" content="D">'
        )

    def test_the_request_of_the_thunk_resolves_the_canonical(
        self, tmp_path: Path
    ) -> None:
        thunk = _thunk(tmp_path, 'metadata = {"canonical": True}\n')
        with override_next_settings(SITE={"URL": "https://acme.example"}):
            html = _render("{% metadata %}", **{METADATA_KEY: thunk})
        assert html == '<link rel="canonical" href="https://acme.example/leaf/">'

    def test_the_resolve_is_published_on_the_request(self, tmp_path: Path) -> None:
        thunk = _thunk(tmp_path, 'metadata = {"title": "T"}\n')
        _render("{% metadata %}", **{METADATA_KEY: thunk})
        resolved = published_metadata(thunk.request)
        assert resolved is not None
        assert resolved.title == "T"

    def test_two_tags_resolve_the_thunk_once(self, tmp_path: Path) -> None:
        calls: list[int] = []
        thunk = _thunk(tmp_path, "x = 1\n", calls)
        with translation.override("en"):
            html = _render("{% metadata %}|{% metadata %}", **{METADATA_KEY: thunk})
        assert html == "<title>en</title>|<title>en</title>"
        assert calls == [1]

    def test_a_tag_in_an_included_template_resolves_the_thunk_once(
        self, tmp_path: Path
    ) -> None:
        calls: list[int] = []
        thunk = _thunk(tmp_path, "x = 1\n", calls)
        inner = Template("{% metadata %}{% breadcrumbs %}")
        with translation.override("en"):
            html = _render(
                "{% metadata %}|{% include inner %}",
                **{METADATA_KEY: thunk, "inner": inner},
            )
        assert html.startswith("<title>en</title>|<title>en</title>")
        assert calls == [1]

    def test_the_callable_reads_the_context_the_tag_renders_in(
        self, tmp_path: Path
    ) -> None:
        thunk = _thunk_over(tmp_path, lambda user: {"title": user})
        html = _render(
            '{% with user="Ann" %}{% metadata %}{% endwith %}', **{METADATA_KEY: thunk}
        )
        assert html == "<title>Ann</title>"

    def test_the_memo_of_another_thunk_is_not_reused(self, tmp_path: Path) -> None:
        first = _thunk_over(tmp_path / "a", lambda: {"title": "First"})
        second = _thunk_over(tmp_path / "b", lambda: {"title": "Second"})
        context = Context({METADATA_KEY: first})
        node = MetadataNode()
        assert node.render(context) == "<title>First</title>"
        context[METADATA_KEY] = second
        assert node.render(context) == "<title>Second</title>"

    def test_a_lazy_title_renders_under_the_language_of_each_render(
        self, tmp_path: Path
    ) -> None:
        thunk = _thunk(tmp_path, "x = 1\n", [])
        template = Template("{% metadata %}")
        context = Context({METADATA_KEY: thunk})
        with translation.override("de"):
            assert template.render(context) == "<title>de</title>"
        with translation.override("en"):
            assert template.render(context) == "<title>en</title>"

    def test_a_raising_renderer_renders_an_empty_head(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        thunk = _thunk(tmp_path, 'metadata = {"title": "T"}\n')
        watch_degraded()
        with patch(
            "next.pages.metadata.nodes.metadata_renderer", return_value=_RAISING
        ):
            assert _render("a{% metadata %}b", **{METADATA_KEY: thunk}) == "ab"
            assert _render("{% metadata %}", **{METADATA_KEY: thunk}) == ""
        assert degraded()
        [record] = [r for r in caplog.records if "renders empty" in r.getMessage()]
        assert "Mock raised ValueError" in record.getMessage()

    @override_settings(DEBUG=True)
    def test_a_raising_renderer_raises_under_debug(self, tmp_path: Path) -> None:
        thunk = _thunk(tmp_path, 'metadata = {"title": "T"}\n')
        with (
            patch("next.pages.metadata.nodes.metadata_renderer", return_value=_RAISING),
            pytest.raises(ValueError, match="broken") as raised,
        ):
            _render("{% metadata %}", **{METADATA_KEY: thunk})
        assert "renders empty" in raised.value.__notes__[0]

    def test_the_tag_reaches_a_component_render(self, tmp_path: Path) -> None:
        (tmp_path / "head.djx").write_text("<head>{% metadata %}</head>")
        info = ComponentInfo(
            name="page_head",
            scope_root=tmp_path,
            scope_relative="",
            template_path=tmp_path / "head.djx",
            module_path=None,
            is_simple=True,
        )
        thunk = _thunk(tmp_path, 'metadata = {"title": "T"}\n')
        with patch.object(components_manager, "get_component", return_value=info):
            html = _render(
                '{% component "page_head" %}',
                **{
                    TEMPLATE_PATH_KEY: str(tmp_path / "template.djx"),
                    METADATA_KEY: thunk,
                },
            )
        assert html == "<head><title>T</title></head>"


BODY = "<html><head>{% metadata %}</head><body>{% breadcrumbs %}</body></html>"
TRAIL_BODY = (
    "{% breadcrumbs as trail %}"
    "{% for crumb in trail %}{{ crumb.label }}|{{ crumb.url }}|{{ crumb.current }};"
    "{% endfor %}"
)
POST = """
from next.pages import page


@page.metadata
def meta(slug: str) -> dict:
    return {"title": f"Post {slug}"}
"""
OWN_LIST = """
from next.pages import ld

metadata = {
    "title": "Own",
    "jsonld": [ld.BreadcrumbList(items=(ld.ListItem(name="X", position=1),))],
}
"""


def _breadcrumb_tree(root: Path) -> Path:
    write_page(root, "", 'metadata = {"title": "Home"}\n', body=BODY)
    write_page(root, "blog", 'metadata = {"breadcrumb": "Blog"}\n', body=BODY)
    write_page(root, "blog/[slug]", POST, body=BODY)
    write_page(root, "docs", 'metadata = {"title": "Docs"}\n')
    write_page(root, "docs/intro", 'metadata = {"title": "Intro"}\n', body=BODY)
    write_page(root, "hidden", 'metadata = {"title": "Hidden", "breadcrumb": False}\n')
    write_page(root, "hidden/leaf", 'metadata = {"title": "Leaf"}\n', body=BODY)
    write_page(root, "own/list", OWN_LIST, body=BODY)
    write_page(root, "trail", 'metadata = {"breadcrumb": "Trail"}\n', body=TRAIL_BODY)
    write_page(root, "shop/item", 'metadata = {"title": "Item"}\n', body=BODY)
    return root


class TestBreadcrumbsTag:
    """The tag renders the crumbs of the page render, or binds them under a name."""

    def test_no_thunk_renders_nothing(self) -> None:
        assert _render("a{% breadcrumbs %}b") == "ab"

    def test_no_thunk_binds_an_empty_trail(self) -> None:
        assert _render("{% breadcrumbs as t %}{{ t|length }}") == "0"

    def test_a_page_without_crumbs_renders_nothing(self, tmp_path: Path) -> None:
        thunk = _thunk(tmp_path, "x = 1\n")
        assert _render("{% breadcrumbs %}", **{METADATA_KEY: thunk}) == ""

    def test_a_crumb_without_a_url_renders_as_text(self, tmp_path: Path) -> None:
        thunk = _thunk(tmp_path, 'metadata = {"title": "<Leaf>"}\n')
        assert _render("{% breadcrumbs %}", **{METADATA_KEY: thunk}) == (
            '<nav aria-label="Breadcrumb"><ol><li>&lt;Leaf&gt;</li></ol></nav>'
        )

    def test_a_current_crumb_without_a_url_carries_aria_current(self) -> None:
        crumbs = (Breadcrumb("Home", "/"), Breadcrumb("Here", None, current=True))
        assert render_breadcrumbs(crumbs) == (
            '<nav aria-label="Breadcrumb"><ol><li><a href="/">Home</a></li>'
            '<li aria-current="page">Here</li></ol></nav>'
        )

    @pytest.mark.parametrize(
        "source",
        ["{% breadcrumbs trail %}", "{% breadcrumbs as %}", "{% breadcrumbs to t %}"],
        ids=["bare_name", "as_alone", "wrong_keyword"],
    )
    def test_a_malformed_tag_does_not_compile(self, source: str) -> None:
        with pytest.raises(
            TemplateSyntaxError, match="takes no arguments or 'as name'"
        ):
            Template(source)

    def test_the_tag_node_defaults_to_rendering(self) -> None:
        assert BreadcrumbsNode().target is None


class TestBreadcrumbTrail:
    """A routed page reverses each crumb against its own match."""

    def test_every_ancestor_links_and_the_page_is_current(self, tmp_path: Path) -> None:
        root = _breadcrumb_tree(tmp_path / "pages")
        with routed(root, **WITH_BASE):
            html = Client().get("/blog/hello/").content.decode()
        assert (
            '<nav aria-label="Breadcrumb"><ol><li><a href="/">Home</a></li>'
            '<li><a href="/blog/">Blog</a></li>'
            '<li><a href="/blog/hello/" aria-current="page">Post hello</a></li>'
            "</ol></nav>"
        ) in html

    def test_a_non_ascii_slug_is_current(self, tmp_path: Path) -> None:
        root = _breadcrumb_tree(tmp_path / "pages")
        with routed(root, **WITH_BASE):
            html = Client().get("/blog/café/").content.decode()
        assert (
            '<li><a href="/blog/caf%C3%A9/" aria-current="page">Post café</a></li>'
        ) in html

    def test_the_graph_gains_a_breadcrumb_list(self, tmp_path: Path) -> None:
        root = _breadcrumb_tree(tmp_path / "pages")
        with routed(root, **WITH_BASE):
            response = Client().get("/blog/hello/")
        (trail,) = head_tags(response.content.decode()).jsonld
        assert trail == {
            "@type": "BreadcrumbList",
            "@id": f"{BASE}/blog/hello/#breadcrumb",
            "itemListElement": [
                {
                    "@type": "ListItem",
                    "name": "Home",
                    "position": 1,
                    "item": f"{BASE}/",
                },
                {
                    "@type": "ListItem",
                    "name": "Blog",
                    "position": 2,
                    "item": f"{BASE}/blog/",
                },
                {
                    "@type": "ListItem",
                    "name": "Post hello",
                    "position": 3,
                    "item": f"{BASE}/blog/hello/",
                },
            ],
        }

    def test_an_unrouted_ancestor_keeps_its_label_without_a_link(
        self, tmp_path: Path
    ) -> None:
        root = _breadcrumb_tree(tmp_path / "pages")
        with routed(root, **WITH_BASE):
            response = Client().get("/docs/intro/")
        html = response.content.decode()
        assert "<li>Docs</li>" in html
        assert head_tags(html).jsonld == []

    def test_a_directory_without_a_page_adds_no_crumb(self, tmp_path: Path) -> None:
        root = _breadcrumb_tree(tmp_path / "pages")
        with routed(root, **WITH_BASE):
            html = Client().get("/shop/item/").content.decode()
        assert (
            '<ol><li><a href="/">Home</a></li>'
            '<li><a href="/shop/item/" aria-current="page">Item</a></li></ol>'
        ) in html

    def test_false_drops_the_segment(self, tmp_path: Path) -> None:
        root = _breadcrumb_tree(tmp_path / "pages")
        with routed(root, **WITH_BASE):
            html = Client().get("/hidden/leaf/").content.decode()
        assert "Hidden" not in html.split("<body>")[1]
        assert '<a href="/hidden/leaf/" aria-current="page">Leaf</a>' in html

    def test_a_declared_breadcrumb_list_replaces_the_derived_one(
        self, tmp_path: Path
    ) -> None:
        root = _breadcrumb_tree(tmp_path / "pages")
        with routed(root, **WITH_BASE):
            html = Client().get("/own/list/").content.decode()
        assert head_tags(html).jsonld == [
            {
                "@type": "BreadcrumbList",
                "itemListElement": [{"@type": "ListItem", "name": "X", "position": 1}],
            }
        ]

    def test_the_as_form_binds_the_crumbs(self, tmp_path: Path) -> None:
        root = _breadcrumb_tree(tmp_path / "pages")
        with routed(root, **WITH_BASE):
            html = Client().get("/trail/").content.decode()
        assert html == "Home|/|False;Trail|/trail/|True;"

    def test_the_crumbs_follow_the_language_prefix(self, tmp_path: Path) -> None:
        root = _breadcrumb_tree(tmp_path / "pages")
        middleware = [
            *settings.MIDDLEWARE[:2],
            "django.middleware.locale.LocaleMiddleware",
            *settings.MIDDLEWARE[2:],
        ]
        with (
            override_settings(MIDDLEWARE=middleware, **I18N),
            routed(root, urlconf=I18N_PREFIXED_URLCONF, **WITH_BASE),
        ):
            html = Client().get("/de/blog/hello/").content.decode()
        assert (
            '<li><a href="/de/">Home</a></li><li><a href="/de/blog/">Blog</a></li>'
            '<li><a href="/de/blog/hello/" aria-current="page">Post hello</a></li>'
        ) in html
