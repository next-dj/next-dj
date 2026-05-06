from django.db.models import QuerySet
from polls.models import Poll

from next.pages import context


@context("polls")
def polls() -> QuerySet[Poll]:
    """Return polls in reverse-chronological order for the index page."""
    return Poll.objects.all()


@context("active_polls_count", inherit_context=True)
def active_polls_count() -> int:
    """Expose the count of polls to every descendant page header."""
    return Poll.objects.count()
