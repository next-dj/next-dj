"""The values deferred execution passes between render layer, coordinator and lanes."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from next.caches import IdentityMemo
from next.conf.defer import defer_count, defer_seconds
from next.conf.settings import next_framework_settings
from next.defer import Addressing, BoundaryFailure, BoundaryOptions, ErrorKind, Site


if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine


class Lane(enum.StrEnum):
    """Where work runs, the pool, the event loop or the thread that ran the view."""

    POOL = "pool"
    LOOP = "loop"
    REQUEST = "request"


class Rule(enum.StrEnum):
    """A request-lane rule, listed by precedence, forcing every job of a response."""

    ATOMIC = "atomic"
    SQLITE = "sqlite"
    CONNECTION_STATE = "connection-state"
    THREAD_LOCAL = "thread-local"
    OPT_OUT = "opt-out"


class Verdict(enum.StrEnum):
    """Why a job no request-lane rule forces went to the pool."""

    POOL = "pool"
    FOREGROUND_FULL = "foreground-full"


@dataclass(frozen=True, slots=True)
class Placement:
    """Where a job's sync work goes, the lane derived from the reason so none drifts."""

    reason: Rule | Verdict

    @property
    def lane(self) -> Lane:
        """Return the request lane for a rule, the pool otherwise."""
        return Lane.REQUEST if self.forced else Lane.POOL

    @property
    def forced(self) -> bool:
        """Whether a request-lane rule placed the job."""
        return isinstance(self.reason, Rule)


class State(enum.StrEnum):
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
class Rendered[D]:
    """What the render layer made of one job, `kind` being `None` for ready content."""

    html: str
    delta: D
    kind: ErrorKind | None = None
    nested: tuple[Job[D], ...] = ()
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
    def state(self) -> State:
        """Return `error` when a kind is set, `ready` otherwise."""
        return State.READY if self.kind is None else State.ERROR


@dataclass(frozen=True, slots=True)
class Job[D]:
    """The work of one deferred boundary, opaque to whoever schedules it.

    Exactly one of `run` and `arun` is set. `fallback` renders the error UI of a kind.
    """

    id: str
    key: str
    site: Site
    options: BoundaryOptions
    deadline: float
    fallback: Callable[[ErrorKind], Rendered[D]]
    run: Callable[[], Rendered[D]] | None = None
    arun: Callable[[], Coroutine[Any, Any, Rendered[D]]] | None = None
    parent: str | None = None
    depth: int = 0

    def __post_init__(self) -> None:
        """Refuse a job without exactly one body or with a parent that disagrees."""
        if (self.run is None) is (self.arun is None):
            msg = f"the job {self.id} takes exactly one of run and arun"
            raise ValueError(msg)
        if self.depth < 0 or (self.parent is None) != (self.depth == 0):
            msg = f"the job {self.id} has a parent exactly when its depth is above 0"
            raise ValueError(msg)

    @property
    def addressing(self) -> Addressing:
        """Return whether the job renders a page body or a component call."""
        return self.site.addressing

    @property
    def asynchronous(self) -> bool:
        """Whether the body is a coroutine factory, run on the loop lane."""
        return self.arun is not None

    def remaining(self, now: float) -> float:
        """Return the seconds left at the monotonic instant `now`, at least zero."""
        return max(0.0, self.deadline - now)


@dataclass(frozen=True, slots=True)
class Group[D]:
    """Instances of one site run one after another as one job, written as one chunk."""

    site: Site
    members: tuple[Job[D], ...]

    def __post_init__(self) -> None:
        """Refuse an empty group or a member of another site."""
        if not self.members:
            msg = "a group holds at least one job"
            raise ValueError(msg)
        if any(member.site != self.site for member in self.members):
            msg = f"every member of a group belongs to the site {self.site.name!r}"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class Plan[D]:
    """The queued jobs in document order as units to run, and the ones over the cap."""

    units: tuple[Job[D] | Group[D], ...]
    expired: tuple[Job[D], ...] = ()


@dataclass(frozen=True, slots=True)
class Timing:
    """Where and how long one job ran, `lane` being `None` for one that never ran."""

    lane: Lane | None = None
    placement: Placement | None = None
    waited: float = 0.0
    ran: float = 0.0


@dataclass(frozen=True, slots=True)
class Outcome[D]:
    """One settled job, ready to become a chunk or a spliced slot."""

    id: str
    rendered: Rendered[D]
    timing: Timing = field(default_factory=Timing)

    @property
    def state(self) -> State:
        """Return how the job settled."""
        return self.rendered.state

    @property
    def kind(self) -> ErrorKind | None:
        """Return the error kind, `None` for ready content."""
        return self.rendered.kind


class Server(enum.StrEnum):
    """The kind of server the request arrived through."""

    ASGI = "asgi"
    WSGI_THREADED = "wsgi-threaded"
    WSGI_SYNC = "wsgi-sync"


@dataclass(frozen=True, slots=True)
class ForegroundFacts:
    """What the view read on the request thread, so no rule reads a connection later.

    The two `sources` list the state no `ConnectionState` or `ThreadState` covers.
    """

    server: Server
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

    def rule(self, options: BoundaryOptions) -> Rule | None:
        """Return the first request-lane rule that holds for a job with `options`."""
        if self.atomic:
            return Rule.ATOMIC
        if self.memory or (self.sqlite and not self.sqlite_parallel):
            return Rule.SQLITE
        if self.connection_sources:
            return Rule.CONNECTION_STATE
        if self.thread_sources:
            return Rule.THREAD_LOCAL
        if not (self.parallel and options.parallel):
            return Rule.OPT_OUT
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
        per_request=defer_count("EXECUTOR", "OPTIONS", "PER_REQUEST"),
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


__all__ = [
    "Addressing",
    "Admit",
    "Capacity",
    "Caps",
    "Captured",
    "Effects",
    "ForegroundFacts",
    "Group",
    "Job",
    "Lane",
    "Outcome",
    "Placement",
    "Plan",
    "Rendered",
    "Rule",
    "Server",
    "Site",
    "State",
    "Timing",
    "TokenRejection",
    "Verdict",
]
