from collections.abc import Iterator

import pytest
from e2e_support.server import serve_realtime
from e2e_support.suite import base_url, browser_context_args, ignore_browser_suite


__all__ = ["base_url", "browser_context_args"]


def pytest_ignore_collect(config: pytest.Config) -> bool | None:
    return ignore_browser_suite(config)


@pytest.fixture()
def asgi_server(request: pytest.FixtureRequest) -> Iterator[str]:
    """Serve the realtime-wrapped app under uvicorn, so pages negotiate a WebSocket."""
    with serve_realtime(request) as server:
        yield server.url
