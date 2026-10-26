.. _topics-partial-rendering-sse:

SSE under WSGI and ASGI
=======================

Server-Sent Events carry patch envelopes to every open tab.
The same envelope that answers an HTTP request rides the stream as an event.

.. contents::
   :local:
   :depth: 1

The stream helper
-----------------

``PatchEventStream`` is a :class:`~django.http.StreamingHttpResponse` returned from a page's ``render`` escape hatch.
For broker-backed topic delivery with automatic WSGI/ASGI selection, use :doc:`/content/topics/realtime`.
There is no dedicated SSE endpoint and no new public URL.
The page view authorizes the subscriber, the same as any other page.

The source is any iterable the application owns.
The feed below is a process-local example that fans each recorded change out to every open stream of the process, and a real project reads its own queue, table, or message bus in its place.

.. code-block:: python
   :caption: polls/changes.py

   import queue
   import threading
   from collections.abc import Iterator
   from dataclasses import dataclass

   @dataclass(frozen=True, slots=True)
   class Change:
       poll_id: int
       request_id: str | None

   _lock = threading.Lock()
   _inboxes: set[queue.Queue[Change]] = set()

   def record(change: Change) -> None:
       with _lock:
           for inbox in _inboxes:
               inbox.put(change)

   def changes(poll_id: int, timeout: float = 20) -> Iterator[Change | None]:
       inbox: queue.Queue[Change] = queue.Queue()
       with _lock:
           _inboxes.add(inbox)
       try:
           while True:
               try:
                   change = inbox.get(timeout=timeout)
               except queue.Empty:
                   yield None
                   continue
               if change.poll_id == poll_id:
                   yield change
       finally:
           with _lock:
               _inboxes.discard(inbox)

The mutation records a change with the request id of the request that made it, for example ``record(Change(poll.pk, request.headers.get("X-Next-Request-Id")))`` in a form's ``on_valid``.

.. code-block:: python
   :caption: polls/pages/polls/[int:id]/stream/page.py

   from collections.abc import Iterator

   from django.http import HttpRequest
   from django.shortcuts import get_object_or_404
   from polls.changes import changes
   from polls.models import Poll

   from next.partial import Patches, PatchEventStream
   from next.urls import DUrl

   def patch_source(request: HttpRequest, poll_id: int) -> Iterator[Patches | None]:
       for change in changes(poll_id):
           if change is None:
               yield None
               continue
           yield Patches(request, echo_of=change.request_id).refresh(zone="poll-results")

   def render(request: HttpRequest, poll_id: DUrl["id", int]) -> PatchEventStream:
       poll = get_object_or_404(Poll, pk=poll_id)
       return PatchEventStream(request, patch_source(request, poll.pk))

Each ``Patches`` the source yields becomes one ``next-patches`` event.
The HTTP envelope shape is preserved and ``server_time`` is added only to the SSE data frame.
Topic streams also add ``topic``, ``epoch``, and ``seq``.
The active protocol backend owns the stream codec.
A ``data-next-stream="/url/"`` element on the page opens an ``EventSource``, or the WebSocket when the server enables it, and routes each event into the same apply pipeline an HTTP response uses.

The refresh fan-out
-------------------

The recommended fan-out is the ``refresh`` verb.
The stream signals that a zone is stale, and every tab re-fetches the zone with its own cookies through the page view.
The stream carries no HTML.

.. code-block:: text
   :caption: a fan-out event

   event: next-patches
   data: {"version":"9f3c2e1b","ops":[{"op":"refresh","zone":"poll-results"}],"assets":[],"form":null,"request_id":"1c9f…-r1","server_time":1792843200000,"topic":"poll:42","epoch":"9f2c41d07a3be851","seq":18}

Topic streams append ``server_time``, ``topic``, ``epoch``, and ``seq`` to the SSE data object.
Manual ``PatchEventStream`` sources receive ``server_time`` as well.
Neither path changes the ordinary ``PatchResponse`` body or ``Envelope.as_dict()``.

The envelope keeps the key order ``version``, ``ops``, ``assets``, ``form``, and ``csrf`` when present, then ``request_id`` when present.
Empty ``assets`` and an absent form are serialized as ``[]`` and ``null``.

Authorization stays in the subscriber's view, because the zone GET travels the page's own URL, the same view, the same guards, the same middleware as a full load.
The stream never broadcasts one user's HTML to another, and the event channel does not have to know who may see what.

The fan-out is built on ``refresh`` rather than a ``context`` patch on purpose.
A ``context`` patch carries the value of a registered serialize provider, and the builder reads that value from the origin page of the request that creates it.
A stream source has no page-render origin, so it cannot build a ``context`` patch from one.
A stream that needs to push fresh context drives a ``refresh``, and the re-fetched zone delivers the new context through its own render.
This is a documented limitation.
The stream source addresses zones to refresh, not provider values to push directly.

Echo suppression
----------------

The tab that triggered the change already has the fresh zone from its own response and must not apply the fan-out again.
The application channel threads the mutation's ``X-Next-Request-Id`` to the stream source, and the builder takes it as ``echo_of``.

.. code-block:: python
   :caption: echoing the originating request id

   Patches(request, echo_of=change.request_id).refresh(zone="poll-results")

The builder carries ``echo_of`` into the envelope as its ``request_id``, and every protocol backend serializes it unchanged.
The client keeps the last 25 of its own ``X-Next-Request-Id`` values and drops an event whose id matches.
Past 25 concurrent in-flight mutations the oldest id falls out of the ring and the subscriber applies one extra ``refresh``, which is safe.

The framework does not smuggle the request id through the application's channel.
A change event has to carry it, which the ``Change`` above does by holding the request id of the mutation that produced it.
``publish`` with a request-bound ``Patches`` builder attaches it automatically, see :doc:`/content/topics/realtime`.

WSGI and ASGI
-------------

A stream holds a connection open, and how it stays alive when idle depends on the source and the server.

The source kind and the server are paired by contract.
An async patch source requires ASGI and a sync source requires WSGI.
A mismatch raises :exc:`~django.core.exceptions.ImproperlyConfigured` when the response is built.
Without the guard the wrong-kind iterator buffers in full and hangs the stream before the first byte.
Pair an async source with ASGI and a sync source with WSGI.

A sync source under WSGI sends no heartbeat of its own.
A blocked ``next()`` on a sync iterator has nothing to interrupt it without a thread, so a quiet sync stream stays quiet.
The source asks for a keepalive by yielding ``None``, and the stream writes a ``: heartbeat`` comment in its place.
A sync source yields ``None`` whenever its wait times out, as ``patch_source`` in `The stream helper`_ does with the ``None`` that ``changes`` yields after each quiet ``timeout``.

An async source yields ``None`` the same way.
The comment is not an envelope, so it does not count in ``envelopes_sent``.
Under WSGI the stream also occupies one worker for the full life of the connection, so the recommendation is ASGI for any real workload.

An async source under ASGI receives heartbeat comments.
The stream interleaves a heartbeat during a quiet period through :func:`asyncio.wait`, so a buffering proxy keeps the connection.
The heartbeat period is ``SSE.HEARTBEAT_SECONDS`` in ``PARTIAL_BACKENDS``.
One stream overrides the period with ``PatchEventStream(request, source, heartbeat_seconds=10)``, which leaves the backend default in place for every other stream.

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * - Source and server
     - Heartbeat
     - Worker cost
   * - Sync source, WSGI
     - None of its own, the source yields ``None`` for a keepalive
     - One worker per open connection
   * - Async source, ASGI
     - Sent on a quiet period, and on every ``None`` the source yields
     - One task per open connection

To move a stream from sync to async, swap the feed's wake primitive for an async one and pass an async source to ``PatchEventStream``.
The page module and the signal layer do not change.

Stream politeness
-----------------

``PatchEventStream`` sets the politeness headers on construction so a proxy or ``GZipMiddleware`` does not eat the flush.

* ``Cache-Control: no-cache, no-transform`` keeps a proxy from buffering and re-compressing the stream.
* ``X-Accel-Buffering: no`` turns off nginx buffering.
* The leading ``retry`` hint comes from ``SSE.RETRY_MS`` so the browser's native reconnect uses the configured interval.

On the client a background tab pauses the stream by closing the connection.
When the tab becomes visible the runtime reconnects and re-fetches the zones the stream addressed since the connection opened.
A tab hidden for less than three seconds reconnects the stream and skips the re-fetches.
Each connection tracks at most 64 zones for the resume re-GET, so a long sleep cannot storm the server.
Events missed while paused are not lost, because ``refresh`` is idempotent.
The re-fetch brings the current state regardless of how many fan-outs were missed.

Stream telemetry
----------------

``PatchEventStream`` announces its own life through two signals, both sent with the stream class as the sender.
``sse_stream_opened`` carries the ``request`` and fires when iteration starts and the first frame is about to be sent.
``sse_stream_closed`` carries the ``request``, the ``duration_ms`` the connection lived, and the ``envelopes_sent`` count yielded to Django's response iterator.
It fires when the source is exhausted or the response closes its iterator.
The count does not confirm network delivery after a disconnect.
A blocked sync source cannot observe a disconnect until another item is yielded or the source returns.
Both signals go out with ``send_robust``, or ``asend_robust`` from an async source, so a receiver that raises is logged and never ends the stream.
Under ASGI a synchronous receiver runs off the event loop, so it may query the database, and an ``async def`` receiver is awaited.

.. code-block:: python
   :caption: notes/signals.py

   import logging

   from django.dispatch import receiver

   from next.signals import sse_stream_closed

   logger = logging.getLogger(__name__)


   @receiver(sse_stream_closed)
   def log_stream_close(sender, **kwargs) -> None:
       logger.info(
           "stream closed after %.0fms, %d envelopes",
           kwargs["duration_ms"],
           kwargs["envelopes_sent"],
       )

A receiver connects once at startup, see :doc:`/content/topics/signals` for the ``ready`` idiom.
The full payloads of every partial signal are listed in :doc:`/content/ref/signals`.

See also
--------

.. seealso::

   :doc:`scenarios` for the live stream scenario end to end.
   :doc:`reference` for the SSE settings and the lifecycle events.
   :doc:`/content/deployment/wsgi-asgi` for choosing a server.
   :doc:`/content/topics/signals` for the ``sse_stream_opened`` and ``sse_stream_closed`` signals.
