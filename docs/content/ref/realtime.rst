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
   ``signals`` and ``checks`` are also available as submodules.

Topic names contain 1 to 200 ASCII letters, digits, underscores, dots, colons, or hyphens.
The ``next:`` prefix belongs to the framework and is rejected.

Configuration
-------------

The ``REALTIME`` key is a mapping in ``NEXT_FRAMEWORK``.
See :doc:`settings` for the complete default, the ``HEARTBEAT`` interval, and the options of every shipped backend.

``REALTIME.BROKER.BACKEND`` names the backend class.
``MemoryBroker`` accepts ``OPTIONS.QUEUE_SIZE``, a positive integer that bounds each subscriber queue.
``RedisBroker`` accepts ``OPTIONS.URL``, ``OPTIONS.PREFIX``, and ``OPTIONS.HEALTH_CHECK_INTERVAL`` and requires the Redis optional dependency from the ``broker`` extra.
Without it, selecting the backend is reported by ``manage.py check`` as ``next.E179``.

The in-memory backend is process-local.
A multi-process production deployment must use a shared backend such as Redis, because the framework does not infer process count from the server configuration.

When ``channels`` is installed, ``next.contrib.channels.ChannelsLayerBroker`` publishes through a Channels channel layer and allocates topic sequences and epochs in a Django cache.
Its ``OPTIONS`` accept ``CHANNEL_LAYER`` (default ``default``), ``CACHE_ALIAS`` (default ``default``), and ``PREFIX`` (default ``next.rt``).
Both the channel layer and the cache must be shared across workers.
Sequence allocation and fan-out are separate operations, so clients recover gaps with their guarded zone GET.
Blocking subscriptions under WSGI run every layer receive on one background event loop, so SSE under WSGI works with ``channels_redis``.

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
``manage.py check`` reports a class that is not an importable concrete ``BrokerBackend`` subclass as ``next.E186`` and leaves the options of a custom backend to that backend.

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

WebSocket integration
---------------------

``next.contrib.channels.application`` wraps a Django ASGI HTTP application with the session-authenticated WebSocket route and Origin validation.
``websocket_urlpatterns`` is available for projects that compose their own Channels router.
A guarded ``TopicStream`` GET negotiates a short-lived, session-bound ticket, and the client sends it as the first WebSocket frame, never in the URL.
The grant fixes the topics, transform, and importable ``authorize(user, topic)`` callback for the connection.
Without a stable callback reference, negotiation answers ``406`` and the client stays on SSE.

The consumer rechecks the session and the callback before it subscribes and then at most once per ``REALTIME['HEARTBEAT']`` interval, the same throttle an SSE ``TopicStream`` applies.
A denial sends ``{"type": "error", "code": "auth"}`` and closes the socket with ``4401`` for a session problem or ``4403`` for a topic denial.
The socket needs a session engine that stores sessions on the server, and ``next.W145`` reports the signed-cookie engine.
Channels group membership is renewed before its configured expiry, and ``server_time`` is stamped when each frame is written.

The WebSocket frames are JSON objects.
``hello``, ``skip``, ``ping``, and ``error`` follow a fixed schema, and the client answers ``ping`` with ``pong``.
``hello`` carries the SSE ``next-hello`` keys plus ``"type": "hello"`` and ``heartbeat``, the server interval in seconds the client sizes its missed-ping watchdog from.
``ping`` is ``{"type": "ping", "server_time": ...}`` and ``skip`` carries ``topic``, ``epoch``, and ``seq``.

A ``patches`` frame is ``{"type": "patches", "data": "<text>"}``, where ``data`` is the exact ``data:`` text of the protocol backend's ``sse_stream_event`` output, never re-parsed on the server.
The client hands that text to the same parse hook the SSE transport uses, so a custom wire format works on both transports.

``application`` reads its Origin allowlist from ``ALLOWED_HOSTS`` unless ``origins=`` is passed.
Each entry becomes a scheme-less pattern that ignores the port, and a leading-dot entry such as ``.example.com`` allows ``example.com`` and every subdomain, as it does in Django.
A ``*`` entry, an empty list, or an entry holding a ``/`` raises ``ValueError``, because a wildcard cannot protect a cookie-authenticated socket.

.. autofunction:: next.contrib.channels.application

.. autofunction:: next.contrib.channels.websocket_urlpatterns

.. autoclass:: next.contrib.channels.ChannelsLayerBroker
   :members:

The integration is optional and is imported from ``next.contrib.channels`` only when Channels support is configured.

Signals
-------

``broker_backend_loaded`` fires when the lazy manager creates the configured backend, after the manager lock is released.
``message_published`` fires after the broker accepts a publication.
See :doc:`signals` for their payloads and :doc:`/content/topics/signals` for receiver usage.

System checks
-------------

Realtime checks validate the ``REALTIME`` keys, the transport list, the ticket age, the WebSocket path, the heartbeat, and the broker backend and its options, and report a missing ``redis`` or ``channels`` dependency.
Deployment warnings flag an enabled WebSocket transport without ``ASGI_APPLICATION``, without ``SessionMiddleware``, or with signed-cookie sessions.
They also flag a ``ChannelsLayerBroker`` on an in-process channel layer or on a cache without an atomic ``incr()``, whatever the transports.
An error also flags a ``TRANSPORTS`` list that names ``"ws"`` while ``channels`` is installed but missing from ``INSTALLED_APPS``.
The checks run against the defaults when ``NEXT_FRAMEWORK`` has no ``REALTIME`` key.
See :doc:`system-checks` for the ids ``next.E177`` to ``next.E189`` and ``next.W141`` to ``next.W145``.

See also
--------

.. seealso::

   :doc:`settings` for the ``REALTIME`` settings mapping.
   :doc:`backends` for the shared backend loading conventions.
   :doc:`/content/topics/realtime` for publication, access, and recovery.
   :doc:`/content/topics/partial-rendering/sse` for the existing stream transport.
