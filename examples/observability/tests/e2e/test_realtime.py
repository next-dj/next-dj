import pytest
from e2e_support.browser import wait_for_runtime
from playwright.sync_api import Locator, Page, expect


pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

RECORD_STREAM_STATES = """
window.__streamStates = [];
const listen = () => {
  if (!window.Next || typeof window.Next.on !== "function") {
    setTimeout(listen, 0);
    return;
  }
  window.Next.on("next:stream-state", (detail) => window.__streamStates.push(detail));
};
listen();
"""
STREAM_OPEN = (
    "(transport) => (window.__streamStates ?? []).some("
    "(detail) => detail.state === 'open' && detail.transport === transport)"
)


def stat(page: Page, label: str) -> Locator:
    return page.locator("article").filter(has_text=label).locator("p").nth(1)


def open_streaming(page: Page, url: str, transport: str) -> Page:
    page.add_init_script(RECORD_STREAM_STATES)
    page.goto(url)
    wait_for_runtime(page)
    page.wait_for_function(STREAM_OPEN, arg=transport)
    return page


def test_a_pulse_refreshes_the_totals_of_another_tab(page: Page, base_url: str) -> None:
    watcher = open_streaming(page.context.new_page(), f"{base_url}/realtime/", "sse")
    published = int(stat(watcher, "Published").inner_text())
    page.goto(f"{base_url}/realtime/")
    wait_for_runtime(page)
    page.get_by_role("button", name="Publish a pulse").click()
    expect(stat(watcher, "Published")).to_have_text(str(published + 1))
    expect(stat(watcher, "Open SSE")).not_to_have_text("0")
    watcher.close()


def test_a_nudge_over_the_websocket_reaches_another_tab(
    page: Page, asgi_server: str
) -> None:
    watcher = open_streaming(page.context.new_page(), f"{asgi_server}/realtime/", "ws")
    nudges = int(stat(watcher, "Nudges").inner_text())
    open_streaming(page, f"{asgi_server}/realtime/", "ws")
    page.get_by_role("button", name="Nudge").click()
    expect(stat(watcher, "Nudges")).to_have_text(str(nudges + 1))
    expect(stat(page, "Nudges")).to_have_text(str(nudges + 1))
    expect(stat(watcher, "Open WebSocket")).to_have_text("2")
    watcher.close()
