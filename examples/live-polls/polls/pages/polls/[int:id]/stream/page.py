from django.http import HttpRequest
from polls.deps import DPoll
from polls.models import Poll
from polls.realtime import can_subscribe_topic

from next.realtime import TopicStream


def render(request: HttpRequest, poll: DPoll[Poll]) -> TopicStream:
    """Open the authorized topic stream for one poll."""
    return TopicStream(
        request, f"poll:{poll.pk}", authorize=can_subscribe_topic, presence=True
    )
