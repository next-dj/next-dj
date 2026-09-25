import logging
import types
from datetime import UTC, date, datetime

import pytest
from django.contrib.sites.requests import RequestSite
from django.test import Client, RequestFactory, override_settings

from next.seo import RouteSitemap, SitemapOriginError, seo_manager
from next.seo.discovery import SeoRoot, SeoSource
from next.seo.sitemaps import (
    SitemapItem,
    SitemapOptions,
    is_excluded,
    lastmod_datetime,
    listed_trails,
    serves_sitemap,
    static_noindex,
)
from next.urls import PageRoot
from tests.seo.trees import BASE, NOINDEX, WITH_BASE, routed, write_tree
from tests.support import write_page


DUPLICATE = """
from next.seo import sitemap

@sitemap.items("about")
def about():
    yield {}
    yield {}
"""

STATIC_HINTS = """
from datetime import date

from next.seo import Entry, sitemap

@sitemap.items("about")
def about():
    yield Entry(lastmod=date(2026, 1, 2), changefreq="daily", priority=0.9)
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
        label="pages",
        section="pages",
        sitemap=SeoSource(root / "sitemap.py", module, None),
        robots=None,
        robots_file=None,
    )
    return RouteSitemap(seo_root, module)


class TestModuleAttributes:
    """The attributes of a `sitemap.py` read leniently beside the defaults."""

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


class TestSitemapOptions:
    """One lenient reader serves the sitemap attributes and the cache lifetime."""

    def test_a_module_declaring_nothing_reads_the_defaults(self) -> None:
        assert SitemapOptions.read(types.ModuleType("sitemap")) == SitemapOptions()

    @pytest.mark.parametrize(
        ("name", "value", "read"),
        [
            ("limit", True, 50000),
            ("limit", -3, 50000),
            ("priority", True, None),
            ("priority", 1, 1.0),
            ("cache", True, None),
            ("cache", 0, None),
            ("cache", 60, 60),
            ("changefreq", "", None),
            ("protocol", 5, None),
            ("languages", "en", ()),
        ],
    )
    def test_each_attribute_reads_only_its_own_shape(
        self, name: str, value: object, read: object
    ) -> None:
        module = types.ModuleType("sitemap")
        setattr(module, name, value)
        assert getattr(SitemapOptions.read(module), name) == read


class TestItemValues:
    """An item value wins over the module default and the key folds its kwargs."""

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
        assert sitemap.lastmod(item) == datetime(2026, 1, 1, tzinfo=UTC)

    def test_the_key_folds_the_kwargs_in_name_order(self) -> None:
        first = SitemapItem("p/[a]/[b]", {"b": 2, "a": "x"})
        second = SitemapItem("p/[a]/[b]", {"a": "x", "b": "2"})
        assert first.key == second.key == ("p/[a]/[b]", (("a", "x"), ("b", "2")))


class TestLastmodDatetime:
    """A `lastmod` reads as an aware datetime in the current time zone."""

    def test_a_date_reads_as_midnight(self) -> None:
        assert lastmod_datetime(date(2026, 1, 2)) == datetime(2026, 1, 2, tzinfo=UTC)

    def test_a_naive_datetime_takes_the_current_zone(self) -> None:
        with override_settings(TIME_ZONE="America/Chicago"):
            naive = datetime(2026, 1, 2, 3, 0, tzinfo=UTC).replace(tzinfo=None)
            value = lastmod_datetime(naive)
        assert value == datetime(2026, 1, 2, 9, 0, tzinfo=UTC)

    def test_an_aware_datetime_stays_as_it_is(self) -> None:
        value = datetime(2026, 1, 2, 3, 0, tzinfo=UTC)
        assert lastmod_datetime(value) is value


class TestOrigin:
    """Protocol and domain prefer the declaration, then `base`, then the request."""

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
            with pytest.raises(SitemapOriginError, match="needs a request"):
                sitemap.get_urls()

    def test_a_request_free_build_with_base_answers_absolute_urls(
        self, tmp_path
    ) -> None:
        root = write_tree(tmp_path / "pages", pages=("", "about"), sitemap="")
        with routed(root, **WITH_BASE):
            urls = seo_manager.sitemaps()["pages"].get_urls()
        assert sorted(url["location"] for url in urls) == [f"{BASE}/", f"{BASE}/about/"]


class TestItems:
    """Items build once per sitemap, dropping duplicates and refusing stray yields."""

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
        assert caplog.text.count("lists 'about' with {} twice") == 1

    def test_an_entry_for_a_static_trail_carries_its_hints_in_place(
        self, tmp_path, caplog
    ) -> None:
        root = write_tree(
            tmp_path / "pages", pages=("", "about", "team"), sitemap=STATIC_HINTS
        )
        with routed(root), caplog.at_level(logging.WARNING, logger="next.seo"):
            items = seo_manager.sitemaps()["pages"].items()
        assert [item.trail for item in items] == ["", "about", "team"]
        assert items[1] == SitemapItem(
            "about", {}, date(2026, 1, 2), changefreq="daily", priority=0.9
        )
        assert "twice" not in caplog.text

    def test_a_page_with_refused_metadata_is_listed_with_a_warning(
        self, tmp_path, caplog
    ) -> None:
        root = write_tree(tmp_path / "pages", pages=("",), sitemap="")
        bad = write_page(root, "bad", 'template = "x"\nmetadata = {"bogus": 1}\n')
        with routed(root), caplog.at_level(logging.WARNING, logger="next.seo"):
            items = seo_manager.sitemaps()["pages"].items()
        assert sorted(item.trail for item in items) == ["", "bad"]
        assert f"the metadata of {bad} is refused" in caplog.text

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


class TestListedTrails:
    """A static trail is listed unless an exclude glob or noindex keeps it out."""

    def test_dynamic_excluded_and_noindex_trails_stay_out(self, tmp_path) -> None:
        trails = {
            trail: write_page(tmp_path, trail)
            for trail in ("", "about", "admin/users", "posts/[slug]")
        }
        trails["draft"] = write_page(tmp_path, "draft", NOINDEX)
        assert listed_trails(trails, ("admin/*",)) == ["", "about"]

    def test_the_exclude_globs_of_a_module(self) -> None:
        module = types.ModuleType("sitemap")
        module.exclude = ["admin/*", 3]
        assert SitemapOptions.read(module).exclude == ("admin/*",)
        assert is_excluded("admin/users", ("admin/*",)) is True
        assert is_excluded("about", ("admin/*",)) is False

    def test_brackets_in_a_glob_are_literal(self) -> None:
        assert is_excluded("posts/[slug]", ("posts/[slug]",)) is True
        assert is_excluded("posts/s", ("posts/[slug]",)) is False
        assert is_excluded("posts/[slug]/edit", ("posts/*",)) is True
        assert is_excluded("posts/[id]", ("posts/[??]",)) is True
        assert is_excluded("posts/[slug]", ("posts/[??]",)) is False
        assert is_excluded("posts.x", ("posts?x",)) is True
        assert is_excluded("postsx", ("posts.x",)) is False


class TestStaticNoindex:
    """The static metadata reads noindex, and a refused chain reads as indexed."""

    def test_a_noindex_page_reads_noindex(self, tmp_path) -> None:
        page_path = write_page(tmp_path, "", NOINDEX)
        assert static_noindex(page_path) is True

    def test_a_conflicting_page_reads_as_indexed(self, tmp_path, caplog) -> None:
        page_path = write_page(
            tmp_path,
            "",
            "from next.pages import page\n"
            'metadata = {"title": "a"}\n'
            "@page.metadata\n"
            "def meta():\n"
            '    return {"title": "b"}\n',
        )
        with caplog.at_level(logging.WARNING, logger="next.seo"):
            assert static_noindex(page_path) is False
        assert "both a metadata dict" in caplog.text


class TestServesSitemap:
    """An imported `sitemap.py` is served unless `NOINDEX` holds."""

    def test_an_imported_sitemap_is_served(self, tmp_path) -> None:
        root = _sitemap(tmp_path).seo_root
        assert serves_sitemap((root,)) is True
        assert serves_sitemap(()) is False

    @override_settings(NEXT_FRAMEWORK={"METADATA": {"NOINDEX": True}})
    def test_noindex_keeps_every_sitemap_unserved(self, tmp_path) -> None:
        assert serves_sitemap((_sitemap(tmp_path).seo_root,)) is False
