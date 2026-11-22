from collections.abc import Callable

import pytest
from e2e_support.browser import wait_for_runtime
from playwright.sync_api import Locator, Page, Request, expect

from next.testing import boundary_gate


DESKTOP_CHROME = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.browser_context_args(user_agent=DESKTOP_CHROME),
]

LOADING = "Averaging the render times"
GATE_TIMEOUT = 10

Depths = list[str | None]


@pytest.fixture()
def router_requests(page: Page, base_url: str) -> list[Request]:
    sent: list[Request] = []
    overview = f"{base_url}/"

    def record(request: Request) -> None:
        if request.url == overview and "x-next-navigate" in request.headers:
            sent.append(request)

    page.on("request", record)
    return sent


def depths(sent: list[Request]) -> Depths:
    return [request.headers.get("x-next-prefetch") for request in sent]


def pressed_once(sent: list[Request]) -> list[Request]:
    sent_depths = depths(sent)
    assert sent_depths.count("full") == 1
    assert None not in sent_depths
    return [
        request for request in sent if request.headers.get("x-next-prefetch") == "full"
    ]


def is_depth(base_url: str, depth: str) -> Callable[[Request], bool]:
    def matches(request: Request) -> bool:
        return (
            request.url == f"{base_url}/"
            and request.headers.get("x-next-prefetch") == depth
        )

    return matches


def open_stats(page: Page, base_url: str) -> None:
    page.goto(f"{base_url}/stats/")
    wait_for_runtime(page)
    expect(page.get_by_role("heading", name="Stats", exact=True)).to_be_visible()


def overview_link(page: Page) -> Locator:
    return page.get_by_role("navigation", name="Main").get_by_role(
        "link", name="Overview"
    )


def latency(page: Page) -> Locator:
    section = page.locator("section").filter(
        has=page.get_by_role("heading", name="Render latency", exact=True)
    )
    return section.locator("[data-next-boundary]")


def expect_ready(page: Page) -> None:
    widget = latency(page)
    expect(widget).to_have_attribute("data-next-state", "ready")
    expect(widget.get_by_role("columnheader", name="Mean ms")).to_be_visible()
    expect(widget).to_contain_text("obs/pages/stats/page.py")
    expect(widget.get_by_text(LOADING)).to_have_count(0)


def center(locator: Locator) -> tuple[float, float]:
    box = locator.bounding_box()
    assert box is not None
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def test_a_mouse_press_sends_the_one_request_the_click_streams_from(
    page: Page, base_url: str, router_requests: list[Request]
) -> None:
    open_stats(page, base_url)

    with boundary_gate(component="latency_widget") as gate:
        overview_link(page).click()
        assert gate.wait(GATE_TIMEOUT)
        expect(page).to_have_url(f"{base_url}/")
        expect(latency(page)).to_contain_text(LOADING)
        expect(latency(page)).to_have_attribute("data-next-state", "loading")
        gate.release()
        expect_ready(page)

    [pressed] = pressed_once(router_requests)
    response = pressed.response()
    assert response is not None
    assert response.headers["content-type"].startswith("text/event-stream")


def test_a_hover_prefetch_puts_the_shell_on_screen_at_the_click(
    page: Page, base_url: str, router_requests: list[Request]
) -> None:
    open_stats(page, base_url)
    link = overview_link(page)

    shell = is_depth(base_url, "1")
    with page.expect_response(lambda response: shell(response.request)) as prefetched:
        link.hover()
    assert prefetched.value.ok

    with boundary_gate(component="latency_widget") as gate:
        link.click()
        expect(page).to_have_url(f"{base_url}/")
        expect(latency(page)).to_contain_text(LOADING)
        assert gate.wait(GATE_TIMEOUT)
        expect(latency(page)).to_have_attribute("data-next-state", "loading")
        gate.release()
        expect_ready(page)

    assert depths(router_requests) == ["1", "full"]


def test_a_press_released_off_the_link_aborts_the_request_and_stays(
    page: Page, base_url: str, router_requests: list[Request]
) -> None:
    open_stats(page, base_url)
    heading = page.get_by_role("heading", name="Stats", exact=True)

    with boundary_gate(component="latency_widget"):
        page.mouse.move(*center(overview_link(page)))
        with page.expect_request(is_depth(base_url, "full")) as pressed:
            page.mouse.down()
        with page.expect_event("requestfailed", lambda failed: failed == pressed.value):
            page.mouse.move(*center(heading))
            page.mouse.up()

    assert "net::ERR_ABORTED" in (pressed.value.failure or "")
    expect(page).to_have_url(f"{base_url}/stats/")
    expect(heading).to_be_visible()
    pressed_once(router_requests)


def test_a_modifier_click_opens_a_tab_without_the_press_request(
    page: Page, base_url: str, router_requests: list[Request]
) -> None:
    open_stats(page, base_url)

    with page.context.expect_page() as opened:
        overview_link(page).click(modifiers=["ControlOrMeta"])
    tab = opened.value
    tab.wait_for_load_state()

    expect(tab).to_have_url(f"{base_url}/")
    expect(page).to_have_url(f"{base_url}/stats/")
    assert "full" not in depths(router_requests)


@pytest.mark.browser_context_args(user_agent=DESKTOP_CHROME, has_touch=True)
def test_a_touch_tap_keeps_the_shell_prefetch_and_navigates(
    page: Page, base_url: str, router_requests: list[Request]
) -> None:
    open_stats(page, base_url)

    overview_link(page).tap()

    expect(page).to_have_url(f"{base_url}/")
    expect_ready(page)
    assert depths(router_requests) == ["1", None]
