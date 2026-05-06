"""In-process pub/sub for live poll snapshots.

The broker holds a `threading.Condition` and a monotonic revision
counter per poll. Publishers update the LocMemCache snapshot, bump
the counter, and `notify_all` so every subscriber wakes from
`wait_for`. Each subscriber tracks its own last-seen revision so a
fan-out to N tabs is race-free regardless of whether one subscriber
clears state faster than another.

Subscribers wake on a 15 second timeout to push an SSE comment frame
as keepalive. The pattern is single-process by design. A
multi-process deployment should swap this module for Redis Pub/Sub
or Postgres LISTEN without touching the page or signal layers.

The byte formatter follows the WHATWG SSE spec. Each event ends with a
blank line. Multi-line payloads split on newlines into separate `data:`
lines. Keepalive frames begin with `:` so clients ignore them while
proxies still see traffic on the connection.
"""

from __future__ import annotations

import json
import threading
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from django.core.cache import cache
from django.db.models import Sum


if TYPE_CHECKING:
    from collections.abc import Iterator

    from polls.models import Poll


KEEPALIVE_SECONDS = 15
SNAPSHOT_KEY = "polls:snapshot:{poll_id}"


@dataclass(frozen=True)
class ChoiceSnapshot:
    id: int
    text: str
    votes: int


@dataclass(frozen=True)
class Snapshot:
    poll_id: int
    total_votes: int
    choices: tuple[ChoiceSnapshot, ...]

    def to_payload(self) -> dict[str, object]:
        """Return the snapshot as a JSON-friendly dict for the cache and SSE."""
        return {
            "poll_id": self.poll_id,
            "total_votes": self.total_votes,
            "choices": [asdict(choice) for choice in self.choices],
        }


def build_snapshot(poll: Poll) -> Snapshot:
    """Read fresh choice counts from the database and build a snapshot."""
    choices = tuple(
        ChoiceSnapshot(id=row.pk, text=row.text, votes=row.votes)
        for row in poll.choices.order_by("pk")
    )
    total = poll.choices.aggregate(total=Sum("votes"))["total"] or 0
    return Snapshot(poll_id=poll.pk, total_votes=total, choices=choices)


def store_snapshot(snapshot: Snapshot) -> None:
    """Persist `snapshot` in the cache so subscribers can read it on wake."""
    cache.set(SNAPSHOT_KEY.format(poll_id=snapshot.poll_id), snapshot.to_payload())


def read_snapshot(poll_id: int) -> dict[str, object] | None:
    """Return the cached snapshot payload for `poll_id`, or `None` if absent."""
    return cache.get(SNAPSHOT_KEY.format(poll_id=poll_id))


def format_event(payload: dict[str, object], *, event: str) -> bytes:
    """Format `payload` as a Server-Sent Events record with a named event type."""
    body = json.dumps(payload, separators=(",", ":"))
    lines = [f"event: {event}"]
    lines.extend(f"data: {part}" for part in body.split("\n"))
    return ("\n".join(lines) + "\n\n").encode("utf-8")


def format_keepalive() -> bytes:
    """Return an SSE comment frame used to keep idle connections alive."""
    return b": keepalive\n\n"


class PollBroker:
    """Per-poll wake conditions plus a monotonic revision counter."""

    def __init__(self) -> None:
        """Initialise empty per-poll condition and revision registries."""
        self._conditions: dict[int, threading.Condition] = defaultdict(
            threading.Condition
        )
        self._revisions: dict[int, int] = defaultdict(int)

    def publish(self, snapshot: Snapshot) -> None:
        """Store `snapshot` in cache, bump the revision, wake every subscriber."""
        store_snapshot(snapshot)
        condition = self._conditions[snapshot.poll_id]
        with condition:
            self._revisions[snapshot.poll_id] += 1
            condition.notify_all()

    def subscribe(self, poll_id: int) -> Iterator[bytes]:
        """Yield SSE bytes starting with the current snapshot, then live updates.

        The first frame is always the cached snapshot under the
        `snapshot` event so a freshly opened tab catches up before
        the next vote arrives. Subsequent frames are sent as `update`
        events. Each subscriber tracks its own `last_revision` so
        every wake reads the same snapshot exactly once and no event
        is lost when several tabs subscribe at once. A 15-second
        timeout on `wait_for` produces an SSE comment frame as
        keepalive during quiet periods.

        The revision baseline is captured before the initial snapshot
        is yielded so a publish that lands while the consumer holds
        the snapshot frame still wakes the subscriber on the next
        `next()` instead of being absorbed silently.

        On client disconnect the generator receives `GeneratorExit`
        and stops looping. No per-subscriber bookkeeping needs to be
        unwound because the per-poll condition and revision counter
        are shared state. A future change that introduces
        per-subscriber data has to wrap the loop in `try`/`finally`.
        """
        condition = self._conditions[poll_id]
        last_revision = self._revisions[poll_id]
        cached = read_snapshot(poll_id)
        if cached is not None:
            yield format_event(cached, event="snapshot")
        while True:
            current_revision = self._wait_for_new_revision(
                poll_id, condition, last_revision
            )
            if current_revision == last_revision:
                yield format_keepalive()
                continue
            last_revision = current_revision
            payload = read_snapshot(poll_id)
            if payload is None:
                continue
            yield format_event(payload, event="update")

    def _wait_for_new_revision(
        self,
        poll_id: int,
        condition: threading.Condition,
        baseline: int,
    ) -> int:
        """Block until the revision differs from `baseline` or the keepalive fires.

        Return the current revision. The caller compares against
        `baseline` to tell wake-up from timeout. Binding `baseline`
        as a default argument on the predicate keeps the lambda free
        of late-binding traps when this helper is called from a loop.
        """
        with condition:
            condition.wait_for(
                lambda b=baseline: self._revisions[poll_id] != b,
                timeout=KEEPALIVE_SECONDS,
            )
            return self._revisions[poll_id]


broker = PollBroker()
