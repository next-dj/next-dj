from catalog.models import Category, Product
from catalog.providers import DFilters, DPage
from catalog.queries import cached_search
from django.http import Http404

from next.pages import context


@context("category", inherit_context=True)
def category(category: object) -> Category:
    """Resolve the category slug from the URL into a `Category` instance.

    The result is registered with `inherit_context=True` so child pages
    receive the same instance through DI without re-querying. The
    parameter is left untyped because `_collect_inherited_context`
    runs this callable twice when rendering `[category]/page.py`. The
    first run receives the URL slug as a string, the second receives
    the resolved instance from `context_data` and is short-circuited.
    """
    if isinstance(category, Category):
        return category
    try:
        return Category.objects.get(slug=category)
    except Category.DoesNotExist as exc:
        raise Http404 from exc


@context("page_obj")
def page_obj(
    category: Category,
    filters: DFilters,
    page: DPage,
) -> dict:
    """Return the cached search payload scoped to the current category."""
    return cached_search(
        filters,
        page.number,
        page.per_page,
        category=category,
    )


@context("all_brands")
def all_brands(category: Category) -> list[str]:
    """Return every brand that has products inside the current category."""
    return Product.objects.filter(category=category).brand_list()
