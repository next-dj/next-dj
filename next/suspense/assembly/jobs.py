"""The run of a session's jobs, its request state, scope, coordinator and slot."""

import asyncio
import contextvars
from collections.abc import AsyncGenerator, AsyncIterator, Generator, Iterator
from concurrent.futures import Future
from contextlib import aclosing, asynccontextmanager, closing, contextmanager
from dataclasses import replace
from pathlib import Path

from django.http import HttpRequest

from next.conf.compression import compression_settings
from next.deps.cache import render_dep_cache, request_memo
from next.deps.loader import open_loaders
from next.deps.runner import batch_scope
from next.deps.scope import RequestScope, running_loop
from next.pages.responses import shared_render
from next.suspense.connections import (
    Outlived,
    capture_states,
    foreground_facts,
    pool_forced,
    settle_request,
)
from next.suspense.coordinator import Coordinator, Thresholds, Totals
from next.suspense.executors import background_loop
from next.suspense.markers import ForegroundFacts, Outcome
from next.suspense.policy import (
    Delivered,
    PageFacts,
    StreamSlot,
    open_streams,
    stream_limit,
)
from next.suspense.render import Delta
from next.suspense.response import Piece, StreamGrant
from next.suspense.session import BoundarySession

from .outcomes import MS, Outcomes


_closing: set[Future[None]] = set()


def prepare(
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
    coalesce = compression_settings().coalesce_ms / MS
    if pool_forced():
        thresholds = replace(Thresholds.from_settings(), mode="pool")
        return Coordinator[Delta](thresholds=thresholds, coalesce=coalesce)
    return Coordinator[Delta](coalesce=coalesce)


def batches(
    request: HttpRequest | None, session: BoundarySession[Delta]
) -> Iterator[tuple[Outcome[Delta], ...]]:
    """Run the jobs of `session` in parallel and yield their outcomes batch by batch."""
    prepare(session, request)
    outcomes = Outcomes(session, request)
    with (
        _job_scope(session, request),
        closing(_coordinator().run(session, buffered=True)) as emits,
    ):
        for emit in emits:
            yield tuple(outcomes.take(item) for item in emit.items)


class Reserved:
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

    def grant(
        self,
        delivered: Delivered,
        boundaries: tuple[tuple[str, str], ...],
        page_path: Path | None,
    ) -> StreamGrant:
        """Return what the stream of this render takes over, its slot among it."""
        reason = delivered.reason
        return StreamGrant(self.slot, page_path, delivered.delivery, reason, boundaries)

    def disown(self) -> None:
        """Stop owning the slot, as the stream response built over it owns it now."""
        self.slot = None


def boundaries(session: BoundarySession[Delta]) -> tuple[tuple[str, str], ...]:
    """Return the name and the mode of each boundary `session` queued."""
    return tuple((job.site.name, job.site.mode.value) for job in session.jobs)


def reserved(facts: PageFacts) -> StreamSlot | None:
    """Reserve a stream slot under `MAX_STREAMS`, `None` when none is free."""
    return open_streams.acquire(stream_limit(facts.server))


class Source:
    """The settled jobs of one page stream, read by its response batch by batch.

    Its jobs run after the view, so their facts drop the atomic blocks since committed.
    """

    __slots__ = ("coordinator", "outcomes", "outlived", "request", "session")

    def __init__(self, session: BoundarySession[Delta], request: HttpRequest) -> None:
        """Hold the session and the coordinator, built on the request thread."""
        self.session = session
        self.request = request
        self.coordinator = _coordinator()
        self.outcomes = Outcomes(session, request, streamed=True)
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

    def pinning_rule(self) -> str | None:
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


def _request_lane(
    facts: ForegroundFacts, session: BoundarySession[Delta]
) -> str | None:
    """Return the request-lane rule that pins a job of the stream, `None` for none."""
    rules = (facts.rule(job.options) for job in session.jobs)
    return next((rule.value for rule in rules if rule is not None), None)


__all__ = ["Reserved", "Source", "batches", "boundaries", "prepare", "reserved"]
