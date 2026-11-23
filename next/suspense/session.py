"""The per-request holder of deferred jobs, opened by the first boundary that queues."""

from __future__ import annotations

import re
import secrets
import threading
import time
from typing import TYPE_CHECKING, Final, Self

from next.suspense.markers import Group, Plan


if TYPE_CHECKING:
    from collections.abc import Iterable

    from next.defer import BoundaryOptions
    from next.deps.scope import RequestScope
    from next.suspense.markers import Caps, Captured, ForegroundFacts, Job, Site
    from next.tasks import ContextSnapshot


_SEAL_BYTES: Final = 4

ID_HEAD: Final = "b-"
"""What every wire id starts with, the whole id reading `b-<seal>-<n>`."""

IDENT: Final = re.compile(
    rf"{ID_HEAD}[0-9a-f]{{{2 * _SEAL_BYTES}}}-[0-9]{{1,9}}", re.ASCII
)
"""The pattern of one wire id, which a reload and the stale wrapper check match."""


def new_seal() -> str:
    """Return a fresh seal, unique to one response among the ids a page holds."""
    return secrets.token_hex(_SEAL_BYTES)


def wire_id(seal: str, number: int) -> str:
    """Return the wire id `number` of the response sealed by `seal`."""
    return f"{ID_HEAD}{seal}-{number}"


def seal_of(ident: str) -> str:
    """Return the seal of the response a wire id was minted in."""
    return ident[len(ID_HEAD) : ident.rindex("-")]


class BoundarySession[D]:
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
        "scope",
        "seal",
        "snapshot",
        "threads",
    )

    def __init__(self, caps: Caps, *, seal: str, deadline: float) -> None:
        """Hold no job yet, `deadline` being the monotonic instant `DEADLINE` ends."""
        self.caps = caps
        self.seal = seal
        self.deadline = deadline
        self.facts: ForegroundFacts | None = None
        self.threads: tuple[Captured, ...] = ()
        self.connections: tuple[Captured, ...] = ()
        self.snapshot: ContextSnapshot | None = None
        self.scope: RequestScope | None = None
        self._lock = threading.Lock()
        self._count = 0
        self._instances = 0
        self._jobs: list[Job[D]] = []
        self._sites: dict[Site, int] = {}
        self._separate = 0
        self._units: list[Job[D] | list[Job[D]]] = []
        self._open: dict[Site, list[Job[D]]] = {}
        self._expired: list[Job[D]] = []

    @classmethod
    def open(cls, caps: Caps) -> Self:
        """Return a session with a fresh seal, its deadline counted from now."""
        return cls(caps, seal=new_seal(), deadline=time.monotonic() + caps.deadline)

    @property
    def jobs(self) -> tuple[Job[D], ...]:
        """Return every queued job in document order."""
        return tuple(self._jobs)

    def next_id(self) -> str:
        """Return the next wire id, from any thread since nested jobs mint them late."""
        with self._lock:
            number = self._count
            self._count += 1
        return wire_id(self.seal, number)

    def deadline_for(self, options: BoundaryOptions, now: float) -> float:
        """Return when a job queued at `now` settles as `timeout`, the earlier bound."""
        if options.timeout is None:
            return self.deadline
        return min(self.deadline, now + options.timeout)

    def instances(self, site: Site) -> int:
        """Return how many jobs of `site` were queued so far."""
        return self._sites.get(site, 0)

    def take_instance(self) -> bool:
        """Count one client-trip wrapper under `instances`, false once none is left."""
        with self._lock:
            if self._instances >= self.caps.instances:
                return False
            self._instances += 1
            return True

    def queue(self, job: Job[D]) -> None:
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

    def narrowed(self, jobs: Iterable[Job[D]]) -> Self:
        """Return a session of the same seal and facts, holding `jobs` queued afresh."""
        narrowed = type(self)(self.caps, seal=self.seal, deadline=self.deadline)
        narrowed.facts = self.facts
        narrowed.threads = self.threads
        narrowed.connections = self.connections
        narrowed.snapshot = self.snapshot
        narrowed.scope = self.scope
        for job in jobs:
            narrowed.queue(job)
        return narrowed

    def plan(self) -> Plan[D]:
        """Return the queued jobs as units in document order and the expired ones."""
        units = tuple(
            Group(unit[0].site, tuple(unit)) if isinstance(unit, list) else unit
            for unit in self._units
        )
        return Plan(units, tuple(self._expired))


__all__ = ["BoundarySession"]
