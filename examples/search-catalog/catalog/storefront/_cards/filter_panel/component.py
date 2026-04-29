from catalog.models import Category
from catalog.providers import DFilters, Filters
from django.urls import reverse

from next.components import component


@component.context("submit_url")
def _submit_url(category: Category | None = None) -> str:
    """Return the URL the filter form submits to.

    The form posts back to the current category page when the panel is
    rendered inside `[category]/`. Otherwise it targets the all-products
    listing.
    """
    if category is not None:
        return reverse(
            "next:page_catalog_category",
            kwargs={"category": category.slug},
        )
    return reverse("next:page_catalog")


@component.context("current_filters")
def _current_filters(filters: DFilters) -> Filters:
    """Return the current `Filters` snapshot used to pre-fill the form fields."""
    return filters
