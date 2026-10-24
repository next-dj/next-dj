from django.http import HttpRequest

from next.realtime import TopicStream
from tests.load.profiles import TOPIC_PREFIX


def authorize(_user: object, topic: str) -> bool:
    """Grant every harness topic to any visitor."""
    return topic.startswith(TOPIC_PREFIX)


async def stream(request: HttpRequest, name: str) -> TopicStream:
    """Stream one harness topic as SSE or answer the WebSocket negotiation."""
    return TopicStream(request, f"{TOPIC_PREFIX}{name}", authorize=authorize)
