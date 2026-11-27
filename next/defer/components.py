"""The glue between a deferred component call, its job and the session of a response."""

from __future__ import annotations

import functools
import logging
import time
from collections.abc import Generator, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from io import IOBase
from typing import TYPE_CHECKING, Any, Final, cast

from django.conf import settings
from django.http import HttpRequest
from django.utils.html import escape
from django.utils.translation import gettext

from next.boundaries.failures import ErrorKind
from next.boundaries.ids import current_deferral, deferral, new_response_id, wire_id
from next.boundaries.markup import note_retry
from next.boundaries.options import DeferMode
from next.boundaries.scope import wrapping
from next.caches import FORK
from next.pages.responses import mark_personal_render
from next.partial.headers import partial_intent
from next.partial.keys import NOSCRIPT_ATTR, NOSCRIPT_PARAM
from next.seeding import COLLECTOR_KEY, REQUEST_KEY
from next.static.collector import StaticCollector, require_bundle
from next.static.runtime import BOUNDARIES_CHUNK_KEY

from .ledger import ResponseJobs
from .markers import Caps, Job, Rendered
from .policy import InlineNotice
from .render import Frame, current_frame, error_ui, run, unframed
from .response import idle, in_place, shell
from .state import state_of
from .trip import retry_attributes, retry_token, shell_token, trip_input


if TYPE_CHECKING:
    from django.template import Context, Template

    from next.boundaries.calls import BoundarySite, DeferredCall
    from next.components.trip import TripInput
    from next.ports import DeferralSink

    from .assembly.delivery import PageRender
    from .policy import DeliveryDecision


logger = logging.getLogger("next.defer")


@dataclass(frozen=True, slots=True)
class SiteCapture:
    """What a job reads of its call site, copied where the template engine mutates."""

    data: Mapping[str, Any]
    template: Template | None
    autoescape: bool
    use_l10n: bool | None
    use_tz: bool | None


def _copied(loop: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a `forloop` dict, its `parentloop` chain copied as well."""
    copy = dict(loop)
    parent = copy.get("parentloop")
    if isinstance(parent, dict):
        copy["parentloop"] = _copied(parent)
    return copy


def _one_shot(value: object) -> bool:
    """Whether `value` is an iterator a first pass over it would exhaust."""
    return isinstance(value, Iterator) and not isinstance(
        value, Sequence | Mapping | IOBase
    )


def _materialise(context: Context, data: dict[str, Any]) -> None:
    """Turn each top-level one-shot iterator into a tuple the shell reads as well."""
    for key, value in tuple(data.items()):
        if not _one_shot(value):
            continue
        items = data[key] = tuple(value)
        holder = next(layer for layer in reversed(context.dicts) if key in layer)
        holder[key] = items


def capture(context: Context) -> SiteCapture:
    """Return the call-site input of a job, every `forloop` dict copied by value.

    `ForNode` mutates one dict per loop, which a flattened context only references.
    """
    data = cast("dict[str, Any]", context.flatten())
    _materialise(context, data)
    loop = data.get("forloop")
    if isinstance(loop, dict):
        data["forloop"] = _copied(loop)
    template = context.render_context.template or context.template
    return SiteCapture(
        data, template, context.autoescape, context.use_l10n, context.use_tz
    )


class Deferring:
    """The deferred calls of one response render, its session opened by the first.

    `sink` is what a page view puts in the context, `delivered` what its policy chose,
    and `page` the render a page view records its stream on.
    """

    __slots__ = ("_session", "delivered", "page", "request", "sink")

    def __init__(
        self,
        sink: DeferralSink | None = None,
        request: HttpRequest | None = None,
        page: PageRender | None = None,
    ) -> None:
        """Hold no session until a call needs one."""
        self._session: ResponseJobs | None = None
        self.sink = sink
        self.request = request
        self.page = page
        self.delivered: DeliveryDecision | None = None

    @property
    def session(self) -> ResponseJobs | None:
        """Return the session, `None` when no call of the render needed one."""
        return self._session

    def open(self) -> ResponseJobs:
        """Return the session, opening it on the first call."""
        session = self._session
        if session is None:
            session = self._session = ResponseJobs.open(Caps.from_settings())
        return session

    def minted(self) -> str | None:
        """Return the next wire id of the session, `None` while none is open."""
        session = self._session
        return None if session is None else session.next_id()

    def take(self) -> ResponseJobs | None:
        """Return the session and let go of it, so a later render queues afresh."""
        session, self._session = self._session, None
        return session


class _Aside:
    """The render session of a loading UI, which mints ids but gathers no call.

    A deferred call inside it renders in place, as no one assembles it.
    """

    __slots__ = ("held",)

    def __init__(self, held: Deferring | _Aside) -> None:
        """Stand beside the render session `held`."""
        self.held = held

    @property
    def page(self) -> PageRender | None:
        """Return the page render of the session beside it."""
        return self.held.page

    def minted(self) -> str | None:
        """Return the next wire id of the session beside it."""
        return self.held.minted()


def current_deferring() -> Deferring | None:
    """Return the block gathering deferred calls in this context, `None` outside one."""
    held = current_deferral()
    return held if isinstance(held, Deferring) else None


def _enclosing() -> Deferring | _Aside | None:
    held = current_deferral()
    return held if isinstance(held, Deferring | _Aside) else None


@contextmanager
def deferring(
    sink: DeferralSink | None = None,
    request: HttpRequest | None = None,
    page: PageRender | None = None,
) -> Generator[Deferring]:
    """Gather the deferred calls of the render inside the block into one session.

    Outside such a block a deferred call renders in place, as no one assembles it. A
    block inside a page render records on that page unless it names its own.
    """
    if page is None:
        outer = _enclosing()
        page = None if outer is None else outer.page
    held = Deferring(sink, request, page)
    with deferral(held):
        yield held


@contextmanager
def _undeferred() -> Generator[None]:
    """Render the block outside every `deferring` block, as a loading UI renders."""
    outer = _enclosing()
    with deferral(None if outer is None else _Aside(outer)):
        yield


def _ident() -> str:
    """Return the id of a wrapper rendered in place, from the session when one runs."""
    frame = current_frame()
    if frame is not None:
        return frame.session.next_id()
    held = current_deferring()
    if held is not None:
        return held.open().next_id()
    return wire_id(new_response_id(), 0)


def _collector(context: Context) -> StaticCollector | None:
    """Return the collector of a render, `None` for a render without one."""
    collector = context.get(COLLECTOR_KEY)
    return collector if isinstance(collector, StaticCollector) else None


def _request(context: Context) -> object:
    """Return the request of a render, the one its `deferring` block holds else."""
    request = context.get(REQUEST_KEY)
    if request is None:
        held = current_deferring()
        request = None if held is None else held.request
    return request


def _opened() -> ResponseJobs:
    return ResponseJobs.open(Caps.from_settings())


def _take_instance(request: object) -> bool:
    """Count one more client-trip wrapper, false once `MAX_INSTANCES` is spent.

    The wrapper counts on the session its stream jobs count on, in document order.
    """
    if not isinstance(request, HttpRequest):
        return True
    frame = current_frame()
    if frame is not None:
        return frame.session.take_instance()
    held = current_deferring()
    session = state_of(request).held(_opened) if held is None else held.open()
    return session.take_instance()


def _noscript(request: object) -> str:
    """Return the `<noscript>` link of an idle wrapper, built once per document.

    It leads a reader without scripts to the page with its client trips in place.
    """
    if not isinstance(request, HttpRequest) or partial_intent(request).partial:
        return ""
    state = state_of(request)
    link = state.noscript
    if link is not None:
        return link
    query = request.GET.copy()
    query[NOSCRIPT_PARAM] = "1"
    href = escape(f"?{query.urlencode()}")
    label = escape(gettext("Show this content"))
    link = state.noscript = (
        f'<noscript><a href="{href}" rel="nofollow" {NOSCRIPT_ATTR}>{label}</a>'
        "</noscript>"
    )
    return link


def _streamed(call: DeferredCall) -> DeferredCall:
    """Return `call` delivered as a stream boundary of the response under way."""
    site = replace(call.site, mode=DeferMode.STREAM)
    return replace(call, site=site, options=replace(call.options, mode=site.mode))


def _run(call: DeferredCall, taken: SiteCapture, frame: Frame, at: float) -> Rendered:
    """Run the job of `call`, its boundary reporting a failure under the job's id."""
    with wrapping(frame.ident):
        return run(call, taken, frame, at)


def _job(
    call: DeferredCall,
    session: ResponseJobs,
    context: Context,
    parent: Frame | None = None,
) -> Job:
    """Return the job of `call`, its input captured from `context` now."""
    taken = capture(context)
    ident = session.next_id()
    at = session.deadline_for(call.options, time.monotonic())
    depth = 0 if parent is None else parent.depth + 1
    return Job(
        id=ident,
        key=call.key,
        site=call.site,
        options=call.options,
        deadline=at,
        error_ui=functools.partial(error_ui, call, taken),
        run=functools.partial(_run, call, taken, Frame(session, ident, depth), at),
        parent=None if parent is None else parent.ident,
        depth=depth,
    )


skipped_notice: Final = InlineNotice()
"""The once-per-site note of a nested call that skipped its loading UI."""

FORK.attach(skipped_notice.forget)


def _note_skipped(site: BoundarySite) -> None:
    """Log once per site under `DEBUG` that a nested call skipped its loading UI."""
    if not settings.DEBUG or not skipped_notice.take(f"{site.origin}:{site.name}"):
        return
    logger.debug(
        "Component %r in %s renders in place inside its parent's job, as it is "
        'nested past DEFER["MAX_DEPTH"], so its loading UI is skipped.',
        site.name,
        site.origin,
    )


class ComponentDeferrals:
    """What every sink shares, queueing into the session a `deferring` block holds.

    A call inside a job nests in it, `MAX_DEPTH` deep and counted in `MAX_INSTANCES`.
    """

    __slots__ = ()

    def queue(self, context: Context, call: DeferredCall) -> str:
        """Queue a job for `call` and return its loading wrapper, or render in place.

        A client-trip call queues no job, its wrapper carrying a token instead.
        """
        trip = trip_input(call)
        if trip is not None:
            return self.trip(context, call, trip)
        frame = current_frame()
        if frame is not None:
            if frame.depth + 1 >= frame.session.caps.depth:
                _note_skipped(call.site)
                return self.inline(context, call)
            if not frame.session.take_instance():
                return self.expired(context, call)
            job = _job(call, frame.session, context, frame)
            frame.nested.append(job)
        else:
            held = current_deferring()
            if held is None:
                return self.inline(context, call)
            session = held.open()
            job = _job(call, session, context)
            session.queue(job)
        with unframed(), _undeferred(), wrapping(job.id):
            loading, failure = call.renderer.loading(context)
        return shell(job.id, call, loading, failure)

    def trip(self, context: Context, call: DeferredCall, trip: TripInput) -> str:
        """Return the idle wrapper of a client-trip call, its token minted now.

        A page asked for with `NOSCRIPT_PARAM` renders the call in place and privately.
        """
        request = _request(context)
        if not _take_instance(request):
            return self.expired(context, call)
        if isinstance(request, HttpRequest) and request.GET.get(NOSCRIPT_PARAM) == "1":
            mark_personal_render(request)
            return self.inline(context, call)
        token = shell_token(request, trip)
        if token is None:
            return self.queue(context, _streamed(call))
        require_bundle(
            context, BOUNDARIES_CHUNK_KEY, urgent=trip.mode is DeferMode.LOAD
        )
        ident = _ident()
        with unframed(), _undeferred(), wrapping(ident):
            loading, refused = call.renderer.loading(context)
        link = "" if refused is not None else _noscript(request)
        if call.tag is not None:
            loading, link = loading + link, ""
        wrapper = idle(
            ident, call, loading, mode=trip.mode, token=token, failure=refused
        )
        return wrapper + link

    def expired(self, context: Context, call: DeferredCall) -> str:
        """Return the settled `timeout` wrapper of a call past `MAX_INSTANCES`."""
        ident = _ident()
        with unframed(), _undeferred():
            html, failure = call.renderer.error(context, ErrorKind.TIMEOUT)
        note_retry(_collector(context), html, "")
        return in_place(ident, call, html, failure)

    def retry(self, request: object, trip: TripInput) -> str:
        """Return the token attributes a failed plain call retries with, or nothing."""
        return retry_attributes(request, trip)

    def inline(self, context: Context, call: DeferredCall) -> str:
        """Render `call` in place inside its settled wrapper."""
        ident = _ident()
        with wrapping(ident):
            html, failure = call.renderer.body(context)
        retry = "" if failure is None else retry_token(_request(context), call, failure)
        note_retry(_collector(context), "", retry)
        return in_place(ident, call, html, failure, retry)


__all__ = [
    "ComponentDeferrals",
    "Deferring",
    "SiteCapture",
    "capture",
    "current_deferring",
    "deferring",
    "skipped_notice",
]
