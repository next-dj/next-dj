import pytest
from e2e_support.browser import (
    PageProbe,
    applied_count,
    expect_no_partial_request,
    request_baseline,
    wait_for_apply,
    wait_for_runtime,
)
from playwright.sync_api import Locator, Page, expect
from wiki.models import Article


pytestmark = pytest.mark.e2e

RESULTS_ZONE = "[data-next-zone='search-results']"
QUERY_FIELD = "#id_q"
BODY_FIELD = "#id_body_md"
PREVIEW = "[data-markdown-preview] .markdown-body"
RESERVED_SLUG_ERROR = "This slug collides with a file route."

TAG_PREVIEW = (
    "() => { document.querySelector"
    '("[data-markdown-preview]").keptAcrossMorph = true; }'
)
READ_PREVIEW_TAG = (
    '() => document.querySelector("[data-markdown-preview]").keptAcrossMorph'
)


def seed_articles() -> None:
    Article.objects.create(
        slug="routing-internals",
        title="Routing internals",
        body_md="Deep dive on the URL pipeline.",
    )
    Article.objects.create(
        slug="lifecycle",
        title="Request lifecycle",
        body_md="Every middleware stage in order.",
    )


def results_group(page: Page, title: str) -> Locator:
    return (
        page.locator(RESULTS_ZONE)
        .locator("section")
        .filter(has=page.get_by_role("heading", name=title, exact=True))
    )


def open_create_form(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/articles/new/")
    wait_for_runtime(page)
    expect(page.locator(PREVIEW)).to_contain_text("Nothing to preview yet.")


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
    expect(page.get_by_role("link", name="Routing", exact=True)).to_be_visible()
    expect_no_partial_request(page, next_probe)


def test_typing_narrows_the_search_without_pressing_enter(
    page: Page, base_url: str
) -> None:
    seed_articles()
    page.goto(f"{base_url}/search/")
    wait_for_runtime(page)
    page.evaluate("() => { window.__stillHere = true; }")
    expect(page.locator(RESULTS_ZONE)).to_contain_text(
        "Type a query to search file docs and articles."
    )

    seen = applied_count(page)
    page.locator(QUERY_FIELD).press_sequentially("routing", delay=20)
    wait_for_apply(page, seen)

    file_hits = results_group(page, "File-backed results").locator("li")
    article_hits = results_group(page, "Article results").locator("li")
    expect(file_hits).to_have_count(1)
    expect(file_hits).to_have_text(["Routing"])
    expect(article_hits).to_have_count(1)
    expect(article_hits).to_have_text(["Routing internals"])
    assert page.evaluate("() => window.__stillHere") is True


def test_a_burst_of_keystrokes_collapses_into_one_zone_request(
    page: Page, base_url: str, next_probe: PageProbe
) -> None:
    seed_articles()
    page.goto(f"{base_url}/search/")
    wait_for_runtime(page)

    seen = applied_count(page)
    page.locator(QUERY_FIELD).press_sequentially("routing", delay=20)
    wait_for_apply(page, seen)

    expect(page.locator(QUERY_FIELD)).to_have_value("routing")
    requests = next_probe.partial_requests(zone="search-results")
    assert len(requests) == 1
    assert requests[0].request.method == "GET"
    assert "q=routing" in requests[0].url
    assert next_probe.partial_requests() == requests


def test_typing_markdown_repaints_the_preview_with_real_markup(
    page: Page, base_url: str, next_probe: PageProbe
) -> None:
    open_create_form(page, base_url)

    seen = request_baseline(page, next_probe)
    page.fill(BODY_FIELD, "# Title\n\nSome **bold** words.")

    strong = page.locator(f"{PREVIEW} strong")
    expect(page.locator(f"{PREVIEW} h1")).to_have_text("Title")
    expect(strong).to_have_text("bold")
    weight = strong.evaluate("node => window.getComputedStyle(node).fontWeight")
    assert int(weight) > 400
    expect(page.locator(PREVIEW)).not_to_contain_text("**")
    expect_no_partial_request(page, next_probe, seen)


def test_the_preview_neutralises_a_javascript_link(page: Page, base_url: str) -> None:
    open_create_form(page, base_url)

    page.fill(BODY_FIELD, "[x](javascript:alert(1))")

    link = page.locator(f"{PREVIEW} a")
    expect(link).to_have_text("x")
    expect(link).to_have_attribute("href", "#")
    assert link.evaluate("node => node.protocol") == "http:"


def test_publishing_an_article_opens_its_freshly_routed_url(
    page: Page, base_url: str
) -> None:
    open_create_form(page, base_url)

    page.fill("#id_slug", "hybrid-routing")
    page.fill("#id_title", "Hybrid routing")
    page.fill(BODY_FIELD, "Served by **two** stores.")
    page.get_by_role("button", name="Publish").click()

    expect(page).to_have_url(f"{base_url}/wiki/hybrid-routing/")
    expect(page.locator("article strong")).to_have_text("two")
    assert Article.objects.count() == 1

    page.goto(f"{base_url}/wiki/hybrid-routing/")

    expect(page.get_by_role("heading", name="Hybrid routing")).to_be_visible()
    expect(page.get_by_role("link", name="Edit this article")).to_have_attribute(
        "href", "/articles/edit/hybrid-routing/"
    )


def test_a_reserved_slug_reports_the_error_and_keeps_the_preview_live(
    page: Page, base_url: str
) -> None:
    open_create_form(page, base_url)
    page.fill("#id_slug", "docs")
    page.fill("#id_title", "Docs")
    page.fill(BODY_FIELD, "Some **bold** words.")
    expect(page.locator(f"{PREVIEW} strong")).to_have_text("bold")
    page.evaluate(TAG_PREVIEW)

    seen = applied_count(page)
    page.get_by_role("button", name="Publish").click()
    wait_for_apply(page, seen)

    expect(page.get_by_text(RESERVED_SLUG_ERROR)).to_be_visible()
    expect(page).to_have_url(f"{base_url}/articles/new/")
    expect(page.locator(f"{PREVIEW} strong")).to_have_text("bold")
    expect(page.locator(BODY_FIELD)).to_have_value("Some **bold** words.")
    assert page.evaluate(READ_PREVIEW_TAG) is True
    assert Article.objects.count() == 0

    page.fill(BODY_FIELD, "Now _italic_ instead.")

    expect(page.locator(f"{PREVIEW} em")).to_have_text("italic")
    expect(page.locator(f"{PREVIEW} strong")).to_have_count(0)
