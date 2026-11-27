"""The deferred jobs of a streamed partial render and the stream that runs them."""

from dataclasses import dataclass
from pathlib import Path

from django.http import HttpRequest
from django.http.response import HttpResponseBase

from next.defer.ledger import ResponseJobs
from next.defer.manager import defer_manager
from next.defer.markers import Delivery
from next.defer.policy import (
    DeliveryDecision,
    DeliveryFacts,
    DeliveryReason,
    StreamSlot,
    declined,
    delivery_facts,
)
from next.defer.response import PartialStream, Payloads, strip, wrappers
from next.defer.state import state_of
from next.partial.envelope import Envelope
from next.partial.stream import Streamable, current_streamable
from next.static.collector import StaticCollector

from .inline import prune
from .jobs import Holder, StreamSource, boundaries, grant, prepare, reserved


@dataclass(slots=True)
class Decision:
    """The delivery the policy chose for one response, and whether it was announced.

    A full `MAX_STREAMS` turns a stream inline, and the response is announced once.
    """

    facts: DeliveryFacts
    delivered: DeliveryDecision
    told: bool = False


def decision(request: HttpRequest) -> Decision:
    """Return the delivery of the response to `request`, the policy asked only once."""
    state = state_of(request)
    made = state.decision
    if made is None:
        facts = delivery_facts(request)
        delivered = defer_manager.policy().delivery(request, facts)
        made = state.decision = Decision(facts, delivered)
    return made


def decided(request: HttpRequest) -> Decision | None:
    """Return the delivery the policy already chose for `request`, `None` before."""
    return state_of(request).decision


def tell(
    request: HttpRequest, made: Decision, delivered: DeliveryDecision | None
) -> None:
    """Settle the response as streamed, or as assembled inline under `delivered`.

    Only the first call announces, so one response reports one delivery.
    """
    if made.told:
        return
    made.told = True
    if delivered is not None:
        made.delivered = delivered
        declined(request, delivered, made.facts)


def take_slot(made: Decision, holder: Holder) -> bool:
    """Give `holder` a stream slot, false when `MAX_STREAMS` turns the stream inline."""
    slot = reserved(made.facts)
    if slot is None:
        made.delivered = DeliveryDecision(Delivery.INLINE, DeliveryReason.CAPACITY)
        return False
    holder.slot = slot
    return True


class PartialRender:
    """The deferred jobs a streamed partial render left, run by the stream it answers.

    Each render inside the block shelves its own session, joined into one at the end.
    """

    __slots__ = ("collector", "delivered", "request", "sessions", "shell", "slot")

    def __init__(self, request: HttpRequest, delivered: DeliveryDecision) -> None:
        """Hold no session until a render shelves one."""
        self.slot: StreamSlot | None = None
        self.request = request
        self.delivered = delivered
        self.sessions: list[ResponseJobs] = []
        self.shell: dict[str, tuple[str | None, str]] = {}
        self.collector: StaticCollector | None = None

    def release(self) -> None:
        """Give back the stream slot no stream response took."""
        slot, self.slot = self.slot, None
        if slot is not None:
            slot.release()

    def shelve(
        self, session: ResponseJobs, html: str, collector: StaticCollector | None
    ) -> str:
        """Keep the jobs of `session` for the stream, returning the bare loading UI."""
        self.sessions.append(session)
        self.shell.update(wrappers(html, session.response_id))
        if self.collector is None:
            self.collector = collector
        return strip(html, session.response_id)

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
                StreamSource(session, request),
                payloads,
                deadline=session.deadline,
                grant=grant(self, self.delivered, boundaries(session), page_path),
                compress=compress,
            )
            self.slot = None
            return response
        finally:
            self.release()


def _joined(sessions: list[ResponseJobs]) -> ResponseJobs:
    """Return one session holding the jobs of every shelved one, in render order.

    A job keeps the session it was queued in for the ids of its nested jobs.
    """
    first, *rest = sessions
    if not rest:
        return first
    joined = ResponseJobs(
        first.caps,
        response_id=first.response_id,
        deadline=min(session.deadline for session in sessions),
    )
    for session in sessions:
        for job in session.jobs:
            joined.queue(job)
    return joined


def _opened(request: HttpRequest) -> PartialRender | None:
    """Return the stream of a partial render the policy chose, reserving its slot.

    It answers `None` when the policy or a full `MAX_STREAMS` holds the stream back.
    """
    made = decision(request)
    delivered = made.delivered
    if delivered.delivery is Delivery.INLINE:
        tell(request, made, delivered)
    if delivered.delivery is not Delivery.STREAM:
        return None
    rendering = PartialRender(request, delivered)
    if not take_slot(made, rendering):
        tell(request, made, made.delivered)
        return None
    tell(request, made, None)
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
    session: ResponseJobs,
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


__all__ = [
    "Decision",
    "PartialRender",
    "decided",
    "decision",
    "streamed",
    "take_slot",
    "tell",
]
