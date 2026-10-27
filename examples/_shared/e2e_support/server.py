import asyncio
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import override

import pytest
import uvicorn
from asgiref.typing import ASGIApplication
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler
from django.core.asgi import get_asgi_application

from e2e_support.suite import call_passed
from next import realtime
from next.testing import override_websocket_mount


WEBSOCKET_IMPLEMENTATION = "websockets-sansio"


class LiveAsgiServer(uvicorn.Server):
    """Uvicorn server a browser suite drives from its own thread, cut and drained."""

    def __init__(self, config: uvicorn.Config) -> None:
        """Create the server with an unset readiness event and no loop yet."""
        super().__init__(config)
        self.ready = threading.Event()
        self.loop: asyncio.AbstractEventLoop | None = None

    @property
    def url(self) -> str:
        """The origin the server listens on."""
        return f"http://{self.config.host}:{self.config.port}"

    def drop_connections(self, timeout: float = 5.0) -> None:
        """Abort every open connection the way a cut network would."""
        loop = self.loop
        if loop is None:
            pytest.fail("the ASGI server has not started yet.")
        aborted = asyncio.run_coroutine_threadsafe(self._abort_connections(), loop)
        try:
            aborted.result(timeout)
        except TimeoutError:
            aborted.cancel()
            pytest.fail(f"the ASGI server did not cut its connections in {timeout}s.")

    def drain(self, timeout: float = 5.0) -> None:
        """Wait for every request and socket task, so a closing stream finishes first.

        Its `request_finished` must run while the test still holds the database.
        """
        loop = self.loop
        if loop is None:
            pytest.fail("the ASGI server has not started yet.")
        settled = asyncio.run_coroutine_threadsafe(self._settled(), loop)
        try:
            settled.result(timeout)
        except TimeoutError:
            settled.cancel()
            pytest.fail(f"the ASGI server kept a task running past {timeout}s.")

    async def _abort_connections(self) -> None:
        for connection in list(self.server_state.connections):
            if connection.transport is not None:
                connection.transport.abort()

    async def _settled(self) -> None:
        pending = set(self.server_state.tasks)
        if pending:
            await asyncio.wait(pending)

    @override
    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        self.loop = asyncio.get_running_loop()
        await super().startup(sockets)
        self.ready.set()


@contextmanager
def serve_asgi(app: ASGIApplication, websocket: str) -> Iterator[LiveAsgiServer]:
    """Serve an ASGI app under uvicorn on an ephemeral local port in a thread."""
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=sock.getsockname()[1],
            ws=websocket,
            lifespan="off",
            log_level="warning",
            timeout_graceful_shutdown=1,
            log_config=None,
        )
        server = LiveAsgiServer(config)
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
            pytest.fail("uvicorn exited while starting the ASGI app.")
        pytest.fail("uvicorn did not start the ASGI app within 5 seconds.")
    try:
        yield server
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=5)


@contextmanager
def serve_realtime(
    request: pytest.FixtureRequest, websocket: str = WEBSOCKET_IMPLEMENTATION
) -> Iterator[LiveAsgiServer]:
    """Serve the project behind its realtime endpoint, drained after a passed test.

    A failed test keeps its pages open for the screenshot, its streams outlive a drain.
    """
    with override_websocket_mount():
        app = realtime.application(ASGIStaticFilesHandler(get_asgi_application()))
        with serve_asgi(app, websocket) as server:
            yield server
            if call_passed(request.node):
                server.drain()
