import logging
import types
from datetime import date

import pytest
from django.contrib.sites.requests import RequestSite
from django.test import Client, RequestFactory

from next.seo import RouteSitemap, SeoBaseError, seo_manager
from next.seo.discovery import SeoRoot
from next.seo.markers import Entry
from next.seo.sitemaps import SitemapItem
from next.urls import PageRoot
from tests.seo.trees import BASE, WITH_BASE, routed, write_tree


DUPLICATE = """
from next.seo import sitemap

@sitemap.items("about")
def about():
    yield {}
"""

BAD_YIELD = """
from next.seo import sitemap

@sitemap.items("posts/[slug]")
def posts():
    yield 42
"""


def _sitemap(root, **attrs) -> RouteSitemap:
    module = types.ModuleType("sitemap")
    for name, value in attrs.items():
        setattr(module, name, value)
    seo_root = SeoRoot(
        root=PageRoot(path=root, label="Root"),
        section="pages",
        sitemap_module=root / "sitemap.py",
        robots_module=None,
        robots_file=None,
    )
    return RouteSitemap(seo_root, module)


class TestModuleAttributes:
    def test_defaults_when_the_module_declares_nothing(self, tmp_path) -> None:
        sitemap = _sitemap(tmp_path)
        assert sitemap.root == tmp_path
        assert sitemap.request is None
        assert sitemap.exclude == ()
        assert (sitemap.default_changefreq, sitemap.default_priority) == (None, None)
        assert (sitemap.i18n, sitemap.alternates, sitemap.x_default) == (0, 0, 0)
        assert sitemap.languages is None
        assert sitemap.protocol is None
        assert sitemap.limit == 50000

    def test_declared_values_are_read(self, tmp_path) -> None:
        sitemap = _sitemap(
            tmp_path,
            exclude=["admin/*", 5],
            changefreq="weekly",
            priority=1,
            i18n=True,
            languages=("en", 5, "de"),
            alternates=True,
            x_default=True,
            protocol="http",
            limit=10,
        )
        assert sitemap.exclude == ("admin/*",)
        assert sitemap.default_changefreq == "weekly"
        assert sitemap.default_priority == 1.0
        assert sitemap.languages == ["en", "de"]
        assert sitemap.protocol == "http"
        assert sitemap.limit == 10

    def test_wrong_shapes_fall_back(self, tmp_path) -> None:
        sitemap = _sitemap(
            tmp_path, exclude="admin", changefreq=5, priority="high", limit=0
        )
        assert sitemap.exclude == ()
        assert sitemap.default_changefreq is None
        assert sitemap.default_priority is None
        assert sitemap.limit == 50000


class TestItemValues:
    def test_the_item_value_wins_over_the_module_default(self, tmp_path) -> None:
        sitemap = _sitemap(tmp_path, changefreq="weekly", priority=0.8)
        item = SitemapItem("about", changefreq="daily", priority=0.3)
        assert sitemap.changefreq(item) == "daily"
        assert sitemap.priority(item) == 0.3
        assert sitemap.lastmod(item) is None

    def test_the_module_default_fills_a_bare_item(self, tmp_path) -> None:
        sitemap = _sitemap(tmp_path, changefreq="weekly", priority=0.8)
        item = SitemapItem("about", lastmod=date(2026, 1, 1))
        assert sitemap.changefreq(item) == "weekly"
        assert sitemap.priority(item) == 0.8
        assert sitemap.lastmod(item) == date(2026, 1, 1)

    def test_the_key_folds_the_kwargs_in_name_order(self) -> None:
        first = SitemapItem("p/[a]/[b]", {"b": 2, "a": "x"})
        second = SitemapItem("p/[a]/[b]", {"a": "x", "b": "2"})
        assert first.key == second.key == ("p/[a]/[b]", (("a", "x"), ("b", "2")))


class TestOrigin:
    def test_protocol_prefers_the_declaration_then_base_then_request(
        self, tmp_path
    ) -> None:
        assert _sitemap(tmp_path, protocol="http").get_protocol("https") == "http"
        assert _sitemap(tmp_path).get_protocol("http") == "http"
        assert _sitemap(tmp_path).get_protocol() == "https"
        with routed(tmp_path, **WITH_BASE):
            assert _sitemap(tmp_path).get_protocol("http") == "https"
            assert _sitemap(tmp_path, protocol="http").get_protocol() == "http"

    def test_domain_prefers_base_then_the_site(self, tmp_path) -> None:
        site = RequestSite(RequestFactory().get("/"))
        assert _sitemap(tmp_path).get_domain(site) == "testserver"
        with routed(tmp_path, **WITH_BASE):
            assert _sitemap(tmp_path).get_domain(site) == "acme.example"
            assert _sitemap(tmp_path).get_domain() == "acme.example"

    def test_a_request_free_build_without_base_raises(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="")
        with routed(root):
            sitemap = seo_manager.sitemaps()["pages"]
            with pytest.raises(SeoBaseError, match="needs a request"):
                sitemap.get_urls()

    def test_a_request_free_build_with_base_answers_absolute_urls(
        self, tmp_path
    ) -> None:
        root = write_tree(tmp_path / "pages", pages=("", "about"), sitemap="")
        with routed(root, **WITH_BASE):
            urls = seo_manager.sitemaps()["pages"].get_urls()
        assert sorted(url["location"] for url in urls) == [f"{BASE}/", f"{BASE}/about/"]


class TestItems:
    def test_items_are_built_once_per_instance(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("", "about"), sitemap="")
        with routed(root):
            sitemap = seo_manager.sitemaps()["pages"]
            assert sitemap.items() is sitemap.items()
            assert sorted(item.trail for item in sitemap.items()) == ["", "about"]

    def test_a_duplicate_location_is_dropped_with_a_warning(
        self, tmp_path, caplog
    ) -> None:
        root = write_tree(tmp_path / "pages", pages=("about",), sitemap=DUPLICATE)
        with routed(root), caplog.at_level(logging.WARNING, logger="next.seo"):
            items = seo_manager.sitemaps()["pages"].items()
        assert [item.trail for item in items] == ["about"]
        assert "lists 'about' with {} twice" in caplog.text

    def test_a_callable_yielding_neither_entry_nor_mapping_raises(
        self, tmp_path
    ) -> None:
        root = write_tree(
            tmp_path / "pages", pages=("posts/[slug]",), sitemap=BAD_YIELD
        )
        with (
            routed(root),
            pytest.raises(
                TypeError, match=r"yielded int instead of a next\.seo\.Entry"
            ),
        ):
            Client().get("/sitemap.xml")

    def test_an_entry_and_a_mapping_read_alike(self) -> None:
        assert Entry(kwargs={"slug": "a"}).kwargs == {"slug": "a"}
