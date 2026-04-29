from catalog.models import Category, Product

from next.pages import context
from next.urls import DQuery


DEFAULT_FEATURED = 3
MAX_FEATURED = 12


@context("featured")
def featured(show: DQuery[int] = DEFAULT_FEATURED) -> list[Product]:
    """Return featured products for the landing page.

    The optional `?show=N` query parameter lets visitors widen the
    featured grid up to `MAX_FEATURED`. The value is clamped on the
    upper bound so a large request cannot exhaust the database.
    """
    count = max(1, min(MAX_FEATURED, show))
    return list(
        Product.objects.filter(in_stock=True)
        .select_related("category")
        .order_by("-created_at")[:count],
    )


@context("categories")
def categories() -> list[Category]:
    """Return every category for the landing-page navigation grid."""
    return list(Category.objects.all())
