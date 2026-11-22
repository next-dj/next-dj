"""The sinks deferred calls reach and the delivery a render settles its jobs by."""

from collections.abc import Callable, Iterator, MutableMapping
from contextlib import contextmanager
from contextvars import ContextVar
from functools import partial
from typing import Final, override

from django.http import HttpRequest
from django.template import Context

from next.boundaries import render_boundaries
from next.defer import DEFERRALS_KEY, JOBLESS, DeferredCall, Delivery
from next.static.collector import StaticCollector
from next.static.inject import SPLIT_NOTE
from next.static.runtime import READER_CHUNK_KEY, partial_streams
from next.suspense.components import (
    ComponentDeferrals,
    Deferring,
    current_deferring,
    deferring,
)
from next.suspense.manager import suspense_manager
from next.suspense.policy import (
    Delivered,
    DeliveryReason,
    PageFacts,
    declined,
    page_facts,
)
from next.suspense.render import Delta, current_frame
from next.suspense.response import strip, wrappers
from next.suspense.session import BoundarySession
from next.suspense.trip import trip_input

from .inline import finish, prune
from .jobs import Reserved, batches, boundaries, reserved
from .partials import streamed


type Render = Callable[[], str]


class PageRender(Reserved):
    """What a page render leaves its view, a stream to deliver or why it is not one."""

    __slots__ = ("boundaries", "collector", "delivered", "session", "shell")

    def __init__(self) -> None:
        """Hold nothing until the render queues a deferred call."""
        super().__init__()
        self.delivered: Delivered | None = None
        self.boundaries: tuple[tuple[str, str], ...] = ()
        self.session: BoundarySession[Delta] | None = None
        self.collector: StaticCollector | None = None
        self.shell: dict[str, tuple[str | None, str]] = {}


_rendering: ContextVar[PageRender | None] = ContextVar("next_page_render", default=None)


@contextmanager
def page_deferring(request: HttpRequest) -> Iterator[PageRender]:
    """Gather the deferred calls of a page render with the sink that asks the policy.

    A failed render gives back the stream slot its policy took.
    """
    rendering = PageRender()
    token = _rendering.set(rendering)
    marked = partial_streams.set(suspense_manager.policy().streams_partials(request))
    try:
        with deferring(page_sink, request):
            yield rendering
    except BaseException:
        rendering.release()
        raise
    finally:
        partial_streams.reset(marked)
        _rendering.reset(token)


def _recorded(held: Deferring, session: BoundarySession[Delta]) -> PageRender | None:
    """Record the delivery and the boundaries of the page view, `None` outside one."""
    rendering = _rendering.get()
    delivered = held.delivered
    if rendering is None or delivered is None or rendering.session is not None:
        return None
    rendering.delivered = delivered
    rendering.boundaries = boundaries(session)
    return rendering


def _shelved(
    held: Deferring,
    html: str,
    collector: StaticCollector | None,
    session: BoundarySession[Delta],
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
    rendering.shell = wrappers(html, session.seal)
    if collector is not None:
        collector.note(SPLIT_NOTE, None)
        collector.require(READER_CHUNK_KEY)
    return strip(html, session.seal)


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
            return strip(html, session.seal)
        shelved = _shelved(held, html, collector, session)
        if shelved is None and request is not None:
            shelved = streamed(request, html, collector, session)
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


def _decided(request: HttpRequest) -> Delivered:
    """Ask the policy how `request` delivers, announcing a page assembled inline."""
    facts = page_facts(request)
    delivered = _delivery_with_slot(request, facts)
    if delivered.delivery is Delivery.INLINE:
        declined(request, delivered, facts)
    return delivered


def _delivery_with_slot(request: HttpRequest, facts: PageFacts) -> Delivered:
    """Return the delivery of the policy, taking a stream slot for a stream.

    A stream past `MAX_STREAMS` is assembled inline after all.
    """
    delivered = suspense_manager.policy().delivery(request, facts)
    rendering = _rendering.get()
    if delivered.delivery is not Delivery.STREAM or rendering is None:
        return delivered
    if facts.partial:
        return Delivered(Delivery.INLINE, DeliveryReason.PARTIAL)
    slot = reserved(facts)
    if slot is None:
        return Delivered(Delivery.INLINE, DeliveryReason.CAPACITY)
    rendering.hold(slot)
    return delivered


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
            held.delivered = _decided(held.request)
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
