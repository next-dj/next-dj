from catalog.models import Category, Product
from catalog.providers import DFilters, DPage
from catalog.queries import cached_search
from catalog.zones import LISTING_ZONES, zone_target

from next import context


@context("filter_zones")
def filter_zones() -> str:
    """Name every zone the live filter re-renders on the all-products listing.

    Page-local, never inherited, so the value stops at this listing. The
    filter panel renders inside the shared `catalog/` layout and reads this
    key to decide whether it may target zones at all. The product detail
    page leaves it unset, so the same panel falls back to a plain GET
    instead of asking for zones that page never declares.
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
