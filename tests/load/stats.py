import math
from array import array
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field


TRANSPORTS = ("sse", "ws")
COUNTS = (
    "clients",
    "hello",
    "delivered",
    "expected",
    "lost",
    "excess",
    "skipped",
    "duplicates",
    "gaps",
    "resyncs",
    "restarts",
    "reconnects",
    "gave_up",
)


def _samples() -> array[float]:
    """Return an empty compact buffer of float samples."""
    return array("d")


@dataclass(slots=True)
class ClientRecord:
    """Everything one subscriber observed, keyed off its `(epoch, seq)` cursor."""

    transport: str
    topic: str
    opened: float = 0.0
    hello_seconds: float | None = None
    cursor: tuple[str, int] | None = None
    delivered: int = 0
    skipped: int = 0
    duplicates: int = 0
    gaps: int = 0
    resyncs: int = 0
    restarts: int = 0
    reconnects: int = 0
    gave_up: bool = False
    settled: bool = False
    complete: bool = False
    errors: Counter[str] = field(default_factory=Counter)

    def on_hello(self, position: tuple[str, int], now: float) -> None:
        """Take the hello position, a reconnect past the cursor counting one resync."""
        if self.hello_seconds is None:
            self.hello_seconds = now - self.opened
        if self.cursor is None:
            self.cursor = position
            return
        epoch, seq = position
        if epoch != self.cursor[0] or seq > self.cursor[1]:
            self.resyncs += 1
            self.cursor = position

    def advance(self, epoch: str, seq: int) -> bool:
        """Move the cursor and report whether the position is new to this client.

        An empty epoch is a never-published topic, so its first epoch is no restart.
        """
        previous = self.cursor
        if previous is None:
            self.errors["before_hello"] += 1
            self.cursor = (epoch, seq)
            return True
        last_epoch, last_seq = previous
        if epoch == last_epoch and seq <= last_seq:
            self.duplicates += 1
            return False
        self.cursor = (epoch, seq)
        continued = epoch == last_epoch or not last_epoch
        if continued and seq > last_seq + 1:
            self.gaps += seq - last_seq - 1
        if not continued:
            self.restarts += 1
        elif seq > last_seq + 1:
            self.resyncs += 1
        return True


@dataclass(slots=True)
class TransportTotals:
    """Counts and raw latency samples of every client on one transport."""

    clients: int = 0
    hello: int = 0
    delivered: int = 0
    expected: int = 0
    lost: int = 0
    excess: int = 0
    skipped: int = 0
    duplicates: int = 0
    gaps: int = 0
    resyncs: int = 0
    restarts: int = 0
    reconnects: int = 0
    gave_up: int = 0
    worst_lost_ratio: float = 0.0
    errors: Counter[str] = field(default_factory=Counter)
    end_to_end: array[float] = field(default_factory=_samples)
    server_to_client: array[float] = field(default_factory=_samples)
    hello_seconds: array[float] = field(default_factory=_samples)

    def add(self, record: ClientRecord, expected: int) -> None:
        """Fold one finished client in, losses measured against the published total."""
        self.clients += 1
        self.delivered += record.delivered
        self.expected += expected
        accounted = record.delivered + record.skipped
        lost = max(expected - accounted, 0)
        self.lost += lost
        if expected:
            self.worst_lost_ratio = max(self.worst_lost_ratio, lost / expected)
        self.excess += max(accounted - expected, 0)
        self.skipped += record.skipped
        self.duplicates += record.duplicates
        self.gaps += record.gaps
        self.resyncs += record.resyncs
        self.restarts += record.restarts
        self.reconnects += record.reconnects
        self.gave_up += record.gave_up
        self.errors.update(record.errors)
        if record.hello_seconds is not None:
            self.hello += 1
            self.hello_seconds.append(record.hello_seconds)

    def merge(self, other: "TransportTotals") -> None:
        """Add another process's totals for the same transport."""
        for name in COUNTS:
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self.worst_lost_ratio = max(self.worst_lost_ratio, other.worst_lost_ratio)
        self.errors.update(other.errors)
        self.end_to_end.extend(other.end_to_end)
        self.server_to_client.extend(other.server_to_client)
        self.hello_seconds.extend(other.hello_seconds)

    def as_dict(self) -> dict[str, object]:
        """Return the JSON report section of this transport."""
        return {
            **{name: getattr(self, name) for name in COUNTS},
            "worst_lost_ratio": round(self.worst_lost_ratio, 6),
            "errors": sum(self.errors.values()),
            "error_kinds": dict(self.errors),
            "latency_ms": {
                "end_to_end": distribution(self.end_to_end),
                "server_to_client": distribution(self.server_to_client),
                "hello": distribution(self.hello_seconds),
            },
        }


@dataclass(slots=True)
class ProcessReport:
    """One client process's per-transport totals."""

    transports: dict[str, TransportTotals] = field(
        default_factory=lambda: {name: TransportTotals() for name in TRANSPORTS}
    )


def combine(reports: Iterable[ProcessReport]) -> dict[str, TransportTotals]:
    """Merge every client process's totals by transport."""
    combined = {name: TransportTotals() for name in TRANSPORTS}
    for report in reports:
        for name, totals in report.transports.items():
            combined[name].merge(totals)
    return combined


def percentile(ordered: list[float], fraction: float) -> float:
    """Return the linearly interpolated percentile of already sorted samples."""
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    weight = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * weight


def distribution(seconds: Iterable[float]) -> Mapping[str, float | int | None]:
    """Return count and p50/p95/p99/max in milliseconds of exact samples."""
    ordered = sorted(seconds)
    if not ordered:
        return {"count": 0, "p50": None, "p95": None, "p99": None, "max": None}
    return {
        "count": len(ordered),
        "p50": round(percentile(ordered, 0.50) * 1000, 3),
        "p95": round(percentile(ordered, 0.95) * 1000, 3),
        "p99": round(percentile(ordered, 0.99) * 1000, 3),
        "max": round(ordered[-1] * 1000, 3),
    }
