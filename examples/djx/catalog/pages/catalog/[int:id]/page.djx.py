from catalog.models import Product
from django.http import HttpRequest
from django.shortcuts import get_object_or_404

from next.templates import djx

djx % """
<h1>Product details</h1>
<h2>{{ product.title }}</h2>

<p>{{ product.description }}</p>
"""


@djx.context
def common_context_with_custom_name(request: HttpRequest, id: int):
    product = get_object_or_404(Product, id=id)

    return {"product": product}
