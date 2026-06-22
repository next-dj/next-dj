from polls.broker import build_snapshot
from polls.models import Poll
from polls.providers import DPoll

from next.pages import context


@context("poll", inherit_context=True)
def poll(active: DPoll[Poll]) -> Poll:
    """Expose the active poll to the layout chain and any nested page."""
    return active


@context("live_results", serialize=True)
def live_results(poll: Poll) -> dict[str, object]:
    """Seed the live snapshot under `window.Next.context.live_results`.

    A page-level serialize provider so the vote handler can push fresh
    counts through `Patches(request).context(live_results=...)`, the
    name resolves against the origin page rather than the component.
    """
    return build_snapshot(poll).to_payload()
