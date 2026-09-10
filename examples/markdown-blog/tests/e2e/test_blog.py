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
        if response.url.endswith("/static/next/next.min.js")
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
