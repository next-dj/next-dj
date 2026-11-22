.. _howto-show-a-loading-state:

Show a loading state for a slow component
=========================================

Problem
-------

A product page shows a list of recommendations that takes a second to compute.
The page waits for it, so the visitor sees nothing until the slowest part is ready.

Solution
--------

Put a ``loading.djx`` beside the component.
The response then sends the page with the loading UI in the component's place and fills the component in the same response as soon as it is ready.
Nothing else in the component or the page changes.

Walkthrough
-----------

Add the loading UI
~~~~~~~~~~~~~~~~~~

``Product`` is the project's model and ``recommend(product)`` the project's slow query, which returns a list of products.
The page lives at ``/product/<pk>/`` and its component sits in the ``_components/`` directory of ``product/``, which the page below it sees.

.. code-block:: text

   shop/pages/product/
       [int:pk]/
           page.py
           template.djx
       _components/recommendations/
           component.py
           component.djx
           loading.djx

The page module loads the product, and the page template calls the component with the ordinary tag.

.. code-block:: python
   :caption: shop/pages/product/[int:pk]/page.py

   from django.shortcuts import get_object_or_404
   from shop.models import Product

   from next import context

   @context("product")
   def product(pk: int) -> Product:
       return get_object_or_404(Product, pk=pk)

.. code-block:: jinja
   :caption: shop/pages/product/[int:pk]/template.djx

   <h1>{{ product.name }}</h1>
   {% component "recommendations" %}

The component keeps its template and its context callable.
The slow work stays where it was.

.. code-block:: python
   :caption: shop/pages/product/_components/recommendations/component.py

   from shop.models import Product
   from shop.services import recommend

   from next import component

   @component.context("related")
   def related(product: Product) -> list[Product]:
       return recommend(product)

.. code-block:: jinja
   :caption: shop/pages/product/_components/recommendations/component.djx

   <ul data-recommendations>
     {% for item in related %}<li>{{ item.name }}</li>{% endfor %}
   </ul>

.. code-block:: jinja
   :caption: shop/pages/product/_components/recommendations/loading.djx

   <p data-recommendations-skeleton>Finding related products&hellip;</p>

The presence of ``loading.djx`` turns each call of the component into a ``stream`` boundary.
The shell carries the loading UI inside a wrapper element, the component's job runs while the response is written, and a chunk replaces the loading UI with the component when the job settles.
The component reads the context of the call site, so ``product`` comes from the page as it does for an inline call.

Show a loading UI that tells what is coming
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The loading UI renders over the context of the call site and reads ``props``, the props of the call.
A skeleton keeps the layout from shifting when the content arrives, and a visually hidden label tells a screen reader what is loading.

.. code-block:: jinja
   :caption: shop/pages/product/_components/recommendations/loading.djx

   <section class="space-y-3" data-recommendations-skeleton>
     <p class="sr-only">Loading related products</p>
     <div class="h-6 w-1/3 animate-pulse rounded bg-muted"></div>
     <div class="h-24 animate-pulse rounded bg-muted"></div>
   </section>

The wrapper carries ``data-next-state``, ``loading`` until the job settles, then ``ready``, so a stylesheet styles the wait without touching the markup.
The wrapper also carries ``aria-busy="true"`` while the state is ``loading``.

.. code-block:: css
   :caption: shop/pages/product/[int:pk]/template.css

   [data-next-state="loading"] { opacity: 0.7; }

Cap the wait
~~~~~~~~~~~~

``timeout`` bounds the job of one boundary, and ``component.defer()`` at the top of ``component.py`` declares it.
The call without a mode keeps the default, ``stream``.

.. code-block:: python
   :caption: shop/pages/product/_components/recommendations/component.py

   from shop.models import Product
   from shop.services import recommend

   from next import component

   component.defer(timeout="2s")

   @component.context("related")
   def related(product: Product) -> list[Product]:
       return recommend(product)

A job past its timeout settles as ``timeout``, and the visitor sees the ``error.djx`` beside the component in place of the loading UI.
The error UI reads ``error``, and ``error.retry`` tells whether the page loads the client runtime a retry control needs.

.. code-block:: jinja
   :caption: shop/pages/product/_components/recommendations/error.djx

   <div data-recommendations-error>
     <p>Related products are unavailable right now.</p>
     {% if error.retry %}<button type="button" data-next-retry>Try again</button>{% endif %}
   </div>

Without an ``error.djx`` a deferred component renders an empty wrapper marked as an error, which ``manage.py check`` reports as ``next.W170``.
:doc:`contain-a-failure-with-an-error-boundary` covers the error UI in full.

Load on sight instead
~~~~~~~~~~~~~~~~~~~~~

A component below the fold can wait until the visitor scrolls to it.
The mode argument of ``component.defer()`` names the delivery, and the call site passes what the component renders from.

.. code-block:: python
   :caption: shop/pages/product/_components/recommendations/component.py

   from shop.models import Product
   from shop.services import recommend

   from next import component

   component.defer("visible", props=("product_id",), timeout="2s")

   @component.context("related")
   def related(product_id: int) -> list[Product]:
       return recommend(Product.objects.get(pk=product_id))

.. code-block:: jinja
   :caption: shop/pages/product/[int:pk]/template.djx

   <h1>{{ product.name }}</h1>
   {% component "recommendations" product_id=product.pk %}

A ``visible`` component renders on its own request from its declared props alone, so it takes ``product_id`` as a prop and not the ``product`` of the page context, see :doc:`/content/topics/boundaries/loading` for the prop types and the rules of a client-trip boundary.
``stream``, the default, ``load``, ``visible``, and ``manual`` differ only in what triggers the fill.
A call site names the mode with a literal, such as ``{% component "recommendations" product_id=product.pk defer="load" %}``, and overrides the declaration for that call.
A client-trip component sends no chunk in the response, so the Verification below reads the ``stream`` version of the earlier sections.

Give a slow page a loading UI
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A ``loading.djx`` in a page directory stands in for everything below that directory, the page body and the inner layouts.
The layouts above it render in the shell, so the navigation stays on screen while the body loads.
The observability example does this for its ``/stats/pages/`` table.

.. code-block:: text

   shop/pages/orders/
       page.py
       template.djx
       loading.djx

``page.defer()`` in the page module caps the job of the page body as ``component.defer()`` does for a component.

.. code-block:: python
   :caption: shop/pages/orders/page.py

   from next import page

   page.defer(timeout="3s")

A page whose body can answer 404 or 403 keeps that status, because the callables that can refuse the request run in the shell, see :doc:`/content/topics/boundaries/loading`.

Verification
------------

Read the page the way a threaded server streams it.
The Django test client serves one request at a time, so ``get_stream`` asks for the stream explicitly.
The tests run under pytest-django, set up as :doc:`test-a-page-with-actions` shows, and a fixture saves the product the page reads.

.. code-block:: python
   :caption: tests/conftest.py

   import pytest
   from shop.models import Product

   @pytest.fixture()
   def product(db) -> Product:
       return Product.objects.create(name="Desk lamp")

.. code-block:: python
   :caption: tests/test_product_page.py

   from next.testing import NextClient

   def test_the_loading_ui_ships_first(product) -> None:
       streamed = NextClient().get_stream(f"/product/{product.pk}/")
       assert "data-recommendations-skeleton" in streamed.shell
       assert "<ul data-recommendations>" not in streamed.shell
       [chunk] = streamed.chunks
       assert chunk.state == "ready"
       assert "<ul data-recommendations>" in chunk.html

``uv run pytest tests/test_product_page.py`` passes.
In a browser, open the page with the network throttled.
The page appears at once with the placeholder, and the list replaces it without a reload.
``manage.py check`` reports no ``next.W157`` or ``next.W170`` for the component.

See also
--------

.. seealso::

   :doc:`/content/topics/boundaries/loading` for the delivery modes, the wrapper, and the tokens of a client-trip boundary.
   :doc:`/content/topics/boundaries/streaming` for what decides whether a response streams.
   :doc:`test-a-page-with-deferred-boundaries` for gates and the settled content.
   :repo:`search-catalog <tree/main/examples/search-catalog>` for two streamed rails on a product page, and :repo:`observability <tree/main/examples/observability>` for a streamed component and a page boundary.
