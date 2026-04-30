from __future__ import annotations

from django.urls import reverse
from wiki.models import Article

from next.pages import context


@context("file_pages")
def file_pages() -> list[dict[str, str]]:
    """Curated catalogue of file-backed documentation pages on the index."""
    routing_summary = (
        "How file paths map to URL patterns and how the unified view runs."
    )
    components_summary = (
        "Composite components with co-located CSS and JS plus markdown_preview."
    )
    return [
        {
            "href": reverse("next:page_docs_routing"),
            "title": "Routing",
            "summary": routing_summary,
        },
        {
            "href": reverse("next:page_docs_components"),
            "title": "Components",
            "summary": components_summary,
        },
    ]


@context("articles")
def articles() -> list[Article]:
    """Latest published articles, freshest first."""
    return list(Article.objects.all())
