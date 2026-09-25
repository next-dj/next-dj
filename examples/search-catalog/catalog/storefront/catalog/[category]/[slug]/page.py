from catalog.models import Category, Product
from django.http import Http404

from next import context, page
from next.pages import MetadataDict


@context("product")
def product(category: Category, slug: str) -> Product:
    """Return the product identified by the inherited category and the URL slug."""
    try:
        return Product.objects.select_related("category").get(
            category=category, slug=slug
        )
    except Product.DoesNotExist as exc:
        raise Http404 from exc


@page.metadata
def product_meta(product: Product) -> MetadataDict:
    """Title and describe the tab after the product the context above resolved."""
    return {"title": product.name, "description": product.description}
