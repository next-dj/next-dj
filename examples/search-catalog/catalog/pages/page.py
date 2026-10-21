from catalog.models import Category, Product
from catalog.services import featured_products, list_categories

from next import context
from next.urls import DQuery


DEFAULT_FEATURED = 3
MAX_FEATURED = 12


@context("featured")
def featured(show: DQuery[int] = DEFAULT_FEATURED) -> list[Product]:
    """Return featured products for the home page.

    `?show=N` widens the grid up to `MAX_FEATURED`, which caps the query size.
    """
    count = max(1, min(MAX_FEATURED, show))
    return featured_products(count)


@context("categories")
def categories() -> list[Category]:
    """Return every category for the landing-page navigation grid."""
    return list_categories()
