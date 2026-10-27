from polls.models import Poll
from polls.snapshots import build_snapshot

from next import context, page
from next.pages import MetadataDict


@page.metadata
def embed_meta(poll: Poll) -> MetadataDict:
    """Title the widget after the poll the parent page resolved."""
    return {"title": f"{poll.question} (embed)"}


@context("live_results", serialize=True)
def live_results(poll: Poll) -> dict[str, object]:
    """Seed the snapshot the vote handler patches, as the detail page does."""
    return build_snapshot(poll).to_payload()
