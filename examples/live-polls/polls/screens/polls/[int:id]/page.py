from polls.models import Poll
from polls.providers import DPoll

from next.pages import context


@context("poll", inherit_context=True)
def poll(active: DPoll[Poll]) -> Poll:
    """Expose the active poll to the layout chain and any nested page."""
    return active
