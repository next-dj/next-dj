"""The process-wide cost table and the lane of a job no request-lane rule forces."""

from __future__ import annotations

import sys
import threading
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final, cast

from next.caches import DEFAULT_CACHE_SIZE
from next.conf.defer import (
    PlacementMode,
    defer_count,
    defer_read,
    defer_seconds,
    fraction,
    placement_choice,
)
from next.defer import Addressing, DeferMode
from next.suspense.markers import Placement, Verdict


if TYPE_CHECKING:
    from collections.abc import Callable

    from next.suspense.markers import ForegroundFacts, Job


type CostKey = tuple[Addressing, str, DeferMode]

WEIGHT: Final = 0.2
"""The share of the newest run in the moving averages."""

BAND: Final = 0.1
"""The share of `HANDOFF` a site's wall time must cross to change lanes."""

_NS: Final = 1_000_000_000
_PATH: Final = ("EXECUTOR", "OPTIONS", "PLACEMENT")


@dataclass(frozen=True, slots=True)
class Cost:
    """The moving averages of one site's job body in nanoseconds, and its run count.

    `handoff` remembers whether the site last placed on the request thread by its cost.
    """

    wall: float
    cpu: float
    runs: int
    handoff: bool = False

    def then(self, wall_ns: int, cpu_ns: int) -> Cost:
        """Return the averages with one more run folded in."""
        return Cost(
            self.wall + WEIGHT * (wall_ns - self.wall),
            self.cpu + WEIGHT * (cpu_ns - self.cpu),
            self.runs + 1,
            self.handoff,
        )


class CostTable:
    """Job costs by site, bounded and evicting the site that ran least recently.

    Reads take the lock like writes, so a free-threaded build never reads beside a move.
    """

    __slots__ = ("_entries", "_lock", "_maxsize")

    def __init__(self, maxsize: int = DEFAULT_CACHE_SIZE) -> None:
        """Hold at most `maxsize` sites."""
        self._maxsize = maxsize
        self._entries: OrderedDict[CostKey, Cost] = OrderedDict()
        self._lock = threading.Lock()

    def __len__(self) -> int:
        """Return how many sites hold an entry."""
        return len(self._entries)

    def cost(self, key: CostKey) -> Cost | None:
        """Return the averages of `key`, `None` for a site that never ran here."""
        with self._lock:
            return self._entries.get(key)

    def observe(self, key: CostKey, wall_ns: int, cpu_ns: int) -> None:
        """Fold one run of `key` into its averages, the first run taken as they are.

        A run makes the site the most recent, so a site that keeps running stays.
        """
        with self._lock:
            entries = self._entries
            held = entries.get(key)
            if held is not None:
                entries[key] = held.then(wall_ns, cpu_ns)
                entries.move_to_end(key)
                return
            entries[key] = Cost(float(wall_ns), float(cpu_ns), 1)
            while len(entries) > self._maxsize:
                entries.popitem(last=False)

    def lean(self, key: CostKey, *, handoff: bool) -> None:
        """Record the lane the cost of `key` chose, the one its next placement keeps."""
        with self._lock:
            held = self._entries.get(key)
            if held is not None and held.handoff is not handoff:
                self._entries[key] = replace(held, handoff=handoff)

    def clear(self) -> None:
        """Forget every site."""
        with self._lock:
            self._entries.clear()


COSTS: Final = CostTable()
"""The table every response of this process places its jobs by."""


def _nanoseconds(key: str) -> int:
    return round(defer_seconds(*_PATH, key) * _NS)


@dataclass(frozen=True, slots=True)
class Thresholds:
    """The `PLACEMENT` options, durations held in nanoseconds like the averages."""

    mode: PlacementMode
    handoff: int
    cpu_ratio: float
    cpu_max: int
    explore: int

    @classmethod
    def from_settings(cls) -> Thresholds:
        """Return the thresholds the settings hold, a misfit read as its default."""
        return cls(
            mode=defer_read(placement_choice, *_PATH, "MODE"),
            handoff=_nanoseconds("HANDOFF"),
            cpu_ratio=float(defer_read(fraction, *_PATH, "CPU_RATIO")),
            cpu_max=_nanoseconds("CPU_MAX"),
            explore=defer_count(*_PATH, "EXPLORE"),
        )


def gil_enabled() -> bool:
    """Whether the GIL holds now, which a free-threaded build may switch at runtime."""
    probe: Callable[[], bool] | None = getattr(sys, "_is_gil_enabled", None)
    return True if probe is None else probe()


def _verdict(cost: Cost | None, options: Thresholds) -> Verdict:
    """Return the lane the measured cost of a site earns, the pool until it is known.

    The `HANDOFF` bound moves by `BAND` away from the lane the site holds, so a site
    whose cost sits at the bound keeps one lane instead of flapping on every request.
    """
    if cost is None or cost.runs < options.explore:
        return Verdict.EXPLORE
    band = BAND if cost.handoff else -BAND
    if cost.wall < options.handoff * (1 + band):
        return Verdict.HANDOFF
    bound = cost.cpu > options.cpu_ratio * cost.wall
    if bound and cost.wall < options.cpu_max and gil_enabled():
        return Verdict.CPU
    return Verdict.COST


def place[D](
    job: Job[D],
    facts: ForegroundFacts,
    options: Thresholds,
    table: CostTable,
    *,
    alone: bool,
) -> Placement:
    """Return where the sync work of `job` runs and why.

    A request-lane rule wins, then a pin, the pool mode and a lone job, then the cost.
    """
    rule = facts.rule(job.options)
    if rule is not None:
        return Placement(rule)
    if job.options.parallel is True:
        return Placement(Verdict.PINNED)
    if options.mode == "pool":
        return Placement(Verdict.POOL_MODE)
    if alone:
        return Placement(Verdict.ALONE)
    key = job.site.cost_key
    cost = table.cost(key)
    verdict = _verdict(cost, options)
    handoff = verdict is Verdict.HANDOFF
    settled = cost is not None and verdict is not Verdict.EXPLORE
    if settled and cast("Cost", cost).handoff is not handoff:
        table.lean(key, handoff=handoff)
    return Placement(verdict)


__all__ = [
    "BAND",
    "COSTS",
    "WEIGHT",
    "Cost",
    "CostKey",
    "CostTable",
    "Thresholds",
    "gil_enabled",
    "place",
]
