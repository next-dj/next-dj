.. _topics-partial-rendering-sse:

SSE Under WSGI and ASGI
=======================

Server-Sent Events carry patch envelopes to every open tab.
The same envelope that answers an HTTP request rides the stream as an event.
This page covers the stream helper, the refresh fan-out, the echo suppression, and the WSGI and ASGI contract that decides whether the stream can send a heartbeat.

.. contents::
   :local:
   :depth: 1

The Stream Helper
-----------------

``PatchEventStream`` is a :class:`~django.http.StreamingHttpResponse` returned from a page's ``render`` escape hatch.
There is no dedicated SSE endpoint and no new public URL.
The page view authorises the subscriber, the same as any other page.

.. code-block:: python
   :caption: stream/page.py

   from collections.abc import Iterator

   from django.http import HttpRequest
   from polls.broker import broker
   from polls.models import Poll
   from polls.providers import DPoll

   from next.partial import Patches, PatchEventStream


   def patch_source(request: HttpRequest, poll_id: int) -> Iterator[Patches]:
       """Yield one refresh envelope for every poll change."""
       for change in broker.changes(poll_id):
           yield Patches(request, echo_of=change.request_id).refresh(zone="poll-results")


   def render(request: HttpRequest, poll: DPoll[Poll]) -> PatchEventStream:
       """Open the patch event stream for one poll."""
       return PatchEventStream(request, patch_source(request, poll.pk))

Each ``Patches`` the source yields becomes one ``next-patches`` event, serialised by the active protocol backend, the same shape an HTTP response carries.
A ``data-next-sse="/url/"`` element on the page opens the ``EventSource`` and routes each event into the same apply pipeline an HTTP response uses.

The Refresh Fan-Out
-------------------

The recommended fan-out is the ``refresh`` verb.
The stream signals that a zone is stale, and every tab re-fetches the zone with its own cookies through the page view.
The stream carries no HTML.

.. code-block:: text
   :caption: a fan-out event

   event: next-patches
   data: {"version":"9f3c2e1b","request_id":"1c9f…-r1","ops":[{"op":"refresh","zone":"poll-results"}]}

Authorization stays in the subscriber's view, because the zone GET travels the page's own URL, the same view, the same guards, the same middleware as a full load.
The stream never broadcasts one user's HTML to another, and the event channel does not have to know who may see what.

The fan-out is built on ``refresh`` rather than a ``context`` patch on purpose.
A ``context`` patch carries the value of a registered serialize provider, and the builder reads that value from the origin page of the request that creates it.
A stream source has no page-render origin, so it cannot build a ``context`` patch from one.
A stream that needs to push fresh context drives a ``refresh``, and the re-fetched zone delivers the new context through its own render.
This is a documented limitation: the stream source addresses zones to refresh, not provider values to push directly.

Echo Suppression
----------------

The tab that triggered the change already has the fresh zone from its own response and must not apply the fan-out again.
The application channel threads the mutation's ``X-Next-Request-Id`` to the stream source, and the builder takes it as ``echo_of``.

.. code-block:: python
   :caption: echoing the originating request id

   Patches(request, echo_of=change.request_id).refresh(zone="poll-results")

The serialiser stamps ``echo_of`` as the envelope's ``request_id``.
The client keeps a ring buffer of its recent ``X-Next-Request-Id`` values and drops an event whose id matches.
When the buffer overflows under a flood of submissions the degradation is safe: an extra ``refresh``, not a failure.

The framework does not smuggle the request id through the broker.
A change event has to carry it, which the broker does by recording the request id of the mutation that produced it.

WSGI and ASGI
-------------

A stream holds a connection open, and how it stays alive when idle depends on the source and the server.

A sync source under WSGI sends no heartbeat.
A blocked ``next()`` on a sync iterator has nothing to interrupt it without a thread, so a quiet sync stream stays quiet.
A keepalive on a sync source is the source's own responsibility, for example a comment frame on a wait timeout.
Under WSGI the stream also occupies one worker for the full life of the connection, so the recommendation is ASGI for any real workload.

An async source under ASGI receives heartbeat comments.
The stream interleaves a heartbeat during a quiet period through :func:`asyncio.wait`, so a buffering proxy keeps the connection.
The heartbeat period is ``SSE.HEARTBEAT_SECONDS`` in ``PARTIAL_BACKENDS``.

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * - Source and server
     - Heartbeat
     - Worker cost
   * - Sync source, WSGI
     - None, keepalive is the source's job
     - One worker per open connection
   * - Async source, ASGI
     - Sent on a quiet period
     - One task per open connection

To move a stream from sync to async, swap the broker's wake primitive for an async one and pass an async source to ``PatchEventStream``.
The page module and the signal layer do not change.

Stream Politeness
-----------------

``PatchEventStream`` sets the politeness headers on construction so a proxy or ``GZipMiddleware`` does not eat the flush.

* ``Cache-Control: no-cache, no-transform`` keeps a proxy from buffering and re-compressing the stream.
* ``X-Accel-Buffering: no`` turns off nginx buffering.
* The leading ``retry`` hint comes from ``SSE.RETRY_MS`` so the browser's native reconnect uses the configured interval.

On the client a background tab pauses the stream by closing the connection.
When the tab becomes visible the runtime reconnects and re-fetches the zones the stream addressed since the connection opened.
Events missed while paused are not lost, because ``refresh`` is idempotent: the re-fetch brings the current state regardless of how many fan-outs were missed.

See Also
--------

.. seealso::

   :doc:`scenarios` for the live stream scenario end to end.
   :doc:`reference` for the SSE settings and the lifecycle events.
   :doc:`/content/deployment/wsgi-asgi` for choosing a server.
   :doc:`/content/topics/signals` for the ``sse_stream_opened`` and ``sse_stream_closed`` signals.
