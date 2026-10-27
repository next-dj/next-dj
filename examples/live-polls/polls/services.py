from django.db.models import Q
from django.utils import timezone

from polls.models import Poll, PollQuerySet


def poll_index() -> PollQuerySet:
    """Return every poll with its totals, the read that needs no visitor."""
    return Poll.objects.with_totals()


def poll_count() -> int:
    """Return how many polls still accept votes."""
    now = timezone.now()
    return (
        Poll.objects.filter(is_open=True)
        .filter(Q(closes_at__isnull=True) | Q(closes_at__gt=now))
        .count()
    )
