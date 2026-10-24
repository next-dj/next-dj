import socket
import threading
from collections.abc import Iterator

import pytest
from daphne.server import Server
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
from django.core.asgi import get_asgi_application
from e2e_support.browser import wait_for_runtime
from playwright.sync_api import Page, expect
from polls.models import Poll
from twisted.internet import reactor

from next.contrib.channels import application as realtime_application
from next.testing import override_next_settings


pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


@pytest.fixture()
def tabs_or_spaces(demo_data: None) -> Poll:
    return Poll.objects.get(question="Tabs or spaces?")


@pytest.fixture(scope="module")
def asgi_server() -> Iterator[str]:
    """Run the Channels-wrapped example app on an ephemeral local port."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    http_app = ASGIStaticFilesHandler(get_asgi_application())
    app = realtime_application(http_app)
    started = threading.Event()
    server = Server(
        app,
        endpoints=[f"tcp:interface=127.0.0.1:port={port}"],
        signal_handlers=False,
        verbosity=0,
        ready_callable=started.set,
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    if not started.wait(timeout=5):
        pytest.fail("Daphne did not start the live-polls ASGI app.")
    try:
        yield origin
    finally:
        reactor.callFromThread(reactor.stop)
        thread.join(timeout=5)


def test_authorized_poll_updates_a_second_tab_over_websocket(
    page: Page, asgi_server: str, tabs_or_spaces: Poll
) -> None:
    tabs = tabs_or_spaces.choices.get(text="Tabs")
    watcher = page.context.new_page()
    websocket_urls: list[str] = []
    page.on("websocket", lambda websocket: websocket_urls.append(websocket.url))
    with override_next_settings(REALTIME={"TRANSPORTS": ["ws", "sse"]}):
        page.goto(f"{asgi_server}/polls/{tabs_or_spaces.pk}/")
        watcher.goto(f"{asgi_server}/polls/{tabs_or_spaces.pk}/")
        wait_for_runtime(page)
        wait_for_runtime(watcher)
        expect(page.locator("[data-poll-chart-votes]").first).to_have_text("0")
        expect(watcher.locator("[data-poll-chart-votes]").first).to_have_text("0")
        page.get_by_role("button", name="Tabs", exact=True).click()
        expect(watcher.locator("[data-poll-chart-votes]").first).to_have_text("1")
        expect(page.locator("[data-poll-chart-votes]").first).to_have_text("1")
        assert any(url.endswith("/_next/ws/") for url in websocket_urls)
    watcher.close()
    tabs.refresh_from_db()
    assert tabs.votes == 1
