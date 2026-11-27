import asyncio

from catalog.deps import product_cards
from catalog.models import Product

from next import component


component.defer(timeout="1s")


@component.context("picks")
async def picks(product: Product, by: str) -> list[Product]:
    """Return the products picked beside `product`, each loaded through the batch."""
    keys = [key async for key in Product.objects.picks_for(product, by=by)]
    return await asyncio.gather(*(product_cards.load(key) for key in keys))
