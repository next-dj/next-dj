from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import UTC, datetime, timedelta

import pytest
from e2e_support.browser import (
    PageProbe,
    applied_count,
    wait_for_apply,
    wait_for_runtime,
)
from obs import metrics
from playwright.sync_api import Locator, Page, Response, expect


pytestmark = pytest.mark.e2e

FrozenNow = Callable[[datetime], AbstractContextManager[object]]

SPARKLINE = "#sparkline-mount"
SPARK_BARS = f"{SPARKLINE} li"
TOTALS_ZONE = "overview-totals"
BUSIEST_ZONE = "busiest-pages"
WINDOW_ZONE = "stats-window"
CANVAS = "#render-chart-canvas"
PULSE_TARGET = "[data-metric-pulse-target]"
SEEDED_MINUTES_BACK = 30
SEEDED_RENDERS = 500

READ_CHART = (
    "() => { const chart = window.Chart.getChart('render-chart-canvas');"
    " return chart === undefined ? null :"
    " { labels: chart.data.labels, values: chart.data.datasets[0].data }; }"
)


def is_totals_poll(response: Response) -> bool:
    return TOTALS_ZONE in response.request.headers.get("x-next-zone", "")


def zone(page: Page, name: str) -> Locator:
    return page.locator(f"[data-next-zone='{name}']")


def stat(page: Page, label: str) -> Locator:
    return page.locator("article").filter(has_text=label).locator("p").nth(1)


def stat_number(page: Page, label: str) -> int:
    return int(stat(page, label).inner_text())


def test_the_overview_boots_and_babel_draws_the_react_sparkline(
    page: Page, base_url: str, next_probe: PageProbe
) -> None:
    page.goto(base_url)
    wait_for_runtime(page)

    expect(page.locator(SPARK_BARS)).to_have_count(4)
    expect(page.locator(SPARKLINE)).to_contain_text("pages_rendered")
    cdn = [
        response
        for response in next_probe.responses
        if "unpkg.com" in response.url or "jsdelivr.net" in response.url
    ]
    assert len(cdn) == 3
    assert {response.status for response in cdn} == {200}


def test_the_lazy_zone_swaps_its_placeholder_for_the_busiest_pages(
    page: Page, base_url: str, next_probe: PageProbe
) -> None:
    page.goto(base_url)
    wait_for_runtime(page)
    busiest = zone(page, BUSIEST_ZONE)
    expect(busiest.locator("li")).not_to_have_count(0)
    expect(busiest.get_by_text("Loading the busiest pages")).to_have_count(0)
    expect(busiest).to_contain_text("dashboards/page.py")
    lazy = next_probe.partial_requests(zone=BUSIEST_ZONE)
    assert [response.request.method for response in lazy] == ["GET"]


def test_the_polling_zone_refreshes_its_counters_on_its_own(
    page: Page, base_url: str, next_probe: PageProbe
) -> None:
    page.clock.install()
    page.goto(base_url)
    wait_for_runtime(page)
    before = stat_number(page, "Pages rendered")

    for _ in range(3):
        with page.expect_response(is_totals_poll):
            page.clock.run_for(5_000)

    expect(stat(page, "Pages rendered")).not_to_have_text(str(before))
    polls = next_probe.partial_requests(zone=TOTALS_ZONE)
    assert [response.request.method for response in polls] == ["GET"] * 3
    assert stat_number(page, "Pages rendered") > before


def test_the_stats_page_hands_chart_js_the_windowed_totals(
    page: Page, base_url: str
) -> None:
    page.goto(base_url)
    page.goto(f"{base_url}/stats/")
    wait_for_runtime(page)

    expect(page.locator(CANVAS)).to_be_visible()
    chart = page.evaluate(READ_CHART)
    assert chart is not None
    assert chart["labels"] == ["pages", "components", "actions"]
    assert chart["values"][1] > 0


def test_switching_the_window_reaggregates_the_totals_and_pulses_them(
    page: Page, base_url: str, frozen_now: FrozenNow
) -> None:
    with frozen_now(datetime.now(tz=UTC) - timedelta(minutes=SEEDED_MINUTES_BACK)):
        metrics.incr("pages.rendered", "long/ago/page.py", by=SEEDED_RENDERS)

    page.goto(f"{base_url}/stats/")
    wait_for_runtime(page)
    expect(zone(page, WINDOW_ZONE)).to_have_text("Window: 5m")
    narrow = stat_number(page, "Pages")
    assert narrow < SEEDED_RENDERS

    seen = applied_count(page)
    page.select_option("select[name='window']", "1h")
    page.get_by_role("button", name="Apply").click()
    wait_for_apply(page, seen)

    expect(zone(page, WINDOW_ZONE)).to_have_text("Window: 1h")
    expect(page.locator(PULSE_TARGET)).to_have_attribute("data-pulse-window", "1h")
    expect(page).to_have_url(f"{base_url}/stats/")
    assert stat_number(page, "Pages") >= narrow + SEEDED_RENDERS


def test_a_stats_subpage_ships_neither_the_pulse_handler_nor_its_zone(
    page: Page, base_url: str, next_probe: PageProbe
) -> None:
    page.goto(base_url)
    page.goto(f"{base_url}/stats/pages/")
    wait_for_runtime(page)

    expect(page.get_by_role("heading", name="Per-page render counts")).to_be_visible()
    expect(page.locator("tbody tr")).not_to_have_count(0)
    expect(zone(page, WINDOW_ZONE)).to_have_text("Window: 5m")
    expect(page.locator(PULSE_TARGET)).to_have_count(0)
    assert [
        response.url for response in next_probe.responses if "stats.js" in response.url
    ] == []
    assert [
        response
        for response in next_probe.partial_requests()
        if response.url.startswith(f"{base_url}/stats/")
    ] == []


def test_the_window_filter_on_a_subpage_carries_it_to_the_live_page(
    page: Page, base_url: str
) -> None:
    page.goto(base_url)
    page.goto(f"{base_url}/stats/pages/")
    wait_for_runtime(page)

    page.select_option("select[name='window']", "1h")
    page.get_by_role("button", name="Apply").click()

    expect(page).to_have_url(f"{base_url}/stats/?window=1h")
    expect(zone(page, WINDOW_ZONE)).to_have_text("Window: 1h")
    expect(page.locator(PULSE_TARGET)).to_have_count(1)
    assert page.locator(PULSE_TARGET).get_attribute("data-pulse-window") is None
