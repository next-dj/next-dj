import re
from collections.abc import Callable, Iterator

import pytest
from e2e_support.browser import wait_for_runtime
from e2e_support.server import serve_realtime
from e2e_support.suite import base_url, browser_context_args, ignore_browser_suite
from kanban.models import Board, Column
from playwright.sync_api import Page, expect

from next.testing import memory_broker


__all__ = ["base_url", "browser_context_args"]

SERVER_CARD_LIST = "[data-kanban-cards]"
SOCKET = re.compile(r"/_next/ws/$")

type OpenBoard = Callable[..., None]


def pytest_ignore_collect(config: pytest.Config) -> bool | None:
    return ignore_browser_suite(config)


@pytest.fixture(autouse=True)
def fresh_broker() -> Iterator[None]:
    """Start every browser test on an empty broker, so no topic carries old seqs."""
    with memory_broker():
        yield


@pytest.fixture()
def asgi_server(
    request: pytest.FixtureRequest, transactional_db: None
) -> Iterator[str]:
    """Serve the realtime-wrapped app under uvicorn, so boards negotiate a WebSocket."""
    with serve_realtime(request) as server:
        yield server.url


@pytest.fixture()
def board(demo_data: None) -> Board:
    return Board.objects.get(slug="engineering-roadmap")


@pytest.fixture()
def backlog(board: Board) -> Column:
    return board.columns.get(title="Backlog")


@pytest.fixture()
def done(board: Board) -> Column:
    return board.columns.get(title="Done")


def _open(page: Page, server: str, board: Board) -> None:
    page.goto(f"{server}/board/{board.pk}/")
    wait_for_runtime(page)


def open_board_page(
    page: Page, server: str, board: Board, *, websocket: bool = False
) -> None:
    """Open a board once the island replaced the server list, its socket up if asked."""
    if websocket:
        with page.expect_websocket(lambda socket: bool(SOCKET.search(socket.url))):
            _open(page, server, board)
    else:
        _open(page, server, board)
    expect(page.locator(SERVER_CARD_LIST)).to_have_count(0)


@pytest.fixture()
def open_board() -> OpenBoard:
    """Return the helper that opens a board and waits for the React island."""
    return open_board_page
