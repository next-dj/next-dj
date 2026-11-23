import time
from collections.abc import Callable, Coroutine, Iterator
from contextlib import AbstractContextManager
from typing import Any, cast
from unittest.mock import Mock

from next.defer import (
    Addressing,
    BoundaryFailure,
    BoundaryOptions,
    CallRenderer,
    DeferMode,
    DeferredCall,
    ErrorKind,
    Site,
)
from next.ports import run_bridged
from next.suspense.markers import Caps, Job, Outcome, Rendered
from next.suspense.render import Delta
from next.suspense.session import BoundarySession
from next.testing import override_next_settings


class RecordingFallback:
    """A fallback that records what it was asked and renders a fixed body."""

    def __init__(
        self,
        body: str = "<p>sorry</p>",
        *,
        tag: str | None = "div",
        retry: bool = True,
        render: Callable[[BoundaryFailure, ErrorKind], str] | None = None,
        token: str = "",
    ) -> None:
        """Hold the body, the wrapper tag, the retry answer and an optional render."""
        self.body = body
        self.token = token
        self._tag = tag
        self._retry = retry
        self._render = render
        self.calls: list[tuple[BoundaryFailure, ErrorKind]] = []

    def retry(self) -> bool:
        return self._retry

    def retry_token(self, error: BoundaryFailure) -> str:
        return self.token

    def tag(self) -> str | None:
        return self._tag

    def render(self, error: BoundaryFailure, kind: ErrorKind) -> str:
        self.calls.append((error, kind))
        if self._render is not None:
            return self._render(error, kind)
        return self.body


def raising(exc: BaseException) -> Callable[[], str]:
    def render() -> str:
        raise exc

    return render


def defer_errors(**values: object) -> AbstractContextManager[None]:
    return override_next_settings(DEFER={"ERRORS": values})


def make_site(
    name: str = "card",
    *,
    addressing: Addressing = Addressing.COMPONENT,
    mode: DeferMode = DeferMode.STREAM,
    origin: str = "page.html",
    index: int = 0,
) -> Site:
    """Return a call site, a streamed component `card` in `page.html` by default."""
    return Site(addressing, name, mode, origin, index)


COMPONENT_SITE = make_site()


def open_session(caps: Caps | None = None) -> BoundarySession[Delta]:
    """Open a session over `caps`, the settings' caps when none are given."""
    return BoundarySession[Delta].open(Caps.from_settings() if caps is None else caps)


def failed(kind: ErrorKind) -> Rendered[Delta]:
    """Render the fallback of `kind` the way a settled error UI reads, as `F:<kind>`."""
    return Rendered(
        f"F:{kind}",
        Delta(),
        kind=kind,
        failure=BoundaryFailure(kind, "d9", kind.status, retry=False),
    )


def make_job(
    session: BoundarySession[Delta],
    html: str = "R",
    *,
    site: Site = COMPONENT_SITE,
    key: str = "k",
    options: BoundaryOptions | None = None,
    deadline: float | None = None,
    parent: str | None = None,
    depth: int | None = None,
    run: Callable[[], Rendered[Delta]] | None = None,
    arun: Callable[[], Coroutine[Any, Any, Rendered[Delta]]] | None = None,
    fallback: Callable[[ErrorKind], Rendered[Delta]] = failed,
) -> Job[Delta]:
    """Build a job with the next id of `session`, rendering `html` unless a body is given.

    The deadline is the session's, and the depth follows whether a parent is named.
    """

    def ready() -> Rendered[Delta]:
        return Rendered(html, Delta())

    return Job(
        id=session.next_id(),
        key=key,
        site=site,
        options=BoundaryOptions() if options is None else options,
        deadline=session.deadline if deadline is None else deadline,
        fallback=fallback,
        run=ready if run is None and arun is None else run,
        arun=arun,
        parent=parent,
        depth=int(parent is not None) if depth is None else depth,
    )


def make_call(
    tag: str | None = "div",
    key: str = "k",
    *,
    site: Site = COMPONENT_SITE,
    options: BoundaryOptions | None = None,
    source: str | None = None,
    renderer: CallRenderer | None = None,
) -> DeferredCall:
    """Build a deferred call whose renderer is a mock showing the loading UI `L`."""
    if renderer is None:
        renderer = Mock()
        renderer.loading.return_value = ("L", None)
    return DeferredCall(
        site,
        BoundaryOptions() if options is None else options,
        key,
        tag,
        source,
        renderer,
    )


def _run_inline(job: Job[Delta], expired: frozenset[str]) -> Iterator[Outcome[Delta]]:
    """Run `job` here, then its nested jobs, a job out of time settling as `timeout`."""
    if job.id in expired or time.monotonic() >= job.deadline:
        yield Outcome(job.id, job.fallback(ErrorKind.TIMEOUT))
        return
    body = job.run
    if body is None:
        arun = cast("Callable[[], Coroutine[Any, Any, Rendered[Delta]]]", job.arun)
        rendered = run_bridged(arun)
    else:
        rendered = body()
    yield Outcome(job.id, rendered)
    for nested in rendered.nested:
        yield from _run_inline(nested, expired)


def run_sequentially(
    session: BoundarySession[Delta],
) -> Iterator[tuple[Outcome[Delta], ...]]:
    """Run every job on the calling thread in document order, one batch per job."""
    expired = frozenset(job.id for job in session.plan().expired)
    for job in session.jobs:
        yield tuple(_run_inline(job, expired))


__all__ = [
    "COMPONENT_SITE",
    "RecordingFallback",
    "defer_errors",
    "failed",
    "make_call",
    "make_job",
    "make_site",
    "open_session",
    "raising",
    "run_sequentially",
]
