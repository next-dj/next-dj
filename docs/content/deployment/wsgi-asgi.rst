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
- WebSocket delivery through the built-in ``WebSocketApp``.
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

The worker count in the command is a placeholder.
Set it from a load run against the target hardware and the measured concurrency and request duration, see `Estimate capacity and scale out`_.

ASGI configuration
------------------

A standard ``config/asgi.py`` works without modification.
See Django's :doc:`ASGI deployment guide <django:howto/deployment/asgi/index>`.

To accept realtime WebSockets, wrap Django's HTTP ASGI application with ``next.realtime.application``.
The wrapper serves the socket at ``REALTIME['WS_PATH']`` and sends everything else to Django, so no extra package is needed.
Creating the ``WebSocketApp`` marks the process as serving WebSocket, and the default ``TRANSPORTS`` then offers ``ws`` before ``sse``.
The socket accepts an Origin that names its scheme and validated ``Host`` or matches ``CSRF_TRUSTED_ORIGINS``, as Django's CSRF check does, and it refuses a connection with no Origin.
Behind a TLS-terminating proxy, set ``SECURE_PROXY_SSL_HEADER`` so the ``https`` Origin matches, which CSRF already needs.
Pass ``WebSocketApp(origins=[...])`` to ``application(..., websocket=...)`` when the public origin differs from those.
See :doc:`/content/howto/stream-over-websockets`.

.. code-block:: python
   :caption: config/asgi.py

   import os

   from django.core.asgi import get_asgi_application

   from next.realtime import application as realtime_application

   os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
   application = realtime_application(get_asgi_application())

A project that split its settings into a package keeps the ``config.settings.dev`` default here and exports ``DJANGO_SETTINGS_MODULE=config.settings.prod`` for the deployed process.
See :doc:`/content/howto/split-settings-per-environment`.

Run with an ASGI server such as ``uvicorn``.
Install ``uvicorn[standard]``, because the WebSocket transport needs a WebSocket library that the bare ``uvicorn`` package does not bring.
Without one uvicorn refuses the upgrade, and the client falls back to SSE.

.. code-block:: bash
   :caption: shell

   uv run uvicorn config.asgi:application --host 0.0.0.0 --port 8000 --workers 4 --ws-max-size 65536

The ``--workers`` value is a placeholder, see `Estimate capacity and scale out`_.
The socket closes an inbound frame larger than 64 KiB with ``1009`` anyway, and ``--ws-max-size 65536`` keeps uvicorn from buffering up to 16 MiB first, as :doc:`/content/howto/stream-over-websockets` explains.

Behind gunicorn, use the uvicorn worker class, ``gunicorn config.asgi:application -k uvicorn.workers.UvicornWorker``.
Every worker imports ``config/asgi.py`` itself, so each one mounts the socket and offers ``ws``.

The development server needs no separate command.
``manage.py runserver`` serves the ASGI application that ``ASGI_APPLICATION`` names once uvicorn is installed, and ``next`` has to sit above ``django.contrib.staticfiles`` in ``INSTALLED_APPS`` for its command to win.
See :doc:`/content/ref/management` for the rule.

Split deployments
~~~~~~~~~~~~~~~~~

A project may serve its pages from a WSGI deployment and run the socket in a separate ASGI deployment, or put a gateway of its own in front.
The process that renders the pages never mounts a ``WebSocketApp``, so the automatic selection keeps it on SSE.
List ``"ws"`` in ``REALTIME['TRANSPORTS']`` there, which the framework trusts as written.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "REALTIME": {
           "TRANSPORTS": ["ws", "sse"],
       },
   }

Route ``WS_PATH`` to the ASGI deployment at the proxy, and share ``SECRET_KEY``, the session store, and the broker between both.
The explicit list also makes ``manage.py check`` run the WebSocket deployment checks, ``next.W141`` and ``next.W142`` among them, in the WSGI project.
Put ``"sse"`` alone in the list to turn WebSocket off whatever is mounted.

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
A ``TopicStream`` sends its heartbeat every ``REALTIME['HEARTBEAT']`` seconds under WSGI and ASGI alike, and the WebSocket sends a ``ping`` frame at the same interval.

For WebSockets, configure the proxy to forward ``Upgrade`` and ``Connection`` headers, set its read timeout above the connection lifetime, and allow heartbeats through without buffering.
Keep ``REALTIME['HEARTBEAT']``, 25 seconds by default, below the shortest proxy or load balancer idle timeout.
A WebSocket client closes a socket whose ``hello`` has not arrived within five seconds of opening, and the server sends ``hello`` only once the broker confirms the subscription, so a slow broker shows up as reconnects and then as SSE fallback.
See :doc:`/content/topics/partial-rendering/sse` and :doc:`/content/howto/stream-over-websockets`.

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
Use ``RedisBroker`` from the ``broker`` extra.
A reconnect catches up from the broker's bounded replay log, and any gap the log cannot cover is repaired by a guarded HTTP zone resync.

A WebSocket loads its session from the cookie at the handshake and on every recheck.
A recheck also runs at the session's deadline, so a session that ``SESSION_SAVE_EVERY_REQUEST`` renews keeps its socket open.
Under load a session cache that evicts live entries closes sockets with ``4401``, so size the session cache for the open connections or use the ``db`` or ``cached_db`` engine.

.. _deployment-capacity:

Estimate capacity and scale out
-------------------------------

The framework fixes no capacity figure, because the number a process holds depends on the hardware, the broker, the proxy, and the work the pages do.
Measure it on the install that serves the traffic.

``make load`` runs the framework's own load harness from a repository checkout.
It publishes and subscribes through the real broker and the real WebSocket and SSE paths, with ``PROFILE`` choosing the scenario, ``LOAD_DURATION`` the run length, and ``REDIS_URL`` the Redis to use.
Point ``REDIS_URL`` at a Redis of the same kind as production and run it on the target hardware.
The harness drives an app of the framework, not the pages of the project.
It therefore measures the transport, meaning delivery latency, losses, and the cost of connections and replay, and it says nothing about how the project's own views, handlers, and queries behave.
Measure those with a load tool of your own pointed at the deployed pages.
:doc:`/content/contributing/quality-gates` describes the harness, its profiles, and its result file.

Watch these signals during a run and in production.

- Delivery latency from publish to the client and the count of lost or replayed messages.
- Server CPU of every ASGI process, since the event loop is single threaded.
- The Redis connection pool, because every open WebSocket and SSE stream holds one connection for its subscription, and an exhausted pool ends the stream and makes the browser reconnect.
- The Redis ``maxclients`` limit, which all processes share.
- The session cache, which closes sockets with ``4401`` when it evicts live sessions.
- The handler thread pool, which runs sync upstream handlers and tunneled views and queues work when handlers are slow.
- File descriptor limits of the process and connection limits of the proxy and load balancer.
- Presence cost, which grows with the attendee count and the ``REALTIME['PRESENCE']['SIZE']`` member cap.
- Replay cost, which grows with ``REALTIME['REPLAY']['SIZE']`` and the size of each message.

Scale out with more ASGI processes behind one ``RedisBroker``, on one host or several.
The broker carries every publication to the subscribers of every process, so no process needs to know where a client sits, and sticky sessions are not required.
Raise ``OPTIONS['CLIENT']['max_connections']`` of the broker with the streams a process holds, and keep the sum across processes under the Redis ``maxclients``, see :doc:`/content/howto/fan-out-across-processes-with-redis`.
Repeat the load run after each change of process count, pool size, or replay and presence caps, and run it before launch against the shared broker, see :doc:`checklist`.

See also
--------

.. seealso::

   :doc:`checklist` for the deployment checklist.
   :doc:`settings` for the production settings.
