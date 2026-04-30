from catalog.models import Category
from catalog.providers import DFilters, Filters
from django.urls import reverse

from next.components import component


@component.context("submit_url")
def submit_url(category: object = None) -> str:
    """Return the URL the filter form submits to.

    The form posts back to the current category page when the panel is
    rendered inside `[category]/`. Otherwise it targets the all-products
    listing. The parameter is typed `object` because DI may inject
    either a slug string or a resolved `Category` instance.
    """
    if isinstance(category, Category):
        return reverse(
            "next:page_catalog_category",
            kwargs={"category": category.slug},
        )
    return reverse("next:page_catalog")


@component.context("current_filters")
def current_filters(filters: DFilters) -> Filters:
    """Return the current `Filters` snapshot used to pre-fill the form fields."""
    return filters
