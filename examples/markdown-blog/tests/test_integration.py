import re
from dataclasses import dataclass
from pathlib import Path

import pytest
from blog.markdown_template import read_post_body, reading_minutes
from django.urls import reverse

from next.testing import (
    assert_has_class,
    assert_missing_class,
    find_anchor,
    init_payload,
)


@dataclass(frozen=True, slots=True)
class NavCase:
    """One top-nav row (visited page, highlighted anchor, dim anchor)."""

    id: str
    path: str
    active_href: str
    active_text: str
    inactive_href: str
    inactive_text: str


NAV_CASES: tuple[NavCase, ...] = (
    NavCase("on-home", "/", "/", "Home", "/about/", "About"),
    NavCase("on-about", "/about/", "/about/", "About", "/", "Home"),
)


WELCOME_POST = Path("blog/screens/posts/welcome/template.md")
ROBOTS_FILE = Path("blog/screens/robots.txt")

STATIC_ROUTES = ("/", "/about/", "/posts/hello-world/", "/posts/welcome/")
URL_BLOCK_RE = re.compile(r"<url>(.*?)</url>")
LOC_RE = re.compile(r"<loc>([^<]+)</loc>")
ALTERNATE_RE = re.compile(
    r'<xhtml:link rel="alternate" hreflang="([^"]+)" href="([^"]+)"/>'
)


def _url_blocks(body: str) -> dict[str, str]:
    """Map every `<loc>` of a sitemap onto the `<url>` block carrying it."""
    return {
        LOC_RE.search(block).group(1): block for block in URL_BLOCK_RE.findall(body)
    }


class TestIndex:
    """The home page lists every post in alphabetical order."""

    def test_home_lists_both_posts(self, next_client) -> None:
        response = next_client.get("/")
        assert response.status_code == 200
        body = response.content.decode()
        assert "Latest posts" in body
        assert "Welcome to the blog" in body
        assert "Hello, world" in body
        assert "/posts/welcome/" in body
        assert "/posts/hello-world/" in body

    def test_home_shows_site_chrome_from_context_processor(self, next_client) -> None:
        response = next_client.get("/")
        body = response.content.decode()
        assert "Small posts, plain Markdown" in body
        assert "© " in body
        assert "you are at <code>/</code>" in body


class TestPost:
    """Each post renders through the nested posts layout."""

    def test_welcome_renders_body_and_meta(self, next_client) -> None:
        response = next_client.get("/posts/welcome/")
        assert response.status_code == 200
        body = response.content.decode()
        assert "Welcome to the blog" in body
        assert "<h2>Why Markdown?</h2>" in body
        assert "min read" in body
        assert "Back to posts" in body

    def test_hello_world_renders_fenced_code(self, next_client) -> None:
        response = next_client.get("/posts/hello-world/")
        assert response.status_code == 200
        body = response.content.decode()
        assert "Hello, world" in body
        assert 'class="language-python"' in body
        assert "print(" in body

    def test_reading_time_matches_the_post_body(self, next_client) -> None:
        expected = reading_minutes(read_post_body(WELCOME_POST))
        body = next_client.get("/posts/welcome/").content.decode()
        assert f"~ {expected} min read" in body


class TestShareButton:
    """The share button sits inside the nested layout.

    It reads `window.Next.context.post` to power the click handler.
    """

    def test_share_button_renders_on_post_page(self, next_client) -> None:
        response = next_client.get("/posts/welcome/")
        body = response.content.decode()
        assert "data-share" in body
        assert "Share" in body

    def test_share_button_not_on_home(self, next_client) -> None:
        response = next_client.get("/")
        assert "data-share" not in response.content.decode()

    def test_serialized_post_context_is_injected_for_js(self, next_client) -> None:
        response = next_client.get("/posts/welcome/")
        payload = init_payload(response.content.decode())
        assert payload["post"]["title"] == "Welcome to the blog"
        assert payload["post"]["slug"] == "welcome"


class TestActiveNav:
    """The shared nav_link component toggles active state via request.resolver_match."""

    @pytest.mark.parametrize("case", NAV_CASES, ids=lambda case: case.id)
    def test_nav_highlights_the_visited_page(self, next_client, case: NavCase) -> None:
        body = next_client.get(case.path).content.decode()
        assert_has_class(
            find_anchor(body, href=case.active_href, text=case.active_text),
            "font-semibold",
        )
        assert_missing_class(
            find_anchor(body, href=case.inactive_href, text=case.inactive_text),
            "font-semibold",
        )


class TestPageMetadata:
    """`{% metadata %}` in `page_head` renders the folded chain of every page."""

    def test_index_folds_the_root_dict_into_the_site_defaults(
        self, next_client
    ) -> None:
        body = next_client.get("/").content.decode()
        assert "<title>Latest posts · next.dj blog</title>" in body
        assert '<link rel="canonical" href="https://blog.example/">' in body
        assert (
            '<meta property="og:title" content="Latest posts · next.dj blog">' in body
        )
        assert '<meta property="og:type" content="website">' in body
        assert (
            '<meta name="description" content="Small posts, plain Markdown, '
            'zero front-end build.">'
        ) in body

    def test_about_declares_its_own_title_and_description(self, next_client) -> None:
        body = next_client.get("/about/").content.decode()
        assert "<title>About · next.dj blog</title>" in body
        assert (
            '<meta name="description" content="A demo blog built on next-dj, '
            'one Markdown file per post.">'
        ) in body
        assert '<link rel="canonical" href="https://blog.example/about/">' in body

    def test_post_reads_its_title_and_excerpt_from_the_markdown(
        self, next_client
    ) -> None:
        body = next_client.get("/posts/welcome/").content.decode()
        assert "<title>Welcome to the blog · next.dj blog</title>" in body
        assert (
            '<meta name="description" content="This is a demo blog built on next-dj.'
        ) in body
        assert (
            '<link rel="canonical" href="https://blog.example/posts/welcome/">'
        ) in body

    def test_every_page_lists_both_languages_as_alternates(self, next_client) -> None:
        body = next_client.get("/posts/hello-world/").content.decode()
        assert (
            '<link rel="alternate" hreflang="en" '
            'href="https://blog.example/posts/hello-world/">'
        ) in body
        assert (
            '<link rel="alternate" hreflang="es" '
            'href="https://blog.example/es/posts/hello-world/">'
        ) in body
        assert (
            '<link rel="alternate" hreflang="x-default" '
            'href="https://blog.example/posts/hello-world/">'
        ) in body

    def test_the_spanish_prefix_switches_the_locale_of_the_same_post(
        self, next_client
    ) -> None:
        body = next_client.get("/es/posts/welcome/").content.decode()
        assert '<html lang="es">' in body
        assert '<meta property="og:locale" content="es">' in body
        assert (
            '<link rel="canonical" href="https://blog.example/es/posts/welcome/">'
        ) in body
        assert "<title>Welcome to the blog · next.dj blog</title>" in body


class TestSitemapAndRobots:
    """`sitemap.py` lists the page tree, the static `robots.txt` is served as is."""

    def test_sitemap_lists_every_static_route_in_both_languages(
        self, next_client
    ) -> None:
        response = next_client.get("/sitemap.xml")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/xml"
        english = {f"https://blog.example{path}" for path in STATIC_ROUTES}
        spanish = {f"https://blog.example/es{path}" for path in STATIC_ROUTES}
        assert set(_url_blocks(response.content.decode())) == english | spanish

    def test_each_entry_links_its_alternates_and_x_default(self, next_client) -> None:
        blocks = _url_blocks(next_client.get("/sitemap.xml").content.decode())
        alternates = dict(
            ALTERNATE_RE.findall(blocks["https://blog.example/es/posts/welcome/"])
        )
        assert alternates == {
            "en": "https://blog.example/posts/welcome/",
            "es": "https://blog.example/es/posts/welcome/",
            "x-default": "https://blog.example/posts/welcome/",
        }

    def test_the_module_changefreq_applies_to_every_entry(self, next_client) -> None:
        blocks = _url_blocks(next_client.get("/sitemap.xml").content.decode())
        assert all(
            "<changefreq>weekly</changefreq>" in block for block in blocks.values()
        )

    def test_the_section_route_serves_the_same_document(self, next_client) -> None:
        whole = next_client.get("/sitemap.xml")
        section = next_client.get("/sitemap-blog.xml")
        assert section.status_code == 200
        assert section.content == whole.content

    def test_the_seo_routes_sit_at_the_host_root_outside_the_prefix(self) -> None:
        assert reverse("next_seo:sitemap") == "/sitemap.xml"
        assert reverse("next_seo:robots") == "/robots.txt"

    def test_robots_is_the_static_file_byte_for_byte(self, next_client) -> None:
        response = next_client.get("/robots.txt")
        assert response.status_code == 200
        assert response["Content-Type"] == "text/plain; charset=utf-8"
        assert response.content == ROBOTS_FILE.read_bytes()
        assert response.content.decode() == (
            "User-agent: *\nAllow: /\n\nSitemap: https://blog.example/sitemap.xml\n"
        )
