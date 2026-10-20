from catalog.deps import DFilters
from catalog.models import Category, Filters
from django.urls import reverse

from next import component


@component.context("submit_url")
def submit_url(category: object = None) -> str:
    """Return the URL the filter form submits to.

    The parameter is typed `object` because DI may inject a slug or a `Category`.
    """
    if isinstance(category, Category):
        return reverse("next:page_catalog_category", kwargs={"category": category.slug})
    return reverse("next:page_catalog")


@component.context("current_filters")
def current_filters(filters: DFilters) -> Filters:
    """Return the current `Filters` snapshot used to pre-fill the form fields."""
    return filters
