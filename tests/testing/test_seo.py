import importlib.util
import sys
import xml.etree.ElementTree as ET

import pytest
from defusedxml import EntitiesForbidden
from django.http import HttpResponse, StreamingHttpResponse
from django.test import Client

import next.testing.seo as seo_module
from next.seo.manager import seo_manager
from next.seo.registry import sitemap_items_registry
from next.testing import SitemapUrl, assert_metadata, parse_sitemap, reset_seo
from tests.support import BASE, POSTS_ITEMS, WITH_BASE, routed, write_tree


URLSET = """<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:xhtml="http://www.w3.org/1999/xhtml">
  <url>
    <loc>https://acme.example/a?x=1&amp;y=2</loc>
    <lastmod>2026-01-02</lastmod>
    <xhtml:link rel="alternate" hreflang="de" href="https://acme.example/de/a"/>
    <xhtml:link rel="stylesheet" hreflang="fr" href="https://acme.example/x"/>
  </url>
  <url><loc> https://acme.example/b </loc></url>
</urlset>
"""


class TestParseSitemap:
    """A sitemap response is parsed as XML into its URLs."""

    def test_a_served_sitemap_reads_back(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages", pages=("", "posts/[slug]"), sitemap=POSTS_ITEMS
        )
        with routed(root, **WITH_BASE):
            urls = parse_sitemap(Client().get("/sitemap.xml"))
        assert urls == [SitemapUrl(f"{BASE}/"), SitemapUrl(f"{BASE}/posts/a/")]

    def test_an_index_reads_back_its_sitemaps(self) -> None:
        response = HttpResponse(
            "<sitemapindex><sitemap><loc>https://a.example/s.xml</loc>"
            "<lastmod>2026-01-01</lastmod></sitemap></sitemapindex>"
        )
        assert parse_sitemap(response) == [
            SitemapUrl("https://a.example/s.xml", lastmod="2026-01-01")
        ]

    def test_a_urlset_reads_every_url_with_entities_decoded(self) -> None:
        assert parse_sitemap(HttpResponse(URLSET)) == [
            SitemapUrl(
                loc="https://acme.example/a?x=1&y=2",
                lastmod="2026-01-02",
                alternates=(("de", "https://acme.example/de/a"),),
            ),
            SitemapUrl(loc="https://acme.example/b"),
        ]

    def test_stray_tags_outside_an_entry_are_ignored(self) -> None:
        response = HttpResponse("<urlset><loc>x</loc><lastmod/></urlset>")
        assert parse_sitemap(response) == []

    def test_a_body_that_is_no_xml_fails_the_test(self) -> None:
        response = HttpResponse("<urlset><url><loc>x</url></urlset>")
        with pytest.raises(ET.ParseError):
            parse_sitemap(response)

    def test_a_streamed_body_reads_to_its_end(self) -> None:
        response = StreamingHttpResponse(
            iter([b"<urlset><url><loc>https://a.example/</loc>", b"</url></urlset>"])
        )
        assert parse_sitemap(response) == [SitemapUrl("https://a.example/")]

    def test_a_streamed_body_reads_again(self) -> None:
        response = StreamingHttpResponse(
            iter([b"<urlset><url><loc>https://a.example/</loc>", b"</url></urlset>"])
        )
        assert parse_sitemap(response) == parse_sitemap(response)
        assert_metadata(response, title=None)

    def test_an_entity_expansion_is_refused(self) -> None:
        response = HttpResponse(
            '<!DOCTYPE u [<!ENTITY e "x">]><urlset><url><loc>&e;</loc></url></urlset>'
        )
        with pytest.raises(EntitiesForbidden):
            parse_sitemap(response)


class TestResetSeo:
    """`reset_seo` drops the sources and every `@sitemap.items` registration."""

    def test_the_next_read_discovers_again(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages", pages=("posts/[slug]",), sitemap=POSTS_ITEMS
        )
        with routed(root):
            roots = seo_manager.roots()
            version = seo_manager.version
            reset_seo()
            assert seo_manager.version != version
            assert sitemap_items_registry.entries_for(root / "sitemap.py") == ()
            assert seo_manager.roots() is not roots


class TestMissingDefusedxml:
    """Without defusedxml the import names the extra that provides it."""

    def test_import_error_names_the_testing_extra(self, monkeypatch) -> None:
        monkeypatch.setitem(sys.modules, "defusedxml.ElementTree", None)
        # A fresh copy under another name keeps the real module and its classes intact.
        spec = importlib.util.spec_from_file_location(
            "next.testing._seo_probe", seo_module.__file__
        )
        module = importlib.util.module_from_spec(spec)
        with pytest.raises(ImportError, match=r"next\.dj\[testing\]") as caught:
            spec.loader.exec_module(module)
        assert isinstance(caught.value.__cause__, ImportError)
