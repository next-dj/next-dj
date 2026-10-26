.. _howto-stream-over-websockets:

Stream updates over WebSockets
==============================

Problem
-------

An application already renders an authorized ``TopicStream`` over SSE and needs WebSocket delivery for deployments that run under ASGI.

Solution
--------

Wrap Django's ASGI application with ``next.realtime.application``.
The same guarded page endpoint then negotiates the transport.
An importable topic authorizer is checked before the subscription and throughout the connection.
The socket is part of the framework and runs on plain ASGI, so an ASGI server is the only extra requirement.

.. contents::
   :local:
   :depth: 1

Mount the socket
----------------

The socket rechecks its session on the server while it stays open, so ``SESSION_ENGINE`` should store sessions on the server.
The ``db``, ``cache``, ``cached_db``, and ``file`` engines work.
The signed-cookie engine re-signs its cookie on every session save, which breaks the ticket of a socket that opens after the save, and ``manage.py check`` reports it as ``next.W142``.
See :ref:`topics-realtime` for the details.

Wrap Django's HTTP ASGI application with ``next.realtime.application``.
The helper serves the socket at ``REALTIME['WS_PATH']``, loads the Django session from the cookie, and validates the WebSocket Origin the way Django's CSRF check does.

.. code-block:: python
   :caption: config/asgi.py

   import os

   from django.core.asgi import get_asgi_application

   from next.realtime import application as realtime_application

   os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
   django_application = get_asgi_application()
   application = realtime_application(django_application)

Creating the socket marks the process as serving WebSocket, so the default ``TRANSPORTS`` offers ``ws`` first and keeps ``sse`` as the fallback.
Nothing else needs configuring.
Set ``NEXT_FRAMEWORK["REALTIME"]["TRANSPORTS"]`` to ``["sse"]`` to disable WebSocket negotiation.

The existing guarded ``TopicStream`` page remains the subscription endpoint.
The browser first performs its normal GET with ``X-Next-Transport: ws``.
The response is ``application/json`` and contains a short-lived, session-bound ticket scoped to that stream's topics, transform, and authorizer.
The client sends the ticket in its first WebSocket frame and never places it in the URL.
A reply of any other content type, or no reply within five seconds, sends the client to SSE.
A cookie that names a missing or expired session would fail the handshake.
In that case negotiation issues the ticket against a fresh session and the response rewrites the session cookie.
The signed-cookie engine has no session to create there, so a forged or expired signed cookie gets the ``503`` that a first visit gets.
See :ref:`topics-realtime` for the details.

Once the socket opens, its ``hello`` must arrive within five seconds, or the client closes the socket as a transient failure and retries.
The server sends ``hello`` only after the broker confirms the subscription.
``RedisBroker`` waits up to five seconds for that confirmation.
A degraded broker can therefore push clients through repeated reconnects and, after three quick failures, onto SSE.
See :ref:`ref-realtime-protocol` for every frame and close code.

.. code-block:: python
   :caption: polls/pages/polls/[int:id]/stream/page.py

   from django.http import HttpRequest
   from django.shortcuts import get_object_or_404
   from polls.models import Poll
   from polls.realtime import can_subscribe_topic

   from next.realtime import TopicStream
   from next.urls import DUrl

   def render(request: HttpRequest, poll_id: DUrl["id", int]) -> TopicStream:
       poll = get_object_or_404(Poll, pk=poll_id)
       return TopicStream(request, f"poll:{poll.pk}", authorize=can_subscribe_topic)

The authorizer is a synchronous, importable function accepting ``(user, topic)`` and returning a boolean.
The ``polls.realtime.can_subscribe_topic`` above is an example of one that a project writes, and :doc:`/content/howto/stream-live-updates-with-sse` defines it together with the rest of the page.
Omitting ``authorize`` leaves the stream open to any visitor who can load the page.
The socket calls the authorizer before joining the broker and then at most once per heartbeat interval, together with a recheck of the session.
A revocation closes the socket within about two heartbeat intervals plus the callback's own latency.
The close code is ``4401`` for a session problem and ``4403`` for a topic denial, each after an ``auth`` error frame.
After ``4403`` the client falls back to SSE at once.
After ``4401`` the client negotiates a fresh ticket once and falls back to SSE only when that fails.
Over SSE a revoked session reconnects through the page guard and a topic denial ends the stream for good.
The checks run on the shared thread pool rather than the single sync thread, so keep the callback's database work small.
WebSocket negotiation returns ``406`` when the ``transform``, ``authorize``, or ``presence`` identify callback has no stable importable reference.
The reply body names the cause in its ``reason`` field, and the client uses SSE.

Keep sensitive page data out of published topic payloads.
Publish a ``refresh`` operation for private zones.
Each subscriber then loads current HTML through its own guarded HTTP request and cookies.

Run the server
--------------

``manage.py runserver`` of next.dj serves the project over ASGI on uvicorn and mounts the socket, so development needs no separate server command.
It needs uvicorn with a WebSocket implementation, which the ``standard`` extra installs.

.. code-block:: bash
   :caption: shell

   uv add "uvicorn[standard]"
   uv run python manage.py runserver

Django runs the command of the application listed first in ``INSTALLED_APPS``, so ``next`` has to come before ``django.contrib.staticfiles``.
The command serves the application that ``ASGI_APPLICATION`` names, and falls back to ``next.realtime.application`` over Django's ASGI handler when the setting is absent.

.. code-block:: python
   :caption: config/settings.py

   INSTALLED_APPS = [
       "django.contrib.auth",
       "django.contrib.contenttypes",
       "django.contrib.sessions",
       "next",
       "django.contrib.staticfiles",
   ]

   ASGI_APPLICATION = "config.asgi.application"

The command restarts on code changes through Django's own autoreloader and serves static files under ``DEBUG``.
With ``staticfiles`` listed first, or without uvicorn, it is the WSGI ``runserver`` and clients stay on SSE.
The command caps inbound frames at 64 KiB and turns off the protocol ping of uvicorn, because the socket checks the liveness of its peer itself.
See :doc:`/content/ref/management` for the options.

Production runs several uvicorn workers, either through uvicorn itself or under gunicorn with the uvicorn worker class.
The ``--workers`` value below is an example to size from measurement, see :ref:`deployment-capacity` for how to measure capacity.

.. code-block:: bash
   :caption: shell

   uv run uvicorn config.asgi:application --host 0.0.0.0 --port 8000 --workers 4 --ws-max-size 65536
   uv run gunicorn config.asgi:application -k uvicorn.workers.UvicornWorker --workers 4

The socket closes a larger inbound frame with ``1009`` anyway.
``--ws-max-size 65536`` keeps uvicorn from buffering up to 16 MiB first.
Uvicorn pings every 20 seconds by default, which the socket does not need, so ``--ws-ping-interval`` is a deployment decision and a server-level ping is only an extra layer.
Every worker imports ``config/asgi.py`` itself, so each one mounts the socket and offers ``ws``.
The default ``MemoryBroker`` reaches subscribers in one process only.
Use ``RedisBroker`` once there is more than one worker, see :doc:`fan-out-across-processes-with-redis`.

The socket sends a ``ping`` frame once a ``NEXT_FRAMEWORK["REALTIME"]["HEARTBEAT"]``, 25 seconds by default, passes with nothing to send or with no frame from the client, and any frame from the client answers it.
A peer that sent nothing for more than two heartbeats after an unanswered ping is dropped without a close frame, leaves presence, and fires ``stream_unsubscribed`` with the reason ``client``.
A busy topic does not hold the ping back, so a half-open peer is dropped before the send buffer of the kernel fills.
Keep that value below the idle timeout of every proxy in front of it.
The browser renews a ticket when it reconnects.
Tests drive the socket and the SSE stream with the helpers of :doc:`/content/ref/testing`.
A project on the signed-cookie session engine or another identity source binds tickets through :doc:`bind-tickets-to-your-own-identity`.
See :doc:`/content/deployment/wsgi-asgi` for proxy settings.

Change the Origin or authentication
-----------------------------------

The default Origin rule accepts the scheme and validated ``Host`` of the socket and ``CSRF_TRUSTED_ORIGINS``, and it refuses a connection with no Origin.
An explicit list replaces that rule.
Pass it to the socket and hand the socket to ``application``.

.. code-block:: python
   :caption: config/asgi.py

   from next.realtime import WebSocketApp, application as realtime_application

   socket = WebSocketApp(origins=["https://example.com", "https://*.example.org"])
   application = realtime_application(django_application, websocket=socket)

The entries follow the ``CSRF_TRUSTED_ORIGINS`` format, and an empty or malformed list raises ``ValueError`` at startup.

A subclass overrides ``allow_origin`` for a rule of its own.
It overrides ``authenticate`` to take the identity from somewhere other than the session cookie, such as a header set by a trusted proxy.
``authenticate`` returns a ``Handshake`` or ``None`` for a refusal, which closes the socket with ``4401``.
The ``credential`` of the handshake has to be the one the ticket was issued under, because the ticket is bound to it and every recheck hands it back to the ticket binding.

.. code-block:: python
   :caption: realtime/socket.py

   from asgiref.typing import WebSocketScope

   from next.realtime import WebSocketApp

   class PartnerSocket(WebSocketApp):
       def allow_origin(self, origin: str | None, scope: WebSocketScope) -> bool:
           return origin == "https://partner.example.com" or super().allow_origin(
               origin, scope
           )

A subclass can also override ``topic_socket`` to return its own ``TopicSocket`` subclass.

.. _howto-stream-over-websockets-router:

Mount the socket inside another ASGI router
-------------------------------------------

``WebSocketApp`` is an ordinary ASGI application, so a project that already routes WebSockets mounts it as one route.
The route path has to match ``REALTIME['WS_PATH']``, because the ticket tells the browser that URL.

A project on Django Channels adds it to its own ``URLRouter``.
The socket loads the session itself, so leave ``AuthMiddlewareStack`` out of that route.
Pass the Django ASGI application as ``http_app``, because the socket runs tunneled requests through it.
A bare mounted socket and a ``TopicSocket`` gateway run no tunneled requests.

.. code-block:: python
   :caption: config/asgi.py

   from channels.routing import ProtocolTypeRouter, URLRouter
   from django.urls import path

   from next.realtime import WebSocketApp

   socket = WebSocketApp(http_app=django_application)
   application = ProtocolTypeRouter(
       {
           "http": django_application,
           "websocket": URLRouter([path("_next/ws/", socket)]),
       }
   )

A Starlette application mounts it as a WebSocket route with the same ``http_app`` argument.

.. code-block:: python
   :caption: app.py

   from django.core.asgi import get_asgi_application
   from starlette.applications import Starlette
   from starlette.routing import WebSocketRoute

   from next.realtime import WebSocketApp

   django_application = get_asgi_application()
   socket = WebSocketApp(http_app=django_application)
   app = Starlette(routes=[WebSocketRoute("/_next/ws/", socket)])

Only ``application`` answers lifespan events and closes the broker on shutdown.
A router that mounts the bare socket closes the broker through its own shutdown hook, which calls ``broker_manager.aclose()``.

Serve the protocol from a Python gateway
----------------------------------------

A gateway built on another WebSocket library reuses the connection logic through ``TopicSocket``.
It supplies a ``SocketPort``, two coroutines that write one text frame and close with a code, and a ``Handshake`` for the identity it accepted.
The sketch below uses the ``websockets`` library.

.. code-block:: python
   :caption: gateway.py

   import asyncio

   import django
   from django.http import parse_cookie
   from websockets.asyncio.server import ServerConnection, serve
   from websockets.exceptions import ConnectionClosed

   django.setup()

   from next.realtime import Handshake, TopicSocket, frames

   class Port:
       def __init__(self, connection: ServerConnection) -> None:
           self.connection = connection

       async def send(self, text: str) -> None:
           try:
               await self.connection.send(text)
           except ConnectionClosed as exc:
               raise OSError from exc

       async def close(self, code: int) -> None:
           await self.connection.close(code)

   async def inbound(connection: ServerConnection):
       try:
           async for message in connection:
               yield message
       except ConnectionClosed:
           return

   async def handler(connection: ServerConnection) -> None:
       cookies = parse_cookie(connection.request.headers.get("Cookie", ""))
       handshake = await Handshake.from_cookies(cookies)
       if handshake is None:
           await connection.send(frames.error("auth"))
           await connection.close(frames.Close.SESSION)
           return
       await TopicSocket(Port(connection), handshake).serve(inbound(connection))

   async def main() -> None:
       async with serve(handler, "0.0.0.0", 8765, origins=["https://example.com"]):
           await asyncio.get_running_loop().create_future()

   asyncio.run(main())

The gateway checks the Origin itself, here through the ``origins`` argument of ``serve``.
It runs in a process of its own.
List ``"ws"`` in ``REALTIME['TRANSPORTS']`` in the project that renders the pages, and share ``SECRET_KEY``, the session store, and the broker with the gateway.
``send`` has to raise ``OSError`` once the peer is gone, and ``close`` has to tolerate a peer that already left.
``Handshake.from_cookies`` asks the configured ticket binding for the credential of the cookies and loads its user on the shared thread pool without writing to the store.
It returns ``None`` for no cookies or once the credential is gone.
``TopicSocket`` verifies the ticket from the ``sub`` frame against the handshake's ``credential`` itself, so the gateway never handles tickets.
A gateway that takes the identity from elsewhere builds ``Handshake(credential, user, expires_at)`` directly.
The credential is the one the ticket was issued under, and ``expires_at`` is on the ``time.monotonic()`` clock or ``None``.
The handshake derives its opaque ``subject`` from the credential, and that digest is all a handler sees.
At ``expires_at`` the socket rechecks the credential and stays open when HTTP requests have renewed it.

Bring your own WebSocket server
-------------------------------

A gateway in Go, Rust, or Node cannot import the ticket code.
It asks the project's ticket introspection endpoint instead, which keeps the signing key and the session store inside Django.

The endpoint is off by default.
Include its URLs and set a bearer token that only the gateway knows.

.. code-block:: python
   :caption: config/urls.py

   from django.urls import include, path

   urlpatterns = [
       path("_next/realtime/", include("next.realtime.urls")),
   ]

.. code-block:: python
   :caption: config/settings.py

   import os

   NEXT_FRAMEWORK = {
       "REALTIME": {
           "TRANSPORTS": ["ws", "sse"],
           "GATEWAY_TOKEN": os.environ["REALTIME_GATEWAY_TOKEN"],
       },
   }

A routed endpoint with no token refuses every caller, and ``manage.py check`` reports it as ``next.E188``.
Keep the endpoint on an internal network, because only the gateway calls it.

The gateway follows these steps for each connection.

#. It accepts the upgrade on ``WS_PATH`` and keeps the raw ``Cookie`` header, after its own Origin check.
#. It reads the first frame, a ``sub`` frame, and takes the ticket from it.
#. It sends a ``POST`` to the endpoint with the bearer token, the ticket, and the cookie.
#. On ``200`` it reads ``topics``, subscribes to those topics on the broker, and sends ``hello`` and then ``patches`` frames as the specification describes.
#. It answers a non-``200`` reply with ``4401``.
#. Once ``expires_in`` seconds pass, it closes the socket with ``1001``, and the client reconnects with a fresh ticket that the endpoint checks against the session again.

.. code-block:: bash
   :caption: shell

   curl -X POST https://app.internal/_next/realtime/ticket/ \
     -H "Authorization: Bearer $REALTIME_GATEWAY_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{"ticket": "<token from the sub frame>", "cookie": "sessionid=<key>"}'

.. code-block:: json
   :caption: 200 response

   {"topics": ["poll:7"], "user": "42", "expires_in": 1209600}

The reply names the user as a string primary key, or ``null`` for an anonymous visitor.
It never contains the session key or session data.
It leaves ``expires_in`` out when the session store keeps no deadline the endpoint can read, as the ``cache`` engine does.
The gateway then picks its own interval for the ``1001`` close.
A ``presence`` field appears only when the ticket's stream reports attendance.
It is ``"count"`` for a stream that shares the number of connections and ``"members"`` for one that also identifies its members.
A wrong bearer, a bad ticket, a lost session or a refused topic answers ``403`` with ``{"error": "denied"}`` whatever the reason.
An authorized call whose body is not JSON or lacks a string ``ticket`` or ``cookie`` answers ``400`` with ``{"error": "invalid"}``.
The endpoint refuses a ticket whose stream uses a transform by default, because a gateway cannot run Python.
A stream that serves such a gateway publishes already-filtered topics.

The default caller check is the bearer token.
A subclass of ``TicketIntrospectionView`` overrides ``authorize(request)`` to check a client certificate header or a source address, and ``grant(claims, user, expires_in)`` to change the reply.
A gateway that applies the transform itself gets a reply from an override of ``grant_transformed(claims, user, expires_in)``, which returns ``None`` by default and so keeps the ``403``.
Mount the subclass on a path of its own with ``as_view()``.

Pick a broker for several processes
-----------------------------------

For a single process, the default ``MemoryBroker`` works.
A deployment with several workers or hosts selects ``RedisBroker``, which shares publications and sequence numbers.
See :doc:`fan-out-across-processes-with-redis` for the setup.

Delivery is best-effort, and clients recover gaps by requesting their guarded zones over HTTP.
Use a durable outbox if the application needs guaranteed delivery after a database commit, see :doc:`deliver-events-durably`.

The same socket carries events from the page to the server and form submissions, see :ref:`topics-realtime-upstream` and :ref:`topics-realtime-tunnel`.

See :doc:`/content/topics/realtime` for topic delivery and recovery guarantees, :doc:`/content/ref/realtime` for the complete API and the frame specification, and :doc:`/content/deployment/wsgi-asgi` for ASGI deployment.
