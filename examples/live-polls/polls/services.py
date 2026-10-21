from polls.models import Poll, PollQuerySet


def poll_index() -> PollQuerySet:
    """Return every poll with its totals, the read that needs no visitor."""
    return Poll.objects.with_totals()


def poll_count() -> int:
    """Return how many polls exist."""
    return Poll.objects.count()
