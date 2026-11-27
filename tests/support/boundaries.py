import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from unittest.mock import Mock

from next.boundaries.calls import Addressing, BoundarySite, CallRenderer, DeferredCall
from next.boundaries.failures import BoundaryFailure, ErrorKind
from next.boundaries.options import BoundaryOptions, DeferMode
from next.defer.ledger import ResponseJobs
from next.defer.markers import Caps, Delta, Job, Piece, Rendered
from next.testing import override_next_settings


class RecordingErrorUI:
    """An error UI that records what it was asked and renders a fixed body."""

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
    return override_next_settings(BOUNDARIES=values)


def make_site(
    name: str = "card",
    *,
    addressing: Addressing = Addressing.COMPONENT,
    mode: DeferMode = DeferMode.STREAM,
    origin: str = "page.html",
    index: int = 0,
) -> BoundarySite:
    """Return a call site, a streamed component `card` in `page.html` by default."""
    return BoundarySite(addressing, name, mode, origin, index)


COMPONENT_SITE = make_site()


def open_session(caps: Caps | None = None) -> ResponseJobs:
    """Open a session over `caps`, the settings' caps when none are given."""
    return ResponseJobs.open(Caps.from_settings() if caps is None else caps)


def failed(kind: ErrorKind) -> Rendered:
    """Render the error UI of `kind` as a settled one reads, as `F:<kind>`."""
    return Rendered(
        f"F:{kind}",
        Delta(),
        kind=kind,
        failure=BoundaryFailure(kind, "d9", kind.status, retry=False),
    )


def make_job(
    session: ResponseJobs,
    html: str = "R",
    *,
    site: BoundarySite = COMPONENT_SITE,
    key: str = "k",
    options: BoundaryOptions | None = None,
    deadline: float | None = None,
    parent: str | None = None,
    depth: int | None = None,
    run: Callable[[], Rendered] | None = None,
    error_ui: Callable[[ErrorKind], Rendered] = failed,
) -> Job:
    """Build a job with the next id of `session`, rendering `html` unless a body is given.

    The deadline is the session's, and the depth follows whether a parent is named.
    """

    def ready() -> Rendered:
        return Rendered(html, Delta())

    return Job(
        id=session.next_id(),
        key=key,
        site=site,
        options=BoundaryOptions() if options is None else options,
        deadline=session.deadline if deadline is None else deadline,
        error_ui=error_ui,
        run=ready if run is None else run,
        parent=parent,
        depth=int(parent is not None) if depth is None else depth,
    )


def make_call(
    tag: str | None = "div",
    key: str = "k",
    *,
    site: BoundarySite = COMPONENT_SITE,
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


def piece_at(ident: str, rendered: Rendered, *, parent: str | None = None) -> Piece:
    """Return a piece of a bare job named `ident`, settled as `rendered`."""
    job = Job(
        id=ident,
        key="k",
        site=COMPONENT_SITE,
        options=BoundaryOptions(),
        deadline=float("inf"),
        error_ui=failed,
        run=lambda: rendered,
        parent=parent,
        depth=int(parent is not None),
    )
    return Piece(job, rendered)


def _run_inline(job: Job, expired: frozenset[str]) -> Iterator[Piece]:
    """Run `job` here, then its nested jobs, a job out of time settling as `timeout`."""
    if job.id in expired or time.monotonic() >= job.deadline:
        yield Piece(job, job.error_ui(ErrorKind.TIMEOUT))
        return
    rendered = job.run()
    yield Piece(job, rendered)
    for nested in rendered.nested:
        yield from _run_inline(nested, expired)


def run_sequentially(session: ResponseJobs) -> Iterator[tuple[Piece, ...]]:
    """Run every job on the calling thread in document order, one batch per job."""
    expired = frozenset(job.id for job in session.plan().expired)
    for job in session.jobs:
        yield tuple(_run_inline(job, expired))


__all__ = [
    "COMPONENT_SITE",
    "RecordingErrorUI",
    "defer_errors",
    "failed",
    "make_call",
    "make_job",
    "make_site",
    "open_session",
    "raising",
    "run_sequentially",
]
