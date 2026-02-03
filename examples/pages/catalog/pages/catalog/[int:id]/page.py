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
    request: HttpRequest, *_args, **kwargs
) -> dict[str, Product]:
    product_id = kwargs.get("id")
    if product_id is None or product_id == "id":
        product_id = (
            request.resolver_match.kwargs.get("id") if request.resolver_match else None
        )

    product = get_object_or_404(Product, id=product_id)
    return {"product": product}
