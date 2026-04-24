from __future__ import annotations

from django.db.models import Sum
from shortener.cache import pending_clicks
from shortener.models import Link

from next.pages import context


@context("totals")
def _totals() -> dict[str, int]:
    totals = Link.objects.aggregate(total_clicks=Sum("clicks"))
    return {
        "links": Link.objects.count(),
        "clicks": int(totals["total_clicks"] or 0),
        "pending": sum(pending_clicks().values()),
    }
