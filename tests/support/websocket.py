import json
from urllib.parse import unquote, urlparse

from asgiref.testing import ApplicationCommunicator
from asgiref.typing import ASGIApplication


class WebsocketCommunicator(ApplicationCommunicator):
    """Drive an ASGI application through one WebSocket connection in a test."""

    def __init__(
        self,
        application: ASGIApplication,
        path: str,
        headers: list[tuple[bytes, bytes]] | None = None,
    ) -> None:
        """Build the websocket scope from the path and the request headers."""
        parsed = urlparse(path)
        scope = {
            "type": "websocket",
            "path": unquote(parsed.path),
            "query_string": parsed.query.encode(),
            "headers": headers or [],
            "subprotocols": [],
        }
        super().__init__(application, scope)

    async def connect(self) -> tuple[bool, object]:
        """Return True and the accepted subprotocol, or False and the close code."""
        await self.send_input({"type": "websocket.connect"})
        response = await self.receive_output()
        if response["type"] == "websocket.close":
            return False, response.get("code", 1000)
        assert response["type"] == "websocket.accept", response
        return True, response.get("subprotocol")

    async def send_to(self, text_data: str) -> None:
        """Send one text frame to the application."""
        await self.send_input({"type": "websocket.receive", "text": text_data})

    async def send_json_to(self, data: object) -> None:
        """Send one JSON text frame to the application."""
        await self.send_to(json.dumps(data))

    async def receive_json_from(self) -> dict[str, object]:
        """Receive one text frame and decode its JSON object."""
        response = await self.receive_output()
        assert response["type"] == "websocket.send", response
        payload = json.loads(response["text"])
        assert isinstance(payload, dict), payload
        return payload

    async def disconnect(self, code: int = 1000) -> None:
        """Close the socket from the client and wait for the application to exit."""
        await self.send_input({"type": "websocket.disconnect", "code": code})
        await self.wait()
