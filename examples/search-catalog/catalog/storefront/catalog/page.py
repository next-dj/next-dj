from catalog.models import Category, Product
from catalog.providers import DFilters, DPage
from catalog.queries import cached_search

from next.pages import context


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
    return list(
        Product.objects.order_by("brand").values_list("brand", flat=True).distinct(),
    )
