from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.template import Context, Template
from django.test import RequestFactory, override_settings
from django.urls import resolve

from next.pages import Page, ld, page as shared_page
from next.pages.manager import reset_metadata_registry
from next.pages.metadata import (
    Alternates,
    Article,
    Crumb,
    Icon,
    Link,
    Metadata,
    OpenGraph,
    OpenGraphImage,
    Robots,
    ThemeColor,
    Twitter,
    TwitterImage,
    Verification,
    resolve_metadata,
)
from next.pages.metadata.backends import metadata_renderer
from next.pages.metadata.fold import fold_metadata
from next.pages.metadata.hreflang import forget_translated_urls
from next.pages.metadata.normalize import normalize_metadata
from next.testing import override_next_settings
from tests.support import (
    BASE,
    I18N_ROUTED,
    WITH_BASE,
    bound_dependency,
    resolve_page_metadata,
    routed,
    write_page,
    write_page_chain,
)


if TYPE_CHECKING:
    from pathlib import Path


_ANCESTOR = (
    'metadata = {"title": {"template": "{title} | S%d"}, "description": "D%d"}\n'
)
_LEAF = 'metadata = {"title": "Leaf", "og": {"type": "website"}}\n'
_DYNAMIC_LEAF = """
from next.deps import Depends
from next.pages import page


@page.metadata
def leaf_meta(wallet=Depends("wallet")):
    return {"title": wallet}
"""


def _static_chain(root: Path, depth: int, *, leaf: str = _LEAF) -> Path:
    """Write ``depth`` ancestors with a static dict each above the ``leaf`` source."""
    specs = [(f"seg_{i}", _ANCESTOR % (i, i)) for i in range(depth)]
    specs.append(("leaf", leaf))
    return write_page_chain(root, specs)[-1]


_FULL = Metadata(
    title="Wallet",
    description="Money",
    site_name="Acme",
    canonical=True,
    alternates=Alternates(languages=(("en", "/wallet/"), ("de", "/de/wallet/"))),
    robots=Robots(index=True, follow=True, googlebot="noimageindex"),
    og=OpenGraph(
        type="article",
        images=(OpenGraphImage(url="/a.png", width=1, height=2, alt="A"),),
        article=Article(published_time="2026-01-02", authors=("Ann",)),
    ),
    twitter=Twitter(card="summary", site="@acme", images=(TwitterImage("/t.png"),)),
    verification=Verification(google=("g",)),
    keywords=("a", "b"),
    viewport="width=device-width, initial-scale=1",
    theme_color=(ThemeColor("#fff"),),
    icons=(Icon("icon", "/icon.svg", sizes="any", type="image/svg+xml"),),
    links=(Link("preconnect", "https://fonts.example"),),
    properties=(("fb:app_id", "1"),),
    jsonld=(
        ld.Node(id="#org", type="Organization", extra={"name": "Acme", "url": "/"}),
        ld.BreadcrumbList(
            id="#trail", items=(ld.ListItem(name="Acme", position=1, item="/"),)
        ),
        {"@type": "WebPage"},
    ),
)


_CRUMBS = Metadata(
    title="Post",
    breadcrumbs=(
        Crumb("Home", ""),
        Crumb("Blog", "blog"),
        Crumb("Post", "blog/[slug]"),
    ),
)


_DEEP_SEGMENT = {
    "description": "D",
    "robots": {"index": True, "googlebot": {"nosnippet": True}},
    "og": {"type": "website", "images": ["/a.png"], "article": {"section": "S"}},
    "twitter": {"card": "summary"},
    "theme_color": "#111",
    "keywords": ["a", "b"],
    "alternates": {"feeds": [{"url": "/feed.xml", "type": "rss"}]},
    "jsonld": [{"@id": "#org", "@type": "Organization"}, {"@type": "Thing"}],
}
_ROTATION = 3000


def _render(meta: Metadata, request: object) -> str:
    """Resolve and render one fold, the work of a `{% metadata %}` tag."""
    return metadata_renderer().render(resolve_metadata(meta, request=request))


def _wallet() -> str:
    """Return the dependency the dynamic leaf reads, at negligible cost."""
    return "wallet"


class TestBenchMetadataChain:
    """The chain memo of one page against the depth of the tree above it."""

    @pytest.mark.parametrize("depth", [3, 8], ids=["d3", "d8"])
    @pytest.mark.benchmark(group="pages.metadata")
    def test_cold(self, tmp_path: Path, depth: int, benchmark) -> None:
        leaf = _static_chain(tmp_path, depth)
        resolve_page_metadata(shared_page, leaf)

        def run() -> None:
            reset_metadata_registry()
            resolve_page_metadata(shared_page, leaf)

        benchmark(run)

    @pytest.mark.parametrize("depth", [3, 8], ids=["d3", "d8"])
    @pytest.mark.benchmark(group="pages.metadata")
    def test_warm(self, tmp_path: Path, depth: int, benchmark) -> None:
        page = Page()
        leaf = _static_chain(tmp_path, depth)
        resolve_page_metadata(page, leaf)
        resolve_page_metadata(page, leaf)
        benchmark(resolve_page_metadata, page, leaf)

    @pytest.mark.benchmark(group="pages.metadata")
    def test_rotation(self, tmp_path: Path, benchmark) -> None:
        page = Page()
        root = write_page_chain(tmp_path, [("root", _ANCESTOR % (0, 0))])[0].parent
        leaves = [
            write_page_chain(root, [(f"leaf_{i}", _LEAF)])[0] for i in range(_ROTATION)
        ]

        def run() -> None:
            for leaf in leaves:
                page.static_metadata(leaf)

        run()
        benchmark(run)

    @pytest.mark.benchmark(group="pages.metadata")
    def test_dynamic(self, tmp_path: Path, benchmark) -> None:
        leaf = _static_chain(tmp_path, 3, leaf=_DYNAMIC_LEAF)
        with bound_dependency("wallet", _wallet):
            resolve_page_metadata(shared_page, leaf)
            resolve_page_metadata(shared_page, leaf)
            benchmark(resolve_page_metadata, shared_page, leaf)


class TestBenchMetadataRender:
    """The head markup of one render, from the empty tag to the memoised hreflang."""

    @pytest.mark.benchmark(group="pages.metadata")
    def test_render_empty(self, tmp_path: Path, benchmark) -> None:
        page = Page()
        leaf = _static_chain(tmp_path, 0, leaf="x = 1\n")
        template = Template("{% metadata %}")
        data = page.build_render_context(leaf)

        def run() -> str:
            return template.render(Context(data))

        benchmark(run)

    @pytest.mark.benchmark(group="pages.metadata")
    def test_render_full(self, benchmark) -> None:
        request = RequestFactory().get("/wallet/?page=2")
        with override_next_settings(SITE={"URL": BASE}):
            benchmark(_render, _FULL, request)

    @pytest.mark.benchmark(group="pages.metadata")
    def test_render_crumbs(self, tmp_path: Path, benchmark) -> None:
        root = tmp_path / "pages"
        for trail in ("", "blog", "blog/[slug]"):
            write_page(root, trail)
        request = RequestFactory().get("/blog/hello/")
        with routed(root, **WITH_BASE):
            request.resolver_match = resolve("/blog/hello/")
            benchmark(_render, _CRUMBS, request)

    @pytest.mark.benchmark(group="pages.metadata")
    def test_hreflang_warm(self, benchmark) -> None:
        meta = Metadata(alternates=Alternates(languages=True))
        request = RequestFactory().get("/headed/")
        with (
            override_settings(**I18N_ROUTED),
            override_next_settings(SITE={"URL": BASE}),
        ):
            forget_translated_urls()
            _render(meta, request)
            benchmark(_render, meta, request)


class TestBenchMetadataFold:
    """The deep fold of a chain whose every segment sets every block."""

    @pytest.mark.benchmark(group="pages.metadata")
    def test_deep_fold_five_levels(self, benchmark) -> None:
        segments = [normalize_metadata(_DEEP_SEGMENT, source=f"s{i}") for i in range(5)]
        benchmark(fold_metadata, segments)
