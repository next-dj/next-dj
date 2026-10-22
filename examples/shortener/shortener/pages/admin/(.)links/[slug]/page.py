from shortener.cache import pending_clicks
from shortener.deps import DLink
from shortener.models import Link

from next import context, page
from next.pages import MetadataDict


page.public()


@context("link")
def link(item: DLink[Link]) -> Link:
    """Inject the link the quick view covers."""
    return item


@context("pending")
def pending(item: DLink[Link]) -> int:
    """Return the clicks the cache holds for the link and the database does not."""
    return pending_clicks().get(item.slug, 0)


@page.metadata
def link_peek_meta(item: DLink[Link]) -> MetadataDict:
    """Title the tab after the slug while the quick view covers the list."""
    return {"title": f"Quick look · {item.slug}"}
