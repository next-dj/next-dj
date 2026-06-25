from __future__ import annotations

import threading
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from django.core.cache import cache
from django.db.models import Sum


if TYPE_CHECKING:
    from collections.abc import Iterator

    from polls.models import Poll


WAKE_TIMEOUT_SECONDS = 15
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
        """Return the snapshot as a JSON-friendly dict for the cache and context."""
        return {
            "poll_id": self.poll_id,
            "total_votes": self.total_votes,
            "choices": [asdict(choice) for choice in self.choices],
        }


@dataclass(frozen=True)
class Change:
    """One poll change, the snapshot plus the originating request id."""

    snapshot: dict[str, object]
    request_id: str | None


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


class PollBroker:
    """Per-poll wake conditions, a revision counter, and the last request id."""

    def __init__(self) -> None:
        """Initialise empty per-poll condition, revision, and request-id maps."""
        self._conditions: dict[int, threading.Condition] = defaultdict(
            threading.Condition
        )
        self._revisions: dict[int, int] = defaultdict(int)
        self._request_ids: dict[int, str | None] = {}

    def publish(self, snapshot: Snapshot, request_id: str | None = None) -> None:
        """Store `snapshot`, record the request id, bump the revision, wake all.

        The request id of the mutation rides with the change so the
        stream page can stamp it as the envelope echo. A caller without
        a request id (a backfill or a test) publishes `None` and no
        subscriber suppresses the change.
        """
        store_snapshot(snapshot)
        condition = self._conditions[snapshot.poll_id]
        with condition:
            self._revisions[snapshot.poll_id] += 1
            self._request_ids[snapshot.poll_id] = request_id
            condition.notify_all()

    def changes(self, poll_id: int) -> Iterator[Change]:
        """Yield a `Change` for every poll mutation while the client stays open.

        Each subscriber tracks its own `last_revision` so every wake
        reads the same snapshot exactly once and no event is lost when
        several tabs subscribe at once. A wake timeout loops without
        yielding, the sync source under WSGI sending no keepalive,
        which is the documented limitation the framework stream notes.

        On client disconnect the generator receives `GeneratorExit` and
        stops looping. No per-subscriber bookkeeping needs unwinding
        because the per-poll condition and revision counter are shared
        state.
        """
        condition = self._conditions[poll_id]
        last_revision = self._revisions[poll_id]
        while True:
            current_revision = self._wait_for_new_revision(
                poll_id, condition, last_revision
            )
            if current_revision == last_revision:
                continue
            last_revision = current_revision
            payload = read_snapshot(poll_id)
            if payload is None:
                continue
            yield Change(snapshot=payload, request_id=self._request_ids.get(poll_id))

    def _wait_for_new_revision(
        self,
        poll_id: int,
        condition: threading.Condition,
        baseline: int,
    ) -> int:
        """Block until the revision differs from `baseline` or the wake fires.

        Return the current revision. The caller compares against
        `baseline` to tell wake-up from timeout. Binding `baseline`
        as a default argument on the predicate keeps the lambda free
        of late-binding traps when this helper is called from a loop.
        """
        with condition:
            condition.wait_for(
                lambda b=baseline: self._revisions[poll_id] != b,
                timeout=WAKE_TIMEOUT_SECONDS,
            )
            return self._revisions[poll_id]


broker = PollBroker()
