import pytest
from e2e_support.browser import (
    applied_count,
    assert_same_document,
    mark_document,
    wait_for_apply,
    wait_for_runtime,
)
from playwright.sync_api import Page, expect


pytestmark = pytest.mark.e2e

DIALOG = "dialog[data-next-dialog]"
MODAL = "dialog[data-next-dialog]:modal"
LISTING = "/catalog/electronics/"
LISTING_TITLE = "Electronics · next.dj catalog"
PRODUCT = "/catalog/electronics/iphone-15/"


def open_listing(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}{LISTING}")
    wait_for_runtime(page)
    mark_document(page)


def open_peek(page: Page, base_url: str):
    seen = applied_count(page)
    page.locator(f"a[href='{PRODUCT}']").click()
    dialog = page.locator(DIALOG)
    expect(dialog).to_have_count(1)
    wait_for_apply(page, seen)
    expect(dialog.locator("[data-product-peek]")).to_be_visible()
    expect(page.locator(MODAL)).to_have_count(1)
    expect(page).to_have_url(f"{base_url}{PRODUCT}")
    return dialog


def test_a_card_opens_the_quick_view_over_the_listing_and_back_closes_it(
    page: Page, base_url: str, demo_data: None
) -> None:
    open_listing(page, base_url)
    expect(page).to_have_title(LISTING_TITLE)

    dialog = open_peek(page, base_url)

    expect(page).to_have_title("Quick view · iPhone 15 · next.dj catalog")
    expect(dialog).to_contain_text("Flagship handset used by routing tests.")

    page.go_back()

    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page).to_have_url(f"{base_url}{LISTING}")
    expect(page).to_have_title(LISTING_TITLE)
    assert_same_document(page)


def test_escape_closes_the_quick_view_and_restores_the_title(
    page: Page, base_url: str, demo_data: None
) -> None:
    open_listing(page, base_url)
    open_peek(page, base_url)

    page.keyboard.press("Escape")

    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page).to_have_url(f"{base_url}{LISTING}")
    expect(page).to_have_title(LISTING_TITLE)


def test_reloading_the_product_url_renders_the_full_page(
    page: Page, base_url: str, demo_data: None
) -> None:
    open_listing(page, base_url)
    open_peek(page, base_url)

    page.reload()
    wait_for_runtime(page)

    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page.get_by_role("heading", name="iPhone 15", level=1)).to_be_visible()
    expect(page).to_have_title("iPhone 15 · next.dj catalog")


def test_the_quick_view_links_to_the_full_page(
    page: Page, base_url: str, demo_data: None
) -> None:
    open_listing(page, base_url)
    dialog = open_peek(page, base_url)

    dialog.get_by_role("link", name="Open the product page").click()

    expect(page.get_by_role("heading", name="iPhone 15", level=1)).to_be_visible()
    expect(page.locator(DIALOG)).to_have_count(0)
