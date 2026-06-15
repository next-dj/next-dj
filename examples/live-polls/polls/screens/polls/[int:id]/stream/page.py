from collections.abc import Iterator

from django.http import HttpRequest
from polls.broker import broker
from polls.models import Poll
from polls.providers import DPoll

from next.partial import Patches, PatchEventStream


def patch_source(request: HttpRequest, poll_id: int) -> Iterator[Patches]:
    """Yield one refresh envelope for every poll change.

    The `refresh` invalidates the `poll-results` zone so every open tab
    re-fetches it with its own cookies through the page view, no foreign
    HTML travels on the stream and authorization is re-checked per
    subscriber. The change's request id rides as the envelope echo so the
    voter's own tab drops the fan-out, its own response already carried
    the fresh zone.
    """
    for change in broker.changes(poll_id):
        yield Patches(request, echo_of=change.request_id).refresh(zone="poll-results")


def render(request: HttpRequest, poll: DPoll[Poll]) -> PatchEventStream:
    """Open the patch event stream for one poll.

    The framework escape hatch returns this response verbatim, no
    layout, no static collector, no template. The source is sync so the
    stream sends no heartbeat, a documented limitation under WSGI. An
    ASGI deployment swaps the broker wake primitive for an async one and
    passes an async source for heartbeat support without touching this
    page.
    """
    return PatchEventStream(request, patch_source(request, poll.pk))
