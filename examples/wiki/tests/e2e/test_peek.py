import pytest
from e2e_support.browser import (
    applied_count,
    assert_same_document,
    mark_document,
    wait_for_apply,
    wait_for_runtime,
)
from playwright.sync_api import Locator, Page, expect
from wiki.models import Article


pytestmark = pytest.mark.e2e

DIALOG = "dialog[data-next-dialog]"
MODAL = "dialog[data-next-dialog]:modal"
SEARCH = "/search/?q=routing"
SEARCH_TITLE = "Search · next.dj Wiki"
ARTICLE = "/wiki/routing-internals/"


def seed_article() -> None:
    Article.objects.create(
        slug="routing-internals",
        title="Routing internals",
        body_md="Deep dive on the **URL** pipeline.",
    )


def open_search(page: Page, base_url: str) -> None:
    seed_article()
    page.goto(f"{base_url}{SEARCH}")
    wait_for_runtime(page)
    mark_document(page)


def open_preview(page: Page, base_url: str) -> Locator:
    seen = applied_count(page)
    page.locator(f"a[href='{ARTICLE}']").click()
    dialog = page.locator(DIALOG)
    expect(dialog).to_have_count(1)
    wait_for_apply(page, seen)
    expect(dialog.locator("[data-article-peek]")).to_be_visible()
    expect(page.locator(MODAL)).to_have_count(1)
    expect(page).to_have_url(f"{base_url}{ARTICLE}")
    return dialog


def test_an_article_result_opens_a_preview_over_the_search_and_back_closes_it(
    page: Page, base_url: str
) -> None:
    open_search(page, base_url)
    expect(page).to_have_title(SEARCH_TITLE)

    dialog = open_preview(page, base_url)

    expect(page).to_have_title("Preview · Routing internals · next.dj Wiki")
    expect(dialog.locator("strong")).to_have_text("URL")

    page.go_back()

    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page).to_have_url(f"{base_url}{SEARCH}")
    expect(page).to_have_title(SEARCH_TITLE)
    assert_same_document(page)


def test_escape_closes_the_preview_and_restores_the_title(
    page: Page, base_url: str
) -> None:
    open_search(page, base_url)
    open_preview(page, base_url)

    page.keyboard.press("Escape")

    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page).to_have_url(f"{base_url}{SEARCH}")
    expect(page).to_have_title(SEARCH_TITLE)


def test_reloading_the_article_url_renders_the_full_article(
    page: Page, base_url: str
) -> None:
    open_search(page, base_url)
    open_preview(page, base_url)

    page.reload()
    wait_for_runtime(page)

    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page.get_by_role("link", name="Edit this article")).to_be_visible()


def test_the_preview_links_to_the_full_article(page: Page, base_url: str) -> None:
    open_search(page, base_url)
    dialog = open_preview(page, base_url)

    dialog.get_by_role("link", name="Open the article").click()

    expect(page.get_by_role("link", name="Edit this article")).to_be_visible()
    expect(page.locator(DIALOG)).to_have_count(0)


def test_a_file_result_navigates_without_a_modal(page: Page, base_url: str) -> None:
    open_search(page, base_url)

    page.get_by_role("link", name="Routing", exact=True).click()

    expect(page).to_have_url(f"{base_url}/docs/routing/")
    expect(page.locator(DIALOG)).to_have_count(0)
