from catalog.providers import DPage, Filters
from catalog.queries import cached_search

from next.pages import context
from next.urls import DQuery


@context("q")
def query_term(q: DQuery[str] = "") -> str:
    """Return the active search term."""
    return q


@context("page_obj")
def page_obj(page: DPage, q: DQuery[str] = "") -> dict:
    """Return paginated products matching the search term across all categories."""
    return cached_search(Filters(q=q), page.number, page.per_page)
