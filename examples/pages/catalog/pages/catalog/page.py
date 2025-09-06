from catalog.models import Product

from next.pages import context


@context("products")
def prepare_products(*args, **kwargs):
    return Product.objects.all()


@context("other_context_variable")
def custom_name_abcdefg(*args, **kwargs):
    return "1234 + 5678"


template = """
<h1>Show products context variable</h1>
<ul>
    {% for product in products %}
        <li><a href="/catalog/{{ product.id }}/">{{ product.title }}</a></li>
    {% empty %}
        <li>No products found</li>
    {% endfor %}
</ul>

<h1>Show other context variable</h1>
<p>{{ other_context_variable }}</p>

<h1>Other context variables</h1>
<p>{{ var1 }}</p>
<p>{{ var2 }}</p>
<p>{{ var3 }}</p>
"""


@context
def show_other_context_variables(*args, **kwargs):
    return {
        "var1": "1",
        "var2": "2",
        "var3": "3",
    }
