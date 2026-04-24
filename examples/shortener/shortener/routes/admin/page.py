from __future__ import annotations

from shortener.cache import pending_clicks
from shortener.models import Link

from next.pages import context


@context("recent_links", inherit_context=True)
def _recent_links() -> list[Link]:
    return list(Link.objects.order_by("-clicks", "-created_at")[:10])


context("pending_clicks")(pending_clicks)
