from catalog.models import Category, Product
from django.http import Http404

from next.pages import context


@context("product")
def product(category: Category, slug: str) -> Product:
    """Return the product identified by the inherited category and the URL slug."""
    try:
        return Product.objects.select_related("category").get(
            category=category,
            slug=slug,
        )
    except Product.DoesNotExist as exc:
        raise Http404 from exc
