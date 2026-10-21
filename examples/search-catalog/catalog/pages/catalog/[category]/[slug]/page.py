from catalog.models import Category, Product
from catalog.services import product_in
from django.http import Http404

from next import context, page
from next.pages import MetadataDict


CURRENCY = "USD"
IN_STOCK = "https://schema.org/InStock"
OUT_OF_STOCK = "https://schema.org/OutOfStock"


@context("product")
def product(category: Category, slug: str) -> Product:
    """Return the product identified by the inherited category and the URL slug."""
    try:
        return product_in(category, slug)
    except Product.DoesNotExist as exc:
        raise Http404 from exc


@page.metadata
def product_meta(product: Product) -> MetadataDict:
    """Title the tab after the product and publish its offer as structured data."""
    return {
        "title": product.name,
        "description": product.description,
        "jsonld": [
            {
                "@type": "Product",
                "@id": f"{product.get_absolute_url()}#product",
                "name": product.name,
                "sku": f"{product.category.slug}-{product.slug}",
                "brand": {"@type": "Brand", "name": product.brand},
                "offers": {
                    "@type": "Offer",
                    "price": product.price,
                    "priceCurrency": CURRENCY,
                    "availability": IN_STOCK if product.in_stock else OUT_OF_STOCK,
                },
            }
        ],
    }
