from collections.abc import Iterator

from django.http import HttpRequest
from polls.broker import broker
from polls.deps import DPoll
from polls.models import Poll

from next.partial import Patches, PatchEventStream


def patch_source(request: HttpRequest, poll_id: int) -> Iterator[Patches]:
    """Yield one refresh envelope for every poll change.

    Tabs re-fetch the zone through the page view, so no foreign HTML travels the stream.
    """
    for change in broker.changes(poll_id):
        yield Patches(request, echo_of=change.request_id).refresh(zone="poll-results")


def render(request: HttpRequest, poll: DPoll[Poll]) -> PatchEventStream:
    """Open the patch event stream for one poll.

    The source is sync, so the stream sends no heartbeat under WSGI.
    """
    return PatchEventStream(request, patch_source(request, poll.pk))
