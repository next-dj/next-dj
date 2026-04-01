from __future__ import annotations

from myapp.models import Post

from next.components import context


@context("recommendation_items")
def recommendation_items() -> list[Post]:
    """Latest posts for the recommendations strip (detail page and home)."""
    return list(Post.objects.select_related("author").order_by("-created_at")[:3])
