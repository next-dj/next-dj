from django.db.models import Sum
from polls.models import Poll

from next.components import component


@component.context("results", serialize=True)
def results(poll: Poll) -> dict[str, object]:
    """Build the snapshot the Vue layer reads from `window.Next.context.results`.

    The payload covers everything the client needs to render the live
    chart: the poll id, per-choice vote counts, running total, and the
    SSE stream URL. Voting is handled server-side via the ``{% form %}``
    tag in the component template, so no ``vote_url`` or CSRF token is
    included here.
    """
    choices = list(poll.choices.order_by("pk"))
    total = poll.choices.aggregate(total=Sum("votes"))["total"] or 0
    return {
        "poll_id": poll.pk,
        "total_votes": total,
        "stream_url": f"/polls/{poll.pk}/stream/",
        "choices": [
            {"id": choice.pk, "text": choice.text, "votes": choice.votes}
            for choice in choices
        ],
    }
