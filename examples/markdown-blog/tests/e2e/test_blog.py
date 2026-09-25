import pathlib

import blog
import pytest
from blog.markdown_template import read_post_body, reading_minutes
from e2e_support.browser import PageProbe, wait_for_runtime
from playwright.sync_api import Page, expect


pytestmark = pytest.mark.e2e

POSTS_DIR = pathlib.Path(blog.__file__).parent / "screens" / "posts"

SHARE = "[data-share]"
META_BAR = "article header div"
BODY = "article > div"


def expected_minutes(slug: str) -> int:
    return reading_minutes(read_post_body(POSTS_DIR / slug / "template.md"))


def open_post(page: Page, base_url: str, slug: str) -> None:
    page.context.grant_permissions(
        ["clipboard-read", "clipboard-write"], origin=base_url
    )
    page.goto(f"{base_url}/posts/{slug}/")
    wait_for_runtime(page)


def test_runtime_boots_and_serves_its_bundle(
    page: Page, base_url: str, next_probe: PageProbe
) -> None:
    page.goto(base_url)
    wait_for_runtime(page)

    bundle = [
        response
        for response in next_probe.responses
        if response.url.partition("?")[0].endswith("/static/next/next.min.js")
    ]
    assert [response.status for response in bundle] == [200]
    assert page.evaluate("() => typeof window.Next") == "function"
    expect(page.get_by_role("link", name="Welcome to the blog")).to_be_visible()


def test_a_post_renders_its_markdown_body_and_reading_time(
    page: Page, base_url: str
) -> None:
    open_post(page, base_url, "hello-world")

    expect(page.locator("article header h1")).to_have_text("Hello, world")
    expect(page.locator(f"{BODY} h1")).to_have_text("Hello, world")
    code = page.locator(f"{BODY} pre code")
    expect(code).to_have_count(1)
    expect(code).to_contain_text('print("hello, world")')
    expect(page.locator(META_BAR)).to_contain_text(
        f"~ {expected_minutes('hello-world')} min read"
    )
    expect(page.locator(META_BAR)).to_contain_text("/posts/hello-world/")


def test_the_share_button_copies_the_title_and_the_current_url(
    page: Page, base_url: str
) -> None:
    open_post(page, base_url, "welcome")
    button = page.locator(SHARE)
    expect(button).to_have_text("📎 Share")

    button.click()

    expect(button).to_have_text("✓ Copied")
    assert page.evaluate("() => navigator.clipboard.readText()") == (
        f"Welcome to the blog — {base_url}/posts/welcome/"
    )
    assert page.evaluate("() => window.Next.context.post.title") == (
        "Welcome to the blog"
    )


def test_the_index_lists_the_posts_without_a_share_button(
    page: Page, base_url: str
) -> None:
    page.goto(base_url)
    wait_for_runtime(page)

    expect(page.locator(SHARE)).to_have_count(0)
    expect(page.locator("main li")).to_have_count(2)
    assert page.evaluate("() => window.Next.context.post") is None

    page.get_by_role("link", name="Welcome to the blog").click()

    expect(page).to_have_url(f"{base_url}/posts/welcome/")
    expect(page.locator(SHARE)).to_have_count(1)


def test_each_page_titles_the_tab_from_its_own_metadata(
    page: Page, base_url: str
) -> None:
    page.goto(base_url)
    wait_for_runtime(page)
    expect(page).to_have_title("Latest posts · next.dj blog")

    page.get_by_role("link", name="Welcome to the blog").click()

    expect(page).to_have_url(f"{base_url}/posts/welcome/")
    expect(page).to_have_title("Welcome to the blog · next.dj blog")


def test_a_post_lists_an_alternate_link_per_language(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/posts/welcome/")
    wait_for_runtime(page)

    alternates = page.locator("link[rel='alternate'][hreflang]")
    expect(alternates).to_have_count(3)
    expect(page.locator("link[rel='alternate'][hreflang='en']")).to_have_attribute(
        "href", "https://blog.example/posts/welcome/"
    )
    expect(page.locator("link[rel='alternate'][hreflang='es']")).to_have_attribute(
        "href", "https://blog.example/es/posts/welcome/"
    )
    expect(
        page.locator("link[rel='alternate'][hreflang='x-default']")
    ).to_have_attribute("href", "https://blog.example/posts/welcome/")


def test_the_sitemap_lists_each_post_under_both_language_prefixes(
    page: Page, base_url: str
) -> None:
    response = page.request.get(f"{base_url}/sitemap.xml")

    assert response.status == 200
    assert response.headers["content-type"] == "application/xml"
    body = response.text()
    assert "<loc>https://blog.example/posts/welcome/</loc>" in body
    assert "<loc>https://blog.example/es/posts/welcome/</loc>" in body
    assert 'hreflang="x-default" href="https://blog.example/posts/welcome/"' in body


def test_robots_allows_every_crawler_and_names_the_sitemap(
    page: Page, base_url: str
) -> None:
    response = page.request.get(f"{base_url}/robots.txt")

    assert response.status == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.text() == (
        "User-agent: *\nAllow: /\n\nSitemap: https://blog.example/sitemap.xml\n"
    )
