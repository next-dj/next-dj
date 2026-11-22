"""The deferred jobs of a streamed partial render and the stream that runs them."""

from pathlib import Path

from django.http import HttpRequest
from django.http.response import HttpResponseBase

from next.defer import Delivery
from next.partial.envelope import Envelope
from next.partial.stream import Streamable, current_streamable
from next.static.collector import StaticCollector
from next.suspense.manager import suspense_manager
from next.suspense.partial import PartialStream
from next.suspense.policy import Delivered, DeliveryReason, declined, page_facts
from next.suspense.render import Delta
from next.suspense.response import Payloads, strip, wrappers
from next.suspense.session import BoundarySession

from .inline import prune
from .jobs import Reserved, Source, boundaries, prepare, reserved


class PartialRender(Reserved):
    """The deferred jobs a streamed partial render left, run by the stream it answers.

    Each render inside the block shelves its own session, joined into one at the end.
    """

    __slots__ = ("collector", "delivered", "request", "sessions", "shell")

    def __init__(self, request: HttpRequest, delivered: Delivered) -> None:
        """Hold no session until a render shelves one."""
        super().__init__()
        self.request = request
        self.delivered = delivered
        self.sessions: list[BoundarySession[Delta]] = []
        self.shell: dict[str, tuple[str | None, str]] = {}
        self.collector: StaticCollector | None = None

    def shelve(
        self,
        session: BoundarySession[Delta],
        html: str,
        collector: StaticCollector | None,
    ) -> str:
        """Keep the jobs of `session` for the stream, returning the bare loading UI."""
        self.sessions.append(session)
        self.shell.update(wrappers(html, session.seal))
        if self.collector is None:
            self.collector = collector
        return strip(html, session.seal)

    def respond(
        self,
        envelope: Envelope,
        *,
        page_path: Path | None = None,
        compress: bool = True,
    ) -> HttpResponseBase:
        """Settle the request, then return the stream opening with `envelope`."""
        request = self.request
        try:
            session = _joined(self.sessions)
            prepare(session, request, stream=True)
            payloads = Payloads(request, shell=self.shell, collector=self.collector)
            response = PartialStream(
                request,
                envelope,
                Source(session, request),
                payloads,
                deadline=session.deadline,
                grant=self.grant(self.delivered, boundaries(session), page_path),
                compress=compress,
            )
            self.disown()
            return response
        finally:
            self.release()


def _joined(sessions: list[BoundarySession[Delta]]) -> BoundarySession[Delta]:
    """Return one session holding the jobs of every shelved one, in render order.

    A job keeps the session it was queued in for the ids of its nested jobs.
    """
    first, *rest = sessions
    if not rest:
        return first
    joined = BoundarySession[Delta](
        first.caps,
        seal=first.seal,
        deadline=min(session.deadline for session in sessions),
    )
    for session in sessions:
        for job in session.jobs:
            joined.queue(job)
    return joined


def _opened(request: HttpRequest) -> PartialRender | None:
    """Ask the policy whether a partial render streams, reserving its slot.

    It answers `None` when the policy or a full `MAX_STREAMS` holds the stream back.
    """
    facts = page_facts(request)
    delivered = suspense_manager.policy().delivery(request, facts)
    if delivered.delivery is Delivery.INLINE:
        declined(request, delivered, facts)
    if delivered.delivery is not Delivery.STREAM:
        return None
    slot = reserved(facts)
    if slot is None:
        declined(request, Delivered(Delivery.INLINE, DeliveryReason.CAPACITY), facts)
        return None
    rendering = PartialRender(request, delivered)
    rendering.hold(slot)
    return rendering


def _rendering_of(block: Streamable, request: HttpRequest) -> PartialRender | None:
    """Return the render the block streams, asking the policy at its first job."""
    held = block.deferred
    if isinstance(held, PartialRender):
        return held
    if block.asked:
        return None
    block.asked = True
    rendering = _opened(request)
    block.deferred = rendering
    return rendering


def streamed(
    request: HttpRequest,
    html: str,
    collector: StaticCollector | None,
    session: BoundarySession[Delta],
) -> str | None:
    """Keep the jobs for the stream of a partial response, returning the loading UI.

    It answers `None` outside a streamable block or when the response cannot stream.
    """
    block = current_streamable()
    if block is None:
        return None
    live = prune(session, html)
    rendering = _rendering_of(block, request) if live.jobs else None
    if rendering is None:
        return None
    return rendering.shelve(live, html, collector)


__all__ = ["PartialRender", "streamed"]
