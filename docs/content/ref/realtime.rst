.. _ref-realtime:

Realtime reference
==================

.. contents::
   :local:
   :depth: 1

Module summary
--------------

``next.realtime`` owns broker backends and the messages they deliver to topic subscribers.
It carries partial-rendering envelopes but does not render HTML.

The broker numbers each topic with a ``seq`` inside an ``epoch`` and delivers messages at most once, on a best-effort basis.
A bounded subscriber queue drops its oldest message when it fills.
Consumers detect a gap or an epoch change from the stream metadata and recover current state through their own guarded HTTP request.
Messages published while a subscriber is disconnected are not replayed.

``MemoryBroker`` is the default and shares messages among subscribers in one process.
For deployments with multiple workers or hosts, select ``RedisBroker`` so they share publication and sequence state.

API tier
--------

Advanced.
   ``BrokerBackend``, ``MemoryBroker``, ``RedisBroker``, ``Message``, ``Position``, ``TopicStream``, ``publish``, ``apublish``, ``BrokerConnectionError``, ``InvalidTopicError``, and ``TopicPayloadError`` are imported from ``next.realtime``.
   The WebSocket side adds ``WebSocketApp``, ``application``, ``TopicSocket``, ``SocketPort``, ``Handshake``, ``TicketClaims``, ``verify_ticket``, ``InvalidTicketError``, ``Close``, and ``TicketIntrospectionView``.
   ``signals``, ``checks``, and ``frames`` are also available as submodules.
   ``resolve_claims`` lives in ``next.realtime.tickets`` for gateway authors.

Topic names contain 1 to 200 ASCII letters, digits, underscores, dots, colons, or hyphens.
The ``next:`` prefix belongs to the framework and is rejected.

Configuration
-------------

The ``REALTIME`` key is a mapping in ``NEXT_FRAMEWORK``.
See :doc:`settings` for the complete default, the ``HEARTBEAT`` interval, and the options of every shipped backend.

``REALTIME.BROKER.BACKEND`` names the backend class.
``MemoryBroker`` accepts ``OPTIONS.QUEUE_SIZE``, a positive integer that bounds each subscriber queue.
``RedisBroker`` accepts ``OPTIONS.URL``, ``OPTIONS.PREFIX``, ``OPTIONS.HEALTH_CHECK_INTERVAL``, and ``OPTIONS.CLIENT`` and requires the Redis optional dependency from the ``broker`` extra.
Without it, selecting the backend is reported by ``manage.py check`` as ``next.E179``.

The in-memory backend is process-local.
A multi-process production deployment must use a shared backend such as Redis, because the framework does not infer process count from the server configuration.

``OPTIONS.CLIENT`` is a mapping of keyword arguments passed whole to the Redis ``from_url`` call, for the synchronous and the asynchronous client.
It overrides the defaults ``health_check_interval`` and ``socket_keepalive``, while ``decode_responses`` is always ``True``.
A subclass that overrides ``RedisBroker.create_client(url, options, *, asynchronous)`` reaches Redis through Sentinel or Cluster.
An exhausted connection pool logs a warning on the ``next.realtime.redis`` logger.

Broker contract
---------------

``BrokerBackend`` is the abstract root every broker subclasses.
The constructor receives the whole ``BROKER`` entry and exposes its ``OPTIONS`` mapping as ``self.options``.
One instance serves the whole process, so every method is called from many request threads and event loops at once.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Member
     - Contract
   * - ``validate_publication(topic, envelope)``
     - Checks the topic name and the envelope type and returns the envelope.
       A publish implementation calls it before it numbers anything.
   * - ``publish(topic, envelope)`` and ``apublish(topic, envelope)``
     - Assign the next ``seq`` of the topic, fan the message out, and return the ``Message`` with its ``seq`` and ``epoch``.
   * - ``subscribe(topics, *, timeout)``
     - Return a context manager that yields an iterator of ``Message`` or ``None``.
       The subscription is live when the context is entered, so a publication after that point reaches the iterator.
       A wait longer than ``timeout`` seconds yields ``None``, which the stream turns into a heartbeat and an access recheck.
   * - ``asubscribe(topics, *, timeout)``
     - The asynchronous form of ``subscribe``, an async context manager over an async iterator.
       Leaving the context, cancellation included, releases the subscription and every task it started.
   * - ``positions(topics)`` and ``apositions(topics)``
     - Return a ``Position`` per topic in one batched read.
       A topic never published answers ``Position(epoch="", seq=0)``.
   * - ``close()`` and ``aclose()``
     - Release connections and threads.
       Both default to doing nothing, and ``aclose`` calls ``close``.
       The broker manager calls ``close`` when the framework settings reload.

A stream enters the subscription first and reads the positions second, then sends ``hello``.
A message published in between appears in the iterator and is also counted in the positions, which the client treats as a duplicate rather than as a gap.
A subscription that cannot guarantee delivery any more, after a reconnect or an unanswered liveness ping, raises ``BrokerConnectionError`` from its iterator.
A backend also translates its own transport failures into ``BrokerConnectionError``, so a stream catches one error type.
The SSE stream then ends without a terminal frame so the browser reconnects and receives a fresh ``hello``, and the WebSocket closes with ``1011``.

Epochs
~~~~~~

Each topic counter has an epoch token stored next to it.
The broker draws a new random token whenever the counter starts again, which is when the increment returns ``1`` or the stored epoch is missing.
Keep the counter and its epoch together, in one record or under a counter key namespaced by the epoch, so a lost counter can never continue inside an old epoch.
Every published ``Message`` carries the epoch its ``seq`` belongs to, and a client that sees a different epoch resets its cursor and resynchronises.

A custom broker
~~~~~~~~~~~~~~~

The skeleton below shows where each rule of the contract lives.
The storage calls are left to the project, and ``self.store`` stands for whatever client the backend opens in its constructor.

.. code-block:: python
   :caption: realtime/brokers.py

   import time
   from collections.abc import AsyncIterator, Iterator, Sequence
   from contextlib import asynccontextmanager, contextmanager

   from next.partial import Envelope
   from next.realtime import BrokerBackend, BrokerConnectionError, Message, Position

   class QueueBroker(BrokerBackend):
       """Fan topic messages out through the project's own message queue."""

       def publish(self, topic: str, envelope: Envelope) -> Message:
           envelope = self.validate_publication(topic, envelope)
           seq, epoch = self.store.increment(topic)
           message = Message(topic, seq, envelope, time.time(), epoch)
           self.store.send(topic, message.as_dict())
           return message

       async def apublish(self, topic: str, envelope: Envelope) -> Message:
           envelope = self.validate_publication(topic, envelope)
           seq, epoch = await self.store.aincrement(topic)
           message = Message(topic, seq, envelope, time.time(), epoch)
           await self.store.asend(topic, message.as_dict())
           return message

       @contextmanager
       def subscribe(
           self, topics: Sequence[str], *, timeout: float
       ) -> Iterator[Iterator[Message | None]]:
           listener = self.store.listen(topics)
           try:
               yield self._messages(listener, timeout)
           finally:
               listener.close()

       def _messages(self, listener, timeout: float) -> Iterator[Message | None]:
           while True:
               if listener.lost:
                   raise BrokerConnectionError("The queue dropped the subscription.")
               payload = listener.wait(timeout)
               yield None if payload is None else Message.from_dict(payload)

       @asynccontextmanager
       async def asubscribe(
           self, topics: Sequence[str], *, timeout: float
       ) -> AsyncIterator[AsyncIterator[Message | None]]:
           listener = await self.store.alisten(topics)
           try:
               yield self._amessages(listener, timeout)
           finally:
               await listener.aclose()

       async def _amessages(
           self, listener, timeout: float
       ) -> AsyncIterator[Message | None]:
           while True:
               payload = await listener.await_message(timeout)
               yield None if payload is None else Message.from_dict(payload)

       def positions(self, topics: Sequence[str]) -> dict[str, Position]:
           rows = self.store.read_counters(topics)
           return {
               topic: Position("", 0) if row is None else Position(row.epoch, row.seq)
               for topic, row in zip(topics, rows, strict=True)
           }

       async def apositions(self, topics: Sequence[str]) -> dict[str, Position]:
           rows = await self.store.aread_counters(topics)
           return {
               topic: Position("", 0) if row is None else Position(row.epoch, row.seq)
               for topic, row in zip(topics, rows, strict=True)
           }

       def close(self) -> None:
           self.store.close()

``listen`` returns only after the queue confirms the subscription, which is what makes the subscription live on entry.
``Message.from_dict`` rejects wrong wire types with ``TypeError`` or ``ValueError``, so a backend that reads from an untrusted channel logs and skips such a payload.
``manage.py check`` reports a class that is not an importable concrete ``BrokerBackend`` subclass as ``next.E184`` and leaves the options of a custom backend to that backend.

Public API
----------

.. autoclass:: next.realtime.BrokerBackend
   :members:

.. autoclass:: next.realtime.MemoryBroker
   :members:

.. autoclass:: next.realtime.RedisBroker
   :members:

.. autoclass:: next.realtime.Message
   :members:

.. autoclass:: next.realtime.Position
   :members:

.. autoclass:: next.realtime.TopicStream
   :members:

.. autofunction:: next.realtime.publish

.. autofunction:: next.realtime.apublish

.. autoexception:: next.realtime.BrokerConnectionError
   :members:

.. autoexception:: next.realtime.InvalidTopicError
   :members:

.. autoexception:: next.realtime.TopicPayloadError
   :members:

.. _ref-realtime-protocol:

WebSocket protocol
------------------

The framework serves the topic protocol over a plain ASGI WebSocket, with no package beyond the ASGI server.
This section is the normative specification, so a gateway in any language can speak it.
Every frame is one text message holding a UTF-8 JSON object, and the server writes compact JSON without spaces after ``,`` and ``:``.
The socket negotiates no subprotocol.

Handshake
~~~~~~~~~

A page opens a ``TopicStream`` through its guarded GET, and a client that asks for ``X-Next-Transport: ws`` receives a JSON ticket instead of a stream.
The body is ``{"protocol": 2, "transport": "ws", "ticket": ..., "topics": [...], "url": ..., "expires_in": ...}``, where ``url`` is ``REALTIME['WS_PATH']`` under the script prefix of the request and ``expires_in`` is ``TICKET_MAX_AGE``.
The ticket binds the session, the topics, and the transform and authorizer references of the stream.
The connection then runs in this order.

#. The client opens ``WS_PATH`` with the session cookie.
   The server checks the ``Origin`` header and, on a refusal, closes before accepting, which a server reports as HTTP ``403``.
#. The server accepts the connection and loads the session named by the cookie.
   Without a live session it sends ``{"type": "error", "code": "auth"}`` and closes with ``4401``.
#. The client sends a ``sub`` frame within five seconds.
   A first frame that is not a valid ``sub``, or a silence past the deadline, ends in ``{"type": "error", "code": "protocol"}`` and ``4400``.
#. The server verifies the ticket against the session of the handshake and imports the transform and authorizer it names.
   A ticket that is forged, expired, bound to another session, or names a callable that no longer imports ends in an ``auth`` error and ``4401``.
#. The server checks the session and the authorizer for every topic.
   A revoked session ends in an ``auth`` error and ``4401``, and a denied topic ends in an ``auth`` error and ``4403``.
#. The server subscribes to the broker, reads the positions of the topics, and sends ``hello``.
   A message published between the subscription and the position read arrives after ``hello`` as well, and the client treats it as a duplicate rather than a gap.

From then on the server sends ``patches`` and ``skip`` frames as messages arrive, and a ``ping`` whenever a whole heartbeat passes without one.
The same access check repeats when a message or a heartbeat arrives after the heartbeat interval has elapsed, and a failure closes the socket as in the fifth step.

Client frames
~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 14 46 40

   * - Frame
     - Shape
     - Rules
   * - ``sub``
     - ``{"type": "sub", "ticket": "...", "last_seq": {...}}``
     - It must be the first frame.
       A missing or non-string ``ticket`` reads as empty and fails as a bad ticket.
       ``last_seq`` is optional and maps a topic to a non-negative integer ``seq`` or to ``{"epoch": "...", "seq": 7}``, for at most 64 topics.
       Any other key, or a malformed cursor, closes with ``4400``.
   * - ``pong``
     - ``{"type": "pong"}``
     - The answer to ``ping``.
       Extra keys close with ``4400``, and the server takes no other action.
   * - any other ``type``
     - a JSON object with an unknown or missing ``type``
     - The server answers ``{"type": "error", "code": "unsupported"}`` and keeps the socket open.
       This includes a second well-formed ``sub``.

Frames that are not JSON objects close with ``4400``.
That covers an empty text frame, invalid JSON, a JSON value that is not an object, and every binary frame.
A frame over 64 KiB closes with ``1009``.

The server records the ``last_seq`` cursor and sends the current positions in ``hello``, and it does not replay what the client missed.
The client compares the positions with its own cursor and resynchronises through the guarded zone GET, see :doc:`/content/topics/realtime`.

Server frames
~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 14 46 40

   * - Frame
     - Shape
     - Meaning
   * - ``hello``
     - ``{"type": "hello", "protocol": 2, "server_time": 1761300000000, "topics": {"room:42": {"epoch": "9f2c41d07a3be851", "seq": 17}}, "transports": ["ws", "sse"], "heartbeat": 25.0}``
     - The subscription is live.
       It carries the keys of the SSE ``next-hello`` frame plus ``type`` and ``heartbeat``, the server interval in seconds.
       A client sizes its missed-ping watchdog from ``heartbeat``.
   * - ``patches``
     - ``{"type": "patches", "data": ...}``
     - One published message.
       ``data`` is the object the protocol backend's ``stream_data`` returns, which for the stock JSON backend is the envelope with ``server_time``, ``topic``, ``epoch``, and ``seq``.
       A backend whose ``stream_data`` returns ``None`` sends the exact ``data:`` text of its ``sse_stream_event`` as a string.
   * - ``skip``
     - ``{"type": "skip", "topic": "room:42", "epoch": "...", "seq": 18}``
     - A message the subscriber transform filtered out, sent so the cursor still advances.
   * - ``ping``
     - ``{"type": "ping", "server_time": 1761300000000}``
     - A heartbeat on an idle subscription.
       The client answers with ``pong``.
   * - ``error``
     - ``{"type": "error", "code": "protocol"}``
     - ``code`` is ``protocol``, ``auth``, or ``unsupported``.
       ``protocol`` and ``auth`` precede a close with ``4400``, ``4401``, or ``4403``, while ``unsupported`` leaves the socket open.

A stock JSON backend returns ``None`` from ``stream_data`` once a subclass sets its own ``content_type`` or overrides ``sse_event`` or ``sse_stream_event``.
The client hands a string ``data`` to the same parse hook the SSE transport uses, so a custom wire format works on both transports.
``server_time`` is stamped in milliseconds when each frame is written.

Close codes
~~~~~~~~~~~

``next.realtime.frames.Close`` names the codes.

.. list-table::
   :header-rows: 1
   :widths: 12 22 66

   * - Code
     - Name
     - Sent when
   * - ``1001``
     - ``GOING_AWAY``
     - A gateway shuts down.
       The built-in socket never sends it, since the ASGI server closes open sockets itself.
   * - ``1009``
     - ``TOO_BIG``
     - An inbound frame exceeds 64 KiB.
       No ``error`` frame precedes it.
   * - ``1011``
     - ``INTERNAL``
     - The broker subscription is lost or an unexpected error ends the stream.
   * - ``1013``
     - ``TRY_AGAIN``
     - Available to a gateway that closes a client that stopped reading.
       The built-in socket drops such a connection without a close frame.
   * - ``4400``
     - ``PROTOCOL``
     - A binary, empty, non-JSON, or malformed frame, a first frame that is not ``sub``, or no ``sub`` within five seconds.
   * - ``4401``
     - ``SESSION``
     - No live session, a ticket that fails verification, or a session revoked while the socket is open.
   * - ``4403``
     - ``TOPIC_DENIED``
     - The authorizer refuses a topic of the grant.
   * - ``4429``
     - ``RATE_LIMITED``
     - Defined for gateways that rate limit.
       The built-in socket never sends it.

The browser client falls back to SSE for good on ``4401`` and ``4403``, and on an ``auth`` error frame.
Every other close is transient.
The client retries after ``150 * 2^(n-1)`` milliseconds plus up to 150 milliseconds of jitter, and it falls back to SSE after three failures in a row.
A watchdog wakes once per heartbeat and drops a peer whose send has been blocked for longer than a heartbeat.
A peer that stops reading is therefore dropped one to two heartbeats after a send to it blocks, and the client reconnects and learns the current positions from a fresh ``hello``.

Limits
~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 34 16 50

   * - Limit
     - Value
     - Constant
   * - Inbound frame size
     - 64 KiB of UTF-8
     - ``frames.MAX_INBOUND_BYTES``
   * - Topics in a ``last_seq`` cursor
     - 64
     - ``frames.MAX_CURSOR_TOPICS``
   * - Wait for the first ``sub``
     - 5 seconds
     - ``frames.SUB_TIMEOUT``
   * - Topics in a ticket
     - 64 unique names
     - Fixed by the ticket format.
   * - Ticket lifetime
     - 1 to 600 seconds
     - ``REALTIME['TICKET_MAX_AGE']``
   * - Heartbeat
     - 25 seconds by default
     - ``REALTIME['HEARTBEAT']``

Origin and session
~~~~~~~~~~~~~~~~~~

A cookie-authenticated socket needs the same cross-site protection as a form post, so the default Origin rule mirrors Django's CSRF check.
The Origin is accepted when it equals the scheme and ``Host`` of the socket, or when it matches ``CSRF_TRUSTED_ORIGINS``, wildcards such as ``https://*.example.com`` included.
The ``Host`` is validated against ``ALLOWED_HOSTS`` and honours ``USE_X_FORWARDED_HOST``, and the scheme is ``https`` for a ``wss`` scope or when ``SECURE_PROXY_SSL_HEADER`` says so.
A TLS-terminating proxy hands the socket a ``ws`` scope, so it needs ``SECURE_PROXY_SSL_HEADER``, as Django's CSRF check does, or the ``https`` Origin is refused.
A connection with no ``Origin`` header is refused, because only a browser carries ambient cookies.
``WebSocketApp(origins=[...])`` replaces the rule with an explicit list in the ``CSRF_TRUSTED_ORIGINS`` format and raises ``ValueError`` for an empty or malformed list.
A ``*`` entry in ``ALLOWED_HOSTS`` is not an error, because the check compares the Origin with the validated ``Host``.

The session comes from the cookie named by ``SESSION_COOKIE_NAME`` and is read through the configured ``SESSION_ENGINE`` on the shared thread pool.
The load is read-only.
A session whose hash only matches a ``SECRET_KEY_FALLBACKS`` entry keeps working, since rotating the key belongs to the next HTTP request and the socket has no response to carry a new cookie.
The ``db``, ``cache``, ``cached_db``, and ``file`` engines work, and the signed-cookie engine does not, which ``next.W142`` reports.
The ``file`` engine replaces an expired session with a new file when it loads one, and the socket turns that write into a no-op.
The socket loads the session at the handshake and on each recheck, so a cache-backed store that evicts live sessions closes sockets with ``4401``.

A recheck runs on every heartbeat and at the session's deadline, and it closes the socket only when the session is gone.
A session renewed over HTTP, by ``SESSION_SAVE_EVERY_REQUEST`` or ``set_expiry``, moves the deadline and keeps its socket open.
The deadline is the ``expire_date`` row of the ``db`` and ``cached_db`` engines, or an expiry date passed to ``set_expiry``.
The ``cache`` and ``file`` engines keep no deadline the socket can read otherwise, so for them the heartbeat recheck alone notices an expired session.

Serving the socket
~~~~~~~~~~~~~~~~~~

``WebSocketApp`` is a plain ASGI application for WebSocket scopes, so it mounts under any ASGI router.
``application(http_app)`` is the ready default for a Django project.
It sends HTTP to ``http_app``, serves the socket at ``REALTIME['WS_PATH']``, and closes any other WebSocket path before accepting it, since Django's own ASGI handler rejects WebSocket scopes.
The path is compared below ``FORCE_SCRIPT_NAME`` or the ASGI ``root_path``, the way Django computes ``path_info``, and the ticket URL carries the same prefix.
``REALTIME['WS_PATH']`` is the only place the route is configured, because the ticket tells the client the same URL.

Django's ASGI handler does not speak lifespan, so ``application`` answers it and closes the broker on shutdown.
Another HTTP application gets the lifespan events, and the broker closes before its shutdown complete goes out.
An application that fails or returns before sending a lifespan event, such as a middleware around Django's handler, leaves the answer to ``application``.
A broker that fails to close is logged and reported as ``lifespan.shutdown.failed``.
Creating a ``WebSocketApp`` marks the process as serving ``ws``, which switches the default ``TRANSPORTS`` to ``["ws", "sse"]``.
It also logs the session warnings of ``manage.py check`` as ``WARNING`` records on the ``next.realtime.asgi`` logger.

Three methods of ``WebSocketApp`` are the extension points of a subclass.
``allow_origin(origin, scope)`` decides the Origin, ``authenticate(scope)`` returns the ``Handshake`` of the connection or ``None`` for a refusal, and ``topic_socket(port, handshake, scope)`` returns the ``TopicSocket`` that serves it.
An ``authenticate`` that raises is logged and closes the socket with ``1011``.

``TopicSocket`` knows nothing of ASGI.
It reads frames from an async iterable and writes through a ``SocketPort``, which is two coroutines, ``send(text)`` and ``close(code)``.
``send`` raises ``OSError`` once the peer is gone, and ``close`` tolerates a peer that already left.
A gateway on another server loads the ``Handshake`` of a session key with ``await Handshake.from_session(session_key)``, which ``WebSocketApp.authenticate`` uses too, and runs ``TopicSocket(port, handshake).serve(frames)``.
A gateway that takes the identity from elsewhere builds ``Handshake(session_key, user, expires_at)`` itself, with ``expires_at`` on the ``time.monotonic()`` clock or ``None``.
``expires_at`` is the moment of a forced session recheck, not of a close.
The ``Handshake.session_key`` has to be the key the ticket was issued under.
``TopicSocket.catch_up`` and ``TopicSocket.tick`` are the hooks a subclass overrides, the first between the position read and ``hello`` and the second on each idle heartbeat.

``verify_ticket(token, session_key)`` returns the ``TicketClaims`` of a valid ticket and raises ``InvalidTicketError`` otherwise.
``resolve_claims(claims)`` imports the transform and the authorizer the claims name.

.. autoclass:: next.realtime.WebSocketApp
   :members:

.. autofunction:: next.realtime.application

.. autoclass:: next.realtime.TopicSocket
   :members:

.. autoclass:: next.realtime.SocketPort
   :members:

.. autoclass:: next.realtime.Handshake
   :members:

.. autoclass:: next.realtime.TicketClaims
   :members:

.. autofunction:: next.realtime.verify_ticket

.. autofunction:: next.realtime.tickets.resolve_claims

.. autoexception:: next.realtime.InvalidTicketError
   :members:

.. autoclass:: next.realtime.Close
   :members:
   :undoc-members:

.. automodule:: next.realtime.frames
   :members:
   :exclude-members: Close

Ticket introspection endpoint
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A gateway outside Python cannot import the signing code, so the framework ships an endpoint that checks a ticket for it.
The endpoint is off until a URLconf includes it.

.. code-block:: python
   :caption: config/urls.py

   from django.urls import include, path

   urlpatterns = [
       path("_next/realtime/", include("next.realtime.urls")),
   ]

The include registers the ``next_realtime`` namespace with a ``ticket`` route, and ``TicketIntrospectionView.as_view()`` mounts the same view on a path of the project's choosing.
The gateway sends a ``POST`` with ``Authorization: Bearer`` set to ``REALTIME['GATEWAY_TOKEN']``, compared in constant time, and a JSON body of the ticket and the raw ``Cookie`` header of the upgrade request.

.. list-table::
   :header-rows: 1
   :widths: 14 86

   * - Status
     - Body and meaning
   * - ``200``
     - ``{"topics": [...], "user": "<pk>" or null, "expires_in": <seconds>}``.
       The ticket is valid for the session in the cookie, and every authorizer accepts it.
       ``expires_in`` is the time the store still keeps the session, and it is absent when the store keeps no deadline the endpoint can read, as with the ``cache`` engine.
   * - ``400``
     - ``{"error": "invalid"}``.
       The caller is authorized and the body is not JSON or lacks a string ``ticket`` or ``cookie``.
   * - ``403``
     - ``{"error": "denied"}``.
       The bearer is wrong, the ticket is forged or expired, the session is gone or foreign, an authorizer refuses a topic, or the ticket names a transform.
       One body covers every case so a caller learns nothing of the reason, and the reason is logged at ``INFO`` on ``next.realtime.views``.
   * - ``405``
     - Any method but ``POST``, with an ``Allow: POST`` header.

A ticket that names a transform is refused, because a gateway cannot run Python.
Responses carry never-cache headers, and the view is exempt from CSRF and from the default login requirement.
A subclass overrides ``authorize(request)`` for a caller check of its own, such as a client certificate header, and ``grant(claims, user, expires_in)`` to change the body.
A routed endpoint with the stock ``authorize`` and no ``GATEWAY_TOKEN`` is reported as ``next.E188``.

.. autoclass:: next.realtime.TicketIntrospectionView
   :members:

Signals
-------

``broker_backend_loaded`` fires when the lazy manager creates the configured backend, after the manager lock is released.
``message_published`` fires after the broker accepts a publication.
See :doc:`signals` for their payloads and :doc:`/content/topics/signals` for receiver usage.

System checks
-------------

Realtime checks validate the ``REALTIME`` keys, the transport list, the ticket age, the WebSocket path, the heartbeat, and the broker backend and its options, and report a missing ``redis`` dependency.
Deployment warnings flag an explicit ``"ws"`` transport without ``SessionMiddleware`` or with signed-cookie sessions.
A ``WebSocketApp`` logs the session warnings itself when it is created, since ``manage.py check`` never sees a mounted socket.
An error flags a routed ticket introspection endpoint that has no ``GATEWAY_TOKEN``.
The checks run against the defaults when ``NEXT_FRAMEWORK`` has no ``REALTIME`` key.
See :doc:`system-checks` for the ids ``next.E177`` to ``next.E188`` and ``next.W141`` and ``next.W142``.

See also
--------

.. seealso::

   :doc:`settings` for the ``REALTIME`` settings mapping.
   :doc:`backends` for the shared backend loading conventions.
   :doc:`/content/topics/realtime` for publication, access, and recovery.
   :doc:`/content/topics/partial-rendering/sse` for the existing stream transport.
