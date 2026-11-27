"""The sinks deferred calls reach and the delivery a render settles its jobs by."""

from collections.abc import Callable, Generator, MutableMapping
from contextlib import contextmanager
from functools import partial
from typing import Final, override

from django.http import HttpRequest
from django.template import Context

from next.boundaries.calls import DEFERRALS_KEY, DeferredCall
from next.boundaries.scope import render_boundaries
from next.defer.components import (
    ComponentDeferrals,
    Deferring,
    current_deferring,
    deferring,
)
from next.defer.ledger import ResponseJobs
from next.defer.manager import defer_manager
from next.defer.markers import JOBLESS, Delivery
from next.defer.payload import partial_streams
from next.defer.policy import DeliveryDecision, DeliveryReason, StreamSlot
from next.defer.render import current_frame
from next.defer.response import strip, wrappers
from next.defer.trip import trip_input
from next.static.collector import StaticCollector
from next.static.inject import SPLIT_NOTE
from next.static.runtime import BOUNDARIES_CHUNK_KEY

from .inline import finish, prune
from .jobs import batches, boundaries
from .partials import decided, decision, streamed, take_slot, tell


type Render = Callable[[], str]


class PageRender:
    """What a page render leaves its view, a stream to deliver or why it is not one.

    It holds the stream slot its policy reserved until the stream response takes it.
    """

    __slots__ = ("boundaries", "collector", "delivered", "session", "shell", "slot")

    def __init__(self) -> None:
        """Hold nothing until the render queues a deferred call."""
        self.slot: StreamSlot | None = None
        self.delivered: DeliveryDecision | None = None
        self.boundaries: tuple[tuple[str, str], ...] = ()
        self.session: ResponseJobs | None = None
        self.collector: StaticCollector | None = None
        self.shell: dict[str, tuple[str | None, str]] = {}

    def release(self) -> None:
        """Give back the stream slot no stream response took."""
        slot, self.slot = self.slot, None
        if slot is not None:
            slot.release()


@contextmanager
def page_deferring(request: HttpRequest) -> Generator[PageRender]:
    """Gather the deferred calls of a page render with the sink that asks the policy.

    A failed render gives back the stream slot its policy took.
    """
    rendering = PageRender()
    marked = partial_streams.set(defer_manager.policy().streams_partials(request))
    try:
        with deferring(page_sink, request, rendering):
            yield rendering
    except BaseException:
        rendering.release()
        raise
    finally:
        partial_streams.reset(marked)


def _recorded(held: Deferring, session: ResponseJobs) -> PageRender | None:
    """Record the delivery and the boundaries of the page view, `None` outside one."""
    rendering = held.page
    delivered = held.delivered
    if rendering is None or delivered is None or rendering.session is not None:
        return None
    rendering.delivered = delivered
    rendering.boundaries = boundaries(session)
    return rendering


def _shelved(
    held: Deferring, html: str, collector: StaticCollector | None, session: ResponseJobs
) -> str | None:
    """Keep the jobs for the stream of the page view, returning the bare shell.

    It answers `None` when the response cannot stream them, so they settle inline.
    """
    rendering = _recorded(held, session)
    if rendering is None or held.delivered is None:
        return None
    if held.delivered.delivery is not Delivery.STREAM:
        return None
    live = prune(session, html)
    if not live.jobs:
        return None
    rendering.session = live
    rendering.collector = collector
    rendering.shell = wrappers(html, session.response_id)
    if collector is not None:
        collector.note(SPLIT_NOTE, None)
        collector.require(BOUNDARIES_CHUNK_KEY)
    return strip(html, session.response_id)


def _deliver(
    held: Deferring,
    html: str,
    collector: StaticCollector | None,
    request: HttpRequest | None,
) -> str:
    """Return `html` with the jobs `held` gathered run and spliced in, the shell alone.

    A `SHELL` or `PREFETCH` delivery answers the loading UI without its markers and
    runs no job, and a `STREAM` one leaves its jobs to the stream of the page view.
    """
    session = held.session
    try:
        if session is None or not session.jobs:
            return html
        delivered = held.delivered
        if delivered is not None and delivered.delivery in JOBLESS:
            _recorded(held, session)
            return strip(html, session.response_id)
        shelved = _shelved(held, html, collector, session)
        if shelved is None and request is not None:
            shelved = streamed(request, html, collector, session)
            if shelved is None and prune(session, html).jobs:
                _assembled_partial(request)
        if shelved is not None:
            return shelved
        return finish(html, session, collector, partial(batches, request))
    finally:
        held.take()


def assembled(
    render: Render,
    *,
    collector: StaticCollector | None,
    request: HttpRequest | None,
    context: MutableMapping[str, object] | None = None,
) -> str:
    """Return what `render` renders with every deferred call it queued settled in place.

    A page view's block puts its sink in `context`, a render inside a job runs as is.
    """
    if current_frame() is not None:
        return render()
    held = current_deferring()
    if held is not None:
        if held.sink is not None and context is not None:
            context[DEFERRALS_KEY] = held.sink
        return _deliver(held, _shared(render), collector, request)
    with deferring(request=request) as opened:
        return _deliver(opened, _shared(render), collector, request)


def _shared(render: Render) -> str:
    """Run `render` with one boundary state, so its wrappers share a single probe."""
    with render_boundaries():
        return render()


class InlineDeferrals(ComponentDeferrals):
    """The sink renders reach through `deferrals_slot`, its jobs run and spliced in."""

    __slots__ = ()

    def assembled(
        self,
        render: Render,
        *,
        collector: StaticCollector | None,
        request: HttpRequest | None,
        context: MutableMapping[str, object] | None = None,
    ) -> str:
        """Return what `render` renders with its deferred calls settled in place."""
        return assembled(render, collector=collector, request=request, context=context)


def _decided(request: HttpRequest, rendering: PageRender | None) -> DeliveryDecision:
    """Return how the page view delivers, taking a stream slot for a page stream.

    A partial render streams from its own block, which announces how the response went.
    """
    made = decision(request)
    delivered = made.delivered
    if delivered.delivery is Delivery.STREAM and rendering is not None:
        if made.facts.partial:
            return DeliveryDecision(Delivery.INLINE, DeliveryReason.PARTIAL)
        if take_slot(made, rendering):
            return delivered
        delivered = made.delivered
    if delivered.delivery is Delivery.INLINE:
        tell(request, made, delivered)
    return delivered


def _assembled_partial(request: HttpRequest) -> None:
    """Announce a partial response the policy let stream that no block streamed."""
    made = decided(request)
    if made is not None and made.facts.partial:
        tell(request, made, DeliveryDecision(Delivery.INLINE, DeliveryReason.PARTIAL))


class PageDeferrals(InlineDeferrals):
    """The sink a page view hands its render, asking the delivery policy once."""

    __slots__ = ()

    @override
    def queue(self, context: Context, call: DeferredCall) -> str:
        """Decide the delivery at the first queued call, then queue it."""
        held = current_deferring()
        if (
            trip_input(call) is None
            and held is not None
            and held.delivered is None
            and held.request is not None
            and current_frame() is None
        ):
            held.delivered = _decided(held.request, held.page)
        return super().queue(context, call)


inline_sink: Final = InlineDeferrals()
"""The sink `AppConfig.ready` binds to `deferrals_slot`."""

page_sink: Final = PageDeferrals()
"""The sink a page view puts in its render context."""


__all__ = [
    "InlineDeferrals",
    "PageDeferrals",
    "PageRender",
    "Render",
    "assembled",
    "inline_sink",
    "page_deferring",
    "page_sink",
]
