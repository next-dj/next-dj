.. _howto-send-boundary-failures-to-sentry-and-tracing:

Send boundary failures to Sentry and a tracer
=============================================

Problem
-------

A page with deferred boundaries answers ``200`` while a boundary inside it fails, times out, or is shed, so access logs and alerts on ``5xx`` see nothing.
The project wants the exceptions in its error tracker, a span for each boundary in its traces, and counters for how its streams end.

Solution
--------

An exception that leaves a boundary uncontained reaches :data:`~django.core.signals.got_request_exception` and the ``django.request`` logger, which an error tracker that watches Django finds without setup.
A failure that an ``error.djx`` contained is a handled error, so it goes through ``BOUNDARIES["REPORTER"]`` or the ``boundary_failed`` signal.
A pair of signals, ``boundary_job_started`` and ``boundary_job_finished``, brackets every job on its own thread, which is the place for a span.
``page_stream_closed`` and ``page_stream_declined`` carry the counters.
The snippets import the Sentry and OpenTelemetry packages inside the receiver modules, and neither is a dependency of next.dj.

Walkthrough
-----------

Know which failure takes which path
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 34 33 33

   * - Failure
     - Reaches
     - Logger
   * - A job raised outside every error boundary of its call
     - ``got_request_exception`` on every occurrence, and ``boundary_failed``
     - ``django.request`` at ``ERROR``, at most once per call site and quiet period
   * - A failure an ``error.djx`` contained
     - ``REPORTER`` and ``boundary_failed`` on every occurrence
     - ``next.boundaries`` at ``ERROR`` once per quiet period, unless ``REPORTER`` returned a digest
   * - A job that timed out, was shed, or was cut
     - ``boundary_settled`` and ``page_stream_closed``
     - ``next.defer`` at ``WARNING``

A contained failure does not reach ``got_request_exception``, because a test client would then re-raise every error UI a test checks, see :doc:`/content/topics/boundaries/errors`.

Report contained failures to Sentry
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Initialise Sentry as usual.
Its Django integration takes the uncontained failures from ``got_request_exception`` and needs nothing more.
A receiver of ``boundary_failed`` sends the contained ones.

.. code-block:: python
   :caption: shop/signals.py

   import sentry_sdk
   from django.dispatch import receiver

   from next.signals import boundary_failed

   @receiver(boundary_failed)
   def report_boundary(
       sender: object,
       exception: Exception,
       name: str,
       addressing: str,
       kind: object,
       digest: str,
       **kwargs: object,
   ) -> None:
       with sentry_sdk.new_scope() as scope:
           scope.set_tag("boundary", name)
           scope.set_tag("boundary.addressing", addressing)
           scope.set_tag("boundary.kind", str(kind))
           scope.set_tag("boundary.digest", digest)
           sentry_sdk.capture_exception(exception)

Import the module from ``AppConfig.ready`` of the app so the receiver connects once.
A job that raised outside its boundaries sends ``boundary_failed`` as well, so the same exception can arrive from both paths.
The default deduplication of the Sentry SDK drops the second event for one exception object.

The framework also logs a contained failure on ``next.boundaries`` at ``ERROR``, which the Sentry logging integration turns into an event.
Tell it to leave that logger out, since the receiver already sends each failure.

.. code-block:: python
   :caption: config/settings/prod.py

   import sentry_sdk
   from sentry_sdk.integrations.logging import ignore_logger

   sentry_sdk.init(dsn=SENTRY_DSN)
   ignore_logger("next.boundaries")

Show the event id to the visitor
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``BOUNDARIES["REPORTER"]`` is the alternative to the receiver, and a project uses one of the two.
It receives every contained failure and returns the digest the error UI shows, so the event id of Sentry can serve as the reference a visitor quotes.
A reporter that returns a digest owns the report, so the framework logs nothing for that failure and ``ignore_logger`` is unneeded.

.. code-block:: python
   :caption: shop/reporting.py

   import sentry_sdk
   from django.http import HttpRequest

   def report(
       exc: Exception,
       *,
       name: str,
       addressing: str,
       kind: object,
       request: HttpRequest | None,
   ) -> str | None:
       with sentry_sdk.new_scope() as scope:
           scope.set_tag("boundary", name)
           scope.set_tag("boundary.addressing", addressing)
           return sentry_sdk.capture_exception(exc)

.. code-block:: python
   :caption: config/settings/prod.py

   NEXT_FRAMEWORK = {
       "BOUNDARIES": {"REPORTER": "shop.reporting.report"},
   }

``capture_exception`` returns ``None`` when the SDK is off or sampled the event out, and the framework then mints a digest and logs the failure itself.

Trace each boundary
~~~~~~~~~~~~~~~~~~~

``boundary_job_started`` runs right before the body of a job and ``boundary_job_finished`` right after it, both on the thread and in the context of the body.
A context variable that the first receiver sets is therefore visible to the body and to the second receiver.
The receivers below open a span, make it current for the body, and close it afterwards.
A job that never starts, because it timed out in the queue or was shed, sends neither signal and shows up in ``boundary_settled`` instead.

.. code-block:: python
   :caption: shop/tracing.py

   from contextvars import ContextVar

   from django.dispatch import receiver
   from opentelemetry import context, trace
   from opentelemetry.trace import Span, Status, StatusCode

   from next.signals import boundary_job_finished, boundary_job_started

   tracer = trace.get_tracer("shop.boundaries")
   held: ContextVar[tuple[Span, object] | None] = ContextVar("held", default=None)


   @receiver(boundary_job_started)
   def open_span(
       sender: object, name: str, addressing: str, mode: str, lane: str, **kwargs: object
   ) -> None:
       span = tracer.start_span(
           f"boundary {name}",
           attributes={
               "next.boundary.addressing": addressing,
               "next.boundary.mode": str(mode),
               "next.boundary.lane": str(lane),
           },
       )
       token = context.attach(trace.set_span_in_context(span))
       held.set((span, token))


   @receiver(boundary_job_finished)
   def close_span(
       sender: object, error: BaseException | None, run_ms: float, **kwargs: object
   ) -> None:
       open_job = held.get()
       if open_job is None:
           return
       span, token = open_job
       held.set(None)
       if error is not None:
           span.record_exception(error)
           span.set_status(Status(StatusCode.ERROR))
       span.set_attribute("next.boundary.run_ms", run_ms)
       context.detach(token)
       span.end()

The context of the request is copied into the job, so the span becomes a child of the span that the request instrumentation of the project opened.
``error`` is what the body raised past its error boundary and ``None`` when it returned, including a return that rendered an error UI, so the failures an ``error.djx`` contained are marked through ``boundary_failed``.
Both signals go out through ``send_robust``, so a receiver that raises is logged and never breaks the page.

Count how the streams end
~~~~~~~~~~~~~~~~~~~~~~~~~

``page_stream_closed`` goes out once per streamed page or partial response, and its fields are the counters.
``page_stream_declined`` goes out once per response that was assembled inline, and its ``reason`` names the row of the delivery policy.

.. code-block:: python
   :caption: shop/metrics.py

   from django.dispatch import receiver

   from next.signals import page_stream_closed, page_stream_declined

   from .stats import incr

   COUNTERS = (
       "sent", "failed", "late", "shed", "timeouts",
       "abandoned", "cancelled", "dropped", "cut", "aborted",
   )

   @receiver(page_stream_closed)
   def count_closed(
       sender: object, page_path: str, duration_ms: float, **fields: object
   ) -> None:
       for counter in COUNTERS:
           if value := int(fields[counter]):
               incr(f"stream.{counter}", page_path, by=value)
       incr("stream.duration_ms_total", page_path, by=int(duration_ms))


   @receiver(page_stream_declined)
   def count_declined(
       sender: object, reason: str, server_reason: str, partial: bool, **kwargs: object
   ) -> None:
       incr("stream.declined", f"{reason}:{server_reason}")

``incr`` is the project's own counter helper, as in :doc:`observe-framework-signals`.
Alert on ``timeouts``, ``shed``, and ``cut``, since a page whose boundaries all timed out still answers ``200``.
A rising count of ``declined`` with the reason ``buffered``, ``sync_worker``, or ``capacity`` means the deployment stopped streaming, see :doc:`/content/deployment/boundaries`.

Keep the receivers cheap
~~~~~~~~~~~~~~~~~~~~~~~~

The job signals go out for every job of every response, and the framework skips them while no receiver is connected.
Keep each receiver synchronous and free of network calls, since the body of the job waits for it, and hand slow work to the queue of the tracer or the metrics client.

Verification
------------

A test connects a recording receiver to ``boundary_failed``, renders a page whose boundary breaks, and checks the payload the Sentry receiver would send.
Inside ``boundary_errors("render")`` the test client renders the error UI instead of re-raising, so the signal goes out and the response stays a page.

.. code-block:: python
   :caption: shop/tests/test_reporting.py

   from next.signals import boundary_failed
   from next.testing import NextClient, boundary_errors

   def test_a_contained_failure_reaches_the_receiver(broken_price_chart) -> None:
       seen: list[dict[str, object]] = []

       def record(sender: object, **kwargs: object) -> None:
           seen.append(kwargs)

       boundary_failed.connect(record, weak=False)
       try:
           with boundary_errors("render"):
               NextClient().get("/products/7/")
       finally:
           boundary_failed.disconnect(record)
       assert seen[0]["name"] == "price_chart"
       assert isinstance(seen[0]["exception"], Exception)

In staging, break a boundary on purpose and look for the event with its ``boundary`` tag and its digest in Sentry, and for the span in the trace of the request.

See also
--------

.. seealso::

   :doc:`/content/ref/signals` for the payload of every signal.
   :doc:`/content/topics/boundaries/errors` for what a contained failure is and how it is reported.
   :doc:`observe-framework-signals` for the signals of the rest of the framework.
