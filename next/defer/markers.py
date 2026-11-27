"""The values deferred execution passes between render layer, coordinator and lanes."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from next.boundaries.calls import Addressing, BoundarySite
from next.boundaries.failures import BoundaryFailure, ErrorKind
from next.caches import IdentityMemo
from next.conf.defer import defer_count, defer_seconds
from next.conf.settings import next_framework_settings


if TYPE_CHECKING:
    from collections.abc import Callable

    from next.boundaries.options import BoundaryOptions
    from next.static.assets import StaticAsset
    from next.static.collector import StaticCollector
    from next.static.serializers import JsContextSerializer


class Delivery(enum.StrEnum):
    """How one response delivers its deferred boundaries, decided once per request."""

    STREAM = "stream"
    INLINE = "inline"
    SHELL = "shell"
    PREFETCH = "prefetch"


JOBLESS: Final[frozenset[Delivery]] = frozenset({Delivery.SHELL, Delivery.PREFETCH})
"""The deliveries that answer the loading UI and run no deferred job."""


class Lane(enum.StrEnum):
    """Where the work of a job runs, the pool or the thread that ran the view."""

    POOL = "pool"
    REQUEST = "request"


class LaneRule(enum.StrEnum):
    """A request-lane rule, listed by precedence, forcing every job of a response."""

    ATOMIC = "atomic"
    SQLITE = "sqlite"
    CONNECTION_STATE = "connection_state"
    THREAD_LOCAL = "thread_local"
    OPT_OUT = "opt_out"


class PoolVerdict(enum.StrEnum):
    """Why a job no request-lane rule forces went to the pool."""

    POOL = "pool"
    REQUEST_FULL = "request_full"


@dataclass(frozen=True, slots=True)
class LaneChoice:
    """Where a job's sync work goes, the lane derived from the reason so none drifts."""

    reason: LaneRule | PoolVerdict

    @property
    def lane(self) -> Lane:
        """Return the request lane for a rule, the pool otherwise."""
        return Lane.REQUEST if self.forced else Lane.POOL

    @property
    def forced(self) -> bool:
        """Whether a request-lane rule placed the job."""
        return isinstance(self.reason, LaneRule)


class JobState(enum.StrEnum):
    """How a boundary settled."""

    READY = "ready"
    ERROR = "error"


_CLIENT_KINDS: Final = frozenset({ErrorKind.INCOMPLETE, ErrorKind.NETWORK})


@dataclass(frozen=True, slots=True)
class Effects:
    """The writes a job made to request state after the first byte left."""

    session_modified: bool = False
    session_key_changed: bool = False
    messages_used: bool = False
    csrf_changed: bool = False

    def __bool__(self) -> bool:
        """Whether the job made any of the writes."""
        return (
            self.session_modified
            or self.session_key_changed
            or self.messages_used
            or self.csrf_changed
        )


@dataclass(frozen=True, slots=True)
class Added:
    """One asset a job added, `prepend` placing it before the others of its slot."""

    asset: StaticAsset
    prepend: bool = False


@dataclass(frozen=True, slots=True)
class Noted:
    """One value a job noted for another area to read at injection time."""

    key: str
    value: object


@dataclass(frozen=True, slots=True)
class Merged:
    """One JS context entry a job registered, with the serializer it named."""

    key: str
    value: object
    serializer: JsContextSerializer | None = None


type Step = Added | Noted | Merged


@dataclass(frozen=True, slots=True)
class Delta:
    """What a job added to its own collector, in the order it added it."""

    steps: tuple[Step, ...] = ()

    def apply(self, collector: StaticCollector) -> None:
        """Replay every step onto `collector`, whose dedup and merge policy decide."""
        for step in self.steps:
            if isinstance(step, Added):
                collector.add(step.asset, prepend=step.prepend)
            elif isinstance(step, Noted):
                collector.note(step.key, step.value)
            else:
                collector.add_js_context(
                    step.key, step.value, serializer=step.serializer
                )


@dataclass(frozen=True, slots=True)
class Rendered:
    """What the render layer made of one job, `kind` being `None` for ready content."""

    html: str
    delta: Delta
    kind: ErrorKind | None = None
    nested: tuple[Job, ...] = ()
    degraded: bool = False
    status: int | None = None
    effects: Effects = field(default_factory=Effects)
    intended: Exception | None = None
    failure: BoundaryFailure | None = None
    retry: str = ""

    def __post_init__(self) -> None:
        """Refuse the kinds only the client raises."""
        if self.kind in _CLIENT_KINDS:
            msg = f"the server never settles a boundary as {self.kind!s}"
            raise ValueError(msg)

    @property
    def state(self) -> JobState:
        """Return `error` when a kind is set, `ready` otherwise."""
        return JobState.READY if self.kind is None else JobState.ERROR


@dataclass(frozen=True, slots=True)
class Job:
    """The work of one deferred boundary, opaque to whoever schedules it.

    `run` renders the boundary and `error_ui` renders the error UI of a kind instead.
    """

    id: str
    key: str
    site: BoundarySite
    options: BoundaryOptions
    deadline: float
    error_ui: Callable[[ErrorKind], Rendered]
    run: Callable[[], Rendered]
    parent: str | None = None
    depth: int = 0

    def __post_init__(self) -> None:
        """Refuse a job whose parent disagrees with its depth."""
        if self.depth < 0 or (self.parent is None) != (self.depth == 0):
            msg = f"the job {self.id} has a parent exactly when its depth is above 0"
            raise ValueError(msg)

    @property
    def addressing(self) -> Addressing:
        """Return whether the job renders a page body or a component call."""
        return self.site.addressing

    def remaining(self, now: float) -> float:
        """Return the seconds left at the monotonic instant `now`, at least zero."""
        return max(0.0, self.deadline - now)


@dataclass(frozen=True, slots=True)
class Group:
    """Instances of one site run one after another as one job, written as one chunk."""

    site: BoundarySite
    members: tuple[Job, ...]

    def __post_init__(self) -> None:
        """Refuse an empty group or a member of another site."""
        if not self.members:
            msg = "a group holds at least one job"
            raise ValueError(msg)
        if any(member.site != self.site for member in self.members):
            msg = f"every member of a group belongs to the site {self.site.name!r}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class JobPlan:
    """The queued jobs in document order as units to run, and the ones over the cap."""

    units: tuple[Job | Group, ...]
    expired: tuple[Job, ...] = ()


@dataclass(frozen=True, slots=True)
class Timing:
    """Where and how long one job ran, `lane` being `None` for one that never ran."""

    lane: Lane | None = None
    placement: LaneChoice | None = None
    waited: float = 0.0
    ran: float = 0.0


@dataclass(frozen=True, slots=True)
class Outcome:
    """How the machine settled one job, `rendered` being `None` without a result.

    A job without one shows the error UI of `settled`, `error` holding what it raised.
    """

    job: Job
    rendered: Rendered | None = None
    timing: Timing = field(default_factory=Timing)
    settled: ErrorKind = ErrorKind.ERROR
    error: BaseException | None = None

    @property
    def id(self) -> str:
        """Return the wire id of the settled job."""
        return self.job.id

    @property
    def kind(self) -> ErrorKind | None:
        """Return the error kind the job settled as, `None` for ready content."""
        rendered = self.rendered
        return self.settled if rendered is None else rendered.kind


@dataclass(frozen=True, slots=True)
class Piece:
    """One settled job as a response splices or writes it, with its parent's key."""

    job: Job
    rendered: Rendered
    timing: Timing = field(default_factory=Timing)
    parent: str | None = None

    @property
    def id(self) -> str:
        """Return the wire id of the settled job."""
        return self.job.id

    @property
    def state(self) -> JobState:
        """Return how the job settled."""
        return self.rendered.state

    @property
    def kind(self) -> ErrorKind | None:
        """Return the error kind, `None` for ready content."""
        return self.rendered.kind


class ServerKind(enum.StrEnum):
    """The kind of server the request arrived through."""

    ASGI = "asgi"
    WSGI_THREADED = "wsgi_threaded"
    WSGI_SYNC = "wsgi_sync"


@dataclass(frozen=True, slots=True)
class LaneFacts:
    """What the view read on the request thread, so no rule reads a connection later.

    The two `sources` list the state no `ConnectionState` or `ThreadState` covers.
    """

    server: ServerKind
    atomic: tuple[str, ...] = ()
    sqlite: tuple[str, ...] = ()
    memory: tuple[str, ...] = ()
    sqlite_parallel: bool = False
    connection_sources: tuple[str, ...] = ()
    thread_sources: tuple[str, ...] = ()
    parallel: bool = True

    def __post_init__(self) -> None:
        """Refuse an in-memory alias missing from the SQLite aliases."""
        if not set(self.memory) <= set(self.sqlite):
            msg = "every in-memory alias is a SQLite alias too"
            raise ValueError(msg)

    def rule(self, options: BoundaryOptions) -> LaneRule | None:
        """Return the first request-lane rule that holds for a job with `options`."""
        if self.atomic:
            return LaneRule.ATOMIC
        if self.memory or (self.sqlite and not self.sqlite_parallel):
            return LaneRule.SQLITE
        if self.connection_sources:
            return LaneRule.CONNECTION_STATE
        if self.thread_sources:
            return LaneRule.THREAD_LOCAL
        if not (self.parallel and options.parallel):
            return LaneRule.OPT_OUT
        return None


@dataclass(frozen=True, slots=True)
class Capacity:
    """The pool lane in one reading, `delay` being how long its oldest job waited."""

    workers: int
    busy: int
    queued: int
    delay: float

    @property
    def idle(self) -> int:
        """Return the workers free to take a job now."""
        return max(0, self.workers - self.busy)


class Admit(enum.StrEnum):
    """Whether admission queues a job, hands it to the request thread or sheds it."""

    POOL = "pool"
    REQUEST = "request"
    SHED = "shed"


@dataclass(frozen=True, slots=True)
class Caps:
    """The limits one response runs under, read once when it queues its first job."""

    deadline: float
    write_grace: float
    boundaries: int
    per_site: int
    depth: int
    instances: int
    per_request: int

    @classmethod
    def from_settings(cls) -> Caps:
        """Return the caps of the `DEFER` settings, held until the mapping moves."""
        return _CAPS.get(next_framework_settings.DEFER, _read_caps)


def _read_caps() -> Caps:
    """Read the caps of the merged `DEFER` mapping, a misfit read as its default."""
    return Caps(
        deadline=defer_seconds("DEADLINE"),
        write_grace=defer_seconds("WRITE_GRACE"),
        boundaries=defer_count("MAX_BOUNDARIES"),
        per_site=defer_count("MAX_PER_SITE"),
        depth=defer_count("MAX_DEPTH"),
        instances=defer_count("MAX_INSTANCES"),
        per_request=defer_count("EXECUTOR", "PER_REQUEST"),
    )


_CAPS: Final[IdentityMemo[Caps]] = IdentityMemo()


@dataclass(frozen=True, slots=True)
class Captured:
    """One state a `ThreadState` or `ConnectionState` backend read in the view.

    `alias` names the database for connection state and is `None` for thread state.
    """

    source: object
    state: object
    alias: str | None = None


class TokenRejection(enum.StrEnum):
    """Why a boundary token was refused."""

    MALFORMED = "malformed"
    SIGNATURE = "signature"
    EXPIRED = "expired"
    VERSION = "version"
    BINDING = "binding"
    URL_MISMATCH = "url_mismatch"
    TOO_LARGE = "too_large"
    STORAGE_MISS = "storage_miss"
    STORAGE_DOWN = "storage_down"


__all__ = [
    "JOBLESS",
    "Added",
    "Addressing",
    "Admit",
    "BoundarySite",
    "Capacity",
    "Caps",
    "Captured",
    "Delivery",
    "Delta",
    "Effects",
    "Group",
    "Job",
    "JobPlan",
    "JobState",
    "Lane",
    "LaneChoice",
    "LaneFacts",
    "LaneRule",
    "Merged",
    "Noted",
    "Outcome",
    "Piece",
    "PoolVerdict",
    "Rendered",
    "ServerKind",
    "Step",
    "Timing",
    "TokenRejection",
]
