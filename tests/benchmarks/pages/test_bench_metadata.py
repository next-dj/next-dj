from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.template import Context, Template
from django.test import RequestFactory, override_settings

from next.deps import Depends
from next.pages import Page
from next.pages.metadata import Metadata, render_metadata
from next.pages.metadata.backends import forget_translated_urls
from next.pages.metadata.schema import (
    Alternates,
    Article,
    OpenGraph,
    OpenGraphImage,
    Robots,
    Twitter,
    Verification,
)
from tests.support import bound_dependency, write_page_chain


if TYPE_CHECKING:
    from pathlib import Path


_ANCESTOR = (
    'metadata = {"title": {"template": "{title} | S%d"}, "description": "D%d"}\n'
)
_LEAF = 'metadata = {"title": "Leaf", "og": {"type": "website"}}\n'


def _static_chain(root: Path, depth: int, *, leaf: str = _LEAF) -> Path:
    """Write ``depth`` ancestors with a static dict each above the ``leaf`` source."""
    specs = [(f"seg_{i}", _ANCESTOR % (i, i)) for i in range(depth)]
    specs.append(("leaf", leaf))
    return write_page_chain(root, specs)[-1]


_BASE = "https://acme.example"
_FULL = Metadata(
    title="Wallet",
    description="Money",
    base=_BASE,
    site_name="Acme",
    canonical=True,
    alternates=Alternates(languages={"en": "/wallet/", "de": "/de/wallet/"}),
    robots=Robots(index=True, follow=True, googlebot="noimageindex"),
    og=OpenGraph(
        type="article",
        images=(OpenGraphImage(url="/a.png", width=1, height=2, alt="A"),),
        article=Article(published_time="2026-01-02", authors=("Ann",)),
    ),
    twitter=Twitter(card="summary", site="@acme", images=("/t.png",)),
    verification=Verification(google=("g",)),
    other=(("keywords", "a, b"),),
    jsonld=({"@type": "WebPage"},),
)
_I18N = {
    "ROOT_URLCONF": "tests.support.urls_i18n_pages",
    "LANGUAGES": [("en", "English"), ("de", "German")],
    "LANGUAGE_CODE": "en",
    "USE_I18N": True,
}


def _wallet() -> str:
    """Answer the dependency the dynamic leaf reads, priced near zero."""
    return "wallet"


def _leaf_meta(wallet: str = Depends("wallet")) -> dict[str, str]:
    return {"title": wallet}


class TestBenchMetadataChain:
    """The chain memo of one page against the depth of the tree above it."""

    @pytest.mark.parametrize("depth", [3, 8], ids=["d3", "d8"])
    @pytest.mark.benchmark(group="pages.metadata")
    def test_cold(self, tmp_path: Path, depth: int, benchmark) -> None:
        page = Page()
        leaf = _static_chain(tmp_path, depth)
        page.resolve_metadata(leaf)

        def run() -> None:
            page._metadata_registry.reset()
            page.resolve_metadata(leaf)

        benchmark(run)

    @pytest.mark.parametrize("depth", [3, 8], ids=["d3", "d8"])
    @pytest.mark.benchmark(group="pages.metadata")
    def test_warm(self, tmp_path: Path, depth: int, benchmark) -> None:
        page = Page()
        leaf = _static_chain(tmp_path, depth)
        page.resolve_metadata(leaf)
        page.resolve_metadata(leaf)
        benchmark(page.resolve_metadata, leaf)

    @pytest.mark.benchmark(group="pages.metadata")
    def test_dynamic(self, tmp_path: Path, benchmark) -> None:
        page = Page()
        leaf = _static_chain(tmp_path, 3, leaf="x = 1\n")
        page._metadata_registry.register(leaf, _leaf_meta)
        with bound_dependency("wallet", _wallet):
            page.resolve_metadata(leaf)
            page.resolve_metadata(leaf)
            benchmark(page.resolve_metadata, leaf)


class TestBenchMetadataRender:
    """The head markup of one render, from the empty tag to the memoised hreflang."""

    @pytest.mark.benchmark(group="pages.metadata")
    def test_render_empty(self, tmp_path: Path, benchmark) -> None:
        page = Page()
        leaf = _static_chain(tmp_path, 0, leaf="x = 1\n")
        template = Template("{% metadata %}")
        context = Context(page.build_render_context(leaf))
        benchmark(template.render, context)

    @pytest.mark.benchmark(group="pages.metadata")
    def test_render_full(self, benchmark) -> None:
        request = RequestFactory().get("/wallet/?page=2")
        benchmark(render_metadata, _FULL, request=request)

    @pytest.mark.benchmark(group="pages.metadata")
    def test_hreflang_warm(self, benchmark) -> None:
        meta = Metadata(base=_BASE, alternates=Alternates(languages=True))
        request = RequestFactory().get("/headed/")
        with override_settings(**_I18N):
            forget_translated_urls()
            render_metadata(meta, request=request)
            benchmark(render_metadata, meta, request=request)
