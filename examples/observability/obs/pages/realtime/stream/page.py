from django.http import HttpRequest
from obs.realtime import PULSE_TOPIC, can_watch

from next.realtime import TopicStream


def render(request: HttpRequest) -> TopicStream:
    """Open the pulse topic stream, over a WebSocket when the process mounts one."""
    return TopicStream(request, PULSE_TOPIC, authorize=can_watch)
