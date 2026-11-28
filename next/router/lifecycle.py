"""The part the client router takes in the requests a page answers.

It claims a navigation, gates it before the page is read, renders under the router
state, answers with the envelope of what changed and stamps its directives last.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import nullcontext
from functools import partial
from typing import TYPE_CHECKING, Final, cast

from django.http import HttpResponse

from next.conf.router import router_enabled
from next.intercept import intercepts
from next.lifecycle import Gate, ResponseSource, Share
from next.pages.responses import never_store
from next.partial.headers import SEC_FETCH_SITE, partial_intent, set_partial_vary
from next.partial.intercept import pick_intercept
from next.partial.lifecycle import stream_of
from next.requests import RequestKind, depth_of, purpose_of
from next.utils import request_header

from . import navigation
from .declarations import page_router
from .manager import client_router
from .markers import CutKind
from .navigation import (
    debug_cut,
    fragment_marking,
    navigation_envelope,
    origin_vary,
    page_marking,
    refused_prefetch,
    visit_envelope,
    zone_marking,
)


if TYPE_CHECKING:
    from contextlib import AbstractContextManager
    from types import TracebackType

    from django.http import HttpRequest
    from django.http.response import HttpResponseBase

    from next.lifecycle import Claiming, PageCall, PageFacts
    from next.pages.manager import Renderable
    from next.partial.stream import Deferred

    from .markers import Cut
    from .navigation import Compiler, Foreseen, Routing


NAME: Final = "router"
_FORBIDDEN: Final = 403
_SAME_ORIGIN: Final = "same-origin"
_UNMARKED: Final[AbstractContextManager[None]] = nullcontext()

type Fixed = Callable[[], Mapping[str, tuple[str, str | None]]]


class _Navigation:
    """What one navigation keeps from its render to its response."""

    __slots__ = ("cut", "foreseen", "routed", "streamed")

    routed: Routing
    cut: Cut

    def __init__(self, foreseen: Foreseen | None) -> None:
        """Hold the cut fixed chrome foresaw, the router state set once it opens."""
        self.foreseen = foreseen
        self.streamed = False


def _navigation(call: PageCall) -> _Navigation:
    """Return what the navigation of `call` keeps, opened by its block."""
    return cast("_Navigation", call.held[NAME])


class _Navigating:
    """The router block of one navigation, keeping its state on the call."""

    __slots__ = ("_call", "_held", "_inner")

    def __init__(
        self,
        call: PageCall,
        foreseen: Foreseen | None,
        fixed: Fixed,
        compiler: Compiler,
    ) -> None:
        """Open the router state, the containers compiled when the cut is foreseen."""
        self._call = call
        self._held = _Navigation(foreseen)
        request = call.request
        self._inner = navigation.routing(
            request,
            call.file_path,
            purpose_of(request),
            fragments=None if foreseen is None else compiler,
            fixed=fixed(),
        )

    def __enter__(self) -> None:
        """Bind the router state and keep it on the call."""
        self._held.routed = cast("Routing", self._inner.__enter__())
        self._call.held[NAME] = self._held

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Unbind the router state."""
        return self._inner.__exit__(exc_type, exc, traceback)


def _navigating(
    fixed: Fixed,
    slot: Callable[[str], Renderable | None] | None,
    compiler: Compiler,
    call: PageCall,
) -> AbstractContextManager[None]:
    """Return the block of a navigation, rendering the kept slot alone when it can.

    A page whose containers mark themselves and whose kept chrome is fixed renders
    the slot the cut keeps, and any other renders whole.
    """
    foreseen = None
    if slot is not None:
        foreseen = navigation.foresee(call.request, call.file_path, fixed())
        view = None if foreseen is None else _slot_view(foreseen, slot)
        if view is None:
            foreseen = None
        else:
            call.part = view
    return _Navigating(call, foreseen, fixed, compiler)


def _slot_view(
    foreseen: Foreseen, slot: Callable[[str], Renderable | None]
) -> Renderable | None:
    """Return the part of the page the foreseen cut keeps, `None` without a slot."""
    kept = foreseen.cut.slot
    return None if kept is None else slot(kept)


def _navigated(call: PageCall, html: str) -> HttpResponseBase:
    """Answer a navigation with the envelope of what changed, streamed when it defers.

    A page rendered whole cuts by the chrome a document load would show, and one
    foreseen rendered the kept slot alone.
    """
    block = stream_of(call)
    pending = None if block is None else block.deferred
    deferral = call.deferral
    try:
        return _enveloped(call, html, pending)
    finally:
        if deferral is not None:
            deferral.release()
        if pending is not None:
            pending.release()


def _enveloped(call: PageCall, html: str, pending: Deferred | None) -> HttpResponseBase:
    """Answer a navigation with its envelope, streaming what `pending` holds after.

    The caller frees each slot no stream took, a failing envelope's at once.
    """
    held = _navigation(call)
    routed = held.routed
    request, file_path, policy = call.request, call.file_path, call.policy
    foreseen = held.foreseen
    if foreseen is None:
        shell = routed.settled(html)
        cut = navigation.cleared(
            request,
            client_router.cut().cut(navigation.declaration(request), shell),
            shell,
        )
    else:
        shell = foreseen.shell(html, routed.response_id)
        cut = foreseen.settle(request, shell)
    deferral = call.deferral
    complete = depth_of(request) is not None and (
        deferral is None or deferral.complete()
    )
    shared = policy is not None and policy.shared
    patches = navigation_envelope(
        request, routed, shell, cut, shared=shared, complete=complete
    )
    deferred = None if cut.kind is CutKind.VISIT else pending
    held.cut = cut
    held.streamed = deferred is not None
    if deferred is None:
        return navigation.answered(patches)
    compress = policy is None or policy.compress
    return deferred.respond(patches.envelope(), page_path=file_path, compress=compress)


def _navigation_finished(
    call: PageCall, response: HttpResponseBase
) -> HttpResponseBase:
    """Finish a navigation answer, passing on a response its `render()` returned.

    A redirect reaches the router's fetch and anything else loads hard.
    """
    request = call.request
    if call.source is ResponseSource.BUILT:
        if navigation.redirects(response):
            return navigation.passed(response, request)
        return navigation.stamp(navigation.answered(visit_envelope(request)), request)
    held = _navigation(call)
    cut = held.cut
    if held.streamed:
        set_partial_vary(response)
    debug_cut(response, cut)
    navigation.stamp(response, request, snapshot=page_router(call.file_path).snapshot)
    navigation.announce(request, call.file_path, cut, streamed=held.streamed)
    return response


def _routed(
    fixed: Fixed, compiler: Compiler | None, call: PageCall
) -> AbstractContextManager[Routing | None]:
    """Return the router block of a document, its containers compiled when they can."""
    request = call.request
    return navigation.routing(
        request, call.file_path, purpose_of(request), fragments=compiler, fixed=fixed()
    )


def _fragments(compiler: Compiler, call: PageCall) -> AbstractContextManager[None]:
    """Return the block marking each container of a document as it renders."""
    return fragment_marking(call.request, compiler)


def _marking(call: PageCall) -> AbstractContextManager[None]:
    """Return the block marking the current links of a whole document."""
    return page_marking(call.request)


def _stamped(call: PageCall, response: HttpResponseBase) -> HttpResponseBase:
    """Stamp the router directives on the response of a document."""
    snapshot = page_router(call.file_path).snapshot
    return navigation.stamp(response, call.request, snapshot=snapshot)


def _zone_marking(call: PageCall, *, marks: bool) -> AbstractContextManager[None]:
    """Return the block marking the zone HTML, a plain one an inert page needs.

    An intercept serves a template the plan never read, so it is always marked.
    """
    if marks or intercepts.of(call.file_path):
        return zone_marking(call.request)
    return _UNMARKED


def _zone_finished(
    call: PageCall, response: HttpResponseBase, *, links: bool
) -> HttpResponseBase:
    """Vary a zone answer on the page the client shows and stamp the directives."""
    if links:
        origin_vary(response)
    return navigation.stamp(response, call.request)


def _cross_site(request: HttpRequest) -> bool:
    """Whether the browser reports the request as made from another site."""
    site = request_header(request, SEC_FETCH_SITE)
    return site is not None and site != _SAME_ORIGIN


def _gated(call: PageCall) -> HttpResponseBase | None:
    """Answer a navigation that needs no render, `None` to render the page.

    Another site, a stale version, a layer, an opt-out, a busy pool or `//` stops it.
    """
    request, file_path = call.request, call.file_path
    if _cross_site(request):
        refused = HttpResponse(b"", status=_FORBIDDEN)
        never_store(refused)
        set_partial_vary(refused)
        return refused
    conflict: HttpResponseBase | None = navigation.stale(request)
    if conflict is None:
        conflict = navigation.intercepted(request, file_path)
    if conflict is not None:
        return conflict
    options = page_router(file_path)
    if partial_intent(request).prefetch:
        if not (options.prefetch and options.navigate):
            return refused_prefetch()
        lagging = navigation.shed(request, file_path)
        if lagging is not None:
            return lagging
    if not options.navigate or navigation.hostlike(request):
        return navigation.stamp(navigation.answered(visit_envelope(request)), request)
    return None


def _zone_gated(call: PageCall) -> HttpResponseBase | None:
    """Answer a zone prefetch its page refuses or a busy pool sheds, else `None`.

    A layer prefetch renders what the layer would, so a serving intercept may refuse.
    """
    request, file_path = call.request, call.file_path
    intent = partial_intent(request)
    if intent.prefetch is None:
        return None
    rule = pick_intercept(file_path, intent.zones)
    served = file_path if rule is None else rule.file
    if not (page_router(file_path).prefetch and page_router(served).prefetch):
        return refused_prefetch()
    return navigation.shed(request, file_path)


def _refused(call: PageCall, denial: HttpResponseBase) -> HttpResponseBase:
    """Return the refusal of a navigation, private, varied and stamped."""
    return navigation.passed(denial, call.request)


def _claimed(request: HttpRequest) -> RequestKind | None:
    """Return `NAVIGATE` for a navigation the router is on to answer."""
    navigates = partial_intent(request).navigate
    return RequestKind.NAVIGATE if navigates and router_enabled() else None


_NAVIGATION_GATE: Final = Gate(admit=_gated, refuse=_refused)
_ZONE_GATE: Final = Gate(admit=_zone_gated)
_GATES: Final = {RequestKind.NAVIGATE: _NAVIGATION_GATE, RequestKind.ZONE: _ZONE_GATE}


class RouterParticipant:
    """The client router's part in the requests a page answers."""

    name = NAME

    def claim(self) -> Claiming:
        """Return the claim of a navigation."""
        return _claimed

    def gate(self, kind: RequestKind) -> Gate | None:
        """Return the gate of a navigation and of a zone prefetch."""
        return _GATES.get(kind)

    def share(self, facts: PageFacts) -> Share | None:
        """Return the router block and finish of a document, navigation or zone."""
        kind = facts.kind
        if kind is RequestKind.PAGE:
            return self._document(facts)
        if kind is RequestKind.NAVIGATE:
            settled = facts.chain is not None and not (
                facts.chain.errors or facts.chain.deferred
            )
            slot = facts.slot if facts.fragments and settled else None
            return Share(
                block=partial(_navigating, facts.fixed, slot, facts.compiler),
                respond=_navigated,
                finish=_navigation_finished,
            )
        if kind is RequestKind.ZONE:
            straight = facts.plain and facts.template is not None
            return Share(
                block=partial(_zone_marking, marks=facts.marks or not straight),
                finish=partial(_zone_finished, links=facts.links),
            )
        return None

    def _document(self, facts: PageFacts) -> Share | None:
        """Return the router block and stamp of a document, the link pass alone off."""
        if router_enabled():
            compiler = facts.compiler if facts.plain and facts.fragments else None
            return Share(block=partial(_routed, facts.fixed, compiler), finish=_stamped)
        if not facts.plain:
            return Share(block=_marking) if facts.links else None
        if facts.fragments:
            return Share(block=partial(_fragments, facts.compiler))
        return Share(block=_marking) if facts.marks else None


__all__ = ["NAME", "RouterParticipant"]
