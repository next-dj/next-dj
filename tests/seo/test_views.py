import errno
import logging
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from django.conf import settings
from django.core.cache import cache
from django.test import Client, RequestFactory, override_settings
from django.urls import NoReverseMatch, reverse

from next.conf import next_framework_settings
from next.pages.loaders import has_load_errors
from next.seo import SitemapTrailError, seo_manager, views
from next.testing import override_next_settings, parse_sitemap
from tests.seo.sources import CALLS
from tests.support import (
    BASE,
    CLOSED_SITE,
    I18N,
    I18N_URLCONF,
    NOINDEX,
    PREFIXED_URLCONF,
    SEO_I18N_AFTER_URLCONF,
    SEO_I18N_PREFIXED_URLCONF,
    SEO_I18N_URLCONF,
    USER_URLCONF,
    WITH_BASE,
    routed,
    touch_later,
    write_page,
    write_tree,
)


I18N_ROOT_URLCONFS = [SEO_I18N_URLCONF, SEO_I18N_AFTER_URLCONF]
HOSTS = {"ALLOWED_HOSTS": ["testserver", "req.example", "docs.example"]}
NOINDEX_TEXT = 'template = "x"\nmetadata = {"robots": "noindex, nofollow"}\n'
ITEMS = """
from datetime import date

from django.http import HttpRequest

from next import Depends
from next.seo import SitemapEntry, sitemap
from tests.seo.sources import CALLS


def slugs() -> list[str]:
    return ["a", "b"]


@sitemap.items("posts/[slug]")
def posts(request: HttpRequest, names: list[str] = Depends(slugs)):
    CALLS.append(request.path)
    for name in names:
        yield SitemapEntry(kwargs={"slug": name}, lastmod=date(2026, 1, 2))
    yield {"slug": "c"}
"""
DATED = """
from datetime import date

from next.seo import SitemapEntry, sitemap


@sitemap.items("posts/[slug]")
def posts():
    return [SitemapEntry(kwargs={"slug": "a"}, lastmod=date(2026, 1, 2))]
"""
STAMPED = """
from datetime import UTC, datetime

from next.seo import SitemapEntry, sitemap


@sitemap.items("docs/[slug]")
def docs():
    return [SitemapEntry(kwargs={"slug": "z"}, lastmod=datetime(2026, 1, 3, tzinfo=UTC))]
"""
NAMES = """
from next.seo import sitemap


@sitemap.items("tags/[str:name]")
def tags():
    return [{"name": "a&b c"}, {"name": "café"}]
"""
RAISING = """
from next.seo import sitemap
from tests.seo.sources import CALLS


@sitemap.items("posts/[slug]")
def posts():
    CALLS.append("call")
    raise RuntimeError("database down")
"""
UNKNOWN = """
from next.seo import sitemap


@sitemap.items("nope/[slug]")
def nope():
    return [{"slug": "a"}]
"""
CACHED = "cache = 60\n" + ITEMS
ROBOTS = """
from next.seo import RobotsRule

sitemaps = ["https://cdn.example/news.xml"]
rules = [
    RobotsRule(user_agent="*", disallow=["/private/"], crawl_delay=10),
    RobotsRule(user_agent=("a", "b"), allow=["/"]),
]
"""
HOST_RULES = """
from django.http import HttpRequest

from next.seo import RobotsRule


def rules(request: HttpRequest) -> list[RobotsRule]:
    groups = [
        RobotsRule(disallow=["/search/"]),
        RobotsRule(user_agent=("GPTBot", "ClaudeBot"), disallow="/"),
    ]
    if request.get_host().startswith("docs."):
        groups.append(RobotsRule(user_agent="Bingbot", crawl_delay=2))
    return groups
"""
URLSET = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
    'xmlns:xhtml="http://www.w3.org/1999/xhtml">\n{}\n</urlset>\n'
)
GUARDED_ROBOTS = """
from next.seo import RobotsRule

rules = [RobotsRule(disallow="{}")]
"""
INJECTED = """
from next.seo import RobotsRule

rules = [RobotsRule(disallow="/x\\nUser-agent: evil\\nAllow: /")]
"""


@pytest.fixture(autouse=True)
def _fresh_calls() -> Iterator[None]:
    CALLS.clear()
    yield
    CALLS.clear()


def _locs(response) -> list[str]:
    return sorted(url.loc for url in parse_sitemap(response))


class TestNoSource:
    """Without a source the SEO routes stay out, so the address is free."""

    def test_no_route_is_mounted_without_a_source(self, tmp_path) -> None:
        with routed(write_tree(tmp_path / "pages")):
            for name in ("sitemap", "robots"):
                with pytest.raises(NoReverseMatch):
                    reverse(f"next:{name}")
            assert Client().get("/sitemap.xml").status_code == 404
            assert Client().get("/robots.txt").status_code == 404

    def test_a_user_pattern_after_the_include_answers(self, tmp_path) -> None:
        with routed(write_tree(tmp_path / "pages"), urlconf=USER_URLCONF):
            sitemap = Client().get("/sitemap.xml")
            robots = Client().get("/robots.txt")
        assert (sitemap.status_code, sitemap.content) == (200, b"mine")
        assert (robots.status_code, robots.content) == (200, b"mine")

    def test_a_source_puts_the_framework_route_ahead(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="", robots="")
        with routed(root, urlconf=USER_URLCONF):
            sitemap = Client().get("/sitemap.xml")
            robots = Client().get("/robots.txt")
        assert sitemap.status_code == robots.status_code == 200
        assert sitemap["Content-Type"] == "application/xml"
        assert sitemap.content.decode() == URLSET.format(
            "<url><loc>http://testserver/</loc></url>"
        )
        assert robots.content.decode() == (
            "User-agent: *\nDisallow:\n\nSitemap: http://testserver/sitemap.xml\n"
        )

    def test_the_views_answer_a_plain_404_without_a_source(self, tmp_path) -> None:
        factory = RequestFactory()
        with routed(write_tree(tmp_path / "pages")):
            for view, path in (
                (views.sitemap_view, "/sitemap.xml"),
                (views.robots_view, "/robots.txt"),
            ):
                response = view(factory.get(path))
                assert response.status_code == 404
                assert response["Content-Type"] == "text/plain; charset=utf-8"
                assert response.content == b"Not found"

    @override_settings(DEBUG=True)
    def test_debug_names_the_reason_in_the_body(self, tmp_path) -> None:
        factory = RequestFactory()
        with routed(write_tree(tmp_path / "pages")):
            for view, path, reason in (
                (
                    views.sitemap_view,
                    "/sitemap.xml",
                    b"No backend lists a sitemap, or the site is closed to search",
                ),
                (
                    views.robots_view,
                    "/robots.txt",
                    b"No robots.py or robots.txt declares robots",
                ),
            ):
                response = view(factory.get(path))
                assert response.status_code == 404
                assert response["Content-Type"] == "text/plain; charset=utf-8"
                assert response.content == reason


class TestSafeMethods:
    """Every SEO view answers `GET` and `HEAD`, and 405 to anything else."""

    def test_head_answers_without_a_body_and_post_is_refused(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="", robots="")
        with routed(root, **WITH_BASE):
            for path in ("/sitemap.xml", "/robots.txt"):
                head = Client().head(path)
                post = Client().post(path)
                assert head.status_code == 200, path
                assert head.content == b""
                assert post.status_code == 405, path
                assert post["Allow"] == "GET, HEAD"


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

    @override_settings(**HOSTS)
    def test_without_a_site_url_the_request_host_is_the_origin(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("about",), sitemap="")
        with routed(root):
            response = Client().get("/sitemap.xml", HTTP_HOST="req.example")
        assert _locs(response) == ["http://req.example/about/"]

    def test_a_disallowed_host_answers_400(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("about",), sitemap="")
        with routed(root):
            response = Client().get("/sitemap.xml", HTTP_HOST="evil.example")
        assert response.status_code == 400

    def test_module_defaults_render_for_every_url(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("about",),
            sitemap="changefreq = 'weekly'\npriority = 0.4\n",
        )
        with routed(root, **WITH_BASE):
            response = Client().get("/sitemap.xml")
        assert response.status_code == 200
        assert response.content.decode() == URLSET.format(
            f"<url><loc>{BASE}/about/</loc><changefreq>weekly</changefreq>"
            "<priority>0.4</priority></url>"
        )

    def test_a_loc_escapes_every_character_it_carries(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("tags/[str:name]",), sitemap=NAMES)
        with routed(root, **WITH_BASE):
            response = Client().get("/sitemap.xml")
        assert response.content.decode() == URLSET.format(
            f"<url><loc>{BASE}/tags/a&amp;b%20c/</loc></url>"
            f"<url><loc>{BASE}/tags/caf%C3%A9/</loc></url>"
        )
        assert _locs(response) == [f"{BASE}/tags/a&b%20c/", f"{BASE}/tags/caf%C3%A9/"]

    @override_settings(**I18N)
    def test_an_accept_language_leaves_the_urls_alone(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("about",), sitemap="")
        middleware = [
            *settings.MIDDLEWARE[:2],
            "django.middleware.locale.LocaleMiddleware",
            *settings.MIDDLEWARE[2:],
        ]
        with (
            override_settings(MIDDLEWARE=middleware),
            routed(root, urlconf=SEO_I18N_URLCONF, **WITH_BASE),
        ):
            english = Client().get("/sitemap.xml")
            german = Client().get("/sitemap.xml", HTTP_ACCEPT_LANGUAGE="de")
        assert english.content == german.content
        assert _locs(german) == [f"{BASE}/about/"]

    def test_a_broken_sitemap_py_answers_404_and_marks_no_page(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("about",), sitemap="1/0\n")
        with routed(root, **WITH_BASE):
            response = Client().get("/sitemap.xml")
            assert has_load_errors() is False
        assert response.status_code == 404


class TestBrokenSources:
    """A source that fails to import holds its route and answers 404."""

    @pytest.mark.parametrize(
        ("sources", "path", "failed"),
        [
            ({"sitemap": "1/0\n"}, "/sitemap.xml", "sitemap.py"),
            (
                {"robots": "1/0\n", "robots_txt": b"User-agent: *\n"},
                "/robots.txt",
                "robots.py",
            ),
        ],
        ids=["sitemap", "robots"],
    )
    def test_every_broken_source_answers_404_and_logs_once(
        self, tmp_path, caplog, sources, path, failed
    ) -> None:
        root = write_tree(tmp_path / "pages", **sources)
        with (
            routed(root, **WITH_BASE),
            caplog.at_level(logging.ERROR, logger="next.seo"),
        ):
            first = Client().get(path)
            second = Client().get(path)
        assert first.status_code == second.status_code == 404
        assert first.content == b"Not found"
        assert caplog.text.count(f"{root / failed} failed to import") == 1


class TestDeclaredItems:
    """`@sitemap.items` fills a dynamic route through the page URL."""

    def test_entries_reverse_through_the_page_url(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("", "posts/[slug]"), sitemap=ITEMS)
        with routed(root, **WITH_BASE):
            response = Client().get("/sitemap.xml")
        urls = parse_sitemap(response)
        assert sorted(url.loc for url in urls) == [
            f"{BASE}/",
            f"{BASE}/posts/a/",
            f"{BASE}/posts/b/",
            f"{BASE}/posts/c/",
        ]
        assert [url.lastmod for url in urls].count("2026-01-02") == 2
        assert "Last-Modified" not in response
        assert CALLS == ["/sitemap.xml"]

    def test_last_modified_needs_every_item_dated(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("posts/[slug]",), sitemap=DATED)
        with routed(root, **WITH_BASE):
            response = Client().get("/sitemap.xml")
        assert response["Last-Modified"] == "Fri, 02 Jan 2026 00:00:00 GMT"

    @override_settings(TIME_ZONE="America/Chicago")
    def test_a_date_renders_as_declared_west_of_utc(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("posts/[slug]",), sitemap=DATED)
        with routed(root, **WITH_BASE):
            response = Client().get("/sitemap.xml")
        assert parse_sitemap(response)[0].lastmod == "2026-01-02"
        assert response["Last-Modified"] == "Fri, 02 Jan 2026 06:00:00 GMT"

    def test_an_unknown_trail_raises_at_build(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("about",), sitemap=UNKNOWN)
        with routed(root), pytest.raises(SitemapTrailError, match="nope"):
            Client().get("/sitemap.xml")

    def test_an_items_trail_under_exclude_lists_nothing(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("", "posts/[slug]"),
            sitemap="exclude = ['posts/*']\n" + ITEMS,
        )
        with routed(root, **WITH_BASE):
            response = Client().get("/sitemap.xml")
        assert _locs(response) == [f"{BASE}/"]
        assert CALLS == []


class TestIndex:
    """Several sections or pages answer an index that links each one."""

    def test_two_roots_answer_an_index_absolute_on_the_site_url(self, tmp_path) -> None:
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

    def test_a_paginated_section_links_every_page(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages", pages=("about", "team"), sitemap="limit = 1\n"
        )
        with routed(root, **WITH_BASE):
            index = Client().get("/sitemap.xml")
            first = Client().get("/sitemap-pages.xml")
            second = Client().get("/sitemap-pages.xml?p=2")
            missing = Client().get("/sitemap-pages.xml?p=3")
            garbled = Client().get("/sitemap-pages.xml?p=x")
            unknown = Client().get("/sitemap-other.xml")
        assert _locs(index) == [
            f"{BASE}/sitemap-pages.xml",
            f"{BASE}/sitemap-pages.xml?p=2",
        ]
        assert first.status_code == second.status_code == 200
        assert _locs(first) == [f"{BASE}/about/"]
        assert _locs(second) == [f"{BASE}/team/"]
        assert missing.status_code == garbled.status_code == unknown.status_code == 404

    def test_last_modified_takes_the_latest_when_every_section_is_dated(
        self, tmp_path
    ) -> None:
        first = write_tree(tmp_path / "a", pages=("posts/[slug]",), sitemap=DATED)
        second = write_tree(tmp_path / "b", pages=("docs/[slug]",), sitemap=STAMPED)
        with routed(first, second, **WITH_BASE):
            index = Client().get("/sitemap.xml")
        assert index["Last-Modified"] == "Sat, 03 Jan 2026 00:00:00 GMT"
        assert [url.lastmod for url in parse_sitemap(index)] == [
            "2026-01-02T00:00:00+00:00",
            "2026-01-03T00:00:00+00:00",
        ]

    def test_an_undated_section_drops_the_header(self, tmp_path) -> None:
        first = write_tree(tmp_path / "a", pages=("about",), sitemap="")
        second = write_tree(tmp_path / "b", pages=("docs/[slug]",), sitemap=STAMPED)
        with routed(first, second, **WITH_BASE):
            index = Client().get("/sitemap.xml")
        assert "Last-Modified" not in index

    @override_settings(**I18N)
    def test_i18n_alternates_travel_through_the_index_pages(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("about",),
            sitemap="i18n = True\nalternates = True\nx_default = True\n",
        )
        with routed(root, urlconf=I18N_URLCONF, **WITH_BASE):
            [url, _german] = parse_sitemap(Client().get("/sitemap.xml"))
        assert url.alternates == (
            ("en", f"{BASE}/about/"),
            ("de", f"{BASE}/de/about/"),
            ("x-default", f"{BASE}/about/"),
        )


class TestCache:
    """`cache` caches the view under a key the sources fingerprint."""

    @pytest.fixture(autouse=True)
    def _empty_cache(self) -> Iterator[None]:
        cache.clear()
        yield
        cache.clear()

    def test_the_sitemap_is_cached_and_a_plain_robots_is_not(self, tmp_path) -> None:
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

    def test_the_key_prefix_carries_the_fingerprint(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("posts/[slug]",), sitemap=CACHED)
        with (
            routed(root, **WITH_BASE),
            patch("next.seo.views.cache_page", wraps=views.cache_page) as wrapper,
        ):
            Client().get("/sitemap.xml")
            fingerprint = seo_manager.fingerprint()
        wrapper.assert_called_once_with(60, key_prefix=f"next-seo-{fingerprint}")

    def test_a_declared_robots_cache_caches_robots(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", robots="cache = 3600\n")
        with routed(root, **WITH_BASE):
            response = Client().get("/robots.txt")
        assert response["Cache-Control"] == "public, max-age=3600"

    def test_a_cache_dict_sets_its_directives_and_caches_its_age(
        self, tmp_path
    ) -> None:
        cached = "cache = {'public': True, 's_maxage': 300, 'vary': ['Accept']}\n"
        root = write_tree(
            tmp_path / "pages", pages=("posts/[slug]",), sitemap=cached + ITEMS
        )
        with routed(root, **WITH_BASE):
            first = Client().get("/sitemap.xml")
            second = Client().get("/sitemap.xml")
        assert first["Cache-Control"].startswith("public, s-maxage=300")
        assert "Accept" in first["Vary"]
        assert second.content == first.content
        assert CALLS == ["/sitemap.xml"]

    @pytest.mark.parametrize(
        ("cached", "header"),
        [
            ("False", "private, no-store"),
            ("{'no_store': True, 'max_age': 60}", "no-store, max-age=60"),
        ],
    )
    def test_no_store_is_sent_and_never_cached(self, tmp_path, cached, header) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("posts/[slug]",),
            sitemap=f"cache = {cached}\n" + ITEMS,
        )
        with routed(root, **WITH_BASE):
            first = Client().get("/sitemap.xml")
            Client().get("/sitemap.xml")
        assert first["Cache-Control"] == header
        assert CALLS == ["/sitemap.xml", "/sitemap.xml"]

    def test_a_404_carries_no_declared_cache(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", sitemap="cache = 60\n")
        with routed(root, **WITH_BASE):
            response = Client().get("/sitemap-ghost.xml")
        assert response.status_code == 404
        assert "public" not in response.get("Cache-Control", "")

    def test_a_failing_items_callable_is_never_cached(self, tmp_path) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("posts/[slug]",),
            sitemap="cache = 60\n" + RAISING,
        )
        with routed(root, **WITH_BASE):
            client = Client(raise_request_exception=False)
            assert client.get("/sitemap.xml").status_code == 500
            assert client.get("/sitemap.xml").status_code == 500
        assert CALLS == ["call", "call"]

    def test_an_edited_cache_takes_effect_once_the_manager_resets(
        self, tmp_path
    ) -> None:
        root = write_tree(tmp_path / "pages", pages=("posts/[slug]",), sitemap=CACHED)
        with routed(root, **WITH_BASE):
            first = Client().get("/sitemap.xml")
            (root / "sitemap.py").write_text("cache = 120\n" + ITEMS)
            touch_later(root / "sitemap.py")
            seo_manager.reset()
            second = Client().get("/sitemap.xml")
            third = Client().get("/sitemap.xml")
        assert first["Cache-Control"] == "public, max-age=60"
        assert (
            second["Cache-Control"] == third["Cache-Control"] == "public, max-age=120"
        )
        assert CALLS == ["/sitemap.xml", "/sitemap.xml"]

    def test_without_cache_every_request_builds_afresh(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("posts/[slug]",), sitemap=ITEMS)
        with routed(root, **WITH_BASE):
            first = Client().get("/sitemap.xml")
            Client().get("/sitemap.xml")
        assert CALLS == ["/sitemap.xml", "/sitemap.xml"]
        assert "Cache-Control" not in first


class TestReloadKeepsDeclaredItems:
    """A settings reload loads the sources again, so their entries stay."""

    def test_entries_survive_a_settings_reload(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("", "posts/[slug]"), sitemap=ITEMS)
        with routed(root, **WITH_BASE):
            before = _locs(Client().get("/sitemap.xml"))
            next_framework_settings.reload()
            after = _locs(Client().get("/sitemap.xml"))
        assert before == after
        assert f"{BASE}/posts/a/" in after


class TestRobots:
    """`/robots.txt` renders the `robots.py` groups or serves the static file."""

    def test_the_groups_render_with_every_sitemap_line(self, tmp_path) -> None:
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
            f"Sitemap: {BASE}/sitemap.xml\n"
            "Sitemap: https://cdn.example/news.xml\n"
        )

    def test_an_empty_module_allows_everything(self, tmp_path) -> None:
        with routed(write_tree(tmp_path / "pages", robots="")):
            response = Client().get("/robots.txt")
        assert response.content.decode() == "User-agent: *\nDisallow:\n"

    @override_settings(**HOSTS)
    def test_rules_resolve_per_request_host(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", robots=HOST_RULES)
        with routed(root):
            main = Client().get("/robots.txt", HTTP_HOST="req.example")
            docs = Client().get("/robots.txt", HTTP_HOST="docs.example")
            again = Client().get("/robots.txt", HTTP_HOST="docs.example")
        assert main.content.decode() == (
            "User-agent: *\n"
            "Disallow: /search/\n"
            "\n"
            "User-agent: GPTBot\n"
            "User-agent: ClaudeBot\n"
            "Disallow: /\n"
        )
        assert docs.content.decode().endswith(
            "User-agent: Bingbot\nDisallow:\nCrawl-delay: 2\n"
        )
        assert docs.content == again.content

    def test_an_injected_line_break_never_renders(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", robots=INJECTED)
        with routed(root):
            response = Client().get("/robots.txt")
        assert response.status_code == 404
        assert seo_manager.robots_source() is None

    def test_a_static_file_is_served_byte_for_byte(self, tmp_path) -> None:
        raw = b"\xff\xfeUser-agent: *\r\nDisallow: /x/\r\n"
        root = write_tree(tmp_path / "pages", sitemap="", robots_txt=raw)
        with routed(root):
            response = Client().get("/robots.txt")
        assert response["Content-Type"] == "text/plain; charset=utf-8"
        assert response.content == raw

    @override_settings(DEBUG=True)
    def test_a_watched_robots_module_serves_its_edit(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", robots=GUARDED_ROBOTS.format("/a/"))
        with routed(root, SITE={"INDEXABLE": True}):
            before = Client().get("/robots.txt")
            touch_later(root / "robots.py", GUARDED_ROBOTS.format("/b/"))
            after = Client().get("/robots.txt")
        assert before.content == b"User-agent: *\nDisallow: /a/\n"
        assert after.content == b"User-agent: *\nDisallow: /b/\n"

    def test_an_unwatched_robots_module_waits_for_a_reset(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", robots=GUARDED_ROBOTS.format("/a/"))
        with routed(root):
            Client().get("/robots.txt")
            touch_later(root / "robots.py", GUARDED_ROBOTS.format("/b/"))
            kept = Client().get("/robots.txt")
            seo_manager.reset()
            fresh = Client().get("/robots.txt")
        assert kept.content == b"User-agent: *\nDisallow: /a/\n"
        assert fresh.content == b"User-agent: *\nDisallow: /b/\n"

    def test_a_rewritten_then_removed_file_answers_then_404s(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", robots_txt=b"one\n")
        with routed(root):
            assert Client().get("/robots.txt").content == b"one\n"
            (root / "robots.txt").write_bytes(b"two\n")
            touch_later(root / "robots.txt")
            assert Client().get("/robots.txt").content == b"two\n"
            (root / "robots.txt").unlink()
            assert Client().get("/robots.txt").status_code == 404

    def test_a_failing_read_answers_the_last_copy_then_503(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", robots_txt=b"one\n")
        denied = PermissionError(errno.EACCES, "denied")
        with routed(root):
            assert Client().get("/robots.txt").content == b"one\n"
            touch_later(root / "robots.txt")
            with patch.object(Path, "read_bytes", side_effect=denied):
                stale = Client().get("/robots.txt")
            seo_manager.reset()
            with patch.object(Path, "read_bytes", side_effect=denied):
                failed = Client().get("/robots.txt")
        assert stale.content == b"one\n"
        assert failed.status_code == 503
        assert failed["Retry-After"] == "300"

    def test_robots_py_wins_in_one_root(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", robots="", robots_txt=b"static\n")
        with routed(root):
            assert Client().get("/robots.txt").content.startswith(b"User-agent: *")

    def test_the_first_root_wins_across_roots(self, tmp_path) -> None:
        first = write_tree(tmp_path / "a", robots_txt=b"first\n")
        second = write_tree(tmp_path / "b", robots="")
        with routed(first, second):
            assert Client().get("/robots.txt").content == b"first\n"


class TestClosedSite:
    """A site closed to search lets crawlers in and lists nothing."""

    def test_the_sitemap_answers_404_while_the_site_is_closed(self, tmp_path) -> None:
        root = write_tree(tmp_path / "pages", pages=("about",), sitemap="")
        with routed(root):
            assert Client().get("/sitemap.xml").status_code == 200
            with override_next_settings(SITE={"INDEXABLE": False}):
                assert Client().get("/sitemap.xml").status_code == 404
                assert Client().get("/sitemap-pages.xml").status_code == 404
            assert Client().get("/sitemap.xml").status_code == 200

    @pytest.mark.parametrize(
        "sources",
        [{"robots": ROBOTS}, {"robots_txt": b"User-agent: *\nDisallow: /\n"}],
        ids=["robots-py", "robots-txt"],
    )
    def test_robots_answers_the_fixed_open_document(self, tmp_path, sources) -> None:
        root = write_tree(tmp_path / "pages", sitemap="", **sources)
        with routed(root, **CLOSED_SITE):
            response = Client().get("/robots.txt")
        assert response.content == b"User-agent: *\nDisallow:\n"
        assert response["X-Robots-Tag"] == "noindex, nofollow"

    @pytest.mark.parametrize("path", ["/sitemap.xml", "/robots.txt"])
    def test_every_route_and_its_404_carry_the_closed_header(
        self, tmp_path, path
    ) -> None:
        root = write_tree(tmp_path / "pages", sitemap="", robots="")
        with routed(root, **CLOSED_SITE):
            head = Client().head(path)
            post = Client().post(path)
        assert head["X-Robots-Tag"] == post["X-Robots-Tag"] == "noindex, nofollow"
        assert post.status_code == 405

    @override_settings(DEBUG=True)
    def test_debug_under_auto_previews_both_routes_under_noindex(
        self, tmp_path
    ) -> None:
        robots = b"User-agent: *\nDisallow: /x/\n"
        root = write_tree(
            tmp_path / "pages", pages=("about",), sitemap="", robots_txt=robots
        )
        with routed(root):
            sitemap = Client().get("/sitemap.xml")
            served = Client().get("/robots.txt")
        assert sitemap.status_code == served.status_code == 200
        assert _locs(sitemap) == ["http://testserver/about/"]
        assert served.content == robots
        assert sitemap["X-Robots-Tag"] == served["X-Robots-Tag"] == "noindex, nofollow"

    @override_settings(DEBUG=True)
    def test_debug_keeps_an_explicitly_closed_site_closed(self, tmp_path) -> None:
        robots = b"User-agent: *\nDisallow: /x/\n"
        root = write_tree(tmp_path / "pages", sitemap="", robots_txt=robots)
        with routed(root, **CLOSED_SITE):
            assert Client().get("/sitemap.xml").status_code == 404
            served = Client().get("/robots.txt")
        assert served.content == b"User-agent: *\nDisallow:\n"


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
    def test_a_prefixed_default_language_lists_every_prefixed_url(
        self, tmp_path
    ) -> None:
        root = write_tree(
            tmp_path / "pages",
            pages=("", "about"),
            sitemap="i18n = True\nalternates = True\nx_default = True\n",
        )
        with routed(root, urlconf=SEO_I18N_PREFIXED_URLCONF, **WITH_BASE):
            response = Client().get("/sitemap.xml")
            copies = [
                Client().get(path) for path in ("/en/sitemap.xml", "/de/robots.txt")
            ]
        urls = parse_sitemap(response)
        assert [url.loc for url in urls] == [
            f"{BASE}/en/",
            f"{BASE}/de/",
            f"{BASE}/en/about/",
            f"{BASE}/de/about/",
        ]
        assert urls[3].alternates == (
            ("en", f"{BASE}/en/about/"),
            ("de", f"{BASE}/de/about/"),
            ("x-default", f"{BASE}/en/about/"),
        )
        assert [copy.status_code for copy in copies] == [404, 404]

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
