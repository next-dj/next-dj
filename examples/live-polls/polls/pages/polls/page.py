from polls import services
from polls.models import PollQuerySet

from next import context
from next.pages import MetadataDict


metadata: MetadataDict = {"title": "Polls"}


@context("polls")
def polls() -> PollQuerySet:
    """Return polls annotated with aggregate counts for the index page.

    The annotations let `poll_card` read counts instead of issuing per-row queries.
    """
    return services.poll_index()


@context("active_polls_count", inherit_context=True)
def active_polls_count() -> int:
    """Expose the count of polls to every descendant page header."""
    return services.poll_count()
