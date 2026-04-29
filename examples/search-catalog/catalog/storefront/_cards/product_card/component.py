from catalog.models import Product
from django.urls import reverse

from next.components import component


@component.context("detail_url")
def _detail_url(product: Product) -> str:
    """Return the canonical detail URL for the product card."""
    return reverse(
        "next:page_catalog_category_slug",
        kwargs={"category": product.category.slug, "slug": product.slug},
    )


@component.context("price_label")
def _price_label(product: Product) -> str:
    """Return the formatted price label rendered by the card."""
    return f"${product.price}"


@component.context("stock_class")
def _stock_class(product: Product) -> str:
    """Return Tailwind classes that mark the stock badge state."""
    if product.in_stock:
        return "bg-emerald-100 text-emerald-800"
    return "bg-rose-100 text-rose-800"
