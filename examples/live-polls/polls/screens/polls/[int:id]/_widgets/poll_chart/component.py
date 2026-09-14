from django.db.models import Sum
from polls.models import Poll

from next import component


@component.context("results", serialize=True)
def results(poll: Poll) -> dict[str, object]:
    """Build the snapshot the Vue layer reads from `window.Next.context.results`.

    `serialize=True` seeds `window.Next.context` so the island has data on mount.
    """
    choices = list(poll.choices.order_by("pk"))
    total = poll.choices.aggregate(total=Sum("votes"))["total"] or 0
    return {
        "poll_id": poll.pk,
        "total_votes": total,
        "choices": [
            {"id": choice.pk, "text": choice.text, "votes": choice.votes}
            for choice in choices
        ],
    }
