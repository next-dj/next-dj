import json
import re
import socket
import threading
from collections.abc import Iterator
from typing import override

import pytest
import uvicorn
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
from django.core.asgi import get_asgi_application
from e2e_support.browser import (
    PageProbe,
    expect_no_partial_request,
    request_baseline,
    wait_for_runtime,
)
from playwright.sync_api import Page, Route, WebSocket, WebSocketRoute, expect
from polls.models import Poll

from next import realtime
from next.partial import Patches
from next.realtime import publish
from next.testing import override_websocket_mount


pytestmark = [pytest.mark.e2e, pytest.mark.django_db(transaction=True)]

ANNOUNCEMENT = "[data-poll-vote-announcement]"
RECORD_GAPS = """() => {
    window.gaps = [];
    window.Next.on("next:stream-gap", (gap) => window.gaps.push(gap));
}"""
COUNT_GAPS = "() => window.gaps.length"


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
def websocket_mount() -> Iterator[None]:
    with override_websocket_mount():
        yield


@pytest.fixture(params=["wsproto", "websockets-sansio"])
def asgi_server(
    request: pytest.FixtureRequest, transactional_db: None, websocket_mount: None
) -> Iterator[str]:
    """Serve the realtime-wrapped app under uvicorn on an ephemeral local port."""
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        app = realtime.application(ASGIStaticFilesHandler(get_asgi_application()))
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            ws=request.param,
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
    sockets: list[str] = []

    def record_frame(payload: str | bytes) -> None:
        frames.append(payload)

    def record_frames(websocket: WebSocket) -> None:
        if websocket.url.endswith("/_next/ws/"):
            sockets.append(websocket.url)
            websocket.on("framereceived", record_frame)

    page.on("websocket", record_frames)
    watcher.on("websocket", record_frames)
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
    assert len(sockets) == 2
    assert any(
        message.get("type") == "patches" and isinstance(message.get("data"), dict)
        for message in messages
    )
    tabs.refresh_from_db()
    assert tabs.votes == 1


def test_a_socket_from_a_foreign_origin_is_refused(
    page: Page, next_probe: PageProbe, asgi_server: str, tabs_or_spaces: Poll
) -> None:
    port = asgi_server.rsplit(":", 1)[1]
    page.goto(f"http://localhost:{port}/polls/{tabs_or_spaces.pk}/")
    opened = page.evaluate(
        """url => new Promise(resolve => {
            const socket = new WebSocket(url);
            socket.onopen = () => resolve(true);
            socket.onclose = () => resolve(false);
        })""",
        f"ws://127.0.0.1:{port}/_next/ws/",
    )
    assert opened is False
    assert any("403" in entry for entry in next_probe.console)
    next_probe.console.clear()


def announce_votes(poll: Poll, total_votes: int) -> None:
    publish(
        f"poll:{poll.pk}",
        Patches(None).event("poll:votes", {"total_votes": total_votes}),
        on_commit=False,
    )


def is_hello(payload: str | bytes) -> bool:
    return isinstance(payload, str) and json.loads(payload).get("type") == "hello"


def test_a_dropped_socket_replays_the_missed_votes_without_a_resync_get(
    page: Page, next_probe: PageProbe, asgi_server: str, tabs_or_spaces: Poll
) -> None:
    sockets: list[WebSocketRoute] = []

    def proxy(route: WebSocketRoute) -> None:
        first = not sockets
        sockets.append(route)
        server = route.connect_to_server()

        def relay(message: str | bytes) -> None:
            route.send(message)
            if first and is_hello(message):
                announce_votes(tabs_or_spaces, 1)

        server.on_message(relay)

    def vote_while_down(route: Route) -> None:
        announce_votes(tabs_or_spaces, 2)
        announce_votes(tabs_or_spaces, 3)
        route.continue_()

    page.route_web_socket(re.compile(r"/_next/ws/$"), proxy)
    page.goto(f"{asgi_server}/polls/{tabs_or_spaces.pk}/")
    wait_for_runtime(page)
    page.evaluate(RECORD_GAPS)
    expect(page.locator(ANNOUNCEMENT)).to_have_text("1 total votes")
    baseline = request_baseline(page, next_probe)
    gaps = page.evaluate(COUNT_GAPS)
    page.route(re.compile(r"/stream/$"), vote_while_down, times=1)

    with page.expect_websocket(lambda websocket: websocket.url.endswith("/_next/ws/")):
        sockets[0].close(code=4000)

    expect(page.locator(ANNOUNCEMENT)).to_have_text("3 total votes")
    expect_no_partial_request(page, next_probe, baseline)
    assert page.evaluate(COUNT_GAPS) == gaps
