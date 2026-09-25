from catalog.models import Category, Product
from catalog.providers import DFilters, DPage
from catalog.queries import cached_search
from catalog.zones import CATEGORY_ZONES, zone_target
from django.http import Http404

from next import context, page
from next.pages import MetadataDict


@context("filter_zones")
def filter_zones() -> str:
    """Name every zone the live filter re-renders on a category listing.

    The category listing paginates instead of growing on scroll, so the set drops
    `catalog-more` and keeps the pager beside the results.
    """
    return zone_target(CATEGORY_ZONES)


@context("category", inherit_context=True)
def category(category: object) -> Category:
    """Resolve the category slug from the URL into a `Category` instance.

    Stays untyped because `_collect_inherited_context` runs this callable twice, once
    with the URL slug and once with the resolved instance, to hand child pages the same.
    """
    if isinstance(category, Category):
        return category
    try:
        return Category.objects.get(slug=category)
    except Category.DoesNotExist as exc:
        raise Http404 from exc


@page.metadata
def category_meta(category: Category) -> MetadataDict:
    """Title the listing after the category the inherited context resolved."""
    return {"title": category.name}


@context("page_obj")
def page_obj(category: Category, filters: DFilters, page: DPage) -> dict:
    """Return the cached search payload scoped to the current category."""
    return cached_search(filters, page.number, page.per_page, category=category)


@context("all_brands")
def all_brands(category: Category) -> list[str]:
    """Return every brand that has products inside the current category."""
    return Product.objects.filter(category=category).brand_list()
