import re

import pytest
from e2e_support.browser import (
    PageProbe,
    applied_count,
    wait_for_apply,
    wait_for_runtime,
)
from playwright.sync_api import Page, expect


pytestmark = pytest.mark.e2e

SENTINEL_BELOW_THE_FOLD = {"width": 1280, "height": 360}

RESULTS = "[data-next-zone='catalog-results'] li"
COUNT = "[data-next-zone='catalog-count']"
SENTINEL = "#results-sentinel"
SHOW_MORE = "a#results-sentinel"
END_MARKER = "#results-sentinel[data-catalog-end]"
SEARCH = "#filter-q"
HELP = "[data-filter-help]"
SEARCH_CHIP = "[data-active-filter='q']"
FIRST_CARD = "[data-next-zone='catalog-results'] li:first-child [data-product-card]"

TAG_FIRST_ROW = (
    "() => { document.querySelector"
    "(\"[data-next-zone='catalog-results'] li\").keptAcrossAppends = true; }"
)
READ_FIRST_ROW_TAG = (
    "() => document.querySelector"
    "(\"[data-next-zone='catalog-results'] li\").keptAcrossAppends"
)


def open_listing(page: Page, base_url: str) -> None:
    page.set_viewport_size(SENTINEL_BELOW_THE_FOLD)
    page.goto(f"{base_url}/catalog/")
    wait_for_runtime(page)


def test_runtime_boots_and_serves_its_bundle(
    page: Page, base_url: str, demo_data: None, next_probe: PageProbe
) -> None:
    open_listing(page, base_url)

    bundle = [
        response
        for response in next_probe.responses
        if response.url.endswith("/static/next/next.min.js")
    ]
    assert [response.status for response in bundle] == [200]
    assert page.evaluate("() => typeof window.Next") == "function"
    expect(page.locator(RESULTS)).to_have_count(6)
    assert next_probe.partial_requests() == []


def test_typing_narrows_the_listing_without_pressing_enter(
    page: Page, base_url: str, demo_data: None
) -> None:
    open_listing(page, base_url)
    expect(page.locator(COUNT)).to_have_text("25 products")
    expect(page.locator(SEARCH_CHIP)).to_have_count(0)

    seen = applied_count(page)
    page.locator(SEARCH).press_sequentially("iph", delay=30)
    wait_for_apply(page, seen)

    expect(page.locator(RESULTS)).to_have_count(1)
    expect(page.locator(FIRST_CARD)).to_contain_text("iPhone 15")
    expect(page.locator(COUNT)).to_have_text("1 products")
    expect(page.locator(SEARCH_CHIP)).to_contain_text("Search iph")


def test_a_burst_of_keystrokes_collapses_into_one_request(
    page: Page, base_url: str, demo_data: None, next_probe: PageProbe
) -> None:
    open_listing(page, base_url)

    seen = applied_count(page)
    page.locator(SEARCH).press_sequentially("Item 1", delay=20)
    wait_for_apply(page, seen)

    expect(page.locator(SEARCH)).to_have_value("Item 1")
    expect(page.locator(COUNT)).to_have_text("10 products")
    requests = next_probe.partial_requests()
    assert len(requests) == 1
    assert requests[0].request.method == "GET"
    assert "q=Item+1" in requests[0].url


def test_the_live_filter_syncs_the_address_without_a_history_entry(
    page: Page, base_url: str, demo_data: None
) -> None:
    page.set_viewport_size(SENTINEL_BELOW_THE_FOLD)
    page.goto(f"{base_url}/")
    page.goto(f"{base_url}/catalog/")
    wait_for_runtime(page)

    seen = applied_count(page)
    page.locator(SEARCH).press_sequentially("iph", delay=30)
    wait_for_apply(page, seen)
    expect(page).to_have_url(re.compile(r"\?q=iph&"))

    page.go_back()
    expect(page).to_have_url(f"{base_url}/")


def test_changing_the_sort_select_refetches_the_listing(
    page: Page, base_url: str, demo_data: None, next_probe: PageProbe
) -> None:
    open_listing(page, base_url)
    expect(page.locator(FIRST_CARD)).to_contain_text("iPhone 15")

    seen = applied_count(page)
    page.select_option("select[name='sort']", "price_asc")
    wait_for_apply(page, seen)

    expect(page.locator(FIRST_CARD)).to_contain_text("Item 00")
    expect(page).to_have_url(re.compile(r"sort=price_asc"))
    assert len(next_probe.partial_requests()) == 1


def test_a_preset_pushes_the_url_and_back_returns_to_the_listing(
    page: Page, base_url: str, demo_data: None
) -> None:
    open_listing(page, base_url)

    seen = applied_count(page)
    page.get_by_role("button", name="Cheapest first").click()
    wait_for_apply(page, seen)

    expect(page.locator(FIRST_CARD)).to_contain_text("Item 00")
    expect(page).to_have_url(f"{base_url}/catalog/?sort=price_asc")

    page.go_back()
    expect(page).to_have_url(f"{base_url}/catalog/")


def test_revealing_the_sentinel_appends_the_next_page(
    page: Page, base_url: str, demo_data: None, next_probe: PageProbe
) -> None:
    open_listing(page, base_url)
    expect(page.locator(RESULTS)).to_have_count(6)
    page.evaluate(TAG_FIRST_ROW)

    page.locator(SENTINEL).scroll_into_view_if_needed()
    expect(page.locator(RESULTS)).to_have_count(12)

    assert page.evaluate(READ_FIRST_ROW_TAG) is True
    assert len(next_probe.partial_requests()) == 1
    expect(page.locator(FIRST_CARD)).to_contain_text("iPhone 15")
    expect(page.locator(SENTINEL)).to_have_count(1)
    assert page.locator(SENTINEL).get_attribute("data-next-merge") == "append"
    assert "page=3" in page.locator(SENTINEL).get_attribute("href")


def test_the_sentinel_walks_the_pages_once_and_then_retires(
    page: Page, base_url: str, demo_data: None, next_probe: PageProbe
) -> None:
    open_listing(page, base_url)

    for total in (12, 18, 24, 25):
        page.locator(SENTINEL).scroll_into_view_if_needed()
        expect(page.locator(RESULTS)).to_have_count(total)

    fetched = [
        response.url.split("/catalog/")[1] for response in next_probe.partial_requests()
    ]
    assert fetched == ["?page=2", "?page=3", "?page=4", "?page=5"]

    expect(page.locator(SHOW_MORE)).to_have_count(0)
    expect(page.locator(END_MARKER)).to_have_count(1)
    expect(page.locator(END_MARKER)).to_have_text("End of results.")

    page.locator(SENTINEL).scroll_into_view_if_needed()
    page.locator(SENTINEL).click()

    seen = applied_count(page)
    page.locator(SEARCH).press_sequentially("iph", delay=30)
    wait_for_apply(page, seen)

    settled = [
        response.url.split("/catalog/")[1] for response in next_probe.partial_requests()
    ]
    assert settled[:4] == fetched
    assert len(settled) == 5
    assert "q=iph" in settled[4]
    expect(page.locator(RESULTS)).to_have_count(1)
    expect(page.locator(END_MARKER)).to_have_count(1)


def test_the_slash_hotkey_focuses_the_search_field(
    page: Page, base_url: str, demo_data: None
) -> None:
    open_listing(page, base_url)
    page.locator("h1").click()
    expect(page.locator(SEARCH)).not_to_be_focused()

    page.keyboard.press("/")

    expect(page.locator(SEARCH)).to_be_focused()
    expect(page.locator(SEARCH)).to_have_value("")

    page.keyboard.press("/")

    expect(page.locator(SEARCH)).to_have_value("/")


def test_the_minlength_hint_follows_the_field_state(
    page: Page, base_url: str, demo_data: None
) -> None:
    open_listing(page, base_url)
    expect(page.locator(HELP)).to_have_text("Type at least 3 characters.")

    page.locator(SEARCH).fill("ip")

    expect(page.locator(HELP)).to_have_text(
        "Need 1 more — at least 3 characters in total."
    )
    assert page.locator(SEARCH).evaluate("field => field.validity.valid") is False

    seen = applied_count(page)
    page.locator(SEARCH).fill("iph")
    wait_for_apply(page, seen)

    expect(page.locator(HELP)).to_have_text(
        "Looks good — typing filters the catalog live."
    )
    assert page.locator(SEARCH).evaluate("field => field.validity.valid") is True
