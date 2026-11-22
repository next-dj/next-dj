.. _howto-test-a-page-with-deferred-boundaries:

Test a page with deferred boundaries
====================================

Problem
-------

A page streams a slow component behind its loading UI.
The Django test client assembles every page in one piece, so a plain ``get`` never shows the loading UI, and a test that waits for the real job is slow and flaky.

Solution
--------

Read the page as a stream with ``NextClient.get_stream``, which returns the shell, the chunks, and the page as the browser ends up with it.
Hold a job with ``boundary_gate`` to test the loading UI and the timeout, and render the error UI with ``boundary_errors("render")``.
Reload a client-trip component with ``get_boundary``.

Walkthrough
-----------

Set up the plugin once, as :doc:`test-a-page-with-actions` shows, so ``next_client`` and the page root are in place.
The examples read the product page of :doc:`show-a-loading-state`, with its page module, its ``product`` fixture, and its ``recommendations`` component, which streams with a ``loading.djx``, an ``error.djx``, and ``timeout="2s"``.

Read the shell and the chunks
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``get_stream`` runs the request the way a threaded server streams it and returns a ``StreamedPage``.
Its ``shell`` is the first write with every deferred body replaced by its loading UI, its ``chunks`` are the settled boundaries in the order they arrived, and its ``tail`` is the rest of the document.

.. code-block:: python
   :caption: tests/test_product_page.py

   def test_the_shell_ships_the_loading_ui(next_client, product) -> None:
       streamed = next_client.get_stream(f"/product/{product.pk}/")
       assert "data-recommendations-skeleton" in streamed.shell
       assert "<ul data-recommendations>" not in streamed.shell

   def test_a_chunk_settles_the_boundary(next_client, product) -> None:
       streamed = next_client.get_stream(f"/product/{product.pk}/")
       [chunk] = streamed.chunks
       assert chunk.state == "ready"
       assert "<ul data-recommendations>" in chunk.html

   def test_the_page_ends_with_the_content_in_place(next_client, product) -> None:
       streamed = next_client.get_stream(f"/product/{product.pk}/")
       assert "<ul data-recommendations>" in streamed.final_html()
       assert streamed.ended

``final_html()`` applies each chunk to the shell in order, as the browser would, so a test reads the settled page without parsing scripts.
``streamed.boundary(key)`` returns the chunk of one boundary by its wire id, its stable key, or its name, the component name or the path a page boundary is named by.
A test of an async stack awaits ``aread_stream(AsyncClient(), url)`` of ``next.testing`` for the same result.
``get_stream`` raises ``AssertionError`` when the page did not stream, and names the reason when ``DEBUG`` is on.

Hold a job
~~~~~~~~~~

A gate holds the jobs of one component, or of a page boundary, until the test releases it or the job runs out of time.
Held until its timeout, a job settles as ``timeout``, which tests the error UI of the slow path with no slow code in the test.
The test lasts as long as that timeout, two seconds for this component, and for a component that declares no ``timeout`` the test settings lower ``DEFER["DEADLINE"]``.

.. code-block:: python
   :caption: tests/test_product_page.py

   from next.testing import boundary_errors, boundary_gate

   def test_a_slow_component_gives_way_to_its_error_ui(next_client, product) -> None:
       with boundary_errors("render"), boundary_gate(component="recommendations"):
           streamed = next_client.get_stream(f"/product/{product.pk}/")
       [chunk] = streamed.chunks
       assert (chunk.state, chunk.kind) == ("error", "timeout")
       assert "data-next-retry" in chunk.html

``boundary_gate(page=directory)`` holds the job of the page boundary whose ``loading.djx`` sits in ``directory``, an absolute path or one relative to ``BASE_DIR`` such as ``"shop/pages/orders"``.
A directory without a ``loading.djx`` and a component name no component source holds raise ``LookupError`` as the block opens, so a typo fails the test instead of holding nothing.
``after_shell=True`` releases the gate once the shell was read, so a test asserts the loading UI while the job is held and still receives the settled chunk.

.. code-block:: python
   :caption: tests/test_product_page.py

   def test_the_loading_ui_holds_until_the_shell_is_read(next_client, product) -> None:
       with boundary_gate(component="recommendations", after_shell=True) as held:
           streamed = next_client.get_stream(f"/product/{product.pk}/")
       assert held.entered.is_set()
       assert "data-recommendations-skeleton" in streamed.shell
       assert streamed.chunks[0].state == "ready"

Without ``boundary_errors("render")`` a failure a boundary contains is re-raised when the response ends, so a broken component fails its test instead of hiding behind its error UI.
Every failure is also on ``response.boundary_failures`` for a test that renders them.

Run jobs on the pool
~~~~~~~~~~~~~~~~~~~~

Under the test client each job runs on the request thread, and a test that checks concurrency or a batch needs the pool.
``parallel_boundaries()`` places the jobs of the block on the pool.
A pool thread reads only committed rows, so the test is marked ``@pytest.mark.django_db(transaction=True)``.

.. code-block:: python
   :caption: tests/test_product_page.py

   import pytest

   from next.testing import parallel_boundaries

   @pytest.mark.django_db(transaction=True)
   def test_the_job_settles_on_the_pool(next_client, product) -> None:
       with parallel_boundaries():
           streamed = next_client.get_stream(f"/product/{product.pk}/")
       [chunk] = streamed.chunks
       assert chunk.state == "ready"
       assert "<ul data-recommendations>" in chunk.html

The product page streams one job, so the test shows the setup and has no concurrency to measure.
:doc:`batch-lookups-with-a-loader` uses the same block on a page with two streamed rails, and its test asserts that both rails share one call of the batch function of their ``Loader``.

Reload a client-trip component
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A ``load``, ``visible``, or ``manual`` component renders on its own request, from the props it declares alone.
The product page gains one below the fold, ``also_viewed``, which loads on sight and takes the product as a prop.

.. code-block:: text

   shop/pages/product/_components/also_viewed/
       component.py
       component.djx
       loading.djx
       error.djx

.. code-block:: python
   :caption: shop/pages/product/_components/also_viewed/component.py

   from shop.models import Product
   from shop.services import recommend

   from next import component

   component.defer("visible", props=("product_id",), timeout="2s")

   @component.context("viewed")
   def viewed(product_id: int) -> list[Product]:
       return recommend(Product.objects.get(pk=product_id))

.. code-block:: jinja
   :caption: shop/pages/product/_components/also_viewed/component.djx

   <ul data-also-viewed>
     {% for item in viewed %}<li>{{ item.name }}</li>{% endfor %}
   </ul>

.. code-block:: jinja
   :caption: shop/pages/product/_components/also_viewed/loading.djx

   <p data-also-viewed-skeleton>Finding what others viewed&hellip;</p>

.. code-block:: jinja
   :caption: shop/pages/product/_components/also_viewed/error.djx

   <p data-also-viewed-error>The list is unavailable right now.</p>

.. code-block:: jinja
   :caption: shop/pages/product/[int:pk]/template.djx

   <h1>{{ product.name }}</h1>
   {% component "recommendations" %}
   {% component "also_viewed" product_id=product.pk %}

The shell carries the loading UI of ``also_viewed``, and no chunk of the response fills it, so the tests above still read one chunk.
``get_boundary(url, name, **props)`` mints the token of the component and sends the request the browser sends, and the envelope it returns holds the op that settles the boundary.

.. code-block:: python
   :caption: tests/test_product_page.py

   def test_the_reload_delivers_the_list(next_client, product) -> None:
       envelope = next_client.get_boundary(
           f"/product/{product.pk}/", "also_viewed", product_id=product.pk
       )
       [op] = envelope.ops
       assert op["s"] == "ready"
       assert "<ul data-also-viewed>" in op["html"]

``follow_boundary(response, key)`` reloads a wrapper the rendered document holds with the token that document carries, which tests the full round trip including the transport the settings choose.

Test a token
~~~~~~~~~~~~

A token lives ``MAX_AGE`` seconds, and a reload with an expired one answers ``409`` with ``X-Next-Reload: token-expired``.
``freeze_token_clock(at)`` holds the clock the tokens read, ``issue_boundary_token`` mints a token through the configured backend, and ``expire_boundary_token`` mints one whose life ended a second ago.

.. code-block:: python
   :caption: tests/test_tokens.py

   import pytest
   from django.test import RequestFactory

   from next.suspense import BoundaryTokenError, TokenRejection
   from next.suspense.manager import suspense_manager
   from next.suspense.tokens import BoundaryClaims, BoundaryTarget
   from next.testing import expire_boundary_token

   def test_an_expired_token_is_refused() -> None:
       request = RequestFactory().get("/product/7/")
       claims = BoundaryClaims.of(
           "recommendations", "pages/product", url_path="/product/7/"
       )
       token = expire_boundary_token(request, claims)
       target = BoundaryTarget("recommendations", "pages/product", "/product/7/")
       with pytest.raises(BoundaryTokenError) as refused:
           suspense_manager.tokens().redeem(request, token, target)
       assert refused.value.reason is TokenRejection.EXPIRED

Test the stream policy
~~~~~~~~~~~~~~~~~~~~~~

The test client reports a single-threaded worker, which the policy reads as a threaded one inside ``get_stream`` so the page streams.
A plain ``get`` assembles the page inline, which is what a ``POST`` or a crawler receives, so a test for that path uses ``get`` and asserts the loading UI is absent.

Verification
------------

.. code-block:: bash
   :caption: shell

   uv run pytest tests/test_product_page.py

Every test passes.
Break the component on purpose and the shell and chunk tests fail with the failure the boundary contained, while the error UI test, which renders it, keeps passing.

See also
--------

.. seealso::

   :doc:`/content/ref/testing` for the signatures of every helper.
   :doc:`/content/topics/boundaries/streaming` for what decides whether a response streams.
   :doc:`test-a-page-with-actions` for the plugin and the action tests.
   :repo:`observability <tree/main/examples/observability>` and :repo:`search-catalog <tree/main/examples/search-catalog>` for streamed components, page boundaries, gates, and batches under test.
