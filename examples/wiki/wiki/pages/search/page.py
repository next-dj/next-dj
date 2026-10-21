from django.http import HttpRequest
from django.urls import reverse
from wiki.services import search_articles

from next import context
from next.pages import MetadataDict


metadata: MetadataDict = {"title": "Search"}

FILE_DOC_CATALOGUE = (
    {
        "url_name": "next:page_docs_routing",
        "title": "Routing",
        "haystack": "routing url patterns hybrid file router database",
    },
    {
        "url_name": "next:page_docs_components",
        "title": "Components",
        "haystack": "components composite markdown preview javascript",
    },
)


@context("query")
def query(request: HttpRequest) -> str:
    """Echo the trimmed user query for the search box."""
    return request.GET.get("q", "").strip()


@context("results")
def results(request: HttpRequest) -> dict[str, list]:
    """Mixed result set with file pages and database articles."""
    needle = request.GET.get("q", "").strip().lower()
    if not needle:
        return {"file_pages": [], "articles": []}
    file_matches = [
        {"href": reverse(entry["url_name"]), "title": entry["title"]}
        for entry in FILE_DOC_CATALOGUE
        if needle in entry["title"].lower() or needle in entry["haystack"]
    ]
    return {"file_pages": file_matches, "articles": search_articles(needle)}
