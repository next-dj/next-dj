from catalog.models import Product
from django.shortcuts import get_object_or_404

from next.pages import page

template = """
<h1>Product details</h1>
<h2>{{ product.title }}</h2>

<p>{{ product.description }}</p>
"""


@page.context
def common_context_with_custom_name(*args, **kwargs):
    product = get_object_or_404(Product, id=kwargs.get("id"))

    return {"product": product}
