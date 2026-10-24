import json
import socket
import threading
from collections.abc import Iterator
from typing import override

import pytest
import uvicorn
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
from django.core.asgi import get_asgi_application
from e2e_support.browser import wait_for_runtime
from playwright.sync_api import Page, WebSocket, expect
from polls.models import Poll

from next.contrib.channels import application as realtime_application
from next.testing import override_next_settings


pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]


class SignallingServer(uvicorn.Server):
    """Uvicorn server that sets an event once its sockets accept connections."""

    def __init__(self, config: uvicorn.Config) -> None:
        """Create the server with an unset readiness event."""
        super().__init__(config)
        self.ready = threading.Event()

    @override
    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets)
        self.ready.set()


@pytest.fixture()
def tabs_or_spaces(demo_data: None) -> Poll:
    return Poll.objects.get(question="Tabs or spaces?")


@pytest.fixture()
def asgi_server(transactional_db: None) -> Iterator[str]:
    """Serve the Channels-wrapped app under uvicorn on an ephemeral local port."""
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        app = realtime_application(ASGIStaticFilesHandler(get_asgi_application()))
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            ws="wsproto",
            lifespan="off",
            log_level="warning",
            timeout_graceful_shutdown=1,
            log_config=None,
        )
        server = SignallingServer(config)
        thread = threading.Thread(
            target=server.run, kwargs={"sockets": [sock]}, daemon=True
        )
        thread.start()
    except BaseException:
        sock.close()
        raise
    if not server.ready.wait(timeout=5):
        died = not thread.is_alive()
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        if died:
            pytest.fail("uvicorn exited while starting the live-polls ASGI app.")
        pytest.fail("uvicorn did not start the live-polls ASGI app within 5 seconds.")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=5)


def test_authorized_poll_updates_a_second_tab_over_websocket(
    page: Page, asgi_server: str, tabs_or_spaces: Poll
) -> None:
    tabs = tabs_or_spaces.choices.get(text="Tabs")
    watcher = page.context.new_page()
    frames: list[str | bytes] = []

    def record_frame(payload: str | bytes) -> None:
        frames.append(payload)

    def record_frames(websocket: WebSocket) -> None:
        if websocket.url.endswith("/_next/ws/"):
            websocket.on("framereceived", record_frame)

    page.on("websocket", record_frames)
    watcher.on("websocket", record_frames)
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
    watcher.close()
    page.close()
    messages = [json.loads(frame) for frame in frames if isinstance(frame, str)]
    assert any(
        message.get("type") == "patches" and isinstance(message.get("data"), dict)
        for message in messages
    )
    tabs.refresh_from_db()
    assert tabs.votes == 1
