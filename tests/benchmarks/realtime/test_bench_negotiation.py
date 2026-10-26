import json
from collections.abc import Iterator

import pytest
from django.http import HttpRequest

from next.realtime.negotiation import negotiate
from next.realtime.tickets import verify_ticket
from next.realtime.transports import active_transports
from next.testing.realtime import override_websocket_mount
from tests.benchmarks.factories import topic_names
from tests.support.realtime import ROOM_AUTHORIZER_REF, allow_room_topic


@pytest.fixture()
def ws_mounted() -> Iterator[None]:
    with override_websocket_mount(mounted=True):
        yield


def _negotiate(request: HttpRequest, topics: tuple[str, ...]):
    return negotiate(
        request,
        topics,
        transform=(None, None),
        authorize=(allow_room_topic, ROOM_AUTHORIZER_REF),
        presence=(False, None),
    )


@pytest.mark.usefixtures("ws_mounted")
class TestBenchNegotiation:
    """Answer a page's request for a WebSocket ticket on a saved session."""

    @pytest.mark.parametrize("count", [1, 64])
    @pytest.mark.benchmark(group="realtime.negotiation")
    def test_ticket(self, session_request: HttpRequest, count: int, benchmark) -> None:
        """Validates the topics, resolves the authorizer and signs the ticket."""
        session_request.method = "GET"
        topics = tuple(topic_names(count))
        assert "ws" in active_transports()
        response = benchmark(_negotiate, session_request, topics)
        assert response.status_code == 200
        body = json.loads(response.content)
        claims = verify_ticket(body["ticket"], session_request.session.session_key)
        assert claims.topics == topics

    @pytest.mark.benchmark(group="realtime.negotiation")
    def test_sse_fallback(self, session_request: HttpRequest, benchmark) -> None:
        """An authorizer without an import-stable path keeps the client on SSE."""
        session_request.method = "GET"

        def run():
            return negotiate(
                session_request,
                ("room:1",),
                transform=(None, None),
                authorize=(lambda _user, _topic: True, None),
                presence=(False, None),
            )

        response = benchmark(run)
        assert response.status_code == 406
        assert json.loads(response.content) == {
            "transport": "sse",
            "reason": "authorization",
        }
