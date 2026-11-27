from typing import Annotated

from catalog.deps import DFilters, listing
from catalog.models import Category, Product
from catalog.services import brands, cached_count, list_categories, search
from catalog.zones import LISTING_ZONES, zone_target

from next import context
from next.pages import MetadataDict
from next.partial import Paging, Window


metadata: MetadataDict = {"title": "All products", "canonical": True}


@context("filter_zones")
def filter_zones() -> str:
    """Name every zone the live filter re-renders on the all-products listing.

    Page-local and never inherited, so the product detail page uses a plain GET.
    """
    return zone_target(LISTING_ZONES)


@context("products")
def products(filters: DFilters, page: Annotated[Paging, listing]) -> Window[Product]:
    """Return the window of the search the listing shows or restores."""
    return page.paginate(search(filters))


@context("total")
def total(filters: DFilters) -> int:
    """Return how many products the search selects, counted once per minute."""
    return cached_count(filters)


@context("all_categories")
def all_categories() -> list[Category]:
    """Return every category for sidebar navigation."""
    return list_categories()


@context("all_brands")
def all_brands() -> list[str]:
    """Return every brand currently present in the catalog."""
    return brands()
