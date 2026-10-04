from dataclasses import dataclass

import pytest
from blog.posts import load_post
from django.contrib.staticfiles import finders

from next.testing import (
    assert_has_class,
    assert_metadata,
    assert_missing_class,
    find_anchor,
    init_payload,
    parse_sitemap,
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

ROUTES = ("/", "/about/", "/posts/hello-world/", "/posts/welcome/")
SOCIAL_IMAGE = "https://blog.example/static/blog/opengraph-image.png"
WELCOME_DESCRIPTION = (
    "A tour of the blog, where every post is a Markdown file and one dynamic route "
    "serves them all."
)


class TestIndex:
    """The home page lists every post, the newest first."""

    def test_home_lists_both_posts_newest_first(self, next_client) -> None:
        response = next_client.get("/")
        assert response.status_code == 200
        body = response.content.decode()
        hello = find_anchor(body, href="/posts/hello-world/", text="Hello, world")
        welcome = find_anchor(body, href="/posts/welcome/", text="Welcome to the blog")
        assert body.index(hello) < body.index(welcome)
        assert WELCOME_DESCRIPTION in body

    def test_home_shows_site_chrome_from_context_processor(self, next_client) -> None:
        response = next_client.get("/")
        body = response.content.decode()
        assert "Small posts, plain Markdown" in body
        assert "© " in body
        assert "you are at <code>/</code>" in body


class TestPost:
    """One dynamic route renders every Markdown file through the posts layout."""

    def test_welcome_renders_body_and_meta(self, next_client) -> None:
        response = next_client.get("/posts/welcome/")
        assert response.status_code == 200
        body = response.content.decode()
        assert "<h2>Why Markdown?</h2>" in body
        assert '<time datetime="2026-01-12">12 Jan 2026</time>' in body

    def test_hello_world_renders_fenced_code(self, next_client) -> None:
        body = next_client.get("/posts/hello-world/").content.decode()
        assert 'class="language-python"' in body
        assert "print(" in body

    def test_reading_time_matches_the_post_body(self, next_client) -> None:
        expected = load_post("welcome").reading_minutes
        body = next_client.get("/posts/welcome/").content.decode()
        assert f"~ {expected} min read" in body

    def test_a_slug_without_a_file_is_not_found(self, next_client) -> None:
        assert next_client.get("/posts/missing/").status_code == 404


class TestBreadcrumbs:
    """`{% breadcrumbs as crumbs %}` walks the page tree from the index down."""

    def test_the_trail_links_home_and_marks_the_post(self, next_client) -> None:
        body = next_client.get("/posts/welcome/").content.decode()
        assert find_anchor(body, href="/", text="Home")
        assert (
            '<span aria-current="page" class="font-medium text-foreground">'
            "Welcome to the blog</span>"
        ) in body


class TestShareButton:
    """The share button sits inside the nested layout.

    It reads `window.Next.context.post` to power the click handler.
    """

    def test_share_button_renders_on_post_page(self, next_client) -> None:
        body = next_client.get("/posts/welcome/").content.decode()
        assert "data-share" in body

    def test_share_button_not_on_home(self, next_client) -> None:
        response = next_client.get("/")
        assert "data-share" not in response.content.decode()

    def test_serialized_post_context_carries_only_what_the_button_reads(
        self, next_client
    ) -> None:
        response = next_client.get("/posts/welcome/")
        assert init_payload(response.content.decode())["post"] == {
            "slug": "welcome",
            "title": "Welcome to the blog",
        }


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
        assert_metadata(
            next_client.get("/"),
            title="Latest posts · next.dj blog",
            canonical="https://blog.example/",
            description="Small posts, plain Markdown, zero front-end build.",
            og={"title": "Latest posts · next.dj blog", "type": "website"},
            robots=None,
        )

    def test_about_declares_its_own_title_and_description(self, next_client) -> None:
        assert_metadata(
            next_client.get("/about/"),
            title="About · next.dj blog",
            description="A demo blog built on next-dj, one Markdown file per post.",
            canonical="https://blog.example/about/",
        )

    def test_a_post_reads_its_head_from_the_front_matter(self, next_client) -> None:
        assert_metadata(
            next_client.get("/posts/welcome/"),
            title="Welcome to the blog · next.dj blog",
            description=WELCOME_DESCRIPTION,
            keywords="next.dj, markdown, django",
            canonical="https://blog.example/posts/welcome/",
            og={"type": "article", "image": SOCIAL_IMAGE},
        )

    def test_the_keywords_render_as_one_tag(self, next_client) -> None:
        body = next_client.get("/posts/welcome/").content.decode()
        assert body.count('<meta name="keywords"') == 1

    def test_a_post_without_a_description_falls_back_to_its_first_paragraph(
        self, next_client
    ) -> None:
        assert_metadata(
            next_client.get("/posts/hello-world/"),
            description="Short one. Just enough to prove the render pipeline works.",
        )

    def test_a_post_publishes_its_article_dates_and_tags(self, next_client) -> None:
        body = next_client.get("/posts/welcome/").content.decode()
        for tag in (
            '<meta property="article:published_time" content="2026-01-12">',
            '<meta property="article:modified_time" content="2026-03-02">',
            '<meta property="article:author" content="Ada Lovelace">',
            '<meta property="article:tag" content="markdown">',
        ):
            assert tag in body

    def test_a_post_is_a_blog_posting_inside_its_breadcrumb_trail(
        self, next_client
    ) -> None:
        assert_metadata(
            next_client.get("/posts/welcome/"),
            jsonld=[
                {
                    "@type": "BlogPosting",
                    "headline": "Welcome to the blog",
                    "datePublished": "2026-01-12",
                    "image": [SOCIAL_IMAGE],
                    "dateModified": "2026-03-02",
                    "author": [{"@type": "Person", "name": "Ada Lovelace"}],
                },
                {
                    "@type": "BreadcrumbList",
                    "@id": "https://blog.example/posts/welcome/#breadcrumb",
                    "itemListElement": [
                        {
                            "@type": "ListItem",
                            "name": "Home",
                            "position": 1,
                            "item": "https://blog.example/",
                        },
                        {
                            "@type": "ListItem",
                            "name": "Welcome to the blog",
                            "position": 2,
                            "item": "https://blog.example/posts/welcome/",
                        },
                    ],
                },
            ],
        )

    def test_the_social_image_is_a_static_file_with_its_size(self, next_client) -> None:
        assert_metadata(
            next_client.get("/about/"),
            og={
                "image": SOCIAL_IMAGE,
                "image:type": "image/png",
                "image:width": "1200",
                "image:height": "630",
                "image:alt": "A white band with an indigo mark on a slate background",
            },
        )
        assert finders.find("blog/opengraph-image.png")

    def test_every_page_advertises_the_feed(self, next_client) -> None:
        body = next_client.get("/about/").content.decode()
        assert (
            '<link rel="alternate" type="application/rss+xml" title="next.dj blog" '
            'href="https://blog.example/feed.xml">'
        ) in body

    def test_the_tab_icon_is_a_static_file(self, next_client) -> None:
        body = next_client.get("/").content.decode()
        assert (
            '<link rel="icon" href="https://blog.example/static/blog/icon.svg" '
            'type="image/svg+xml">'
        ) in body
        assert finders.find("blog/icon.svg")


class TestFeed:
    """`/feed.xml` lists every post as RSS, absolute on the published origin."""

    def test_the_feed_lists_the_posts_newest_first(self, next_client) -> None:
        response = next_client.get("/feed.xml")
        body = response.content.decode()
        assert response.status_code == 200
        assert response["Content-Type"] == "application/rss+xml; charset=utf-8"
        assert '<atom:link href="https://blog.example/feed.xml" rel="self"/>' in body
        hello = body.index("<link>https://blog.example/posts/hello-world/</link>")
        welcome = body.index("<link>https://blog.example/posts/welcome/</link>")
        assert hello < welcome

    def test_an_entry_carries_its_date_and_keywords(self, next_client) -> None:
        body = next_client.get("/feed.xml").content.decode()
        assert "<pubDate>Mon, 12 Jan 2026 00:00:00 +0000</pubDate>" in body
        assert "<category>markdown</category>" in body
        assert f"<description>{WELCOME_DESCRIPTION}</description>" in body


class TestSitemapAndRobots:
    """`sitemap.py` lists the page tree, `robots.py` points crawlers at it."""

    def test_sitemap_lists_every_route(self, next_client) -> None:
        response = next_client.get("/sitemap.xml")
        assert response.status_code == 200
        assert response["Content-Type"] == "application/xml"
        assert {url.loc for url in parse_sitemap(response)} == {
            f"https://blog.example{path}" for path in ROUTES
        }

    def test_a_post_is_dated_by_its_last_edit(self, next_client) -> None:
        urls = {url.loc: url for url in parse_sitemap(next_client.get("/sitemap.xml"))}
        assert urls["https://blog.example/posts/welcome/"].lastmod == "2026-03-02"
        assert urls["https://blog.example/posts/hello-world/"].lastmod == "2026-01-19"
        assert urls["https://blog.example/about/"].lastmod is None

    def test_the_section_of_the_one_root_is_the_same_document(
        self, next_client
    ) -> None:
        section = next_client.get("/sitemap-blog.xml")
        assert section.status_code == 200
        assert section.content == next_client.get("/sitemap.xml").content

    def test_the_module_changefreq_applies_to_every_entry(self, next_client) -> None:
        body = next_client.get("/sitemap.xml").content.decode()
        assert body.count("<changefreq>weekly</changefreq>") == len(ROUTES)

    def test_robots_allows_everything_and_names_the_sitemap(self, next_client) -> None:
        response = next_client.get("/robots.txt")
        assert response.status_code == 200
        assert response["Content-Type"] == "text/plain; charset=utf-8"
        assert response.content.decode().splitlines() == [
            "User-agent: *",
            "Allow: /",
            "",
            "Sitemap: https://blog.example/sitemap.xml",
        ]
