from django.db.models import Count, QuerySet, Sum, Value
from django.db.models.functions import Coalesce
from polls.models import Poll

from next.pages import context


@context("polls")
def polls() -> QuerySet[Poll]:
    """Return polls annotated with aggregate counts for the index page.

    Each row carries ``choice_count`` and ``total_votes`` so the
    ``poll_card`` template reads pre-computed attributes instead of
    issuing per-row aggregate queries.
    """
    return Poll.objects.annotate(
        choice_count=Count("choices", distinct=True),
        total_votes=Coalesce(Sum("choices__votes"), Value(0)),
    )


@context("active_polls_count", inherit_context=True)
def active_polls_count() -> int:
    """Expose the count of polls to every descendant page header."""
    return Poll.objects.count()
