import os
import re
from collections.abc import Iterator

import pytest
from django.conf import settings
from django.core.cache import cache
from django.http import Http404
from django.test import Client, RequestFactory, override_settings
from django.urls import NoReverseMatch, reverse

from next.conf import next_framework_settings
from next.seo import SitemapTrailError, seo_manager, views
from next.testing import override_next_settings
from tests.seo.trees import (
    BASE,
    CALLS,
    NOINDEX,
    PREFIXED_URLCONF,
    USER_URLCONF,
    WITH_BASE,
    routed,
    write_tree,
)
from tests.support import write_page


I18N = {
    "LANGUAGES": [("en", "English"), ("de", "German")],
    "LANGUAGE_CODE": "en",
    "USE_I18N": True,
}
I18N_URLCONF = "tests.support.urls_i18n_pages"
I18N_ROOT_URLCONFS = ["tests.seo.urls_i18n", "tests.seo.urls_i18n_after"]
HOSTS = {"ALLOWED_HOSTS": ["testserver", "req.example"]}
NOINDEX_TEXT = 'template = "x"\nmetadata = {"robots": "noindex, nofollow"}\n'
NOINDEX_SITE = {"METADATA": {"NOINDEX": True}}
ITEMS = """
from datetime import date

from django.http import HttpRequest

from next import Depends
from next.seo import Entry, sitemap
from tests.seo.trees import CALLS


def slugs() -> list[str]:
    return ["a", "b"]


@sitemap.items("posts/[slug]")
def posts(request: HttpRequest, names: list[str] = Depends(slugs)):
    CALLS.append(request.path)
    for name in names:
        yield Entry(kwargs={"slug": name}, lastmod=date(2026, 1, 2))
    yield {"slug": "c"}
"""
DATED = """
from datetime import date

from next.seo import Entry, sitemap


@sitemap.items("posts/[slug]")
def posts():
    yield Entry(kwargs={"slug": "a"}, lastmod=date(2026, 1, 2))
"""
STAMPED = """
from datetime import UTC, datetime

from next.seo import Entry, sitemap


@sitemap.items("docs/[slug]")
def docs():
    yield Entry(kwargs={"slug": "z"}, lastmod=datetime(2026, 1, 3, tzinfo=UTC))
"""
MIXED = """
from datetime import UTC, date, datetime

from next.seo import Entry, sitemap


@sitemap.items("posts/[slug]")
def posts():
    yield Entry(kwargs={"slug": "a"}, lastmod=date(2026, 1, 2))
    yield Entry(kwargs={"slug": "b"}, lastmod=datetime(2026, 1, 1, 12, tzinfo=UTC))
    yield Entry(kwargs={"slug": "c"}, lastmod=datetime(2026, 1, 1, 18))
"""
UNKNOWN = """
from next.seo import sitemap


@sitemap.items("nope/[slug]")
def nope():
    yield {"slug": "a"}
"""
CACHED = "cache = 60\n" + ITEMS
ROBOTS = """
from next.seo import Rule

rules = [
    Rule(user_agent="*", disallow=["/private/"], crawl_delay=10),
    Rule(user_agent=("a", "b"), allow=["/"]),
]
host = "acme.example"
"""


@pytest.fixture(autouse=True)
def _fresh_calls() -> Iterator[None]:
    CALLS.clear()
    yield
    CALLS.clear()


def _locs(response) -> list[str]:
    return sorted(re.findall(r"<loc>(.*?)</loc>", response.content.decode()))


def _touch(path, delta_ns: int) -> None:
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + delta_ns))


class TestNoSource:
    """Without a source the SEO routes stay out, so the address is free."""

    def test_no_route_is_mounted_without_a_source(self, tmp_path) -> None:
        with routed(write_tree(tmp_path / "pages")):
            with pytest.raises(NoReverseMatch):
                reverse("next:sitemap")
            with pytest.raises(NoReverseMatch):
                reverse("next:robots")
            assert Client().get("/sitemap.xml").status_code == 404
            assert Client().get("/robots.txt").status_code == 404

    def test_a_user_pattern_after_the_include_answers(self, tmp_path) -> None:
        with routed(write_tree(tmp_path / "pages"), urlconf=USER_URLCONF):
            assert Client().get("/sitemap.xml").content == b"mine"
            assert Client().get("/robots.txt").content == b"mine"

    def test_a_source_puts_the_framework_route_ahead(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="", robots="")
        with routed(root, urlconf=USER_URLCONF):
            assert Client().get("/sitemap.xml").content != b"mine"
            assert Client().get("/robots.txt").content != b"mine"

    def test_the_views_answer_404_without_a_source(self, tmp_path) -> None:
        factory = RequestFactory()
        with routed(write_tree(tmp_path / "pages")):
            with pytest.raises(Http404, match=r"No sitemap\.py"):
                views.sitemap_view(factory.get("/sitemap.xml"))
            with pytest.raises(Http404, match=r"No robots\.py"):
                views.robots_view(factory.get("/robots.txt"))


class TestStaticSitemap:
    """A bare `sitemap.py` lists every static route a crawler may index."""

    def test_lists_every_static_route_and_nothing_else(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("", "about", "posts/[slug]", "admin/users"),
            sitemap="exclude = ['admin/*']\n",
        )
        write_page(root, "secret", NOINDEX)
        write_page(root, "hidden", NOINDEX_TEXT)
        with routed(root, **WITH_BASE):
            response = Client().get("/sitemap.xml")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/xml"
        assert response["X-Robots-Tag"] == "noindex, noodp, noarchive"
        assert _locs(response) == [f"{BASE}/", f"{BASE}/about/"]

    def test_an_exclude_trail_drops_only_that_route(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("posts/[slug]", "posts/s", "posts/g"),
            sitemap="exclude = ['posts/[slug]']\n",
        )
        with routed(root, **WITH_BASE):
            response = Client().get("/sitemap.xml")
        assert _locs(response) == [f"{BASE}/posts/g/", f"{BASE}/posts/s/"]

    @override_settings(**HOSTS)
    def test_without_base_the_request_host_is_the_origin(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("about",), sitemap="")
        with routed(root):
            response = Client().get("/sitemap.xml", HTTP_HOST="req.example")
        assert _locs(response) == ["http://req.example/about/"]

    def test_module_defaults_render_for_every_url(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("about",),
            sitemap="changefreq = 'weekly'\npriority = 0.4\n",
        )
        with routed(root, **WITH_BASE):
            body = Client().get("/sitemap.xml").content.decode()
        assert "<changefreq>weekly</changefreq>" in body
        assert "<priority>0.4</priority>" in body


class TestDeclaredItems:
    """`@sitemap.items` fills a dynamic route through the page URL."""

    def test_entries_reverse_through_the_page_url(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("", "posts/[slug]"), sitemap=ITEMS)
        with routed(root, **WITH_BASE):
            response = Client().get("/sitemap.xml")
        assert _locs(response) == [
            f"{BASE}/",
            f"{BASE}/posts/a/",
            f"{BASE}/posts/b/",
            f"{BASE}/posts/c/",
        ]
        assert response.content.decode().count("<lastmod>2026-01-02</lastmod>") == 2
        assert "Last-Modified" not in response
        assert CALLS == ["/sitemap.xml"]

    def test_last_modified_needs_every_item_dated(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("posts/[slug]",), sitemap=DATED)
        with routed(root, **WITH_BASE):
            response = Client().get("/sitemap.xml")
        assert response["Last-Modified"] == "Fri, 02 Jan 2026 00:00:00 GMT"

    def test_dates_and_datetimes_mix_in_one_sitemap(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("posts/[slug]",), sitemap=MIXED)
        with routed(root, **WITH_BASE):
            response = Client().get("/sitemap.xml")
        assert response.status_code == 200
        assert response["Last-Modified"] == "Fri, 02 Jan 2026 00:00:00 GMT"
        body = response.content.decode()
        assert body.count("<lastmod>2026-01-02</lastmod>") == 1
        assert body.count("<lastmod>2026-01-01</lastmod>") == 2

    @override_settings(TIME_ZONE="America/Chicago")
    def test_a_date_renders_as_declared_west_of_utc(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("posts/[slug]",), sitemap=DATED)
        with routed(root, **WITH_BASE):
            response = Client().get("/sitemap.xml")
        assert "<lastmod>2026-01-02</lastmod>" in response.content.decode()
        assert response["Last-Modified"] == "Fri, 02 Jan 2026 06:00:00 GMT"

    def test_a_page_with_refused_metadata_leaves_the_sitemap_served(
        self, tmp_path
    ) -> None:
        root = write_tree(tmp_path / "pages", pages=("",), sitemap="")
        write_page(root, "bad", 'template = "x"\nmetadata = {"bogus": 1}\n')
        with routed(root, **WITH_BASE):
            response = Client().get("/sitemap.xml")
        assert response.status_code == 200
        assert _locs(response) == [f"{BASE}/", f"{BASE}/bad/"]

    def test_an_unknown_trail_raises_at_build(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("about",), sitemap=UNKNOWN)
        with routed(root), pytest.raises(SitemapTrailError, match="nope") as caught:
            Client().get("/sitemap.xml")
        assert caught.value.file == root / "sitemap.py"


class TestIndex:
    """Several sections or pages answer an index that links each one."""

    def test_two_roots_answer_an_index_absolute_on_base(self, tmp_path) -> None:
        first = write_tree(tmp_path / "a" / "pages", pages=("about",), sitemap="")
        second = write_tree(tmp_path / "b" / "pages", pages=("team",), sitemap="")
        with routed(first, second, **WITH_BASE):
            index = Client().get("/sitemap.xml")
            sections = [
                Client().get("/sitemap-pages.xml"),
                Client().get("/sitemap-pages-2.xml"),
            ]
        assert index["Content-Type"] == "application/xml"
        assert index["X-Robots-Tag"] == "noindex, noodp, noarchive"
        assert _locs(index) == [
            f"{BASE}/sitemap-pages-2.xml",
            f"{BASE}/sitemap-pages.xml",
        ]
        assert "Last-Modified" not in index
        assert [_locs(section) for section in sections] == [
            [f"{BASE}/about/"],
            [f"{BASE}/team/"],
        ]

    @override_settings(**HOSTS)
    def test_without_base_the_index_reads_the_request(self, tmp_path) -> None:
        first = write_tree(tmp_path / "a" / "pages", pages=("about",), sitemap="")
        second = write_tree(tmp_path / "b" / "pages", pages=("team",), sitemap="")
        with routed(first, second):
            index = Client().get("/sitemap.xml", HTTP_HOST="req.example")
        assert _locs(index) == [
            "http://req.example/sitemap-pages-2.xml",
            "http://req.example/sitemap-pages.xml",
        ]

    def test_a_paginated_section_answers_the_index_with_page_links(
        self, tmp_path
    ) -> None:
        root = write_tree(
            tmp_path / "pages", pages=("about", "team"), sitemap="limit = 1\n"
        )
        with routed(root, **WITH_BASE):
            index = Client().get("/sitemap.xml")
            first = Client().get("/sitemap-pages.xml")
            second = Client().get("/sitemap-pages.xml?p=2")
            missing = Client().get("/sitemap-pages.xml?p=3")
            unknown = Client().get("/sitemap-other.xml")
        assert _locs(index) == [
            f"{BASE}/sitemap-pages.xml",
            f"{BASE}/sitemap-pages.xml?p=2",
        ]
        assert len(_locs(first)) == 1
        assert len(_locs(second)) == 1
        assert _locs(first) != _locs(second)
        assert missing.status_code == 404
        assert unknown.status_code == 404

    def test_last_modified_takes_the_latest_when_every_section_is_dated(
        self, tmp_path
    ) -> None:
        first = write_tree(tmp_path / "a", pages=("posts/[slug]",), sitemap=DATED)
        second = write_tree(tmp_path / "b", pages=("docs/[slug]",), sitemap=STAMPED)
        with routed(first, second, **WITH_BASE):
            index = Client().get("/sitemap.xml")
        assert index["Last-Modified"] == "Sat, 03 Jan 2026 00:00:00 GMT"
        assert "<lastmod>2026-01-02T00:00:00+00:00</lastmod>" in index.content.decode()

    def test_an_undated_section_ahead_of_a_dated_one_drops_the_header(
        self, tmp_path
    ) -> None:
        first = write_tree(tmp_path / "a", pages=("about",), sitemap="")
        second = write_tree(tmp_path / "b", pages=("docs/[slug]",), sitemap=STAMPED)
        with routed(first, second, **WITH_BASE):
            index = Client().get("/sitemap.xml")
        assert "Last-Modified" not in index


class TestI18n:
    """`i18n` lists one location per language, with alternates on demand."""

    @override_settings(**I18N)
    def test_one_location_per_language(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("about",), sitemap="i18n = True\n")
        with routed(root, urlconf=I18N_URLCONF, **WITH_BASE):
            response = Client().get("/sitemap.xml")
        assert _locs(response) == [f"{BASE}/about/", f"{BASE}/de/about/"]
        assert "xhtml:link" not in response.content.decode()

    @override_settings(**I18N)
    def test_alternates_and_x_default(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("about",),
            sitemap="i18n = True\nalternates = True\nx_default = True\n",
        )
        with routed(root, urlconf=I18N_URLCONF, **WITH_BASE):
            body = Client().get("/sitemap.xml").content.decode()
        assert (
            f'<xhtml:link rel="alternate" hreflang="de" href="{BASE}/de/about/"/>'
            in body
        )
        assert (
            f'<xhtml:link rel="alternate" hreflang="x-default" href="{BASE}/about/"/>'
            in body
        )

    @override_settings(**I18N)
    def test_languages_narrows_the_set(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("about",),
            sitemap="i18n = True\nlanguages = ['de']\n",
        )
        with routed(root, urlconf=I18N_URLCONF, **WITH_BASE):
            response = Client().get("/sitemap.xml")
        assert _locs(response) == [f"{BASE}/de/about/"]


class TestCache:
    """`cache` in a `sitemap.py` caches the sitemap view and never robots."""

    @pytest.fixture(autouse=True)
    def _empty_cache(self) -> Iterator[None]:
        cache.clear()
        yield
        cache.clear()

    def test_the_sitemap_is_cached_and_robots_is_not(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("posts/[slug]",), sitemap=CACHED)
        (root / "robots.py").write_text("")
        with routed(root, **WITH_BASE):
            first = Client().get("/sitemap.xml")
            second = Client().get("/sitemap.xml")
            robots = Client().get("/robots.txt")
        assert first.content == second.content
        assert CALLS == ["/sitemap.xml"]
        assert "max-age=60" in second["Cache-Control"]
        assert "Cache-Control" not in robots

    def test_the_cached_view_is_wrapped_once_per_manager_version(
        self, tmp_path
    ) -> None:
        root = write_tree(tmp_path / "pages", pages=("posts/[slug]",), sitemap=CACHED)
        with routed(root, **WITH_BASE):
            wrapped = views._wrapped_sitemap.get()
            assert views._wrapped_sitemap.get() is wrapped
            seo_manager.reset()
            assert views._wrapped_sitemap.get() is not wrapped

    def test_a_bool_cache_leaves_the_sitemap_uncached(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("posts/[slug]",),
            sitemap="cache = True\n" + ITEMS,
        )
        with routed(root, **WITH_BASE):
            first = Client().get("/sitemap.xml")
            Client().get("/sitemap.xml")
        assert CALLS == ["/sitemap.xml", "/sitemap.xml"]
        assert "Cache-Control" not in first

    def test_without_cache_every_request_builds_afresh(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("posts/[slug]",), sitemap=ITEMS)
        with routed(root, **WITH_BASE):
            first = Client().get("/sitemap.xml")
            Client().get("/sitemap.xml")
        assert CALLS == ["/sitemap.xml", "/sitemap.xml"]
        assert "Cache-Control" not in first


class TestReloadKeepsDeclaredItems:
    """A settings reload leaves the memoised `sitemap.py` unexecuted, entries stay."""

    def test_entries_survive_a_settings_reload(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("", "posts/[slug]"), sitemap=ITEMS)
        with routed(root, **WITH_BASE):
            before = _locs(Client().get("/sitemap.xml"))
            next_framework_settings.reload()
            after = _locs(Client().get("/sitemap.xml"))
        assert before == after
        assert f"{BASE}/posts/a/" in after

    def test_entries_survive_an_override_settings_block(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("", "posts/[slug]"), sitemap=ITEMS)
        with routed(root, **WITH_BASE):
            before = _locs(Client().get("/sitemap.xml"))
            with override_settings(
                NEXT_FRAMEWORK={**settings.NEXT_FRAMEWORK, "METADATA": {}}
            ):
                inside = _locs(Client().get("/sitemap.xml"))
            after = _locs(Client().get("/sitemap.xml"))
        assert inside == [
            "http://testserver/",
            "http://testserver/posts/a/",
            "http://testserver/posts/b/",
            "http://testserver/posts/c/",
        ]
        assert before == after
        assert f"{BASE}/posts/a/" in after


class TestRobots:
    """`/robots.txt` renders the `robots.py` rules or serves the static file."""

    def test_rules_and_host_render_with_an_absolute_sitemap_line(
        self, tmp_path
    ) -> None:
        root = write_tree(tmp_path / "pages", sitemap="", robots=ROBOTS)
        with routed(root, **WITH_BASE):
            response = Client().get("/robots.txt")
        assert response["Content-Type"] == "text/plain; charset=utf-8"
        assert response.content.decode() == (
            "User-agent: *\n"
            "Disallow: /private/\n"
            "Crawl-delay: 10\n"
            "\n"
            "User-agent: a\n"
            "User-agent: b\n"
            "Allow: /\n"
            "\n"
            "Host: acme.example\n"
            f"Sitemap: {BASE}/sitemap.xml\n"
        )

    def test_an_empty_module_allows_everything_and_names_the_sitemap(
        self, tmp_path
    ) -> None:
        root = write_tree(tmp_path / "pages", sitemap="", robots="")
        with routed(root):
            response = Client().get("/robots.txt")
        assert response.content.decode() == (
            "User-agent: *\nAllow: /\n\nSitemap: http://testserver/sitemap.xml\n"
        )

    def test_without_a_sitemap_no_sitemap_line_is_written(self, tmp_path) -> None:
        with routed(write_tree(tmp_path / "pages", robots="")):
            response = Client().get("/robots.txt")
        assert response.content.decode() == "User-agent: *\nAllow: /\n"

    def test_a_static_file_is_served_byte_for_byte(self, tmp_path) -> None:
        raw = b"\xff\xfeUser-agent: *\r\nDisallow: /x/\r\n"
        root = write_tree(tmp_path / "pages", sitemap="", robots_txt=raw)
        with routed(root):
            response = Client().get("/robots.txt")
        assert response["Content-Type"] == "text/plain; charset=utf-8"
        assert response.content == raw

    def test_a_rewritten_static_file_is_picked_up(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", robots_txt=b"one\n")
        with routed(root):
            assert Client().get("/robots.txt").content == b"one\n"
            (root / "robots.txt").write_bytes(b"two\n")
            _touch(root / "robots.txt", 1_000_000)
            assert Client().get("/robots.txt").content == b"two\n"
            (root / "robots.txt").unlink()
            assert Client().get("/robots.txt").status_code == 404

    def test_robots_py_wins_in_one_root(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", robots="", robots_txt=b"static\n")
        with routed(root):
            assert Client().get("/robots.txt").content == b"User-agent: *\nAllow: /\n"

    def test_the_first_root_wins_across_roots(self, tmp_path) -> None:
        first = write_tree(tmp_path / "a", robots_txt=b"first\n")
        second = write_tree(tmp_path / "b", robots="")
        with routed(first, second):
            assert Client().get("/robots.txt").content == b"first\n"

    def test_a_bare_mount_reverses_the_sitemap_under_the_next_namespace(
        self, tmp_path
    ) -> None:
        root = write_tree(tmp_path / "pages", sitemap="", robots="")
        with routed(root, urlconf=USER_URLCONF):
            response = Client().get("/direct/robots.txt")
        assert response.content.decode().endswith(
            "Sitemap: http://testserver/sitemap.xml\n"
        )


class TestNoindex:
    """`NOINDEX` drops the sitemap and its line in a generated robots."""

    def test_noindex_drops_the_sitemap_routes_while_it_holds(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("about",), sitemap="")
        with routed(root):
            assert Client().get("/sitemap.xml").status_code == 200
            with override_next_settings(METADATA={"NOINDEX": True}):
                assert Client().get("/sitemap.xml").status_code == 404
                assert Client().get("/sitemap-pages.xml").status_code == 404
            assert Client().get("/sitemap.xml").status_code == 200

    def test_noindex_answers_404_at_the_host_root_mount(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("about",), sitemap="")
        with routed(root, urlconf=PREFIXED_URLCONF, **NOINDEX_SITE):
            assert Client().get("/sitemap.xml").status_code == 404

    def test_noindex_drops_the_sitemap_line_of_generated_robots(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="", robots="")
        with routed(root, **NOINDEX_SITE):
            response = Client().get("/robots.txt")
        assert response.content.decode() == "User-agent: *\nAllow: /\n"

    def test_noindex_leaves_a_static_robots_file_as_written(self, tmp_path) -> None:
        raw = b"User-agent: *\nSitemap: https://acme.example/sitemap.xml\n"
        root = write_tree(tmp_path / "pages", sitemap="", robots_txt=raw)
        with routed(root, **NOINDEX_SITE):
            assert Client().get("/robots.txt").content == raw


class TestHostRootMount:
    """`next.seo.urls` at the host root serves a tree mounted under a prefix."""

    def test_the_seo_urls_serve_at_the_host_root_and_only_there(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("about",), sitemap="", robots="")
        with routed(root, urlconf=PREFIXED_URLCONF):
            assert reverse("next_seo:robots") == "/robots.txt"
            assert reverse("next_seo:sitemap") == "/sitemap.xml"
            assert reverse("next:sitemap") == "/prefix/sitemap.xml"
            robots = Client().get("/robots.txt")
            top = Client().get("/sitemap.xml")
            prefixed = Client().get("/prefix/sitemap.xml")
            prefixed_robots = Client().get("/prefix/robots.txt")
        assert robots.content.decode().endswith(
            "Sitemap: http://testserver/sitemap.xml\n"
        )
        assert _locs(top) == ["http://testserver/prefix/about/"]
        assert prefixed.status_code == prefixed_robots.status_code == 404

    def test_the_index_links_the_sections_under_the_serving_mount(
        self, tmp_path
    ) -> None:
        first = write_tree(tmp_path / "a", pages=("about",), sitemap="")
        second = write_tree(tmp_path / "b", pages=("team",), sitemap="")
        with routed(first, second, urlconf=PREFIXED_URLCONF):
            top = Client().get("/sitemap.xml")
            section = Client().get("/sitemap-a.xml")
            prefixed = Client().get("/prefix/sitemap-a.xml")
        assert _locs(top) == [
            "http://testserver/sitemap-a.xml",
            "http://testserver/sitemap-b.xml",
        ]
        assert _locs(section) == ["http://testserver/prefix/about/"]
        assert prefixed.status_code == 404

    @override_settings(**I18N)
    @pytest.mark.parametrize("urlconf", I18N_ROOT_URLCONFS)
    def test_a_language_prefix_serves_no_copy(self, tmp_path, urlconf) -> None:
        root = write_tree(tmp_path / "pages", pages=("about",), sitemap="", robots="")
        with routed(root, urlconf=urlconf):
            robots = Client().get("/robots.txt")
            top = Client().get("/sitemap.xml")
            copies = [
                Client().get("/de/sitemap.xml"),
                Client().get("/de/sitemap-pages.xml"),
                Client().get("/de/robots.txt"),
            ]
        assert robots.content.decode().endswith(
            "Sitemap: http://testserver/sitemap.xml\n"
        )
        assert _locs(top) == ["http://testserver/about/"]
        assert [copy.status_code for copy in copies] == [404, 404, 404]
