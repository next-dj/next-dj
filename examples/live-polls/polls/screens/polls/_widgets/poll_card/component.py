from django.db.models import Sum
from polls.models import Poll

from next.components import component


@component.context("total_votes")
def total_votes(poll: Poll) -> int:
    """Aggregate every choice on `poll` into a single vote total."""
    return poll.choices.aggregate(total=Sum("votes"))["total"] or 0


@component.context("choice_count")
def choice_count(poll: Poll) -> int:
    """Return the number of choices on `poll` for the index summary."""
    return poll.choices.count()
