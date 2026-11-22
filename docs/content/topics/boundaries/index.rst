.. _topics-boundaries:

Deferred boundaries
===================

A boundary is a page body or a component call that owns its own loading UI, its own error UI, or its own delivery.
Only pages and components own boundaries, because only they have a directory for the two files that declare one.

``loading.djx``
   The loading UI the response shows first, in place of content that renders later.
   Its presence turns the page body or the component into a deferred boundary.

``error.djx``
   The error UI that replaces a subtree whose render failed.
   Its presence turns the page directory or the component into an error boundary, in every render, deferred or not.

A deferred boundary is delivered later than the rest of the response in one of four modes.
A ``stream`` boundary arrives in the same response, after the shell.
A ``load``, ``visible``, or ``manual`` boundary is a client-trip boundary, which the browser fetches on a later request once the page is ready, once the wrapper scrolls into view, or once a control asks for it.
The shell is the response as first rendered, every deferred body replaced by its loading UI, and a job is the work that fills one deferred boundary.

A zone is a different thing.
A ``{% zone %}`` is an inline reload region that actions, polls, and reloads target, it is never deferred, and a part of a page that should load later is a component with a ``loading.djx`` instead, see :doc:`/content/topics/partial-rendering/zones`.

A first deferred component
--------------------------

A component whose data is slow to load gets a loading UI beside it.
The product page below loads its product from the URL and calls a ``recommendations`` component, which ``_components/`` in ``product/`` holds.

.. code-block:: text

   shop/pages/product/
       [int:pk]/
           page.py
           template.djx
       _components/recommendations/
           component.py
           component.djx
           loading.djx

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

   <ul>{% for item in related %}<li>{{ item.name }}</li>{% endfor %}</ul>

.. code-block:: jinja
   :caption: shop/pages/product/_components/recommendations/loading.djx

   <p>Finding related products&hellip;</p>

The page answers at once with the paragraph in the component's place, and the response fills the component in when ``recommend`` returns.
The ``loading.djx`` is all it takes, since its presence makes every call of the component a ``stream`` boundary.
Two changes move the fill to the browser, the declaration in ``component.py`` and the prop on the call site.

.. code-block:: python
   :caption: shop/pages/product/_components/recommendations/component.py

   from shop.models import Product
   from shop.services import recommend

   from next import component

   component.defer("visible", props=("product_id",))

   @component.context("related")
   def related(product_id: int) -> list[Product]:
       return recommend(Product.objects.get(pk=product_id))

.. code-block:: jinja
   :caption: shop/pages/product/[int:pk]/template.djx

   <h1>{{ product.name }}</h1>
   {% component "recommendations" product_id=product.pk %}

The component then loads on its own request once its wrapper scrolls into view.
That request carries the declared props and not the context of the page, so the callable takes ``product_id`` where it took ``product``, see :doc:`loading`.
:doc:`/content/howto/show-a-loading-state` walks through the same task with a skeleton, a timeout, and the tests, and :doc:`/content/howto/contain-a-failure-with-an-error-boundary` adds the error UI.

Choosing a mechanism
--------------------

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - The need
     - The mechanism
   * - A slow part of a page should not hold the first byte.
     - A ``loading.djx`` beside the component or the page, delivered as a ``stream`` boundary.
   * - A part below the fold should cost nothing until it is seen.
     - ``component.defer("visible")``, a client-trip boundary.
   * - A failure of one widget should not take the page down.
     - An ``error.djx`` beside the component or in the page directory.
   * - Several independent queries should run at once.
     - Async ``@context`` callables, or several deferred boundaries whose jobs the pool runs in parallel, see :doc:`async`.
   * - Many components look up rows by primary key.
     - A ``Loader``, which gathers the keys one request scope asks for concurrently into a few calls of a batch function, see :doc:`async`.
   * - A region reloads after an action or on a timer.
     - A ``{% zone %}``, with ``reload="loading"`` when it should show its loading UI while it reloads.

The pages of this guide
-----------------------

:doc:`loading`
   The ``loading.djx`` file of a page and of a component, the delivery modes, the call-site options, the wrapper element, and the tokens of a client-trip boundary.

:doc:`errors`
   The ``error.djx`` file, what an error boundary catches, the kinds an error UI reads, retry, and what a failed subtree rolls back and what it leaves behind.

:doc:`streaming`
   The delivery policy, what a streamed response means for headers, cookies, status codes, and middleware, compression, the client states and events, and the deployment notes.

:doc:`async`
   Async page and component code, the request scope, the ``Loader``, the lanes a job runs on, the thread and transaction rules, and the limits stated as the code has them.

.. toctree::
   :hidden:
   :maxdepth: 1

   loading
   errors
   streaming
   async

.. seealso::

   :doc:`/content/topics/partial-rendering/index` for zones, infinite lists through ``{% more %}``, and the reload skeletons of ``loading.<zone>.djx``.
   :doc:`/content/ref/defer` for the boundary vocabulary, the error boundary functions, and the transaction helpers.
   :doc:`/content/ref/settings` for the ``DEFER``, ``TRANSACTIONS``, and ``COMPRESSION`` scopes.
   :doc:`/content/intro/limitations` for what deferred boundaries do not do.
   :repo:`observability <tree/main/examples/observability>`, :repo:`search-catalog <tree/main/examples/search-catalog>`, and :repo:`audit-forms <tree/main/examples/audit-forms>` demonstrate the boundaries in running code, each page of this guide names the ones that show its subject.
