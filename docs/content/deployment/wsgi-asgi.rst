.. _deployment-wsgi-asgi:

WSGI and ASGI
=============

This page covers the choice between WSGI and ASGI, the configuration each requires, and the recommended hosts for each option.

.. contents::
   :local:
   :depth: 2

Which to Choose
---------------

WSGI is the default and suits most projects.
Pick WSGI when every response is synchronous and short lived.

ASGI is the right choice when the project uses any of these features.

- Server Sent Events for real time updates.
- Websockets for bidirectional communication.
- Async view handlers that await I/O.
- Streaming responses that hold the connection open.

Both servers run the same next.dj pipeline.
The difference lies in how Django dispatches the request.

WSGI Configuration
------------------

A standard ``config/wsgi.py`` works without modification. See Django's :doc:`WSGI deployment guide <django:howto/deployment/wsgi/index>`.

.. code-block:: python
   :caption: config/wsgi.py

   import os
   from django.core.wsgi import get_wsgi_application

   os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
   application = get_wsgi_application()

Run with a production WSGI server such as ``gunicorn`` or ``uwsgi``.

.. code-block:: bash
   :caption: shell

   uv run gunicorn config.wsgi:application --workers 4 --bind 0.0.0.0:8000

The example uses four workers.
Gunicorn's own default ``--workers`` count is the number of CPU cores plus one.
Tune based on the expected concurrency and the average request duration.

ASGI Configuration
------------------

A standard ``config/asgi.py`` works without modification. See Django's :doc:`ASGI deployment guide <django:howto/deployment/asgi/index>`.

.. code-block:: python
   :caption: config/asgi.py

   import os
   from django.core.asgi import get_asgi_application

   os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
   application = get_asgi_application()

Run with an ASGI server such as ``daphne`` or ``uvicorn``.

.. code-block:: bash
   :caption: shell

   uv run uvicorn config.asgi:application --host 0.0.0.0 --port 8000 --workers 4

Worker Sizing
-------------

WSGI workers are blocking.
One worker handles one request at a time.
Size the worker count proportional to the request rate and the average response time.

ASGI workers can multiplex many connections through an event loop.
A single worker handles many concurrent SSE or websocket connections.

Reverse Proxy
-------------

Place a reverse proxy in front of either server for TLS termination and connection handling.
The most common choice is ``nginx`` or ``caddy``.
The next.dj pipeline does not require any special configuration on the proxy beyond standard Django requirements.

Health Checks
-------------

Add a health check page that returns ``200`` quickly.
A pure ``render`` function avoids the layout chain and the static collector.

.. code-block:: python
   :caption: notes/routes/healthz/page.py

   from django.http import HttpRequest, JsonResponse

   def render(request: HttpRequest) -> JsonResponse:
       return JsonResponse({"status": "ok"})

Configure the load balancer or container orchestrator to hit ``/healthz/`` for liveness.

Concurrency Notes
-----------------

Form dispatch reuses the request scoped dependency cache so a re-render after validation failure is cheap.
This is safe under both WSGI and ASGI because the cache lives on the request, not on the worker process.

The router manager and the components registry are process scoped.
Hot reload through ``router_manager.reload`` updates the running worker.
A multi worker deployment must trigger the reload from every worker or restart the process group.

See Also
--------

.. seealso::

   :doc:`checklist` for the deployment checklist.
   :doc:`settings` for the production settings.
