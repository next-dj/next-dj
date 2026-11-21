"""The glue between a deferred component call, its job and the session of a response."""

from __future__ import annotations

import functools
import logging
import secrets
import threading
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from io import IOBase
from typing import TYPE_CHECKING, Any, Final, cast

from django.conf import settings
from django.http import HttpRequest
from django.utils.html import escape
from django.utils.translation import gettext

from next.boundaries import minting, note_retry, wrapping
from next.defer import DeferMode, ErrorKind
from next.pages.responses import mark_personal_render
from next.partial.headers import partial_intent
from next.seeding import COLLECTOR_KEY, REQUEST_KEY
from next.static.bundles import require_bundle
from next.static.collector import StaticCollector
from next.static.runtime import DEFER_CHUNK_KEY
from next.suspense.markers import Caps, Job, Rendered
from next.suspense.render import Delta, Frame, current_frame, fallback, run, unframed
from next.suspense.response import idle, settled, shell
from next.suspense.session import BoundarySession
from next.suspense.trip import retry_attributes, retry_token, shell_token, trip_input


if TYPE_CHECKING:
    from django.template import Context, Template

    from next.components.trip import TripInput
    from next.defer import DeferredCall, Site
    from next.ports import DeferralSink
    from next.suspense.policy import Delivered


logger = logging.getLogger("next.suspense")

_SEAL_BYTES: Final = 4
_SESSION_ATTR: Final = "_next_trip_session"
_NOSCRIPT_ATTR: Final = "_next_noscript_link"

NOSCRIPT_PARAM: Final = "_nojs"
"""The query flag of a page render that resolves every client-trip boundary in place."""


@dataclass(frozen=True, slots=True)
class Capture:
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


def capture(context: Context) -> Capture:
    """Return the call-site input of a job, every `forloop` dict copied by value.

    `ForNode` mutates one dict per loop, which a flattened context only references.
    """
    data = cast("dict[str, Any]", context.flatten())
    _materialise(context, data)
    loop = data.get("forloop")
    if isinstance(loop, dict):
        data["forloop"] = _copied(loop)
    template = context.render_context.template or context.template
    return Capture(data, template, context.autoescape, context.use_l10n, context.use_tz)


class Deferring:
    """The deferred calls of one response render, its session opened by the first.

    `sink` is what a page view puts in the context, `delivered` what its policy chose.
    """

    __slots__ = ("_session", "delivered", "request", "sink")

    def __init__(
        self, sink: DeferralSink | None = None, request: HttpRequest | None = None
    ) -> None:
        """Hold no session until a call needs one."""
        self._session: BoundarySession[Delta] | None = None
        self.sink = sink
        self.request = request
        self.delivered: Delivered | None = None

    @property
    def session(self) -> BoundarySession[Delta] | None:
        """Return the session, `None` when no call of the render needed one."""
        return self._session

    def open(self) -> BoundarySession[Delta]:
        """Return the session, opening it on the first call."""
        session = self._session
        if session is None:
            session = self._session = BoundarySession[Delta].open(Caps.from_settings())
        return session

    def minted(self) -> str | None:
        """Return the next wire id of the session, `None` while none is open."""
        session = self._session
        return None if session is None else session.next_id()

    def take(self) -> BoundarySession[Delta] | None:
        """Return the session and let go of it, so a later render queues afresh."""
        session, self._session = self._session, None
        return session


_deferring: ContextVar[Deferring | None] = ContextVar("next_deferring", default=None)


def current_deferring() -> Deferring | None:
    """Return the block gathering deferred calls in this context, `None` outside one."""
    return _deferring.get()


@contextmanager
def deferring(
    sink: DeferralSink | None = None, request: HttpRequest | None = None
) -> Iterator[Deferring]:
    """Gather the deferred calls of the render inside the block into one session.

    Outside such a block a deferred call renders in place, as no one assembles it.
    """
    held = Deferring(sink, request)
    token = _deferring.set(held)
    try:
        with minting(held.minted):
            yield held
    finally:
        _deferring.reset(token)


@contextmanager
def _undeferred() -> Iterator[None]:
    """Render the block outside every `deferring` block, as a loading UI renders."""
    token = _deferring.set(None)
    try:
        yield
    finally:
        _deferring.reset(token)


def _ident() -> str:
    """Return the id of a wrapper rendered in place, from the session when one runs."""
    frame = current_frame()
    if frame is not None:
        return frame.session.next_id()
    held = _deferring.get()
    if held is not None:
        return held.open().next_id()
    return f"b-{secrets.token_hex(_SEAL_BYTES)}-0"


def _collector(context: Context) -> StaticCollector | None:
    """Return the collector of a render, `None` for a render without one."""
    collector = context.get(COLLECTOR_KEY)
    return collector if isinstance(collector, StaticCollector) else None


def _request(context: Context) -> object:
    """Return the request of a render, the one its `deferring` block holds else."""
    request = context.get(REQUEST_KEY)
    if request is None:
        held = _deferring.get()
        request = None if held is None else held.request
    return request


_held_lock = threading.Lock()


def _held_session(request: HttpRequest) -> BoundarySession[Delta]:
    """Return the session a render outside a `deferring` block counts wrappers on."""
    with _held_lock:
        held = getattr(request, _SESSION_ATTR, None)
        if not isinstance(held, BoundarySession):
            held = BoundarySession[Delta].open(Caps.from_settings())
            setattr(request, _SESSION_ATTR, held)
        return held


def _admitted(request: object) -> bool:
    """Whether one more client-trip wrapper fits `MAX_INSTANCES` of its response.

    The wrapper counts on the session its stream jobs count on, in document order.
    """
    if not isinstance(request, HttpRequest):
        return True
    frame = current_frame()
    if frame is not None:
        return frame.session.admit()
    held = _deferring.get()
    session = _held_session(request) if held is None else held.open()
    return session.admit()


def _noscript(request: object) -> str:
    """Return the `<noscript>` link of an idle wrapper, built once per document.

    It leads a reader without scripts to the page with its client trips in place.
    """
    if not isinstance(request, HttpRequest) or partial_intent(request).partial:
        return ""
    link = getattr(request, _NOSCRIPT_ATTR, None)
    if isinstance(link, str):
        return link
    query = request.GET.copy()
    query[NOSCRIPT_PARAM] = "1"
    href = escape(f"?{query.urlencode()}")
    label = escape(gettext("Show this content"))
    link = (
        f'<noscript><a href="{href}" rel="nofollow" data-next-noscript>{label}</a>'
        "</noscript>"
    )
    setattr(request, _NOSCRIPT_ATTR, link)
    return link


def _streamed(call: DeferredCall) -> DeferredCall:
    """Return `call` delivered as a stream boundary of the response under way."""
    site = replace(call.site, mode=DeferMode.STREAM)
    return replace(call, site=site, options=replace(call.options, mode=site.mode))


def _run(
    call: DeferredCall, taken: Capture, frame: Frame, at: float
) -> Rendered[Delta]:
    """Run the job of `call`, its boundary reporting a failure under the job's id."""
    with wrapping(frame.ident):
        return run(call, taken, frame, at)


def _job(
    call: DeferredCall,
    session: BoundarySession[Delta],
    context: Context,
    parent: Frame | None = None,
) -> Job[Delta]:
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
        fallback=functools.partial(fallback, call, taken),
        run=functools.partial(_run, call, taken, Frame(session, ident, depth), at),
        parent=None if parent is None else parent.ident,
        depth=depth,
    )


_skipped: set[Site] = set()
_skipped_lock = threading.Lock()


def _note_skipped(site: Site) -> None:
    """Log once per site under `DEBUG` that a nested call skipped its loading UI."""
    if not settings.DEBUG:
        return
    with _skipped_lock:
        if site in _skipped:
            return
        _skipped.add(site)
    logger.debug(
        "Component %r in %s renders in place inside its parent's job, as it is "
        'nested past DEFER["MAX_DEPTH"], so its loading UI is skipped.',
        site.name,
        site.origin,
    )


class ComponentDeferrals:
    """What every sink shares, queueing into the session a `deferring` block holds.

    A call inside a job joins that job as a nested one, up to `MAX_DEPTH` levels.
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
            job = _job(call, frame.session, context, frame)
            frame.nested.append(job)
        else:
            held = _deferring.get()
            if held is None:
                return self.inline(context, call)
            session = held.open()
            job = _job(call, session, context)
            session.queue(job)
        with unframed(), _undeferred(), wrapping(job.id):
            loading, failure = call.renderer.loading(context)
        return shell(job.id, call.tag, call.key, call.source, loading, failure)

    def trip(self, context: Context, call: DeferredCall, trip: TripInput) -> str:
        """Return the idle wrapper of a client-trip call, its token minted now.

        A page asked for with `NOSCRIPT_PARAM` renders the call in place and privately.
        """
        request = _request(context)
        if not _admitted(request):
            ident = _ident()
            with unframed(), _undeferred():
                html, failure = call.renderer.error(context, ErrorKind.TIMEOUT)
            note_retry(_collector(context), html, "")
            return settled(ident, call.tag, call.key, call.source, html, failure)
        if isinstance(request, HttpRequest) and request.GET.get(NOSCRIPT_PARAM) == "1":
            mark_personal_render(request)
            return self.inline(context, call)
        token = shell_token(request, trip)
        if token is None:
            return self.queue(context, _streamed(call))
        require_bundle(context, DEFER_CHUNK_KEY, urgent=trip.mode is DeferMode.LOAD)
        ident = _ident()
        with unframed(), _undeferred(), wrapping(ident):
            loading, refused = call.renderer.loading(context)
        link = "" if refused is not None else _noscript(request)
        if call.tag is not None:
            loading, link = loading + link, ""
        wrapper = idle(
            ident, call.tag, call.key, call.source, (trip.mode, token), loading, refused
        )
        return wrapper + link

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
        return settled(ident, call.tag, call.key, call.source, html, failure, retry)


__all__ = [
    "NOSCRIPT_PARAM",
    "Capture",
    "ComponentDeferrals",
    "Deferring",
    "capture",
    "current_deferring",
    "deferring",
]
