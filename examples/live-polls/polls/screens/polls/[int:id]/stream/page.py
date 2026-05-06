from django.http import StreamingHttpResponse
from polls.broker import broker
from polls.models import Poll
from polls.providers import DPoll


def render(poll: DPoll[Poll]) -> StreamingHttpResponse:
    """Open a Server-Sent Events stream for the requested poll.

    The framework escape hatch returns this response verbatim. No
    layout, no static collector, no template. The first frame is the
    cached snapshot so a fresh subscriber sees a baseline immediately.
    Subsequent frames flow as votes land. The 15-second wait inside
    `broker.subscribe` produces SSE comment frames as keepalive so
    proxies do not reap an idle connection.

    The response stays sync because the broker waits on
    `threading.Condition` and the example targets `runserver` and
    pytest. An ASGI deployment swaps the wake primitive for an
    `asyncio.Condition` (or an `asyncio.Queue` per subscriber) and
    yields from an async generator without touching the page or the
    signal layer.
    """
    return StreamingHttpResponse(
        broker.subscribe(poll.pk),
        content_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
