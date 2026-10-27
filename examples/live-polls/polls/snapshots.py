from dataclasses import asdict, dataclass

from django.db.models import Sum
from django.utils import timezone

from polls.models import Poll


@dataclass(frozen=True)
class ChoiceSnapshot:
    id: int
    text: str
    votes: int


@dataclass(frozen=True)
class Snapshot:
    poll_id: int
    total_votes: int
    is_open: bool
    accepting_votes: bool
    deadline: int | None
    choices: tuple[ChoiceSnapshot, ...]

    def to_payload(self) -> dict[str, object]:
        """Return the snapshot as a JSON-friendly dict for the page context."""
        return {
            "poll_id": self.poll_id,
            "total_votes": self.total_votes,
            "is_open": self.is_open,
            "accepting_votes": self.accepting_votes,
            "deadline": self.deadline,
            "choices": [asdict(choice) for choice in self.choices],
        }


def build_snapshot(poll: Poll) -> Snapshot:
    """Read fresh choice counts from the database and build a snapshot."""
    choices = tuple(
        ChoiceSnapshot(id=row.pk, text=row.text, votes=row.votes)
        for row in poll.choices.order_by("pk")
    )
    total = poll.choices.aggregate(total=Sum("votes"))["total"] or 0
    deadline = (
        int(poll.closes_at.timestamp() * 1000) if poll.closes_at is not None else None
    )
    return Snapshot(
        poll_id=poll.pk,
        total_votes=total,
        is_open=poll.is_open,
        accepting_votes=poll.accepts_votes(at=timezone.now()),
        deadline=deadline,
        choices=choices,
    )
