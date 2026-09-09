import pytest
from e2e_support.browser import (
    PageProbe,
    applied_count,
    expect_no_partial_request,
    request_baseline,
    wait_for_apply,
    wait_for_runtime,
)
from playwright.sync_api import Page, expect
from shortener.models import Link


pytestmark = pytest.mark.e2e

LATEST_LINKS = "[data-next-zone='latest-links']"


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


def test_creating_a_link_prepends_a_row_without_navigating(
    page: Page, base_url: str, next_probe: PageProbe
) -> None:
    page.goto(base_url)
    wait_for_runtime(page)
    page.evaluate("() => { window.__stillHere = true; }")

    expect(page.locator(f"{LATEST_LINKS} li")).to_have_count(0)

    seen = applied_count(page)
    page.fill("#id_url", "https://example.com/a/very/long/path")
    page.get_by_role("button", name="Shorten").click()
    wait_for_apply(page, seen)

    expect(page.locator(f"{LATEST_LINKS} li")).to_have_count(1)
    assert page.evaluate("() => window.__stillHere") is True
    assert Link.objects.count() == 1

    posts = [
        response
        for response in next_probe.partial_requests()
        if response.request.method == "POST"
    ]
    assert len(posts) == 1


def test_two_submissions_accumulate_two_rows(page: Page, base_url: str) -> None:
    page.goto(base_url)
    wait_for_runtime(page)

    for _ in range(2):
        seen = applied_count(page)
        page.fill("#id_url", "https://example.com/same")
        page.get_by_role("button", name="Shorten").click()
        wait_for_apply(page, seen)

    expect(page.locator(f"{LATEST_LINKS} li")).to_have_count(2)


def test_an_invalid_url_never_reaches_the_server(
    page: Page, base_url: str, next_probe: PageProbe
) -> None:
    page.goto(base_url)
    wait_for_runtime(page)

    field = page.locator("#id_url")
    field.fill("not-a-url")
    seen = request_baseline(page, next_probe)
    page.get_by_role("button", name="Shorten").click()

    assert field.evaluate("element => element.validity.valid") is False
    expect_no_partial_request(page, next_probe, seen)
    expect(page.locator(f"{LATEST_LINKS} li")).to_have_count(0)
    assert Link.objects.count() == 0
