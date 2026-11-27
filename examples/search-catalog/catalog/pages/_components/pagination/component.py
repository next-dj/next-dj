from math import ceil

from catalog.deps import listing
from catalog.models import Product

from next import component
from next.partial import Window


@component.context("position")
def position(products: Window[Product], total: int) -> str:
    """Return the page the window shows out of every page, empty past the last one."""
    if not products:
        return ""
    current = int(products.previous) + 1 if products.previous else 1
    return f"Page {current} of {ceil(total / listing.per_page)}"
