import asyncio
from collections.abc import MutableMapping

from django.core.handlers.asgi import ASGIHandler
from django.http import StreamingHttpResponse


class StalledSend:
    """ASGI `send` that delivers `delivered` body chunks, then blocks for good.

    It stands for a client with a zero TCP window, whose server never drains a write.
    """

    def __init__(self, delivered: int) -> None:
        """Let `delivered` body chunks through before every later send blocks."""
        self.delivered = delivered
        self.bodies: list[bytes] = []
        self.blocked = asyncio.Event()

    async def __call__(self, message: MutableMapping[str, object]) -> None:
        """Record a body chunk, or block forever once the quota is spent."""
        if message["type"] != "http.response.body":
            return
        if len(self.bodies) >= self.delivered:
            self.blocked.set()
            await asyncio.Event().wait()
        body = message.get("body", b"")
        assert isinstance(body, bytes)
        self.bodies.append(body)


async def serve_stream(
    response: StreamingHttpResponse,
    send: StalledSend | None = None,
    *,
    within: float = 2.0,
) -> asyncio.Task[None]:
    """Write `response` through Django's ASGI sender and return its finished task.

    The task runs alone, so a cancelled sender leaves the calling test running.
    """
    task = asyncio.create_task(
        ASGIHandler().send_response(response, send or StalledSend(delivered=10**6))
    )
    await asyncio.wait({task}, timeout=within)
    return task
