.. _topics-boundaries-streaming:

Streaming
=========

A response that holds a ``stream`` boundary is delivered in one of two ways, decided once per request before the shell renders.
A streamed response sends the shell at once and writes each boundary as its job settles, and a response assembled inline runs the same jobs in parallel and sends the finished page in one piece.
Either way the jobs run concurrently wherever their lanes allow, so a slow boundary costs its own time rather than the sum of all of them, see :doc:`async`.

.. contents::
   :local:
   :depth: 1

The delivery policy
-------------------

``DEFER["DELIVERY"]["BACKEND"]`` names the ``DeliveryPolicy`` that decides, ``next.defer.DefaultDeliveryPolicy`` by default.
It answers a ``HEAD`` request with the shell alone and streams every ``GET`` that no row below holds back, the first row that holds naming the reason.
A router prefetch at the shell depth is answered with the shell alone before any row is read.
A full prefetch, sent at the press of a link, goes through the rows as a navigation does.

.. list-table::
   :header-rows: 1
   :widths: 18 82

   * - Reason
     - The response is assembled inline when
   * - ``prefetch``
     - The browser sent ``Sec-Purpose: prefetch``, a speculative load.
   * - ``method``
     - The method is not ``GET``, so a form post renders its page whole inside its transaction.
   * - ``noscript``
     - The query carries ``_nojs=1``, the address a streamed page sends a reader without scripts to.
   * - ``bot``
     - The user agent matches ``DELIVERY["OPTIONS"]["BOTS"]``, a regular expression that defaults to the common crawlers.
   * - ``cache``
     - A shared cache may store the page, see :doc:`/content/topics/caching`.
   * - ``gzip``
     - The client takes gzip from a middleware that buffers a stream, Django's ``GZipMiddleware`` below Django 6.1.
   * - ``buffered``
     - The server does not deliver a write as it happens, so a stream would arrive in one piece anyway, see `The server`_.
   * - ``sync_worker``
     - The server serves one request at a time, unless ``DELIVERY["OPTIONS"]["SYNC_WORKERS"]`` is ``"stream"``.
   * - ``runtime``
     - The ``disabled`` script policy keeps the client runtime off the page, so nothing would apply a chunk.
   * - ``capacity``
     - The process already holds ``MAX_STREAMS`` open streams.
   * - ``partial``
     - A partial request did not ask for ``text/event-stream``.
   * - ``option``
     - ``DELIVERY["OPTIONS"]["STREAM"]`` is ``False``.

A project subclasses ``DeliveryPolicy`` for a rule of its own, and its ``delivery(request, facts)`` reads the ``DeliveryFacts`` of the request, ``facts.probe`` among them.

.. _topics-boundaries-streaming-server:

The server
----------

Two facts of the deployment decide whether a stream helps, and ``DEFER["SERVER"]["BACKEND"]`` names the ``ServerProbe`` that answers them once per request, ``next.defer.DefaultServerProbe`` by default.
Its answer is a ``ServerFacts``, whose ``incremental`` says that each write reaches the client as it happens, and whose ``concurrent`` says that the server serves other requests while this one streams.
Each answer carries the fact that decided it, ``incremental_reason`` and ``concurrent_reason``, and ``reason`` names the one that holds a stream back.
A server that is not incremental assembles every page inline under ``buffered``, whatever ``SYNC_WORKERS`` says, and one that is not concurrent follows ``SYNC_WORKERS`` under ``sync_worker``.

The default probe takes the first answer it finds, in this order.

Options
   ``SERVER["OPTIONS"]["INCREMENTAL"]`` and ``SERVER["OPTIONS"]["CONCURRENT"]`` set to ``True`` or ``False`` state the answer, with the reason ``option``, and ``None`` leaves it to detection.
Marks
   A request key that the project lists as left by its buffering adapter answers ``incremental`` as ``False``, with the mark as the reason.
   ``SERVER["OPTIONS"]["BUFFERED_MARKS"]`` lists the exact keys and the key prefixes per interface, the WSGI ones read from ``request.META`` and the ASGI ones from the scope.
   The default is ``None``, which lists no mark, so the framework knows no adapter on its own.
Claims
   The server's own claims answer the rest.
   A WSGI server is concurrent when ``wsgi.multithread`` is true, an ASGI server always is, and both count as incremental, since PEP 3333 forbids a WSGI server to delay a block.

No standard lets a server, a proxy, or a CDN declare that it buffers a response.
An adapter that turns a function invocation into a WSGI or ASGI call collects the whole body and still reports ordinary values, and a server that forwards every write may claim to run a single thread.
The claims are therefore advisory, and the options state the truth where they are wrong.
Anything past the process boundary, a CDN, a reverse proxy with response buffering on, or a function invoke mode that buffers, is invisible to the request, so a deployment behind one sets ``INCREMENTAL`` to ``False``.
A page assembled in one piece still runs its deferred jobs in parallel, so it costs the slowest job rather than the sum of them.

A project subclasses ``ServerProbe`` for a host of its own, and its ``facts(request)`` returns a ``ServerFacts``.

.. code-block:: python
   :caption: myproject/probe.py

   from django.http import HttpRequest

   from next.defer import ServerFacts, ServerProbe

   class EdgeProbe(ServerProbe):
       def facts(self, request: HttpRequest) -> ServerFacts:
           edge = "HTTP_X_EDGE_BUFFERED" in request.META
           return ServerFacts(
               incremental=not edge,
               concurrent=True,
               incremental_reason="edge" if edge else "wsgi",
               concurrent_reason="host",
           )

.. _topics-boundaries-streaming-observing:

What a deployment got
---------------------

Three places tell why a page did not stream.

The header
   Under ``DEBUG`` a response with boundaries names its delivery in ``X-Next-Stream``, ``inline (buffered)`` for instance, and lists its boundaries in ``X-Next-Boundaries``.
The log line
   The first response with deferred work that a process assembles inline for a reason logs one record on the ``next.defer`` logger, naming the reason and the server reason.
   A reason of the deployment, such as ``gzip``, ``buffered``, ``sync_worker``, ``runtime``, or ``capacity``, holds for every page the process serves.
   Its record is therefore a ``WARNING``, which a stock Django logging configuration prints, and it names the setting that changes it.
   A reason of the request, such as ``bot``, ``prefetch``, or ``partial``, logs at ``INFO``, which reaches the logs only through a configuration that handles the ``next.defer`` logger at that level.
The signal
   ``page_stream_declined`` goes out once per page or partial response whose deferred jobs are assembled inline, with ``delivery``, ``reason``, ``server_reason``, ``partial``, and ``request``, so a receiver counts why pages do not stream.
   A response is decided once, so it sends either this signal or ``page_stream_opened``, never both.

.. code-block:: python
   :caption: myproject/signals.py

   from collections import Counter

   from django.dispatch import receiver

   from next.defer import DeliveryPolicy, page_stream_declined

   INLINE = Counter()


   @receiver(page_stream_declined, sender=DeliveryPolicy)
   def count_inline(sender, reason, server_reason, **kwargs):
       INLINE[reason, server_reason] += 1

.. _topics-boundaries-streaming-response:

What a streamed response is
---------------------------

A streamed page is a ``StreamingPageResponse``, a :class:`~django.http.StreamingHttpResponse` whose body is produced while the server reads it, sync under WSGI and async under ASGI.
It writes the shell up to the end of the ``{% collect_scripts %}`` slot, then one chunk per settled boundary, then an end marker and the rest of the document.
A chunk is the HTML of the boundary inside an inert ``<template>``, which the browser parses once as it arrives, followed by a short inline script that names the template and carries the rest of the envelope, its assets and its state.

The runtime moves the parsed nodes into the boundary without parsing the markup again, removes the template and the script, and drops the scripts of the fragment as it does for any patch.
The root element carries ``data-next-streamed``, and every chunk script carries the CSP nonce of the shell.
The ``<head>`` is not sent ahead of the shell, since it holds the assets the whole render collects.

The view finishes before the first job runs, so everything a response decides happens in the shell.

Status
   The status is the one the shell answered with, 200 for a page that rendered.
   A page callable declared ``guard=True``, or one taking a ``DObject`` row, runs before the shell, so its 404, 403, or 400 reaches the response, see :doc:`loading`, and the status an error UI of a job would set never does.
Headers
   The response turns private and revalidated, ``Cache-Control: private, no-cache``, keeping a stricter ``no-store`` or ``no-transform`` the page set and dropping ``max-age``, ``public``, and the other freshness directives.
   It drops ``ETag``, ``Last-Modified``, and ``Content-Length``, adds ``X-Accel-Buffering: no`` and ``Vary: Accept-Encoding, Accept``, and negotiates its content coding last.
Cookies
   The middleware stack writes its cookies before the first byte.
   The request settles what jobs share on the request thread first, the user, the session, the messages, and the CSRF token, whose cookie therefore goes out with the shell, so a form inside a boundary posts.
   A job that writes the session, rotates its key, reads the messages, or rotates the CSRF token writes something no header can carry any more.
   Each such write logs a warning on the ``next.defer`` logger, naming the boundary, its template, and the cure, and sends ``boundary_state_written``.
   The cure is to write in the view or a guard of the page, or to render the component inline with ``defer="inline"`` at the call site.
Middleware
   ``process_response`` sees the response before any job ran, and the response has no ``content``, so a middleware that reads or rewrites the body has to handle ``streaming_content``.
   A middleware that buffers a stream holds every chunk until the end, which is why the ``gzip`` row exists, and ``manage.py check`` warns about Django's ``GZipMiddleware`` as ``next.W153``.

A job past its deadline settles as ``timeout``.
Each job has the earlier of its own ``timeout`` and ``DEFER["DEADLINE"]``, ten seconds after the response opened, and synchronous code cannot be interrupted, so the late result of a sync job is dropped when it returns.
``next.defer.remaining()`` answers the seconds a job has left, for code that can stop early.

A page whose boundaries all timed out still answers 200, so the degradation shows in the logs and the signals rather than in the status.
A job that settles as ``timeout`` or ``overloaded`` without a result logs a ``WARNING`` on the ``next.defer`` logger, at most once per call site, outcome, and quiet period of ten minutes, naming the page path and the settings that bound it.
A stream cut ``WRITE_GRACE`` seconds past the deadline logs a ``WARNING`` of its own, and ``page_stream_closed`` reports ``timeouts``, ``abandoned``, ``cancelled``, ``dropped``, and ``cut`` beside ``late`` and ``shed`` for every stream, see :doc:`/content/ref/signals`.
Under ASGI a write the client has not taken after ``DEFER["WRITE_TIMEOUT"]`` seconds closes the stream with a ``WARNING`` as well, and its slot and jobs go free, see :ref:`deployment-slow-clients` for the WSGI side and the proxy setting.

Compression
-----------

A streamed page compresses itself, padded, compressed, and flushed once per write, so the browser decodes each chunk as it arrives.
``CompressionMiddleware`` passes such a response through and compresses every other one, see :doc:`/content/topics/compression`.
Outcomes that settle within ``COMPRESSION["COALESCE"]`` of each other share one write.
A page that sets ``no-transform``, or calls ``page.compress(enabled=False)``, streams uncompressed.
Under ``COMPRESSION["STREAMS"] = "middleware"`` the middleware compresses the stream instead, and ``next.W193`` reports a ``MIDDLEWARE`` that lacks it.

Partial responses
-----------------

A partial request, such as a zone reload, whose render holds ``stream`` boundaries streams too, as a ``text/event-stream`` of patch frames, when the runtime asked for one.
The runtime asks only while the page told it that partial responses may stream, and it then loads the ``boundaries`` bundle that reads such a response.
The first frame carries the envelope with the loading UI in place, and each later frame fills one boundary.
``page_stream_opened`` and ``page_stream_closed`` go out for a partial stream as for a page, with ``partial=True``.

Client states and events
------------------------

Every wrapper carries ``data-next-state``, which a stylesheet reads.

.. list-table::
   :header-rows: 1
   :widths: 14 86

   * - State
     - Meaning
   * - ``loading``
     - A stream job has not settled yet, and the wrapper carries ``aria-busy="true"``.
   * - ``idle``
     - A client-trip boundary waits for its fetch.
   * - ``ready``
     - The content is in place.
   * - ``error``
     - The error UI is in place, and ``data-next-error`` names the kind.

While the runtime fetches a boundary it adds ``data-next-busy`` and ``aria-busy`` to the wrapper and shows a non-ready wrapper as ``loading`` again.
A streamed page whose stream ended with a boundary still ``loading``, a dropped connection for instance, marks it ``error`` with the kind ``incomplete``.
The ``defer`` bundle then fetches each such boundary that carries a token or sits inside a zone once more, after a random delay of up to two seconds, and stops after a 429 or a 5xx answer.
A streamed partial response that ends that way fires ``partial:error`` of kind ``incomplete`` with the ids of those boundaries.

A chunk applies through the same pipeline as any envelope, so ``partial:before-apply`` and ``partial:applied`` fire for it.
``partial:before-apply`` lists in ``boundaries`` the wrappers the envelope touches, and its ``hold()`` returns a release function, holding the ops of this envelope and every later one until it is called, so an exit animation finishes first.
``partial:settled`` fires after each request and each batch of chunks, with the ``zones`` and ``boundaries`` it settled and its ``status``.
See :doc:`/content/ref/client` for the payloads.

.. code-block:: javascript
   :caption: static/shop/boundaries.js

   document.addEventListener("partial:before-apply", (event) => {
     const { boundaries, hold } = event.detail;
     if (boundaries.length === 0) return;
     const release = hold();
     Promise.all(boundaries.map((el) => el.animate({ opacity: [1, 0] }, 150).finished))
       .then(release);
   });

Without scripts a chunk never applies, so ``DEFER["NOSCRIPT"]``, on by default, opens the ``<head>`` of a streamed page with a ``<noscript>`` that refreshes to the same address with ``_nojs=1`` added to its query string, and the policy assembles that request whole under the ``noscript`` reason.
The same ``<noscript>`` hides each wrapper still ``loading`` meanwhile, for a reader that ignores the refresh.
The flag changes the address, so a cache keys the assembled page apart from the streamed one, and a streamed page is ``private`` already.
The flag stays out of the self URL of the page, so the canonical link and the ``hreflang`` alternates of the assembled page name the address without it.
A request that already carries the flag is never sent on again, even under a project policy that streams it.

Deployment
----------

The transport decides what streams, and :doc:`/content/deployment/platforms` covers serverless and platform hosts.

WSGI, one request per worker
   The ``sync_worker`` row assembles every page inline, so a slow job holds the worker but never an open connection.
   ``SYNC_WORKERS="stream"`` streams anyway, with the worker held for the whole stream.
WSGI, threaded workers
   Pages stream, and ``MAX_STREAMS`` of ``"auto"`` allows half the request threads of the process, at least one.
   The process counts the live request threads that served a page with deferred boundaries, so ``gunicorn --threads 8`` reaches four streams once its threads have served such pages, and ``DEFER["REQUEST_CONCURRENCY"]`` is the floor of that count before they have.
   Set ``REQUEST_CONCURRENCY`` to the threads of one worker anyway, since it also sizes the database connections ``next.W162`` compares a pool against.
ASGI
   Pages stream with an async body, and ``"auto"`` allows four streams per pool worker, ``EXECUTOR["OPTIONS"]["MAX_WORKERS"]``.

A proxy in front of a streaming project passes each write on.
nginx reads ``X-Accel-Buffering: no`` from the response, and another proxy or a CDN needs response buffering turned off for the page routes, or ``SERVER["OPTIONS"]["INCREMENTAL"]`` set to ``False``.
Its idle read timeout stays at or above ``DEFER["DEADLINE"]``, since a job on the request thread keeps a WSGI stream quiet while it runs, see :doc:`/content/intro/limitations`.
A CSP middleware needs ``CSP_NONCE`` on and the nonce in its ``script-src``, or the browser refuses every chunk, which ``next.W160`` reports.

A PostgreSQL connection pool holds at least ``REQUEST_CONCURRENCY`` plus ``MAX_WORKERS`` connections, ``next.W162``, and waits no longer than the deadline for one, ``next.W172``.
``REQUEST_CONCURRENCY`` counts the requests one process serves at once, the threads of a WSGI worker or the concurrency limit of an ASGI one, such as ``uvicorn --limit-concurrency``.
:doc:`/content/deployment/wsgi-asgi` covers the server setup and the capacity estimate.

.. _topics-boundaries-streaming-limits:

Known limits
------------

Broken markup in a fragment
   A boundary renders to markup the framework does not parse again, so an unclosed comment or an unclosed ``<template>`` in a fragment damages what follows it in the stream the way it damages the rest of a page that does not stream.
   Keep the markup of a component well formed, see :ref:`topics-boundaries-streaming-response`.
A stream that breaks before a boundary fills
   A boundary that streams carries no token, so when the connection drops before its fragment arrives, the client shows its error UI and does not fetch it again.
   Only a boundary inside a zone, or a client-trip boundary, is fetched once more, and the visitor reloads the page otherwise, see :doc:`/content/deployment/boundaries`.
An ``async def`` inside a boundary
   The callable awaits on the event loop while the thread of its job waits, so the job holds a pool thread, or the request thread on the request lane, for the whole wait, see :doc:`async`.
SQLite
   A SQLite project runs its boundaries one after another on the request lane by default, so their waits add up, and ``EXECUTOR["SQLITE"]`` set to ``"parallel"`` overlaps them for a file database, see :doc:`async`.
Stored procedures
   The replay of connection state and the statement timeout reach a connection through its execute wrapper, so ``cursor.callproc`` and the raw driver connection run without them.
   Call a procedure through ``cursor.execute("CALL ...")``, see :doc:`async`.
A third-party ``<script>`` in a navigation
   The client router swap runs an inline script only under the nonce of the response, so a page with a ``<script src>`` or an inline script without that nonce in a swapped slot loads whole, and its boundaries stream in that document load, see :ref:`topics-router-browser`.

Tests
-----

The Django test client runs a request on a single-threaded worker, so a page renders inline there.
``read_stream(client, url)`` and the async ``aread_stream(client, url)`` of ``next.testing`` request the page as it streams under a threaded server and return a ``StreamedPage`` with its ``shell``, its ``chunks``, and its ``tail``, and ``NextClient.get_stream(url)`` does the same.
A partial stream of ``text/event-stream`` frames reads through the same helpers, and ``sse_events(text)`` parses any such text into its events and a flag that tells whether the text ends inside a frame.

``parallel_boundaries()`` runs the jobs of the block on the pool, which reads only committed rows, and ``boundary_gate(component=...)`` holds the jobs of one component until the test releases it.
:doc:`/content/howto/test-a-page-with-deferred-boundaries` walks through each of them.

.. seealso::

   :doc:`/content/howto/show-a-loading-state` for the task of putting a loading UI beside a component.
   :doc:`/content/deployment/platforms` for streaming on serverless and platform hosts.
   :repo:`observability <tree/main/examples/observability>` streams a component and a page body behind their loading UIs.
   :repo:`search-catalog <tree/main/examples/search-catalog>` streams two rails of a product page and batches their lookups.
