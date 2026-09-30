import re

import pytest
from catalog.models import Lead
from e2e_support.browser import (
    PageProbe,
    applied_count,
    wait_for_apply,
    wait_for_runtime,
)
from playwright.sync_api import Page, Route, expect


pytestmark = pytest.mark.e2e

VENDORS = re.compile(
    r"^https://(?:www\.googletagmanager\.com|connect\.facebook\.net"
    r"|www\.youtube-nocookie\.com)/"
)
BANNER = "[data-consent-banner]"
VIDEO = "iframe[data-landing-video]"
VIDEO_PLACEHOLDER = "[data-landing-video-placeholder]"
THANKS = "[data-lead-thanks]"
SEARCH = "#filter-q"

SCRIPT_STATUS = "(name) => window.Next.scripts.status(name)"
GTAG_PAGE_VIEWS = (
    "() => (window.dataLayer ?? [])"
    ".filter((entry) => entry[0] === 'event' && entry[1] === 'page_view')"
    ".map((entry) => entry[2].page_location)"
)
PIXEL_PAGE_VIEWS = (
    "() => (window.fbq?.queue ?? [])"
    ".filter((call) => call[0] === 'track' && call[1] === 'PageView').length"
)


def stub_vendors(page: Page) -> None:
    def fulfill(route: Route) -> None:
        html = "youtube" in route.request.url
        route.fulfill(
            status=200,
            content_type="text/html" if html else "application/javascript",
            body="",
        )

    page.route(VENDORS, fulfill)


def open_page(page: Page, url: str) -> None:
    stub_vendors(page)
    page.goto(url)
    wait_for_runtime(page)
    page.evaluate("() => window.Next.ready('scripts')")


def wait_for_status(page: Page, name: str, status: str) -> None:
    page.wait_for_function(
        f"(name) => window.Next.scripts?.status(name) === {status!r}", arg=name
    )


def grant(page: Page, base_url: str, categories: str) -> None:
    page.context.add_cookies(
        [
            {
                "name": "next_consent",
                "value": f"1:{categories}:1700000000",
                "url": base_url,
            }
        ]
    )


def test_the_banner_holds_every_tag_until_the_visitor_accepts(
    page: Page, base_url: str, demo_data: None
) -> None:
    open_page(page, f"{base_url}/shop/electronics/")

    expect(page.locator(BANNER)).to_be_visible()
    wait_for_status(page, "google-analytics", "blocked")
    wait_for_status(page, "meta-pixel", "blocked")
    expect(page.locator(VIDEO_PLACEHOLDER)).to_be_visible()

    page.get_by_role("button", name="Accept all").click()

    expect(page.locator(BANNER)).to_be_hidden()
    wait_for_status(page, "google-analytics", "loaded")
    wait_for_status(page, "meta-pixel", "loaded")
    expect(page.locator(VIDEO)).to_have_count(1)
    expect(page.locator(VIDEO_PLACEHOLDER)).to_have_count(0)
    assert page.evaluate(GTAG_PAGE_VIEWS) == [f"{base_url}/shop/electronics/"]
    assert page.evaluate(PIXEL_PAGE_VIEWS) == 1


def test_analytics_only_leaves_the_pixel_and_the_video_blocked(
    page: Page, base_url: str, demo_data: None
) -> None:
    open_page(page, f"{base_url}/shop/electronics/")

    page.get_by_role("button", name="Analytics only").click()

    wait_for_status(page, "google-analytics", "loaded")
    assert page.evaluate(SCRIPT_STATUS, "meta-pixel") == "blocked"
    expect(page.locator(VIDEO_PLACEHOLDER)).to_be_visible()


def test_a_choice_keeps_the_banner_away_on_the_next_page(
    page: Page, base_url: str, demo_data: None
) -> None:
    open_page(page, f"{base_url}/shop/electronics/")
    page.get_by_role("button", name="Reject all").click()
    expect(page.locator(BANNER)).to_be_hidden()

    open_page(page, f"{base_url}/catalog/")

    wait_for_status(page, "google-analytics", "blocked")
    expect(page.locator(BANNER)).to_be_hidden()


def test_a_decided_visitor_never_sees_the_banner(
    page: Page, base_url: str, demo_data: None
) -> None:
    grant(page, base_url, "analytics")
    open_page(page, f"{base_url}/shop/electronics/")

    wait_for_status(page, "google-analytics", "loaded")
    expect(page.locator(BANNER)).to_be_hidden()
    assert page.evaluate("() => window.Next.consent.decided()") is True


def test_a_partial_navigation_reports_one_more_page_view(
    page: Page, base_url: str, demo_data: None
) -> None:
    grant(page, base_url, "analytics")
    open_page(page, f"{base_url}/catalog/")
    wait_for_status(page, "google-analytics", "rendered")
    page.wait_for_function(f"() => ({GTAG_PAGE_VIEWS})().length === 1")

    seen = applied_count(page)
    page.locator(SEARCH).press_sequentially("iph", delay=30)
    wait_for_apply(page, seen)

    page.wait_for_function(f"() => ({GTAG_PAGE_VIEWS})().length === 2")
    first, second = page.evaluate(GTAG_PAGE_VIEWS)
    assert first == f"{base_url}/catalog/"
    assert "q=iph" in second


def test_the_cached_landing_signs_up_through_the_runtime(
    page: Page, base_url: str, demo_data: None, next_probe: PageProbe
) -> None:
    open_page(page, f"{base_url}/shop/electronics/")
    page.get_by_role("button", name="Reject all").click()

    seen = applied_count(page)
    page.get_by_label("Email").fill("bob@example.com")
    page.get_by_role("button", name="Send me the code").click()
    wait_for_apply(page, seen)

    expect(page.locator(THANKS)).to_contain_text("bob@example.com")
    assert any(r.url.endswith("/_next/csrf/") for r in next_probe.responses)
    assert Lead.objects.get().email == "bob@example.com"
