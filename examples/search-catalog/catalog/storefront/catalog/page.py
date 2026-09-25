from catalog.models import Category, Product
from catalog.providers import DFilters, DPage
from catalog.queries import cached_search
from catalog.zones import LISTING_ZONES, zone_target

from next import context
from next.pages import MetadataDict


metadata: MetadataDict = {"title": "All products", "canonical": True}


@context("filter_zones")
def filter_zones() -> str:
    """Name every zone the live filter re-renders on the all-products listing.

    Page-local and never inherited, so the filter panel in the shared layout targets
    zones here and falls back to a plain GET on the product detail page.
    """
    return zone_target(LISTING_ZONES)


@context("page_obj")
def page_obj(filters: DFilters, page: DPage) -> dict:
    """Return the cached search payload for the all-products listing."""
    return cached_search(filters, page.number, page.per_page)


@context("all_categories")
def all_categories() -> list[Category]:
    """Return every category for sidebar navigation."""
    return list(Category.objects.all())


@context("all_brands")
def all_brands() -> list[str]:
    """Return every brand currently present in the catalog."""
    return Product.objects.brand_list()
