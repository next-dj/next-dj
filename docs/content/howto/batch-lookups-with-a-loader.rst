.. _howto-batch-lookups-with-a-loader:

Batch lookups with a loader
===========================

Problem
-------

A product page shows two rails of recommended products, and each rail is a component that loads its own cards by primary key.
Each rail issues its own query, and a page with ten of them issues ten.

Solution
--------

Declare a ``Loader`` over a batch function and have every component ask it for one key with ``await load(key)``.
The loader gathers the keys one request scope asks for within a short window and calls the batch function once.
For plain rows by primary key, ``rows(Model)`` is the batch function already written.

Walkthrough
-----------

Write the batch function
~~~~~~~~~~~~~~~~~~~~~~~~

The batch function takes the keys and returns a mapping by key, or a sequence in key order.
It is a plain ``def`` when it touches the Django ORM.

.. code-block:: python
   :caption: shop/models.py

   from collections.abc import Sequence

   from django.db import models

   class Category(models.Model):
       name = models.CharField(max_length=100)

   class ProductQuerySet(models.QuerySet["Product"]):
       def cards(self, keys: Sequence[int]) -> dict[int, "Product"]:
           return self.select_related("category").in_bulk(keys)

   class Product(models.Model):
       name = models.CharField(max_length=200)
       category = models.ForeignKey("Category", on_delete=models.CASCADE)

       objects = ProductQuerySet.as_manager()

.. code-block:: python
   :caption: shop/deps.py

   from shop.models import Product

   from next.deps import Loader

   product_cards = Loader(Product.objects.cards)

A loader is a module-level object.
One instance serves every request, since each request keeps its batches and its memo on its own scope.

Load from components
~~~~~~~~~~~~~~~~~~~~

Each component awaits the loader once per key.
The loader batches the keys that its callers ask for concurrently.

.. code-block:: python
   :caption: shop/pages/product/_components/rail/component.py

   import asyncio

   from shop.deps import product_cards
   from shop.models import Product

   from next import component

   component.defer(timeout="1s")

   @component.context("picks")
   async def picks(product: Product, by: str) -> list[Product]:
       keys = [key async for key in Product.objects.picks_for(product, by=by)]
       return list(await asyncio.gather(*(product_cards.load(key) for key in keys)))

``picks_for`` is the project's own method of ``ProductQuerySet`` that answers the primary keys of the picks.
The rails live on the product page at ``/product/<pk>/``, whose ``page.py`` loads the product and whose template calls the component once per rail.

.. code-block:: jinja
   :caption: shop/pages/product/[int:pk]/template.djx

   <h1>{{ product.name }}</h1>
   {% component "rail" by="category" %}
   {% component "rail" by="bought_with" %}

.. code-block:: jinja
   :caption: shop/pages/product/_components/rail/component.djx

   <ul data-rail>
     {% for card in picks %}<li>{{ card.name }}, {{ card.category.name }}</li>{% endfor %}
   </ul>

``component.defer(timeout="1s")`` makes each call a ``stream`` boundary, and the ``loading.djx`` and ``error.djx`` beside it hold its loading UI and its error UI, see :doc:`show-a-loading-state`.
The two rails of the page are two streamed boundaries, and the keys of both reach one call of ``Product.objects.cards``.
``await asyncio.gather`` inside one callable batches the keys of that callable, and several deferred boundaries batch with each other.

A sync callable that runs on a thread calls ``load_sync(key)`` instead, which refuses to run on an event loop.

Load rows by primary key
~~~~~~~~~~~~~~~~~~~~~~~~

``rows(Model)`` returns the shared loader of the rows of a model, one ``pk__in`` query for a batch, through the default manager.

.. code-block:: python
   :caption: shop/pages/orders/_components/customer/component.py

   from shop.models import Customer

   from next import component
   from next.deps import rows

   @component.context("customer")
   async def customer(customer_id: int) -> Customer:
       return await rows(Customer).load(customer_id)

``rows`` applies no access scoping, unlike ``DObject``, so a page checks what the visitor may read before it loads a row this way.

Handle a key with no value
~~~~~~~~~~~~~~~~~~~~~~~~~~

A mapping that lacks a key raises ``MissingKeyError`` for that key alone, and the other keys of the batch answer normally.
A batch function that returns a short sequence, or raises, fails every key of the batch.
A key loaded once in a request is answered from memory, its error included.

.. code-block:: python
   :caption: shop/pages/product/_components/rail/component.py, a helper

   from shop.deps import product_cards
   from shop.models import Product

   from next.deps import MissingKeyError

   async def card_or_none(key: int) -> Product | None:
       try:
           return await product_cards.load(key)
       except MissingKeyError:
           return None

Know when a batch closes
~~~~~~~~~~~~~~~~~~~~~~~~

A batch closes once every deferred job of the response that runs or is about to run waits on a loader or has ended, at ``LOADER["MAX_BATCH"]`` keys, five hundred by default, or ``LOADER["WINDOW"]`` after its first key, one millisecond by default.
A loader built with ``max_batch=`` or ``window=`` keeps its own limits, and one built without them reads ``DEFER`` each time.

Where the jobs run decides how well they share a batch.
On the pool the batch function is called once with the keys of both rails, unless one rail asks for its first key more than one window after the other, as a slow query or a busy machine may make it.
On SQLite the jobs run on the request lane one after the other, so each job loads one batch of its own, which is still one query per rail rather than one per card.
Code that runs serially, such as a fully sync page, the shell render, or the render of a reloaded boundary, calls the batch function with one key per call.
An ``async def`` batch function serves native async I/O only, so a batch that queries the ORM is a plain ``def``.

Verification
------------

Wrap the batch function of the loader in a fixture that records the keys of every call.
The test then asserts how many calls a page made.
The ``product`` fixture of the project saves a product whose two rails both have picks, since a rail without keys never calls the loader.

.. code-block:: python
   :caption: tests/conftest.py

   import pytest
   from shop.deps import product_cards

   @pytest.fixture()
   def batches(monkeypatch) -> list[list[int]]:
       calls: list[list[int]] = []
       batch = product_cards.batch

       def recorded(keys):
           calls.append(sorted(keys))
           return batch(keys)

       monkeypatch.setattr(product_cards, "batch", recorded)
       return calls

.. code-block:: python
   :caption: tests/test_product_page.py

   import pytest

   from next.testing import override_next_settings, parallel_boundaries

   @pytest.mark.django_db(transaction=True)
   def test_the_rails_on_the_pool_share_one_batch(next_client, product, batches) -> None:
       with (
           parallel_boundaries(),
           override_next_settings(LOADER={"WINDOW": "1s"}),
       ):
           next_client.get_stream(f"/product/{product.pk}/")
       assert len(batches) == 1

   def test_the_rails_on_the_request_thread_load_one_after_the_other(
       next_client, product, batches
   ) -> None:
       next_client.get(f"/product/{product.pk}/")
       assert len(batches) == 2

``parallel_boundaries()`` places the jobs on the pool, and a pool thread reads only committed rows, hence ``transaction=True``.
The wider window keeps a loaded test machine from splitting the batch, as the batch still closes the moment both rails wait on it.
The second test runs under the test client, where each job runs on the request thread.

See also
--------

.. seealso::

   :doc:`/content/topics/boundaries/async` for the request scope, the lanes, and the limits of a loader.
   :doc:`/content/ref/deps` for ``Loader``, ``rows``, and ``MissingKeyError``.
   :doc:`/content/ref/settings` for ``LOADER["MAX_BATCH"]`` and ``LOADER["WINDOW"]``.
   :doc:`test-a-page-with-deferred-boundaries` for ``parallel_boundaries`` and ``get_stream``.
   :repo:`search-catalog <tree/main/examples/search-catalog>` for the two rails and the tests of both lanes.
