from catalog.deps import DFilters, DPage
from catalog.models import Category
from catalog.services import brands, cached_search, list_categories
from catalog.zones import LISTING_ZONES, zone_target

from next import context
from next.pages import MetadataDict


metadata: MetadataDict = {"title": "All products", "canonical": True}


@context("filter_zones")
def filter_zones() -> str:
    """Name every zone the live filter re-renders on the all-products listing.

    Page-local and never inherited, so the product detail page uses a plain GET.
    """
    return zone_target(LISTING_ZONES)


@context("page_obj")
def page_obj(filters: DFilters, page: DPage) -> dict:
    """Return the cached search payload for the all-products listing."""
    return cached_search(filters, page.number, page.per_page)


@context("all_categories")
def all_categories() -> list[Category]:
    """Return every category for sidebar navigation."""
    return list_categories()


@context("all_brands")
def all_brands() -> list[str]:
    """Return every brand currently present in the catalog."""
    return brands()
