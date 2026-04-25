from __future__ import annotations

from django.db.models import Count, Sum
from shortener.cache import pending_clicks
from shortener.models import Link
from shortener.receivers import action_counts

from next.pages import context


@context("totals")
def totals() -> dict[str, int]:
    aggregate = Link.objects.aggregate(
        link_count=Count("id"),
        total_clicks=Sum("clicks"),
    )
    return {
        "links": aggregate["link_count"],
        "clicks": int(aggregate["total_clicks"] or 0),
        "pending": sum(pending_clicks().values()),
    }


@context("form_actions")
def form_actions() -> dict[str, int]:
    return action_counts()
