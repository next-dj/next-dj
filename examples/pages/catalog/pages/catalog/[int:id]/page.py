from catalog.models import Product
from django.http import HttpRequest
from django.shortcuts import get_object_or_404

from next.pages import page


template = """
<h1>Product details</h1>
<h2>{{ product.title }}</h2>

<p>{{ product.description }}</p>
"""


@page.context
def common_context_with_custom_name(
    _request: HttpRequest,
    id: int,  # noqa: A002
) -> dict[str, Product]:
    product = get_object_or_404(Product, id=id)
    return {"product": product}
