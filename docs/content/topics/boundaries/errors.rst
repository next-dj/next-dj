.. _topics-boundaries-errors:

Error boundaries
================

An ``error.djx`` beside a component or in a page directory makes an error boundary.
It catches a failure of the subtree it guards in every render, a full page, a zone request, a streamed job, and a client-trip reload alike, and renders in place of that subtree while the rest of the response goes on.

.. contents::
   :local:
   :depth: 1

What a boundary catches
-----------------------

A component boundary guards the render of the component, its ``@component.context`` callables, and every component inside it that has no boundary of its own.
A page boundary guards what renders below its directory, the ``@context`` callables of the page and of the directories under it, the inner layouts, and the page body, and its error UI renders where the layout of its directory places its content.
An ``inherit_context`` callable of the boundary's own directory feeds the layout of that directory, so only a boundary above holds its failure.
The ``error.djx`` files follow the layout chain of the page, so past the page tree the one beside a ``layout.djx`` that wraps the page from another root, such as the root of a ``DIRS`` tree, guards the page as well, and the chain ends where a root layout ends it.
The nearest boundary holds a failure, so an inner ``error.djx`` keeps an outer one out of it.

A boundary catches an ``Exception``.
:exc:`~django.core.exceptions.RequestAborted` and an exception that is no ``Exception``, such as ``KeyboardInterrupt``, always propagate.
Before the first byte of the response is sent, :exc:`~django.http.Http404`, :exc:`~django.core.exceptions.PermissionDenied`, :exc:`~django.core.exceptions.BadRequest`, :exc:`~django.core.exceptions.SuspiciousOperation`, and a multipart parse error propagate as well, so Django answers them with their own status.
Inside a job of a streamed response the status is already sent, so the boundary catches them too, as the kinds ``not_found``, ``forbidden``, and ``bad_request``.

A page whose body a page boundary caught answers with status 500, or with the status of the kind when it has one.
A streamed response already sent 200, so the status an error UI of a job would set never reaches it.

The same :exc:`~django.http.Http404` of a job therefore answers differently by delivery.
A page assembled inline answers 404 as a whole, the way Django answers a view that raised it, while a streamed page keeps its 200 and shows the ``not_found`` error UI in place of the boundary, marking a page boundary ``noindex``.
A bot and a browser can therefore get different statuses for one page, since the policy assembles a bot's page inline.
Each ``Http404``, ``PermissionDenied``, or ``BadRequest`` a stream absorbs logs a ``WARNING`` on the ``next.defer`` logger, at most once per call site and quiet period, naming the page path and the exception, and a status the whole page needs belongs in its view or in a ``@context(..., guard=True)`` callable, which run before the first byte.
A ``SuspiciousOperation`` or a ``MultiPartParserError`` of a streamed job logs an ``ERROR`` on ``django.security.`` followed by its class name instead, the logger Django reports a suspicious request on.

``BOUNDARIES["RAISE"]`` set to ``True`` turns every boundary off, so a contained failure propagates as it would without the file.

The error UI
------------

A component ``error.djx`` renders over the context of the call site, and a page ``error.djx`` renders over the context its directory and the directories above it provide, so nothing the failed subtree produced reaches it.
Both read ``error``, a ``BoundaryFailure`` of strings and integers, never the exception itself.

``error.kind``
   ``error``, ``timeout``, ``overloaded``, ``not_found``, ``forbidden``, or ``bad_request``, and in the browser also ``incomplete`` and ``network``.
``error.status``
   404, 403, 400, or 503 for the kinds that stand for one, ``None`` otherwise.
``error.digest``
   The reference the reporter returned, or a short random id the log record of the failure carries, so a support request can quote it.
``error.retry``
   Whether the page loads the client runtime, which a retry control needs, ``False`` under the ``disabled`` script policy.
``error.message``, ``error.traceback``
   The exception line and the traceback, present under ``DEBUG`` only and empty otherwise.

A component ``error.djx`` also reads the props of the call at its top level, by the same names as ``component.djx``, narrowed to the declared ones when the component declares any.
The props stay out for ``not_found`` and ``forbidden``, and for a component whose access rule did not pass before the failure, so an error UI never shows what the visitor may not see.
The error UI of a reload or a retry renders from the declared props alone, so a variable of its ``error.djx`` outside them and ``error`` is ``next.W156``.

.. code-block:: jinja
   :caption: shop/components/price_chart/error.djx

   <div class="notice">
     <p>The price history is unavailable right now.</p>
     {% if error.retry %}<button type="button" data-next-retry>Try again</button>{% endif %}
     <small>Reference {{ error.digest }}</small>
   </div>

``BOUNDARIES["KINDS"]`` set to ``"opaque"`` reads every kind with a status as ``error``, so an error UI tells a visitor nothing about why.
A deferred component without an ``error.djx`` renders the template ``BOUNDARIES["ERROR_TEMPLATE"]`` names, or else the built-in error UI, an unstyled ``<div role="alert">`` with one translatable sentence and a ``data-next-retry`` button when ``error.retry`` holds.
The built-in error UI names nothing of the failure, so it reads the same with ``DEBUG`` on, where the traceback follows it as for any error UI, and a boundary that adopts the root of a table row or a list item leaves it out, since a ``<div>`` cannot stand there.
An ``error.djx`` that renders its own component is ``next.E229``, and a failure inside an error UI propagates to the boundary above it.

Under ``DEBUG`` the wrapper of a failed boundary carries ``data-next-error-digest`` and ``data-next-boundary-source``, the ``error.djx`` it came from, and an unstyled ``<details data-next-error-detail>`` element with the traceback follows the error UI, or an attribute of the same name where no element may stand.
``BOUNDARIES["DEBUG_DETAIL"]`` set to ``False`` drops the traceback.

Retry
-----

An element with ``data-next-retry`` inside an error UI is a retry control, and the page then loads the ``defer`` bundle that answers it.
A boundary that carries a token, a client-trip component or a component that declares its props, fetches itself again.
Any other boundary reloads the nearest zone around it, and one outside every zone reloads the page.

Reporting
---------

Without a reporter, every contained failure is logged with its traceback on the ``next.boundaries`` logger under a fresh digest, and repeats of one failure inside a quiet period drop to ``DEBUG``.
``BOUNDARIES["REPORTER"]`` names a callable that takes the exception and the keywords ``name``, ``addressing``, ``kind``, and ``request``, and returns the digest to show, such as the event id of an error tracker.
The ``name`` of a page boundary is the path of its ``loading.djx``, or of its ``error.djx`` when it has no ``loading.djx``, see :doc:`/content/ref/signals`.

A reporter that returns a digest owns the report, so the framework logs nothing for that failure.
A reporter that returns ``None`` has no reference for the failure, for example when its backend is off or sampled the event out, so the framework mints a digest and logs the failure as it does without a reporter.
A reporter that raises, or returns anything other than ``None`` or a non-empty string, is a misconfiguration logged at ``ERROR``, and the failure then gets the default digest and log.
The ``boundary_failed`` signal goes out once per failure, after the log or the reporter and before the error UI renders, see :doc:`/content/ref/signals`.

A contained failure is handled, its page renders, so Django's :data:`~django.core.signals.got_request_exception` does not fire for it, and an error tracker that should count every one takes it through ``REPORTER``, which sees each failure, or through ``boundary_failed``.
Forward a failure through one of the two, since one exception sent through both reaches the tracker twice.

A deferred job that raises outside every error boundary of its call shows the default error UI, yet nothing of the project handled it.
It is reported as Django reports an uncaught exception.
:data:`~django.core.signals.got_request_exception` is sent for every occurrence while the exception is the one being handled, so an error tracker subscribed to that signal sees each one.
The ``django.request`` logger gets an ``ERROR`` record with the traceback and the digest at most once per call site and quiet period, and the repeats go out at ``DEBUG``.

An error tracker that already takes Django's uncaught exceptions from that signal leaves ``django.request`` out of its logging integration, so each exception reaches it once.
``boundary_failed`` goes out for such a job too, so a project that forwards that signal as well skips the exceptions ``got_request_exception`` already delivered.
Django's test client re-raises an exception ``got_request_exception`` reported during the request, so a test of a page assembled inline fails on such a job.

What a failure rolls back
-------------------------

A boundary runs its subtree inside a lazy savepoint and a mark on the static collector.
On a failure the database writes of the subtree roll back, and so do the assets, the collector notes, and the JS context values it registered, so the error UI is not followed by the stylesheet of a component that never rendered.
The savepoint issues no statement until the subtree runs one, so a subtree that touches no database costs nothing.

In autocommit
~~~~~~~~~~~~~

``BOUNDARIES["ATOMIC"]`` decides what happens in autocommit, as on a ``GET`` request outside any transaction.
``"writes"``, the default, opens a transaction right before the first write of the subtree, ``True`` opens one before its first query, and ``False`` opens none, so a write in autocommit stays.
In autocommit a boundary opens no connection and issues no statement of its own, so a page that does no database work does none, and a read-only page issues its reads alone, with no ``BEGIN`` and no ``SAVEPOINT``.

Inside a transaction
~~~~~~~~~~~~~~~~~~~~

Inside a transaction the request or the code already opened, such as a ``POST`` under the default ``TRANSACTIONS`` rule, a ``GET`` with ``TRANSACTIONS["SAFE"]`` on or ``page.transaction(atomic=True)``, or a test under ``TestCase``, every mode protects the subtree with a savepoint.
The outermost boundary on a connection takes its savepoint right before the first statement of its subtree, a transaction control statement aside, and releases it when the subtree ends.
A boundary inside it takes its own savepoint only right before its first write, so a page boundary around a hundred components that each read costs one ``SAVEPOINT`` and ``RELEASE`` pair, and each component that writes adds one pair.
A savepoint an :func:`~django.db.transaction.atomic` block of the subtree opens counts as a write, as with the block :meth:`~django.db.models.query.QuerySet.get_or_create` wraps its insert in, so the boundary takes its savepoint before the block's and releasing the block's never destroys it.
A database error, or a doomed transaction, inside a boundary that took no savepoint yet is not contained there, since nothing could return that transaction to a usable state, and the boundary above that holds a savepoint rolls back to it and renders its error UI in place of both.

The savepoint waits for a write because a savepoint per boundary would add a ``SAVEPOINT`` and a ``RELEASE`` round trip to every component of the page, read or not.
The price of the shared savepoint is where a failed read surfaces.
Inside a transaction, one a boundary above opened in autocommit included, a failed read in an inner boundary that took no savepoint shows the error UI of the nearest boundary above that holds one, and that error UI replaces the whole subtree of that boundary, inner boundaries that rendered fine included.

A Python exception, and a failure after the inner boundary wrote, stay contained at the innermost boundary.
While the connection is in autocommit, the default for a ``GET``, a failed read breaks nothing and the innermost boundary shows its own error UI.
No setting makes a boundary take a savepoint before its first read.
A component that needs its failure contained at its own boundary inside a transaction writes inside the boundary, or wraps its queries in :func:`~django.db.transaction.atomic`, since either makes the boundary take its own savepoint.

In tests
~~~~~~~~

pytest-django and Django's ``TestCase`` run each test inside a transaction, so a query count a test takes includes the ``SAVEPOINT`` and ``RELEASE`` pair of the outermost boundary that queried, which the same ``GET`` in autocommit never issues.

What a rollback leaves
~~~~~~~~~~~~~~~~~~~~~~

The protection uses Django's public transaction API, an execute wrapper per connection, savepoints, and atomic blocks, so it covers what passes through them and nothing else.

- An :func:`~django.db.transaction.on_commit` hook the subtree registered still runs when the outer transaction commits, since a savepoint rollback keeps it.
- In autocommit, a write inside an atomic block the subtree opened before its first write commits with that block, not with the subtree.
- A ``durable=True`` atomic block opened after the first write of the subtree raises Django's ``RuntimeError``, as the subtree is then inside a transaction.
- Once a write landed in the transaction the protection opened, a database error the subtree catches itself still dooms that transaction.
- A connection the subtree opens on another thread, or a copy of a connection, is not covered.
- A statement is classified by its leading keyword, and one the classifier does not know counts as a write.
- Everything outside the database stays done, a cache write, a sent email, a file, a call to another service, and a session or messages write.

Tests
-----

``NextClient`` of ``next.testing`` records every failure a boundary contains during a request, keeps them on ``response.boundary_failures`` in document order, and re-raises the first one once the response or its stream is complete, so a broken component fails its test rather than hiding behind its error UI.
A test about an error UI renders it instead, inside ``boundary_errors("render")`` or with the ``rendered_boundary_errors`` fixture of the pytest plugin.

.. code-block:: python
   :caption: shop/tests/test_price_chart.py

   from next.testing import NextClient, boundary_errors

   def test_the_error_ui_offers_a_retry(broken_price_chart) -> None:
       with boundary_errors("render"):
           response = NextClient().get("/products/7/")
       assert b"data-next-retry" in response.content
       assert response.boundary_failures[0].name == "price_chart"

.. seealso::

   :doc:`/content/howto/contain-a-failure-with-an-error-boundary` for the task from the first ``error.djx`` to the test.
   :doc:`/content/howto/run-a-page-in-a-transaction` for the transaction a boundary runs inside.
   :doc:`/content/ref/boundaries` for ``contain``, ``ErrorKind``, and ``boundary_failed``.
   :doc:`/content/ref/transactions` for the savepoint an error boundary guards its subtree with.
   :repo:`audit-forms <tree/main/examples/audit-forms>`, :repo:`observability <tree/main/examples/observability>`, and :repo:`search-catalog <tree/main/examples/search-catalog>` for error UIs with a retry, a page-level one, and the tests that render them.
