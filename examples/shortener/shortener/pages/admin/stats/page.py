from shortener.cache import pending_clicks
from shortener.services import link_totals
from shortener.signals import action_counts

from next import context
from next.pages import MetadataDict


metadata: MetadataDict = {"title": "Stats"}


@context("totals")
def totals() -> dict[str, int]:
    return {**link_totals(), "pending": sum(pending_clicks().values())}


@context("form_actions")
def form_actions() -> dict[str, int]:
    return action_counts()
