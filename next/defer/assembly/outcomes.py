"""The outcomes a coordinator settles, announced and reported on the request thread."""

import logging
from collections.abc import Iterator
from functools import partial
from typing import Final

from django.core.exceptions import SuspiciousOperation
from django.dispatch import Signal
from django.http import HttpRequest
from django.http.multipartparser import MultiPartParserError

from next.boundaries import boundary_failed, contain
from next.boundaries.calls import BoundarySite
from next.boundaries.failures import ErrorKind, kind_of, new_digest
from next.defer.coordinator import Finish
from next.defer.errors import BoundaryRefusedError
from next.defer.ledger import ResponseJobs
from next.defer.markers import Effects, Job, Outcome, Piece, Rendered, Timing
from next.defer.response.pump import on_request_thread
from next.defer.signals import boundary_settled, boundary_state_written
from next.diagnostics import INTENDED_EXCEPTIONS, FailureLog, report_request_exception


logger = logging.getLogger("next.defer")

failure_log = FailureLog(logger)

MS: Final = 1000
_RAISED: Final = (
    "The deferred job of %s %r raised outside its error boundary, so it shows its "
    "error UI, digest %s."
)
_WROTE: Final = (
    "The deferred job of %s %r in %s wrote %s, which a job cannot do, as the shell "
    "already sent its headers and a parallel job may read the state half-written. "
    "Write it in the view or a guard of the page, or render the component inline "
    "with defer='inline' at the call site."
)
_WRITES: Final = {
    "session": "the session",
    "session_key": "a new session key",
    "messages": "the messages",
    "csrf": "a new CSRF token",
}
_SETTLED: Final = (
    "The deferred job of %s %r in %s on %s settled as %s without a result, %s"
)
_TIMED_OUT: Final = (
    "as it outran the earlier of its timeout= and NEXT_FRAMEWORK['DEFER']['DEADLINE']."
)
_SHED: Final = (
    "as neither the pool queue nor its request thread could take it. "
    "NEXT_FRAMEWORK['DEFER']['EXECUTOR']['OPTIONS'] sizes them with MAX_WORKERS "
    "and MAX_QUEUE."
)
_EXPIRED: Final = (
    "as the response queued more deferred calls than "
    "NEXT_FRAMEWORK['DEFER']['MAX_INSTANCES'] allows."
)
_ABSORBED: Final = (
    "The deferred job of %s %r in %s on %s raised %s after the stream sent its 200 "
    "status, so the boundary shows its %s error UI instead. Raise it in the view, "
    "or declare the @context callable raising it guard=True, for the status to "
    "reach the response."
)
_CUT: Final = (
    "The deferred jobs of the response on %s ran past NEXT_FRAMEWORK['DEFER']"
    "['DEADLINE'] plus WRITE_GRACE, so it was assembled without %d settled boundaries."
)
_NO_REQUEST: Final = "no request"
_SECURITY: Final = (SuspiciousOperation, MultiPartParserError)


def _written(found: Effects) -> tuple[str, ...]:
    """Return the request state `found` names, as `boundary_state_written` sends it."""
    kinds = (
        ("session", found.session_modified),
        ("session_key", found.session_key_changed),
        ("messages", found.messages_used),
        ("csrf", found.csrf_changed),
    )
    return tuple(kind for kind, wrote in kinds if wrote)


def _path(request: HttpRequest | None) -> str:
    return _NO_REQUEST if request is None else request.path


def _place(site: BoundarySite) -> tuple[object, ...]:
    """Return the key of one call site, the template that holds it among its parts."""
    return (site.addressing, site.name, site.origin, site.index)


def _report_failure(
    job: Job,
    error: Exception,
    *,
    kind: ErrorKind,
    digest: str,
    request: HttpRequest | None,
) -> None:
    """Report a job that raised past its boundary, announcing it as a contained failure.

    It reaches `got_request_exception` and `django.request` as an uncaught one does.
    """
    site = job.site
    report_request_exception(
        error,
        request,
        ("raised", *_place(site), type(error).__qualname__),
        _RAISED,
        (site.addressing, site.name, digest),
        extra={"digest": digest},
    )
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


def _settled(item: Outcome, request: HttpRequest | None, *, expired: bool) -> None:
    """Warn of a job settled without its result, at a bounded rate per call site."""
    if expired:
        why = _EXPIRED
    elif item.settled is ErrorKind.TIMEOUT:
        why = _TIMED_OUT
    else:
        why = _SHED
    site = item.job.site
    failure_log.emit(
        logging.WARNING,
        ("settled", *_place(site), item.settled, expired),
        _SETTLED,
        (site.addressing, site.name, site.origin, _path(request), item.settled, why),
        extra={"kind": item.settled.value, "request": request},
    )


def _raised(
    item: Outcome, rendered: Rendered, request: HttpRequest | None, *, expired: bool
) -> None:
    """Report what a job raised past its lanes, one that never ran warned of instead."""
    error = item.error
    if error is None or isinstance(error, BoundaryRefusedError):
        _settled(item, request, expired=expired)
        return
    if not isinstance(error, Exception):
        return
    failure = rendered.failure
    digest = new_digest() if failure is None else failure.digest
    _report_failure(item.job, error, kind=item.settled, digest=digest, request=request)


def _wrote(site: BoundarySite, kind: str) -> None:
    """Warn of request state a job wrote, naming the boundary and the cure."""
    logger.warning(_WROTE, site.addressing, site.name, site.origin, _WRITES[kind])


def _settled_kwargs(piece: Piece, request: HttpRequest | None) -> dict[str, object]:
    """Return what `boundary_settled` reports of one piece."""
    site = piece.job.site
    timing = piece.timing
    placement = timing.placement
    return {
        "id": piece.id,
        "name": site.name,
        "addressing": site.addressing,
        "mode": site.mode,
        "state": piece.state,
        "kind": piece.kind,
        "lane": timing.lane,
        "reason": None if placement is None else placement.reason,
        "queue_ms": timing.waited * MS,
        "run_ms": timing.ran * MS,
        "request": request,
    }


def _signals(
    piece: Piece, request: HttpRequest | None
) -> Iterator[tuple[Signal, dict[str, object]]]:
    """Yield each signal one piece announces, warning of each write its job made."""
    if boundary_settled.has_listeners(Job):
        yield boundary_settled, _settled_kwargs(piece, request)
    for kind in _written(piece.rendered.effects):
        _wrote(piece.job.site, kind)
        if boundary_state_written.has_listeners(Job):
            yield (
                boundary_state_written,
                {"id": piece.id, "written": kind, "request": request},
            )


def _announce(piece: Piece, request: HttpRequest | None) -> None:
    """Send the signals of one piece from a thread."""
    for signal, kwargs in _signals(piece, request):
        signal.send_robust(sender=Job, **kwargs)


async def _aannounce(piece: Piece, request: HttpRequest | None) -> None:
    """Send the signals of one piece from the server loop.

    `asend_robust` runs a sync receiver through `sync_to_async` on the request thread.
    """
    for signal, kwargs in _signals(piece, request):
        await signal.asend_robust(sender=Job, **kwargs)


def _late(job: Job, error: Exception, request: HttpRequest | None) -> Rendered:
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
    elif isinstance(error, INTENDED_EXCEPTIONS):
        _absorbed(job.site, error, kind, request)
    else:
        _report_failure(job, error, kind=kind, digest=new_digest(), request=request)
    return job.error_ui(kind)


def _absorbed(
    site: BoundarySite, error: Exception, kind: ErrorKind, request: HttpRequest | None
) -> None:
    """Warn of a status a streamed job raised, which the sent 200 cannot carry."""
    name = type(error).__name__
    failure_log.emit(
        logging.WARNING,
        ("absorbed", *_place(site), name),
        _ABSORBED,
        (site.addressing, site.name, site.origin, _path(request), name, kind),
        extra={"status_code": kind.status, "request": request},
    )


def warn_cut(finish: Finish, request: HttpRequest | None) -> None:
    """Warn of an assembly its deadline cut, at a bounded rate per path."""
    path = _path(request)
    failure_log.emit(
        logging.WARNING,
        ("cut", path),
        _CUT,
        (path, finish.totals.dropped),
        extra={"request": request},
    )


class Outcomes:
    """Turn the batches of a coordinator into outcomes, on the request thread.

    A streamed response settles a job that raised past its boundary as its error kind.
    """

    __slots__ = ("expired", "handed", "jobs", "request", "streamed")

    def __init__(
        self,
        session: ResponseJobs,
        request: HttpRequest | None,
        *,
        streamed: bool = False,
    ) -> None:
        """Know every queued job by id, nested ones joining as their parents settle."""
        self.jobs = {job.id: job for job in session.jobs}
        self.expired = frozenset(job.id for job in session.expired)
        self.request = request
        self.streamed = streamed
        self.handed: set[str] = set()

    def take(self, item: Outcome) -> Piece:
        """Return the piece of one outcome, rendering here the error UI it needs."""
        job = item.job
        rendered = item.rendered
        if rendered is None:
            rendered = job.error_ui(item.settled)
            _raised(item, rendered, self.request, expired=item.id in self.expired)
        elif self.streamed and rendered.intended is not None:
            rendered = _late(job, rendered.intended, self.request)
        piece = self._piece(job, rendered, item)
        _announce(piece, self.request)
        return piece

    async def atake(self, item: Outcome) -> Piece:
        """Return the piece of one outcome on the server loop.

        An error UI renders on the request thread, as a template may reach the database.
        """
        rendered = item.rendered
        if rendered is None or rendered.intended is not None:
            return await on_request_thread(partial(self.take, item))
        piece = self._piece(item.job, rendered, item)
        await _aannounce(piece, self.request)
        return piece

    def unsettled(self) -> tuple[Piece, ...]:
        """Return an `error` piece for each known job no piece was handed over for."""
        pieces: list[Piece] = []
        for job in [job for job in self.jobs.values() if job.id not in self.handed]:
            piece = self._piece(job, job.error_ui(ErrorKind.ERROR))
            _announce(piece, self.request)
            pieces.append(piece)
        return tuple(pieces)

    def _piece(
        self, job: Job, rendered: Rendered, outcome: Outcome | None = None
    ) -> Piece:
        """Return the piece of `job`, knowing the jobs it nests from now on."""
        for nested in rendered.nested:
            self.jobs[nested.id] = nested
        self.handed.add(job.id)
        parent = None if job.parent is None else self.jobs.get(job.parent)
        timing = Timing() if outcome is None else outcome.timing
        return Piece(job, rendered, timing, None if parent is None else parent.key)


__all__ = ["MS", "Outcomes", "failure_log", "warn_cut"]
