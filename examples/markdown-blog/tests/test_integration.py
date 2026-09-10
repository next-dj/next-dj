from dataclasses import dataclass
from pathlib import Path

import pytest
from blog.markdown_template import read_post_body, reading_minutes

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
