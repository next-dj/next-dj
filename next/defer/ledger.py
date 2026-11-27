"""The per-request holder of deferred jobs, opened by the first boundary that queues."""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING, Final, Self

from next.boundaries.ids import new_response_id, wire_id
from next.diagnostics import FailureLog

from .markers import Group, JobPlan


if TYPE_CHECKING:
    from collections.abc import Iterable

    from next.boundaries.options import BoundaryOptions
    from next.bridge.context import ContextSnapshot
    from next.deps.scope import RequestScope

    from .markers import BoundarySite, Caps, Captured, Job, LaneFacts


failure_log = FailureLog(logging.getLogger("next.defer"))

_SPENT: Final = (
    "A response rendered more deferred calls than NEXT_FRAMEWORK['DEFER']"
    "['MAX_INSTANCES'] of %d allows, so each call past it shows its timeout error UI "
    "without running."
)


class ResponseJobs:
    """The jobs of one response with the facts and captures the view read for them.

    The request thread writes it until the response starts, every thread reads it after.
    """

    __slots__ = (
        "_count",
        "_expired",
        "_instances",
        "_jobs",
        "_lock",
        "_open",
        "_separate",
        "_sites",
        "_units",
        "caps",
        "connections",
        "deadline",
        "facts",
        "response_id",
        "scope",
        "snapshot",
        "threads",
    )

    def __init__(self, caps: Caps, *, response_id: str, deadline: float) -> None:
        """Hold no job yet, `deadline` being the monotonic instant `DEADLINE` ends."""
        self.caps = caps
        self.response_id = response_id
        self.deadline = deadline
        self.facts: LaneFacts | None = None
        self.threads: tuple[Captured, ...] = ()
        self.connections: tuple[Captured, ...] = ()
        self.snapshot: ContextSnapshot | None = None
        self.scope: RequestScope | None = None
        self._lock = threading.Lock()
        self._count = 0
        self._instances = 0
        self._jobs: list[Job] = []
        self._sites: dict[BoundarySite, int] = {}
        self._separate = 0
        self._units: list[Job | list[Job]] = []
        self._open: dict[BoundarySite, list[Job]] = {}
        self._expired: list[Job] = []

    @classmethod
    def open(cls, caps: Caps) -> Self:
        """Return a ledger with a fresh response id, its deadline counted from now."""
        return cls(
            caps,
            response_id=new_response_id(),
            deadline=time.monotonic() + caps.deadline,
        )

    @property
    def jobs(self) -> tuple[Job, ...]:
        """Return every queued job in document order."""
        return tuple(self._jobs)

    def next_id(self) -> str:
        """Return the next wire id, from any thread since nested jobs mint them late."""
        with self._lock:
            number = self._count
            self._count += 1
        return wire_id(self.response_id, number)

    def deadline_for(self, options: BoundaryOptions, now: float) -> float:
        """Return when a job queued at `now` settles as `timeout`, the earlier bound."""
        if options.timeout is None:
            return self.deadline
        return min(self.deadline, now + options.timeout)

    def instances(self, site: BoundarySite) -> int:
        """Return how many jobs of `site` were queued so far."""
        return self._sites.get(site, 0)

    @property
    def expired(self) -> tuple[Job, ...]:
        """Return the jobs queued past `instances`, which settle without running."""
        return tuple(self._expired)

    def take_instance(self) -> bool:
        """Count one client-trip wrapper under `instances`, false once none is left."""
        with self._lock:
            spent = self._instances >= self.caps.instances
            if not spent:
                self._instances += 1
        if spent:
            failure_log.warn("instances", _SPENT, self.caps.instances)
        return not spent

    def queue(self, job: Job) -> None:
        """Record `job` in document order, alone, in a group of its site or expired.

        A site's first `per_site` jobs run alone while under `boundaries`, later ones
        join groups of `per_request` in order, and jobs past `instances` expire.
        """
        caps = self.caps
        with self._lock:
            seen = self._instances
            self._instances += 1
        count = self.instances(job.site)
        self._jobs.append(job)
        self._sites[job.site] = count + 1
        if seen >= caps.instances:
            self._expired.append(job)
            return
        if count < caps.per_site and self._separate < caps.boundaries:
            self._separate += 1
            self._units.append(job)
            return
        group = self._open.get(job.site)
        if group is None or len(group) >= caps.per_request:
            group = []
            self._open[job.site] = group
            self._units.append(group)
        group.append(job)

    def narrowed(self, jobs: Iterable[Job]) -> Self:
        """Return a ledger of the same response id and facts, `jobs` queued afresh."""
        narrowed = type(self)(
            self.caps, response_id=self.response_id, deadline=self.deadline
        )
        narrowed.facts = self.facts
        narrowed.threads = self.threads
        narrowed.connections = self.connections
        narrowed.snapshot = self.snapshot
        narrowed.scope = self.scope
        for job in jobs:
            narrowed.queue(job)
        return narrowed

    def plan(self) -> JobPlan:
        """Return the queued jobs as units in document order and the expired ones."""
        units = tuple(
            Group(unit[0].site, tuple(unit)) if isinstance(unit, list) else unit
            for unit in self._units
        )
        return JobPlan(units, tuple(self._expired))


__all__ = ["ResponseJobs"]
