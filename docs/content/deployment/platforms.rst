.. _deployment-platforms:

Serverless and platform hosts
=============================

A page with deferred boundaries streams only where the response reaches the client write by write and the server keeps serving other requests meanwhile.
A serverless function or a managed platform often decides both facts outside the project, and the request rarely says which way they went.
This page explains what each fact means, how a deployment states it, and how to verify the result.

.. contents::
   :local:
   :depth: 1

The two facts
-------------

``DEFER["TRANSPORT"]`` names the backend that answers both facts once per request, see :doc:`/content/topics/boundaries/streaming`.

``incremental``
   Each write of the response leaves the process at once and reaches the client before the next one.
   A transport that is not incremental collects the body first, so a stream would arrive in one piece after the slowest boundary anyway.
   Such a response is assembled inline under the ``buffered`` reason, whatever ``SYNC_WORKERS`` says.
``concurrent``
   The server serves other requests while one response streams.
   A transport that is not concurrent would hold its only worker while a slow client reads, so the response is assembled inline under the ``sync-worker`` reason, unless ``DEFER["POLICY"]["OPTIONS"]["SYNC_WORKERS"]`` is ``"stream"``.

A page assembled inline loses the early shell and nothing else, see :ref:`topics-boundaries-streaming-transport`.

Stating the truth
-----------------

The default transport trusts the server's claims, which can be wrong for a given host, see :ref:`topics-boundaries-streaming-transport`.
``DEFER["TRANSPORT"]["OPTIONS"]`` states each fact explicitly, and a stated fact wins over every mark and every claim.

.. list-table::
   :header-rows: 1
   :widths: 60 40

   * - The deployment
     - The setting
   * - A function invoke mode that returns the response only after the handler finished
     - ``"INCREMENTAL": False``
   * - A reverse proxy with response buffering on for the page routes, or a CDN that buffers
     - ``"INCREMENTAL": False``, or turn the buffering off
   * - An adapter that collects the body on some requests only and leaves a key of its own in those
     - list that key in ``"BUFFERED_MARKS"``
   * - A runtime that serves requests on threads and forwards every write, yet reports ``wsgi.multithread`` as false
     - ``"CONCURRENT": True``
   * - A host that runs one request per instance under an ASGI server
     - ``"CONCURRENT": False``

.. code-block:: python
   :caption: config/settings/prod.py

   NEXT_FRAMEWORK = {
       "DEFER": {
           "TRANSPORT": {
               "OPTIONS": {"INCREMENTAL": False},
           },
       },
   }

A deployment that buffers every response states ``INCREMENTAL`` and needs nothing else.
``BUFFERED_MARKS`` serves a project whose code runs both behind a buffering adapter and behind a streaming server, where one setting answers for both.
It lists the request keys and the key prefixes that the adapter leaves, per interface, and a request that carries one counts as buffered with the mark as its reason.
A WSGI mark is a key of ``request.META`` and an ASGI mark is a key of the scope, and the documentation or the source of the adapter names the keys it sets.
The default is ``None``, which lists no mark.

.. code-block:: python
   :caption: config/settings/prod.py

   NEXT_FRAMEWORK = {
       "DEFER": {
           "TRANSPORT": {
               "OPTIONS": {
                   "BUFFERED_MARKS": {
                       "WSGI": {"KEYS": ["adapter.event"], "PREFIXES": ["adapter."]},
                       "ASGI": {"KEYS": ["adapter.event"]},
                   },
               },
           },
       },
   }

A host whose answer depends on more than a request key takes a ``StreamTransport`` subclass named in ``DEFER["TRANSPORT"]["BACKEND"]``, see :doc:`/content/topics/boundaries/streaming`.

Telling what a deployment got
-----------------------------

The ``X-Next-Stream`` header, the first ``INFO`` record of a process, and the ``page_stream_declined`` signal each name why a page did not stream, see :ref:`topics-boundaries-streaming-observing`.
The log record needs no setup, so the platform logs already show the reason.

Verifying a stream end to end
-----------------------------

The header and the log describe what the process decided, and only a request from outside shows what the client received.
``curl -N`` turns off its own buffering, and its timing variables show when the first byte arrived and when the body ended.

.. code-block:: bash
   :caption: shell

   curl -sN -o /dev/null \
     -w "first byte %{time_starttransfer}s, total %{time_total}s\n" \
     https://example.com/catalog/

A streamed page answers its first byte long before the total, close to the time of the shell alone.
A first byte that arrives with the total means a layer between the process and the client buffered the body, and that layer is stated through ``"INCREMENTAL": False`` until its buffering is off.
``curl -N`` without ``-o`` prints the shell first and each boundary script as it arrives, which shows the order on a slow page.

Database connections behind a pooler
------------------------------------

A response with deferred boundaries holds the connection of its request thread and one connection per pool thread that runs a job of it, up to ``DEFER["EXECUTOR"]["OPTIONS"]["MAX_WORKERS"]`` per process.
A host that starts one process per request multiplies that by the instances it runs at once, so the connection count follows the instance count rather than the traffic of one instance.
A transaction pooler in front of the database absorbs those connections, and its client limit is at least the instances times ``REQUEST_CONCURRENCY`` plus ``MAX_WORKERS``.
A smaller ``MAX_WORKERS`` keeps the count down on such a host, and ``PARALLEL`` set to ``False`` keeps every job on the request thread with a single connection.
Under transaction pooling Django needs ``DISABLE_SERVER_SIDE_CURSORS`` set to ``True``, see :doc:`django:ref/databases`.
A ``ConnectionState`` replays its state with transaction-local statements, which a transaction pooler keeps on the right server connection.
A pool configured in ``OPTIONS['pool']`` of the alias is checked against the same budget by ``next.W162`` and against the deadline by ``next.W172``.
