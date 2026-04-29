from django.urls import reverse

from next.components import component
from next.urls import DQuery


@component.context("search_url")
def _search_url() -> str:
    return reverse("next:page_search")


@component.context("current_q")
def _current_q(q: DQuery[str] = "") -> str:
    """Return the active query term to pre-fill the header input."""
    return q
