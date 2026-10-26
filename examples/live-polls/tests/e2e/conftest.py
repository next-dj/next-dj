from collections.abc import Callable, Iterator

import pytest
from e2e_support.server import LiveAsgiServer, serve_realtime
from e2e_support.suite import browser_context_args, call_passed, ignore_browser_suite
from polls.models import Poll

from next.testing import override_websocket_mount


__all__ = ["base_url", "browser_context_args"]


def pytest_ignore_collect(config: pytest.Config) -> bool | None:
    return ignore_browser_suite(config)


@pytest.fixture(scope="session")
def server(
    request: pytest.FixtureRequest, django_db_setup: None
) -> Iterator[LiveAsgiServer]:
    """Serve the app under uvicorn with the WebSocket endpoint mounted."""
    with serve_realtime(request) as running:
        yield running


@pytest.fixture(scope="session")
def base_url(server: LiveAsgiServer) -> str:
    return server.url


@pytest.fixture()
def cut_network(server: LiveAsgiServer) -> Callable[[], None]:
    """Return a callable that aborts every connection the server holds open."""
    return server.drop_connections


@pytest.fixture(params=["wsproto", "websockets-sansio"])
def asgi_server(request: pytest.FixtureRequest) -> Iterator[str]:
    """Serve one app per WebSocket implementation uvicorn can speak."""
    with serve_realtime(request, request.param) as server:
        yield server.url


@pytest.fixture()
def tabs_or_spaces(demo_data: None) -> Poll:
    return Poll.objects.get(question="Tabs or spaces?")


@pytest.fixture(autouse=True)
def _shared_database(
    request: pytest.FixtureRequest, transactional_db: None, server: LiveAsgiServer
) -> Iterator[None]:
    """Commit for real and finish a passed test's streams while it holds the db."""
    yield
    if call_passed(request.node):
        server.drain()


@pytest.fixture()
def sse_only() -> Iterator[None]:
    """Make pages negotiate SSE although the server also serves the WebSocket."""
    with override_websocket_mount():
        yield


@pytest.fixture(params=["websocket", "sse"])
def transport(request: pytest.FixtureRequest) -> Iterator[str]:
    """Run a test once per transport the page can negotiate."""
    if request.param == "websocket":
        yield request.param
        return
    with override_websocket_mount():
        yield request.param
