from typing import Annotated

from catalog.deps import DFilters, listing
from catalog.models import Category, Product
from catalog.services import brands, cached_count, search
from catalog.zones import CATEGORY_ZONES, zone_target
from django.shortcuts import get_object_or_404

from next import context, page
from next.pages import MetadataDict
from next.partial import Paging, Window


@context("filter_zones")
def filter_zones() -> str:
    """Name every zone the live filter re-renders on a category listing.

    The listing paginates, so the set drops the sentinel zone and keeps the pager.
    """
    return zone_target(CATEGORY_ZONES)


@context("category", inherit_context=True)
def category(category: str) -> Category:
    """Resolve the category slug from the URL, handed to the product page below too."""
    return get_object_or_404(Category, slug=category)


@page.metadata
def category_meta(category: Category) -> MetadataDict:
    """Title the listing after the category the inherited context resolved."""
    return {"title": category.name}


@context("products")
def products(
    category: Category, filters: DFilters, page: Annotated[Paging, listing]
) -> Window[Product]:
    """Return the page of the search scoped to the current category."""
    return page.paginate(search(filters, category=category))


@context("total")
def total(category: Category, filters: DFilters) -> int:
    """Return how many products of the category the search selects."""
    return cached_count(filters, category=category)


@context("all_brands")
def all_brands(category: Category) -> list[str]:
    """Return every brand that has products inside the current category."""
    return brands(category)
