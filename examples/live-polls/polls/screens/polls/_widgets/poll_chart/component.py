from django.db.models import Sum
from django.http import HttpRequest
from django.middleware.csrf import get_token
from polls.models import Poll

from next.components import component
from next.forms import form_action_manager


@component.context("results", serialize=True)
def results(poll: Poll, request: HttpRequest) -> dict[str, object]:
    """Build the snapshot the Vue layer reads from `window.Next.context.results`.

    The payload is everything the client needs to render the chart and
    submit a vote without inspecting the DOM. The vote_url comes from
    the form-action manager so the namespace prefix is never hardcoded
    in the client. The CSRF token rotates per request and ships next
    to the rest of the data so a freshly loaded tab can vote without
    a second round trip.
    """
    choices = list(poll.choices.order_by("pk"))
    total = poll.choices.aggregate(total=Sum("votes"))["total"] or 0
    return {
        "poll_id": poll.pk,
        "total_votes": total,
        "csrf": get_token(request),
        "vote_url": form_action_manager.get_action_url("polls:vote"),
        "stream_url": f"/polls/{poll.pk}/stream/",
        "choices": [
            {"id": choice.pk, "text": choice.text, "votes": choice.votes}
            for choice in choices
        ],
    }
