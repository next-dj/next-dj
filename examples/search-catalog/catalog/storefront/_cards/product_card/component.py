from catalog.models import Product

from next import component


@component.context("detail_url")
def detail_url(product: Product) -> str:
    """Return the canonical detail URL for the product card."""
    return product.get_absolute_url()


@component.context("price_label")
def price_label(product: Product) -> str:
    """Return the formatted price label rendered by the card."""
    return f"${product.price}"


@component.context("stock_class")
def stock_class(product: Product) -> str:
    """Return Tailwind classes that mark the stock badge state."""
    if product.in_stock:
        return "bg-emerald-100 text-emerald-800"
    return "bg-rose-100 text-rose-800"
