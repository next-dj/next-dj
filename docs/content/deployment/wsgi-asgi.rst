.. _deployment-wsgi-asgi:

WSGI and ASGI
=============

A next.dj project runs under WSGI or ASGI, and the choice decides the configuration and the hosts that suit it.

.. contents::
   :local:
   :depth: 2

Which to choose
---------------

WSGI is the default and suits most projects.
Pick WSGI when every response is synchronous and short lived, because a long-lived SSE connection occupies a worker while it waits.

ASGI is the right choice when the project uses any of these features.

- Server Sent Events for real time updates, including the patch streams described in :doc:`/content/topics/partial-rendering/sse`.
- WebSocket delivery through the optional Channels integration.
- Async view handlers that await I/O.
- Streaming responses that hold the connection open.

Both servers run the same next.dj pipeline.
The difference lies in how Django dispatches the request.

For a patch stream the server kind is a contract rather than a preference, and it binds in both directions.
``PatchEventStream`` inspects the request and refuses a mismatched pair in its constructor, because Django buffers a mismatched iterator fully before the first byte and an infinite stream would hang instead of flushing.
An async source under a WSGI request raises :class:`~django.core.exceptions.ImproperlyConfigured`, and so does a sync source under an ASGI request.
A sync source therefore belongs on WSGI and an async one on ASGI, and only the async path interleaves heartbeats, since a blocked ``next()`` has nothing to interrupt it without a thread.
See :doc:`/content/topics/partial-rendering/sse` for the source contract.

WSGI configuration
------------------

A standard ``config/wsgi.py`` works without modification.
See Django's :doc:`WSGI deployment guide <django:howto/deployment/wsgi/index>`.

.. code-block:: python
   :caption: config/wsgi.py

   import os

   from django.core.wsgi import get_wsgi_application

   os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
   application = get_wsgi_application()

A project that split its settings into a package keeps the ``config.settings.dev`` default here and exports ``DJANGO_SETTINGS_MODULE=config.settings.prod`` for the deployed process.
See :doc:`/content/howto/split-settings-per-environment`.

Run with a production WSGI server such as ``gunicorn`` or ``uwsgi``.

.. code-block:: bash
   :caption: shell

   uv run gunicorn config.wsgi:application --workers 4 --bind 0.0.0.0:8000

The example passes ``--workers 4``.
Start from a heuristic such as ``(2 * num_cores) + 1`` and tune against the measured concurrency and request duration.

ASGI configuration
------------------

A standard ``config/asgi.py`` works without modification.
See Django's :doc:`ASGI deployment guide <django:howto/deployment/asgi/index>`.

To accept realtime WebSockets, wrap Django's HTTP ASGI application with ``next.contrib.channels.application`` and install ``channels``.
The wrapper checks the WebSocket Origin against concrete ``ALLOWED_HOSTS`` entries, each read as a scheme-less pattern that ignores the port.
A leading-dot entry such as ``.example.com`` allows ``example.com`` and every subdomain, as it does in Django.
A ``*`` entry is refused with ``ValueError``, because a wildcard cannot protect a cookie-authenticated socket.
Pass an explicit ``origins`` list when the public origin differs from those hosts or when policy requires an exact scheme and port.
See :doc:`/content/howto/stream-over-websockets-with-channels`.

.. code-block:: python
   :caption: config/asgi.py

   import os

   from django.core.asgi import get_asgi_application

   os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
   application = get_asgi_application()

A project that split its settings into a package keeps the ``config.settings.dev`` default here and exports ``DJANGO_SETTINGS_MODULE=config.settings.prod`` for the deployed process.
See :doc:`/content/howto/split-settings-per-environment`.

Run with an ASGI server such as ``daphne`` or ``uvicorn``.

.. code-block:: bash
   :caption: shell

   uv run uvicorn config.asgi:application --host 0.0.0.0 --port 8000 --workers 4

Worker sizing
-------------

WSGI workers are blocking.
One worker handles one request at a time.
Size the worker count proportional to the request rate and the average response time.

ASGI workers can multiplex many connections through an event loop.
A single worker handles many concurrent SSE or websocket connections.
A patch stream (see :doc:`/content/topics/partial-rendering/sse`) holds its connection open until the source ends or the client disconnects, which outlives any reasonable sync worker timeout.
Serve those streams from ASGI workers rather than raising the WSGI worker timeout.

Reverse proxy
-------------

Place a reverse proxy in front of either server for TLS termination and connection handling.
The most common choice is ``nginx`` or ``caddy``.
An ordinary next.dj response asks nothing of the proxy that a Django response does not.

A patch stream asks the proxy to stay out of the way, and ``PatchEventStream`` states that in the response itself.
It sets ``Cache-Control: no-cache, no-transform`` so no intermediary caches an event frame or recompresses the body, and ``X-Accel-Buffering: no`` so nginx forwards each frame rather than filling a buffer first.
What the headers cannot state is the connection lifetime, so raise the proxy read and send timeouts above the expected stream duration and turn response buffering off for the streaming route on any proxy that ignores the hint.

A manual ``PatchEventStream`` with an async source sends a comment heartbeat every ``SSE.HEARTBEAT_SECONDS`` to keep a quiet connection from being reaped, and one with a sync source sends none, so a sync source behind an idle timeout supplies its own keepalive.
A ``TopicStream`` sends its heartbeat every ``REALTIME['HEARTBEAT']`` seconds under WSGI and ASGI alike, and the WebSocket consumer sends a ``ping`` frame at the same interval.

For WebSockets, configure the proxy to forward ``Upgrade`` and ``Connection`` headers, set its read timeout above the connection lifetime, and allow heartbeats through without buffering.
Keep ``REALTIME['HEARTBEAT']``, 25 seconds by default, below the shortest proxy or load balancer idle timeout.
A WebSocket client closes a socket whose ``hello`` has not arrived within five seconds of opening, and the server sends ``hello`` only once the broker confirms the subscription, so a slow broker shows up as reconnects and then as SSE fallback.
See :doc:`/content/topics/partial-rendering/sse` and :doc:`/content/howto/stream-over-websockets-with-channels`.

Health checks
-------------

Add a health check page that returns ``200`` quickly.
A pure ``render`` function avoids the layout chain and the static collector.

.. code-block:: python
   :caption: notes/pages/healthz/page.py

   from django.http import HttpRequest, JsonResponse

   def render(request: HttpRequest) -> JsonResponse:
       return JsonResponse({"status": "ok"})

Configure the load balancer or container orchestrator to hit ``/healthz/`` for liveness.

Concurrency notes
-----------------

Form dispatch reuses the request scoped dependency cache on the re-render that follows a validation failure, see :doc:`/content/topics/forms/validation-rerender`.
This is safe under both WSGI and ASGI because the cache lives on the request, not on the worker process.

The router manager and the components registry are process scoped.
Hot reload through ``router_manager.reload`` updates the running worker.
A multi worker deployment must trigger the reload from every worker or restart the process group.

Realtime publication has the same process boundary.
The default ``MemoryBroker`` reaches subscribers in one process only, so multi-worker deployments need a shared broker.
Use ``RedisBroker`` from the ``broker`` extra or ``ChannelsLayerBroker`` with a shared Channels layer and shared atomic Django cache.
The Channels layer's fan-out and the cache sequence increment are separate operations, so a detected sequence gap is repaired by guarded HTTP zone resync rather than broker replay.

See also
--------

.. seealso::

   :doc:`checklist` for the deployment checklist.
   :doc:`settings` for the production settings.
