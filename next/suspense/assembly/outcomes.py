"""The outcomes a coordinator settles, announced and reported on the request thread."""

import logging
from typing import Final

from asgiref.sync import sync_to_async
from django.core.exceptions import SuspiciousOperation
from django.http import HttpRequest
from django.http.multipartparser import MultiPartParserError

from next.boundaries import boundary_failed, contain, kind_of, new_digest
from next.defer import ErrorKind
from next.diagnostics import INTENDED_EXCEPTIONS
from next.suspense.coordinator import Settlement
from next.suspense.errors import BoundaryRefusedError
from next.suspense.markers import Effects, Job, Outcome
from next.suspense.render import Delta
from next.suspense.response import Piece
from next.suspense.session import BoundarySession
from next.suspense.signals import boundary_rendered, boundary_side_effect


logger = logging.getLogger("next.suspense")

MS: Final = 1000
_RAISED: Final = (
    "The deferred job of %s %r raised outside its error boundary, so it shows its "
    "error UI, digest %s."
)
_WROTE: Final = (
    "The deferred job of %s %r wrote %s request state, which jobs do not support, as "
    "a parallel job may read it half-written."
)
_SECURITY: Final = (SuspiciousOperation, MultiPartParserError)


def _written(found: Effects) -> tuple[str, ...]:
    """Return the kinds of request state `found` names, as `boundary_side_effect`."""
    kinds = (
        ("session", found.session_modified),
        ("session-key", found.session_key_changed),
        ("messages", found.messages_used),
        ("csrf", found.csrf_changed),
    )
    return tuple(kind for kind, wrote in kinds if wrote)


def _report_failure(
    job: Job[Delta],
    error: Exception,
    *,
    kind: ErrorKind,
    digest: str,
    request: HttpRequest | None,
) -> None:
    """Log a job that raised past its boundary, announcing it as a contained failure."""
    site = job.site
    logger.error(_RAISED, site.addressing, site.name, digest, exc_info=error)
    if boundary_failed.has_listeners(contain):
        boundary_failed.send_robust(
            sender=contain,
            id=job.id,
            name=site.name,
            addressing=site.addressing,
            kind=kind,
            digest=digest,
            exception=error,
            request=request,
        )


def _raised(
    item: Settlement[Delta], outcome: Outcome[Delta], request: HttpRequest | None
) -> None:
    """Report the error of a settlement, unless it settled a job that never ran."""
    error = item.error
    if not isinstance(error, Exception) or isinstance(error, BoundaryRefusedError):
        return
    failure = outcome.rendered.failure
    digest = new_digest() if failure is None else failure.digest
    _report_failure(item.job, error, kind=item.kind, digest=digest, request=request)


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
        "queue_ms": timing.waited * MS,
        "run_ms": timing.ran * MS,
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
    """Announce one outcome from the server loop, as `_announce` does from a thread.

    `asend_robust` runs a sync receiver through `sync_to_async` on the request thread.
    """
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
        _report_failure(job, error, kind=kind, digest=new_digest(), request=request)
    return Outcome(outcome.id, job.fallback(kind), outcome.timing)


class Outcomes:
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


__all__ = ["MS", "Outcomes"]
