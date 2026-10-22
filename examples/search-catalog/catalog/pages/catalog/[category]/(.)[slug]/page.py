from catalog.models import Category, Product
from catalog.services import product_in
from django.http import Http404

from next import context, page
from next.pages import MetadataDict


@context("peek")
def peek(category: Category, slug: str) -> Product:
    """Return the product the quick view covers, 404 when the category lacks it."""
    try:
        return product_in(category, slug)
    except Product.DoesNotExist as exc:
        raise Http404 from exc


@page.metadata
def peek_meta(peek: Product) -> MetadataDict:
    """Title the tab after the product while the quick view covers the listing."""
    return {"title": f"Quick view · {peek.name}", "robots": "noindex"}
