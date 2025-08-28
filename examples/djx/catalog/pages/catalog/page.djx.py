from catalog.models import Product
from django.http import HttpRequest

from next.templates import djx

djx % """
<h1>Catalog</h1>

<ul>
    {% for product in products %}
        <li><a href="/catalog/{{ product.id }}/">{{ product.title }}</a></li>
    {% endfor %}
</ul>
"""


@djx.context("products")
def prepare_products(request: HttpRequest):
    return Product.objects.all()
