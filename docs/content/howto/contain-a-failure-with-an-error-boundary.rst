.. _howto-contain-a-failure-with-an-error-boundary:

Contain a failure with an error boundary
========================================

Problem
-------

One widget of a dashboard queries a service that sometimes fails.
The whole page answers 500 when it does, although every other panel is fine.

Solution
--------

Put an ``error.djx`` beside the component.
A failure of the component, or of anything it renders, then shows that file in place of the component and the rest of the response goes on.
A page directory takes the same file to guard the page body.

Walkthrough
-----------

Guard one component
~~~~~~~~~~~~~~~~~~~

The dashboard at ``/`` calls a ``latency`` component, which asks ``dashboard.services.latency()``, the project's call to the latency service, for a figure.

.. code-block:: text

   dashboard/pages/
       template.djx
       _components/latency/
           component.py
           component.djx
           error.djx

.. code-block:: jinja
   :caption: dashboard/pages/template.djx

   <h1>Dashboard</h1>
   {% component "latency" %}

.. code-block:: python
   :caption: dashboard/pages/_components/latency/component.py

   from dashboard import services

   from next import component

   @component.context("milliseconds")
   def milliseconds() -> int:
       return services.latency()

.. code-block:: jinja
   :caption: dashboard/pages/_components/latency/component.djx

   <p data-latency>{{ milliseconds }} ms</p>

The error UI renders over the context of the call site and reads ``error``, a summary of strings and integers.
It never sees the exception.

.. code-block:: jinja
   :caption: dashboard/pages/_components/latency/error.djx

   <div class="notice" data-latency-error>
     {% if error.kind == "timeout" %}
       <p>The latency figures took too long to load.</p>
     {% else %}
       <p>The latency figures are unavailable right now.</p>
     {% endif %}
     {% if error.retry %}<button type="button" data-next-retry>Try again</button>{% endif %}
     <small>Reference {{ error.digest }}</small>
   </div>

``error.kind`` is ``error``, ``timeout``, ``overloaded``, ``not_found``, ``forbidden``, or ``bad_request``, and in the browser also ``incomplete`` and ``network``.
``error.digest`` is the id the log record of the failure carries, so a support request can quote it.
``error.message`` and ``error.traceback`` are filled under ``DEBUG`` only.
The boundary works in every render, deferred or not, so the file guards the plain ``latency`` component as it would a deferred one.
A deferred component without one renders an empty wrapper marked as an error, which ``manage.py check`` reports as ``next.W170``.

Guard a page
~~~~~~~~~~~~

An ``error.djx`` in a page directory guards what renders below that directory, the page body, the callables of the page, and the inner layouts.
The layouts above it stay on screen, and the error UI sits where the page content goes.
The page at ``/stats/pages/`` reads its rows from ``dashboard.metrics.counters()``, the project's query of page views, and streams behind a loading UI.

.. code-block:: text

   dashboard/pages/stats/pages/
       page.py
       template.djx
       loading.djx
       error.djx

.. code-block:: python
   :caption: dashboard/pages/stats/pages/page.py

   from dashboard import metrics

   from next import context

   @context("counters")
   def counters() -> list[tuple[str, int]]:
       return metrics.counters()

.. code-block:: jinja
   :caption: dashboard/pages/stats/pages/template.djx

   <table>
     {% for path, views in counters %}<tr><td>{{ path }}</td><td>{{ views }}</td></tr>{% endfor %}
   </table>

.. code-block:: jinja
   :caption: dashboard/pages/stats/pages/loading.djx

   <p>Counting page views&hellip;</p>

.. code-block:: jinja
   :caption: dashboard/pages/stats/pages/error.djx

   <p data-stats-error>The page views are unavailable right now.</p>

The status depends on the delivery.
A page whose body streams has already answered 200 when its job fails, so the error UI arrives in a chunk.
A page assembled in one piece answers 500, or the status of the kind when it has one, and still renders its layout around the error UI.
:exc:`~django.http.Http404`, :exc:`~django.core.exceptions.PermissionDenied`, and the other request errors propagate before the first byte so Django answers them with their own status.

Know what happens to the database
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A boundary runs its subtree inside a lazy savepoint.
When the subtree fails, its database writes roll back, and so do the assets and the JS context values it registered.
The writes of the error UI itself stay, and the writes made before the boundary stay too.
An ``audit_note`` component, guarded by an ``error.djx`` of its own, shows the rule with a row it writes before it fails.

.. code-block:: python
   :caption: dashboard/pages/_components/audit_note/component.py

   from dashboard.models import AuditNote

   from next import component

   @component.context("note")
   def note() -> None:
       AuditNote.objects.create(text="rendered")
       msg = "the lookup failed"
       raise RuntimeError(msg)

The row never exists once the error UI shows.
In autocommit, the default for a ``GET``, the boundary opens a transaction right before the first write of the subtree and issues no statement for a read.
``DEFER["ERRORS"]["ATOMIC"]`` changes that.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFER": {"ERRORS": {"ATOMIC": True}},
   }

``"writes"`` is the default, ``True`` opens the transaction before the first query, and ``False`` opens none, so a write in autocommit stays.
Inside a transaction the request already opened, a ``POST`` under the default rule for instance, the savepoint is taken before the first write of an inner boundary and before the first statement of the outermost one.
Everything outside the database stays done, a cache write, a sent email, a file, and a call to another service.
:doc:`/content/topics/boundaries/errors` lists the rest, among them how a failed read inside a transaction surfaces at the nearest boundary that holds a savepoint, and :doc:`run-a-page-in-a-transaction` shows the statements.

Retry
~~~~~

An element with ``data-next-retry`` inside an error UI is a retry control, and the page then loads the bundle that answers it.
A boundary that carries a token, a client-trip component or a component that declares its props, fetches itself again.
Any other boundary reloads the nearest zone around it, and one outside every zone reloads the page.
A component that renders from the call site alone retries on its own once it declares its props, or declares an empty list with ``component.props()``.

.. code-block:: python
   :caption: dashboard/pages/_components/latency/component.py

   from dashboard import services

   from next import component

   component.props()

   @component.context("milliseconds")
   def milliseconds() -> int:
       return services.latency()

Report the failure
~~~~~~~~~~~~~~~~~~

Without a reporter every contained failure is logged with its traceback on the ``next.boundaries`` logger under a fresh digest.
``DEFER["ERRORS"]["REPORTER"]`` names a callable that sends the failure to an error tracker and returns the reference to show.

.. code-block:: python
   :caption: dashboard/reporting.py

   import logging

   from django.http import HttpRequest

   logger = logging.getLogger("dashboard.boundaries")

   def report(
       exception: Exception,
       *,
       name: str,
       addressing: str,
       kind: str,
       request: HttpRequest | None,
   ) -> str | None:
       logger.error("boundary %s failed as %s", name, kind, exc_info=exception)
       return None

A reporter that returns ``None`` has no reference, so the framework mints a digest and logs the failure as it does without one.
A returned string becomes the digest of the error UI and replaces the framework log.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFER": {"ERRORS": {"REPORTER": "dashboard.reporting.report"}},
   }

Verification
------------

The test client re-raises the first failure a boundary contains, so a broken component fails its test instead of hiding behind its error UI.
A test about the error UI asks for it with ``boundary_errors("render")``.
The ``latency`` component renders inline, so a plain ``get`` returns the page with the error UI in place of the component.
The component calls ``services.latency()`` through its module, so ``monkeypatch.setattr`` on that module reaches it.

.. code-block:: python
   :caption: tests/test_dashboard.py

   from next.testing import NextClient, boundary_errors

   def lost() -> None:
       msg = "the latency service is down"
       raise ConnectionError(msg)

   def test_a_failed_widget_shows_its_error_ui(db, monkeypatch) -> None:
       monkeypatch.setattr("dashboard.services.latency", lost)
       with boundary_errors("render"):
           response = NextClient().get("/")
       assert response.status_code == 200
       assert b"data-latency-error" in response.content
       assert b"data-next-retry" in response.content

A component with a ``loading.djx`` settles in a chunk, which ``get_stream`` reads, see :doc:`test-a-page-with-deferred-boundaries`.
A client-trip component, ``load``, ``visible``, or ``manual``, reloads through ``get_boundary(url, name)``, which mints the token of the component and sends the request the browser sends, and the envelope it returns holds the op with the error UI.
A plain ``get`` of ``/stats/pages/`` assembles the page in one piece, so its failure answers 500, and ``boundary_failures`` on the response names the addressing of each failure.

.. code-block:: python
   :caption: tests/test_dashboard.py

   def test_a_failed_table_keeps_the_layout(db, monkeypatch) -> None:
       monkeypatch.setattr("dashboard.metrics.counters", lost)
       with boundary_errors("render"):
           response = NextClient().get("/stats/pages/")
       assert response.status_code == 500
       [failure] = response.boundary_failures
       assert failure.addressing == "page"

See also
--------

.. seealso::

   :doc:`/content/topics/boundaries/errors` for what a boundary catches, the kinds, and the full rules of the rollback.
   :doc:`show-a-loading-state` for the loading UI that precedes the error UI of a deferred component.
   :doc:`test-a-page-with-deferred-boundaries` for gates that hold a job past its timeout.
   :repo:`audit-forms <tree/main/examples/audit-forms>`, :repo:`observability <tree/main/examples/observability>`, and :repo:`search-catalog <tree/main/examples/search-catalog>` for error UIs with a retry and the tests that render them.
