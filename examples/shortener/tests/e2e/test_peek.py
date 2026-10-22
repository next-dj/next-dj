import pytest
from e2e_support.browser import (
    applied_count,
    assert_same_document,
    mark_document,
    wait_for_apply,
    wait_for_runtime,
)
from playwright.sync_api import Locator, Page, expect
from shortener.models import Link


pytestmark = pytest.mark.e2e

DIALOG = "dialog[data-next-dialog]"
MODAL = "dialog[data-next-dialog]:modal"
ADMIN_TITLE = "Admin panel · next.dj shortener"
LINK_PATH = "/admin/links/peeked/"


def open_admin(page: Page, base_url: str) -> None:
    Link.objects.create(slug="peeked", url="https://example.com/p", clicks=7)
    page.goto(f"{base_url}/admin/")
    wait_for_runtime(page)
    mark_document(page)


def open_peek(page: Page, base_url: str) -> Locator:
    seen = applied_count(page)
    page.get_by_role("link", name="View details").click()
    dialog = page.locator(DIALOG)
    expect(dialog).to_have_count(1)
    wait_for_apply(page, seen)
    expect(dialog.locator("[data-link-peek]")).to_be_visible()
    expect(page.locator(MODAL)).to_have_count(1)
    expect(page).to_have_url(f"{base_url}{LINK_PATH}")
    return dialog


def test_view_details_opens_a_quick_look_over_the_list_and_back_closes_it(
    page: Page, base_url: str
) -> None:
    open_admin(page, base_url)
    expect(page).to_have_title(ADMIN_TITLE)

    dialog = open_peek(page, base_url)

    expect(page).to_have_title("Quick look · peeked · next.dj shortener")
    expect(dialog).to_contain_text("https://example.com/p")
    expect(dialog).to_contain_text("Persisted clicks")

    page.go_back()

    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page).to_have_url(f"{base_url}/admin/")
    expect(page).to_have_title(ADMIN_TITLE)
    assert_same_document(page)


def test_escape_closes_the_quick_look_and_restores_the_title(
    page: Page, base_url: str
) -> None:
    open_admin(page, base_url)
    open_peek(page, base_url)

    page.keyboard.press("Escape")

    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page).to_have_url(f"{base_url}/admin/")
    expect(page).to_have_title(ADMIN_TITLE)


def test_reloading_the_link_url_renders_the_full_page(
    page: Page, base_url: str
) -> None:
    open_admin(page, base_url)
    open_peek(page, base_url)

    page.reload()
    wait_for_runtime(page)

    expect(page.locator(DIALOG)).to_have_count(0)
    expect(page.get_by_role("button", name="Reset cached clicks")).to_be_visible()


def test_the_quick_look_links_to_the_full_page(page: Page, base_url: str) -> None:
    open_admin(page, base_url)
    dialog = open_peek(page, base_url)

    dialog.get_by_role("link", name="Open the link page").click()

    expect(page.get_by_role("button", name="Reset cached clicks")).to_be_visible()
    expect(page.locator(DIALOG)).to_have_count(0)
