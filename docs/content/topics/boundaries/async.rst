.. _topics-boundaries-async:

Async code, batching, and lanes
===============================

A deferred job, and an async page render, run part of a request away from the thread that ran the view.
This page covers how that code is written, what it shares, and the thread and transaction rules it runs under.

.. contents::
   :local:
   :depth: 1

Async callables
---------------

A page ``render``, an ``@context`` callable, an ``@page.metadata`` callable, an ``@component.context`` callable, a named dependency, and a ``Depends`` factory may be ``async def``.
The view stays a sync Django view.
It drives the async part on the event loop of the ASGI server, or on a background loop under WSGI, while the request thread waits and runs every thread-sensitive call that part makes.
A ``sync_to_async`` call and the Django async ORM therefore run on the request thread, inside the transaction of the request when it holds one.

Within one render the callables form a graph by the keys they read.
Async callables that do not read each other run concurrently, and a sync callable runs on the request thread when its inputs are ready.
A sync callable that reads the key of an async one waits for it on the request thread, so nothing after it overlaps the async work, which ``next.W163`` reports.
A sync resolve that meets an async dependency runs it through the same bridge, and ``DEBUG`` logs each such site once.
An async generator is refused wherever an async callable is allowed, and an ``@action`` handler and a form hook stay sync, see :ref:`topics-pages-sync-callables`.

A provider awaits its own work by answering ``compile_aresolve`` with an async filler, the ``AsyncCompilingParameterProvider`` protocol of ``next.deps``, see :doc:`/content/ref/deps`.

The request scope
-----------------

An async render opens a ``RequestScope`` around its callables, which computes each shared dependency once as a task the scope owns and hands every later reader the same value, or the same error.
The job of a page boundary that runs async callables opens a scope of its own, seeded with what the shell already resolved.
A scope closes when its render ends, failing the waiters it still holds and cancelling the rest.

Batching lookups with a Loader
------------------------------

A ``Loader`` gathers the keys one request scope asks for into a few calls of a batch function, so twenty concurrent lookups of one row each cost one query.

.. code-block:: python
   :caption: shop/loaders.py

   from shop.models import Price

   from next.deps import Loader

   def prices(product_ids):
       found = Price.objects.filter(product_id__in=product_ids)
       return {price.product_id: price for price in found}


   price_of = Loader(prices)

.. code-block:: python
   :caption: shop/pages/cart/page.py

   import asyncio

   from django.http import HttpRequest
   from shop.loaders import price_of
   from shop.models import CartLine, Price

   from next import context

   @context("cart")
   def cart(request: HttpRequest) -> list[CartLine]:
       return list(CartLine.objects.filter(session=request.session.session_key))

   @context("prices")
   async def prices(cart: list[CartLine]) -> list[Price]:
       return await asyncio.gather(*(price_of.load(line.product_id) for line in cart))

The ``cart`` callable is the source of the keys, and ``prices`` reads it by name, so the loader receives one key per line and the batch function runs once for the page.

``load(key)`` is awaited on the event loop, and ``load_sync(key)`` serves a sync callable on a thread and refuses to run on an event loop.
A batch closes once every running deferred job of the response waits on a loader or has ended, at ``LOADER_MAX_BATCH`` keys, five hundred by default, or ``BATCH_WINDOW`` after its first key, one millisecond by default, both read from ``DEFER`` unless the loader is built with ``window=`` or ``max_batch=``.
The batch function returns a mapping by key or a sequence in key order.
A key the mapping lacks raises ``MissingKeyError`` for that key alone, a short sequence and a raising batch fail every key of the batch, and a key loaded once in a request is answered from memory, its error included.

``rows(Model)`` returns the shared loader of the rows of a model by primary key, one ``pk__in`` query per batch through the default manager.
It applies no access scoping, unlike ``DObject``, so a page checks what the visitor may read before it loads a row this way.

A loader batches the keys requested concurrently within one request scope, which covers the async and sync context functions of an async page and every deferred job of one response, streamed or assembled inline.
Code that runs serially, such as a fully sync page, the shell render or the render of a reloaded boundary, calls the batch function with one key per call.
Where the jobs of a response run decides how many keys share a batch.
On the pool they ask within one window and share it, and on SQLite they run on the request lane one after the other, so each job loads one batch of its own, see `Lanes and placement`_.
:doc:`/content/howto/batch-lookups-with-a-loader` walks through a loader shared by two streamed components and the tests of both lanes.

Lanes and placement
-------------------

Work runs on one of three lanes.
The pool lane is a bounded process-wide thread pool, ``EXECUTOR["OPTIONS"]["MAX_WORKERS"]`` threads started on demand, sixteen by default.
The loop lane is the event loop that runs ``async def`` work, and the request lane is the thread that ran the view.

The sync work of a job goes to the request lane when a request-lane rule holds, checked in this order.

``atomic``
   The view runs inside a transaction, as a ``POST`` does by default, so every job shares its connection and sees its writes.
``sqlite``
   A database alias is SQLite, unless ``EXECUTOR["OPTIONS"]["SQLITE"]`` is ``"parallel"``, and an in-memory SQLite alias always.
``connection-state``
   The framework detected per-connection state, such as a tenant middleware that sets a schema, that no ``ConnectionState`` backend of ``DEFER["CONNECTION_STATE"]`` replays, ``next.W155``.
``thread-local``
   The framework detected request state kept on the thread, such as django-crum or a middleware listed in ``EXECUTOR["OPTIONS"]["THREAD_LOCAL_MIDDLEWARE"]``, that no ``ThreadState`` backend of ``DEFER["THREAD_STATE"]`` replays, ``next.W171``.
``opt-out``
   ``EXECUTOR["OPTIONS"]["PARALLEL"]`` is ``False``, or the boundary declares ``parallel=False``.

Without such a rule, ``parallel=True`` pins the job to the pool, and otherwise placement chooses per job from the costs it measured for each call site, keeping a short job on the request thread where a hand-off to the pool would cost more than the job.
A pool job that waits longer than ``QUEUE_TIMEOUT`` in the queue, or meets a full queue, runs on its request thread instead, and one neither can take settles as ``overloaded``.
``boundary_rendered`` reports the lane and the reason for each job, and ``page_stream_opened`` names the request-lane rule that pinned a stream.

Thread and transaction rules
----------------------------

A job on the pool runs on another thread with a copy of the context variables the view held, so a ``contextvars.ContextVar`` set in a middleware reaches it and a ``threading.local`` does not.
A ``ThreadState`` backend captures such state on the request thread and applies it on the pool thread, and a ``ConnectionState`` backend replays per-connection state on the connection a pool thread opens, each naming the detected sources it ``covers``.

A pool job uses a connection of its own in autocommit, so it reads committed rows only and never the uncommitted writes of its request, which is why the ``atomic`` rule keeps such a request on one thread.
Under ``DEFER["DEADLINE_TO_DB"]`` a PostgreSQL connection of a pool job carries the remaining time of the job as its statement timeout, folded into the transaction a ``ConnectionState`` replay opens for ``"state"``, the default, opened for that alone for ``"always"``, and never for ``False``.
A replay runs inside a transaction, so a ``durable=True`` atomic block in code a deferred job runs raises, ``next.W173``.
A pool thread that leaves its connection inside a transaction has it rolled back and closed before its next job.

A stream writes its chunks after the view returned, so the jobs of a streamed page run after the view's own transaction ended.
A request whose jobs must share one transaction is answered inline, as every ``POST`` is under the default policy.
A job that writes the session, the messages, or the CSRF token of a streamed response writes past the headers, see :doc:`streaming`.

Limits
------

- An ``async def`` batch function of a ``Loader`` runs on the event loop and serves native async I/O only, so a Django ORM call or a ``sync_to_async(thread_sensitive=True)`` call inside it raises ``RuntimeError``, and a batch that queries the database is a plain ``def``.
- A sync job cannot be interrupted, so a job past its deadline keeps its thread until it returns, while its boundary has already settled as ``timeout``.
- A job never runs in another process or sub-interpreter, because it needs the live request and its database connection.
- Thread-local state a library keeps without the framework detecting it does not reach a pool job, and the job reads it empty unless a ``ThreadState`` backend replays it.
- A batch function that loads a key of its own batch, directly or through a callable it runs, fails that batch, since the wait would wait on itself.
- A sync batch function runs on the thread of whichever waiting job claims its batch first, the request thread or a pool thread, under the database connection, the transaction, and the statement timeout of that job.
  Every other job waiting on the batch receives what that call read, so a batch the request thread claims sees the uncommitted writes of the request, one a pool job claims sees committed rows only, and under ``DEFER["DEADLINE_TO_DB"]`` the query carries the remaining time of the claiming job.

.. seealso::

   :doc:`/content/howto/batch-lookups-with-a-loader` for a loader shared by two streamed components.
   :repo:`search-catalog <tree/main/examples/search-catalog>` for the loader and the tests of both lanes.
