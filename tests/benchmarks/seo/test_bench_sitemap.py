from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from django.test import RequestFactory

from next.seo import views
from next.seo.backends import PageTreeSitemapBackend
from next.seo.origin import OriginSite
from next.seo.sitemaps import PageTreeSitemap
from tests.support import WITH_BASE, routed, write_tree


if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


SITE = OriginSite("acme.example")
LISTED = """
from next.seo import sitemap

@sitemap.items("posts/[slug]")
def posts():
    return [{{"slug": f"post-{{n}}"}} for n in range({count})]
"""
LAZY = """
from next.seo import sitemap

@sitemap.items("posts/[int:n]", kwargs=lambda n: {"n": n})
def posts():
    return range(100_000)
"""
SECTIONS = "from next.seo import sitemap\n\n" + "".join(
    f"@sitemap.items('s{n}/[slug]', section='s{n}')\n"
    f"def s{n}():\n    return [{{'slug': 'a'}}]\n\n"
    for n in range(20)
)


@pytest.fixture()
def routed_tree(request, tmp_path: Path) -> Iterator[PageTreeSitemapBackend]:
    """Route a tree whose `sitemap.py` is the parametrized source, and its backend."""
    pages, sitemap = request.param
    root = write_tree(tmp_path / "pages", pages=pages, sitemap=sitemap)
    with routed(root, **WITH_BASE):
        yield PageTreeSitemapBackend({})


def _section(backend: PageTreeSitemapBackend) -> PageTreeSitemap:
    """Return the fresh section one request builds."""
    section = next(iter(backend.sections(None).values()))
    assert isinstance(section, PageTreeSitemap)
    return section


def _listed(count: int) -> tuple[tuple[str, ...], str]:
    return ("", "posts/[slug]"), LISTED.format(count=count)


class TestBenchUrlset:
    """The first page of a section, the work one `/sitemap.xml` does per request."""

    @pytest.mark.benchmark(group="seo.sitemap")
    @pytest.mark.parametrize(
        "routed_tree",
        [_listed(1_000), _listed(10_000)],
        indirect=True,
        ids=["1k", "10k"],
    )
    def test_urlset_first_page(
        self, routed_tree: PageTreeSitemapBackend, benchmark
    ) -> None:
        def build() -> int:
            return len(_section(routed_tree).get_urls(1, SITE, "https"))

        assert build() > 0
        benchmark(build)

    @pytest.mark.benchmark(group="seo.sitemap")
    @pytest.mark.parametrize(
        "routed_tree", [(("posts/[int:n]",), LAZY)], indirect=True, ids=["100k"]
    )
    def test_lazy_second_page_reads_one_page(
        self, routed_tree: PageTreeSitemapBackend, benchmark
    ) -> None:
        def second_page() -> int:
            return len(_section(routed_tree).paginator.page(2).object_list)

        assert second_page() == 50_000
        benchmark(second_page)


class TestBenchIndex:
    """The index of twenty sections, one reverse and one lastmod read per section."""

    @pytest.mark.benchmark(group="seo.sitemap")
    def test_index_of_twenty_sections(self, tmp_path: Path, benchmark) -> None:
        pages = tuple(f"s{n}/[slug]" for n in range(20))
        root = write_tree(tmp_path / "pages", pages=pages, sitemap=SECTIONS)
        request = RequestFactory().get("/sitemap.xml")
        with routed(root, **WITH_BASE):
            response = views.sitemap_view(request)
            assert response.status_code == 200
            benchmark(lambda: views.sitemap_view(request).render())


class TestBenchStaticMemo:
    """The static routes of a tree, read from the memo once a module load settled."""

    @pytest.mark.benchmark(group="seo.sitemap")
    def test_static_trails_memo(self, tmp_path: Path, benchmark) -> None:
        pages = tuple(f"page-{n}" for n in range(200))
        root = write_tree(tmp_path / "pages", pages=pages, sitemap="")
        with routed(root, **WITH_BASE):
            backend = PageTreeSitemapBackend({})
            [seo_root] = backend.roots()
            options = backend.sections(None)["pages"].options
            assert len(backend.static_trails(seo_root, options)) == 200
            benchmark(backend.static_trails, seo_root, options)
