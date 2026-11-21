"""The sinks deferred component calls reach, and the assembled delivery of jobs."""

import asyncio
import contextvars
import logging
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Callable,
    Generator,
    Iterator,
    MutableMapping,
)
from concurrent.futures import Future
from contextlib import aclosing, asynccontextmanager, closing, contextmanager
from contextvars import ContextVar
from dataclasses import replace
from functools import partial
from pathlib import Path
from typing import Final, override

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.exceptions import SuspiciousOperation
from django.http import HttpRequest, HttpResponse
from django.http.multipartparser import MultiPartParserError
from django.http.response import HttpResponseBase
from django.template import Context

from next.boundaries import (
    boundary_failed,
    contain,
    kind_of,
    new_digest,
    render_boundaries,
)
from next.conf.compression import compression_settings
from next.conf.defer import defer_option
from next.defer import DEFERRALS_KEY, DeferredCall, Delivery, ErrorKind
from next.deps.cache import render_dep_cache, request_memo
from next.deps.loader import open_loaders
from next.deps.runner import batch_scope
from next.deps.scope import RequestScope, running_loop
from next.diagnostics import INTENDED_EXCEPTIONS
from next.pages.responses import shared_render
from next.partial.envelope import Envelope
from next.partial.stream import Streamable, current_streamable
from next.static import get_static_manager
from next.static.collector import StaticCollector
from next.static.inject import SPLIT_NOTE, split_of
from next.static.nonce import CSP_MIDDLEWARE, request_nonce, resolve_nonce
from next.static.runtime import READER_CHUNK_KEY, partial_streams
from next.suspense.components import (
    ComponentDeferrals,
    Deferring,
    current_deferring,
    deferring,
)
from next.suspense.connections import (
    Outlived,
    capture_states,
    foreground_facts,
    pool_forced,
    settle_request,
)
from next.suspense.coordinator import Coordinator, Settlement, Thresholds, Totals
from next.suspense.errors import BoundaryRefusedError
from next.suspense.executors import background_loop
from next.suspense.manager import suspense_manager
from next.suspense.markers import Effects, ForegroundFacts, Job, Outcome
from next.suspense.partial import PartialStream
from next.suspense.policy import (
    Delivered,
    PageFacts,
    StreamSlot,
    declined,
    open_streams,
    page_facts,
    stream_limit,
)
from next.suspense.render import Delta, current_frame
from next.suspense.response import (
    Admitted,
    Chunks,
    Payloads,
    Piece,
    StreamingPageResponse,
    dress,
    finish,
    prune,
    strip,
    wrappers,
)
from next.suspense.session import BoundarySession
from next.suspense.signals import boundary_rendered, boundary_side_effect
from next.suspense.trip import trip_input
from next.utils import middleware_installed


logger = logging.getLogger("next.suspense")

_MS: Final = 1000
_RAISED: Final = (
    "The deferred job of %s %r raised outside its error boundary, so it shows its "
    "error UI, digest %s."
)
_WROTE: Final = (
    "The deferred job of %s %r wrote %s request state, which jobs do not support, as "
    "a parallel job may read it half-written."
)
_SECURITY: Final = (SuspiciousOperation, MultiPartParserError)
_SHELLED: Final = frozenset({Delivery.SHELL, Delivery.PREFETCH})
_STREAM_HEADER: Final = "X-Next-Stream"
_BOUNDARIES_HEADER: Final = "X-Next-Boundaries"

type Render = Callable[[], str]

_closing: set[Future[None]] = set()


def _written(found: Effects) -> tuple[str, ...]:
    """Return the kinds of request state `found` names, as `boundary_side_effect`."""
    kinds = (
        ("session", found.session_modified),
        ("session-key", found.session_key_changed),
        ("messages", found.messages_used),
        ("csrf", found.csrf_changed),
    )
    return tuple(kind for kind, wrote in kinds if wrote)


def _raised(
    item: Settlement[Delta], outcome: Outcome[Delta], request: HttpRequest | None
) -> None:
    """Log a job that raised past its boundary, announcing it as a contained failure."""
    error = item.error
    if not isinstance(error, Exception) or isinstance(error, BoundaryRefusedError):
        return
    failure = outcome.rendered.failure
    digest = new_digest() if failure is None else failure.digest
    site = item.job.site
    logger.error(_RAISED, site.addressing, site.name, digest, exc_info=error)
    if boundary_failed.has_listeners(contain):
        boundary_failed.send_robust(
            sender=contain,
            id=item.id,
            name=site.name,
            addressing=site.addressing,
            kind=item.kind,
            digest=digest,
            exception=error,
            request=request,
        )


def _rendered_kwargs(
    job: Job[Delta], outcome: Outcome[Delta], request: HttpRequest | None
) -> dict[str, object]:
    """Return what `boundary_rendered` reports of one outcome."""
    site = job.site
    timing = outcome.timing
    placement = timing.placement
    return {
        "id": outcome.id,
        "name": site.name,
        "addressing": site.addressing,
        "mode": site.mode,
        "state": outcome.state,
        "kind": outcome.kind,
        "lane": timing.lane,
        "placement": placement,
        "reason": None if placement is None else placement.reason,
        "queue_ms": timing.waited * _MS,
        "run_ms": timing.ran * _MS,
        "request": request,
    }


def _announce(
    job: Job[Delta], outcome: Outcome[Delta], request: HttpRequest | None
) -> None:
    """Send `boundary_rendered` for one outcome and report the writes its job made."""
    if boundary_rendered.has_listeners(Job):
        boundary_rendered.send_robust(
            sender=Job, **_rendered_kwargs(job, outcome, request)
        )
    site = job.site
    for kind in _written(outcome.rendered.effects):
        logger.warning(_WROTE, site.addressing, site.name, kind)
        if boundary_side_effect.has_listeners(Job):
            boundary_side_effect.send_robust(
                sender=Job, id=outcome.id, kind=kind, request=request
            )


async def _aannounce(
    job: Job[Delta], outcome: Outcome[Delta], request: HttpRequest | None
) -> None:
    """Announce one outcome from the server loop, as `_announce` does from a thread."""
    if boundary_rendered.has_listeners(Job):
        await boundary_rendered.asend_robust(
            sender=Job, **_rendered_kwargs(job, outcome, request)
        )
    site = job.site
    for kind in _written(outcome.rendered.effects):
        logger.warning(_WROTE, site.addressing, site.name, kind)
        if boundary_side_effect.has_listeners(Job):
            await boundary_side_effect.asend_robust(
                sender=Job, id=outcome.id, kind=kind, request=request
            )


def _late(
    job: Job[Delta],
    outcome: Outcome[Delta],
    error: Exception,
    request: HttpRequest | None,
) -> Outcome[Delta]:
    """Return the error a streamed job settles as when it raised past its boundary.

    After the first byte no exception becomes a status, so it maps to its kind, and a
    suspicious request is logged where Django logs one.
    """
    kind = kind_of(error)
    if isinstance(error, _SECURITY):
        security = logging.getLogger(f"django.security.{type(error).__name__}")
        security.error(
            str(error), exc_info=error, extra={"status_code": 400, "request": request}
        )
    elif not isinstance(error, INTENDED_EXCEPTIONS):
        site = job.site
        digest = new_digest()
        logger.error(_RAISED, site.addressing, site.name, digest, exc_info=error)
        if boundary_failed.has_listeners(contain):
            boundary_failed.send_robust(
                sender=contain,
                id=outcome.id,
                name=site.name,
                addressing=site.addressing,
                kind=kind,
                digest=digest,
                exception=error,
                request=request,
            )
    return Outcome(outcome.id, job.fallback(kind), outcome.timing)


class _Outcomes:
    """Turn the batches of a coordinator into outcomes, on the request thread.

    A streamed response settles a job that raised past its boundary as its error kind.
    """

    __slots__ = ("handed", "jobs", "request", "streamed")

    def __init__(
        self,
        session: BoundarySession[Delta],
        request: HttpRequest | None,
        *,
        streamed: bool = False,
    ) -> None:
        """Know every queued job by id, nested ones joining as their parents settle."""
        self.jobs = {job.id: job for job in session.jobs}
        self.request = request
        self.streamed = streamed
        self.handed: set[str] = set()

    def take(self, item: Outcome[Delta] | Settlement[Delta]) -> Outcome[Delta]:
        """Return the outcome of one item, a settlement rendering its error UI here."""
        if isinstance(item, Settlement):
            job = item.job
            outcome = Outcome(item.id, job.fallback(item.kind), item.timing)
            _raised(item, outcome, self.request)
        else:
            job = self.jobs[item.id]
            outcome = item
            error = item.rendered.intended
            if self.streamed and error is not None:
                outcome = _late(job, item, error, self.request)
        self._join(outcome)
        _announce(job, outcome, self.request)
        return outcome

    def piece(self, item: Outcome[Delta] | Settlement[Delta]) -> Piece:
        """Return the piece a stream writes for one item."""
        return self._piece(self.take(item))

    async def apiece(self, item: Outcome[Delta] | Settlement[Delta]) -> Piece:
        """Return the piece of one item on the server loop.

        An error UI renders on the request thread, as a template may reach the database.
        """
        if isinstance(item, Settlement) or item.rendered.intended is not None:
            return await sync_to_async(self.piece, thread_sensitive=True)(item)
        self._join(item)
        await _aannounce(self.jobs[item.id], item, self.request)
        return self._piece(item)

    def _join(self, outcome: Outcome[Delta]) -> None:
        for nested in outcome.rendered.nested:
            self.jobs[nested.id] = nested

    def unsettled(self) -> tuple[Piece, ...]:
        """Return an `error` piece for each known job no piece was handed over for."""
        pieces: list[Piece] = []
        for job in [job for job in self.jobs.values() if job.id not in self.handed]:
            outcome = Outcome(job.id, job.fallback(ErrorKind.ERROR))
            _announce(job, outcome, self.request)
            pieces.append(self._piece(outcome))
        return tuple(pieces)

    def _piece(self, outcome: Outcome[Delta]) -> Piece:
        job = self.jobs[outcome.id]
        self.handed.add(job.id)
        parent = None if job.parent is None else self.jobs.get(job.parent)
        return Piece(job, outcome, None if parent is None else parent.key)


def _prepare(
    session: BoundarySession[Delta],
    request: HttpRequest | None,
    *,
    stream: bool = False,
) -> ForegroundFacts:
    """Store the facts, captures and snapshot of the view, settling what jobs share.

    Request state settles when a job may take the pool, and always before a stream.
    """
    facts = foreground_facts(request)
    states = capture_states(request)
    if request is not None and (
        stream or any(facts.rule(job.options) is None for job in session.jobs)
    ):
        shared = not stream and shared_render(request)
        settle_request(request, stream=stream, shared=shared)
    session.facts = facts
    session.threads = states.threads
    session.connections = states.connections
    session.snapshot = contextvars.copy_context()
    memo = request_memo.get()
    session.scope = batch_scope.get() if memo is None else memo.scope
    if session.scope is not None:
        open_loaders(session.scope, session.snapshot)
    return facts


def _own_scope(
    session: BoundarySession[Delta],
    request: HttpRequest | None,
    loop: asyncio.AbstractEventLoop,
) -> RequestScope:
    """Give the jobs of `session` a request scope of their own on `loop`.

    The page scope closed before the shell rendered, so their loaders batch in this one.
    """
    scope = RequestScope(render_dep_cache(request), loop=loop)
    snapshot = session.snapshot
    if snapshot is None:
        snapshot = session.snapshot = contextvars.copy_context()
    snapshot.run(batch_scope.set, scope)
    session.scope = scope
    open_loaders(scope, snapshot)
    return scope


@contextmanager
def _job_scope(
    session: BoundarySession[Delta], request: HttpRequest | None
) -> Iterator[None]:
    """Hold a scope for the jobs a thread drives, closed after them.

    An untouched scope closes here, any other on its loop, unawaited on the loop thread.
    """
    if session.scope is not None:
        yield
        return
    loop = background_loop.get()
    scope = _own_scope(session, request, loop)
    try:
        yield
    finally:
        if not scope.close_unused():
            closed = asyncio.run_coroutine_threadsafe(scope.close(), loop)
            if running_loop() is loop:
                _closing.add(closed)
                closed.add_done_callback(_closing.discard)
            else:
                closed.result()


@asynccontextmanager
async def _ajob_scope(
    session: BoundarySession[Delta], request: HttpRequest
) -> AsyncIterator[None]:
    """Hold a scope for jobs the running loop drives, closed after them."""
    if session.scope is not None:
        yield
        return
    scope = _own_scope(session, request, asyncio.get_running_loop())
    try:
        yield
    finally:
        await scope.close()


def _coordinator() -> Coordinator[Delta]:
    """Return the coordinator of one response, every job pooled under `forcing_pool`.

    Outcomes ready within `COALESCE_MS` of each other share one write.
    """
    coalesce = compression_settings().coalesce_ms / _MS
    if pool_forced():
        thresholds = replace(Thresholds.from_settings(), mode="pool")
        return Coordinator[Delta](thresholds=thresholds, coalesce=coalesce)
    return Coordinator[Delta](coalesce=coalesce)


def _batches(
    request: HttpRequest | None, session: BoundarySession[Delta]
) -> Iterator[tuple[Outcome[Delta], ...]]:
    """Run the jobs of `session` in parallel and yield their outcomes batch by batch."""
    _prepare(session, request)
    outcomes = _Outcomes(session, request)
    with (
        _job_scope(session, request),
        closing(_coordinator().run(session, buffered=True)) as emits,
    ):
        for emit in emits:
            yield tuple(outcomes.take(item) for item in emit.items)


class _Reserved:
    """A render owning the slot its policy reserved until a stream response takes it."""

    __slots__ = ("__weakref__", "slot")

    def __init__(self) -> None:
        """Own no slot yet."""
        self.slot: StreamSlot | None = None

    def hold(self, slot: StreamSlot) -> None:
        """Own `slot`, given back when the render is dropped while still owning it."""
        slot.bind(self)
        self.slot = slot

    def release(self) -> None:
        """Give back the stream slot no stream response took."""
        slot = self.slot
        self.slot = None
        if slot is not None:
            slot.release()

    def admitted(
        self,
        delivered: Delivered,
        boundaries: tuple[tuple[str, str], ...],
        page_path: Path | None,
    ) -> Admitted:
        """Return what the stream of this render takes over, its slot among it."""
        reason = delivered.reason
        return Admitted(self.slot, page_path, delivered.delivery, reason, boundaries)

    def disown(self) -> None:
        """Stop owning the slot, as the stream response built over it owns it now."""
        self.slot = None


class PageRender(_Reserved):
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


def _boundaries(session: BoundarySession[Delta]) -> tuple[tuple[str, str], ...]:
    return tuple((job.site.name, job.site.mode.value) for job in session.jobs)


def _recorded(held: Deferring, session: BoundarySession[Delta]) -> PageRender | None:
    """Record the delivery and the boundaries of the page view, `None` outside one."""
    rendering = _rendering.get()
    delivered = held.delivered
    if rendering is None or delivered is None or rendering.session is not None:
        return None
    rendering.delivered = delivered
    rendering.boundaries = _boundaries(session)
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


def _settled(
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
        if delivered is not None and delivered.delivery in _SHELLED:
            _recorded(held, session)
            return strip(html, session.seal)
        shelved = _shelved(held, html, collector, session)
        if shelved is None and request is not None:
            shelved = _streamed(request, html, collector, session)
        if shelved is not None:
            return shelved
        return finish(html, session, collector, partial(_batches, request))
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
        return _settled(held, _shared(render), collector, request)
    with deferring(request=request) as opened:
        return _settled(opened, _shared(render), collector, request)


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
    delivered = _admitted(request, facts)
    if delivered.delivery is Delivery.INLINE:
        declined(request, delivered, facts)
    return delivered


def _admitted(request: HttpRequest, facts: PageFacts) -> Delivered:
    """Return the delivery of the policy, taking a stream slot for a stream.

    A stream past `MAX_STREAMS` is assembled inline after all.
    """
    delivered = suspense_manager.policy().delivery(request, facts)
    rendering = _rendering.get()
    if delivered.delivery is not Delivery.STREAM or rendering is None:
        return delivered
    if facts.partial:
        return Delivered(Delivery.INLINE, "partial")
    slot = _reserved(facts)
    if slot is None:
        return Delivered(Delivery.INLINE, "capacity")
    rendering.hold(slot)
    return delivered


def _reserved(facts: PageFacts) -> StreamSlot | None:
    """Reserve a stream slot under `MAX_STREAMS`, `None` when none is free."""
    return open_streams.acquire(stream_limit(facts.server))


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


class _Source:
    """The settled jobs of one page stream, read by its response batch by batch.

    Its jobs run after the view, so their facts drop the atomic blocks since committed.
    """

    __slots__ = ("coordinator", "outcomes", "outlived", "request", "session")

    def __init__(self, session: BoundarySession[Delta], request: HttpRequest) -> None:
        """Hold the session and the coordinator, built on the request thread."""
        self.session = session
        self.request = request
        self.coordinator = _coordinator()
        self.outcomes = _Outcomes(session, request, streamed=True)
        facts = session.facts
        self.outlived = Outlived(() if facts is None else facts.atomic)

    def _settle(self) -> None:
        """Give the jobs the facts of the state they run in, once the view returned."""
        facts = self.session.facts
        if facts is not None:
            self.session.facts = self.outlived.facts(facts)

    @property
    def totals(self) -> Totals | None:
        """Return the counts of the finished run, `None` before it finished."""
        return self.coordinator.totals

    @property
    def failed(self) -> bool:
        """Whether the machinery of the run failed and stopped the jobs it held."""
        return self.coordinator.failed

    def unsettled(self) -> tuple[Piece, ...]:
        """Return an `error` piece for each boundary the run handed no piece for."""
        return self.outcomes.unsettled()

    def lane(self) -> str | None:
        """Return the request-lane rule pinning a job, read in the state jobs run in."""
        self._settle()
        facts = self.session.facts
        return None if facts is None else _request_lane(facts, self.session)

    def run(
        self, *, idle: float | None = None
    ) -> Generator[tuple[Piece, ...], None, None]:
        """Yield the pieces of each batch on the request thread, an idle one empty."""
        self._settle()
        outcomes = self.outcomes
        with (
            _job_scope(self.session, self.request),
            closing(self.coordinator.run(self.session, idle=idle)) as emits,
        ):
            for emit in emits:
                yield tuple(outcomes.piece(item) for item in emit.items)

    async def arun(self) -> AsyncGenerator[tuple[Piece, ...], None]:
        """Yield the pieces of each batch on the server loop."""
        self._settle()
        outcomes = self.outcomes
        async with (
            _ajob_scope(self.session, self.request),
            aclosing(self.coordinator.arun(self.session)) as emits,
        ):
            async for emit in emits:
                yield tuple([await outcomes.apiece(item) for item in emit.items])


def _listed_header(boundaries: tuple[tuple[str, str], ...]) -> str:
    return ",".join(f"{name}={mode}" for name, mode in boundaries)


def _request_lane(
    facts: ForegroundFacts, session: BoundarySession[Delta]
) -> str | None:
    """Return the request-lane rule that pins a job of the stream, `None` for none."""
    rules = (facts.rule(job.options) for job in session.jobs)
    return next((rule.value for rule in rules if rule is not None), None)


def _stream(
    html: str,
    rendering: PageRender,
    shelved: tuple[BoundarySession[Delta], Delivered],
    request: HttpRequest,
    page_path: Path | None,
) -> StreamingPageResponse:
    """Settle the request and the headers, then return the stream of the shell.

    Every chunk, the end marker and the hiding style carry the nonce the shell used.
    """
    session, delivered = shelved
    _prepare(session, request, stream=True)
    if any(map(middleware_installed, CSP_MIDDLEWARE)):
        request_nonce(request)
    nonce = resolve_nonce(request)
    collector = rendering.collector
    split = None if collector is None else split_of(collector)
    document = dress(
        html,
        len(html) if split is None else split,
        nonce=nonce,
        noscript=defer_option("NOSCRIPT") is not False,
    )
    chunks = Chunks(
        request,
        shell=rendering.shell,
        collector=collector,
        builder=get_static_manager().script_builder(),
        nonce=nonce,
    )
    response = StreamingPageResponse(
        request,
        document,
        _Source(session, request),
        chunks,
        admitted=rendering.admitted(delivered, rendering.boundaries, page_path),
    )
    rendering.disown()
    if settings.DEBUG:
        response[_STREAM_HEADER] = delivered.delivery.value
        response[_BOUNDARIES_HEADER] = _listed_header(rendering.boundaries)
    return response


def page_response(
    html: str,
    rendering: PageRender | None,
    request: HttpRequest,
    page_path: Path | None = None,
) -> HttpResponse | StreamingPageResponse:
    """Return the response of a rendered page, its stream when the render left one.

    Under `DEBUG` an assembled page names why it did not stream and its boundaries.
    """
    if rendering is None:
        return HttpResponse(html)
    session = rendering.session
    delivered = rendering.delivered
    if session is None or delivered is None:
        rendering.release()
        response = HttpResponse(html)
        if settings.DEBUG and delivered is not None and rendering.boundaries:
            response[_STREAM_HEADER] = f"{delivered.delivery} ({delivered.reason})"
            response[_BOUNDARIES_HEADER] = _listed_header(rendering.boundaries)
        return response
    try:
        return _stream(html, rendering, (session, delivered), request, page_path)
    finally:
        rendering.release()


class PartialRender(_Reserved):
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
            _prepare(session, request, stream=True)
            payloads = Payloads(request, shell=self.shell, collector=self.collector)
            response = PartialStream(
                request,
                envelope,
                _Source(session, request),
                payloads,
                deadline=session.deadline,
                admitted=self.admitted(self.delivered, _boundaries(session), page_path),
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
    slot = _reserved(facts)
    if slot is None:
        declined(request, Delivered(Delivery.INLINE, "capacity"), facts)
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


def _streamed(
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


inline_sink: Final = InlineDeferrals()
"""The sink `AppConfig.ready` binds to `deferrals_slot`."""

page_sink: Final = PageDeferrals()
"""The sink a page view puts in its render context."""


__all__ = [
    "InlineDeferrals",
    "PageDeferrals",
    "PageRender",
    "PartialRender",
    "assembled",
    "inline_sink",
    "page_deferring",
    "page_response",
    "page_sink",
]
