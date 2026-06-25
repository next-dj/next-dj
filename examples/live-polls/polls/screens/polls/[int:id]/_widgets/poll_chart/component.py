from django.db.models import Sum
from polls.models import Poll

from next.components import component


@component.context("results", serialize=True)
def results(poll: Poll) -> dict[str, object]:
    """Build the snapshot the Vue layer reads from `window.Next.context.results`.

    The payload covers the poll id, per-choice vote counts, and the
    running total. `serialize=True` seeds it into `window.Next.context`
    so the island has data the instant it first mounts. The live stream
    sends a `refresh` of the `poll-results` zone, not a context patch, so
    after the first paint the island re-reads the fresh counts from the
    re-rendered `data-poll-chart-data` block the server embeds in the
    zone, never from this context value. Voting is handled server-side
    via the ``{% form %}`` tag in the component template.
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
