.. _topics-realtime:

Realtime topics
===============

Realtime topics fan out small patch instructions to connected pages.
The browser loads current HTML through the page's normal guarded HTTP request.

.. contents::
   :local:
   :depth: 1

Publish after a mutation
------------------------

``publish`` accepts a ``Patches`` builder or a partial ``Envelope``.
The default ``on_commit=True`` delays sync publication until the selected database connection commits.
Pass ``using="analytics"`` when the mutation belongs to a non-default database, otherwise the default connection is used.
The callback is process-local and is not a durable outbox.
An ``apublish`` call publishes immediately through the backend's asynchronous API.

.. code-block:: python
   :caption: rooms/signals.py

   from next.partial import Patches
   from next.realtime import publish

   publish(f"room:{room.code}", Patches(request).refresh(zone="scoreboard"))

The request ID from a request-bound ``Patches`` builder is attached automatically.
This lets the browser ignore the update produced by its own successful action.
An explicit ``echo_of`` value on the builder takes precedence.

Topics accept only ``refresh``, ``event``, ``toast``, and ``remove`` operations.
The framework rejects HTML operations, custom operations, assets, form metadata, CSRF state, and values that cannot be serialized as JSON.
Use ``refresh(zone=...)`` when a subscriber must load private or request-specific HTML.

A deferred publication runs as a robust ``on_commit`` callback.
When the broker fails after the commit, Django logs the exception with the topic in the callback's repr and the committed write still answers normally.
``publish(..., on_commit=False)`` and ``apublish`` call the broker at once and raise its error to the caller.

Subscribe from an authorized page
---------------------------------

Return ``TopicStream`` from a page's ``render`` function.
The page's regular access checks and dependency resolution run before the stream opens.

.. code-block:: python
   :caption: rooms/pages/rooms/[slug:code]/stream/page.py

   from django.http import HttpRequest

   from next.realtime import TopicStream

   def render(request: HttpRequest, room: DRoom[Room]) -> TopicStream:
       return TopicStream(request, f"room:{room.code}")

The backend subscription is selected from the request's WSGI or ASGI type.
The broker wait timeout is ``NEXT_FRAMEWORK['REALTIME']['HEARTBEAT']``, 25 seconds by default, and each timeout writes a heartbeat comment under WSGI and ASGI alike.
Under WSGI, disconnect cleanup is best-effort at the next yield, write, or close.
Closing the stream releases the broker subscription.

An optional ``transform(message)`` runs for each subscriber and can return a safe ``Patches`` builder or ``Envelope``.
Returning ``None`` suppresses the message for that subscriber.
Async streams run synchronous transforms in a worker thread.

Topic publication carries JSON patch instructions only.
In particular, do not publish HTML for another subscriber to apply.
Use ``refresh(zone=...)`` for private or request-specific content.
The browser then fetches that zone through its own guarded page request and session cookies.

Access while a stream is open
-----------------------------

The page guard runs once, when the stream opens.
An optional ``authorize(user, topic)`` callback on ``TopicStream`` keeps watching the grant afterwards on both transports.
Each live connection rechecks the session behind it and calls ``authorize`` for every topic at most once per heartbeat interval.
The session counts as revoked when it was deleted, flushed, rotated, logged out, or now loads a different or inactive user.

Revocation needs a session that was live when the stream opened.
An anonymous request whose cookie names a session that no longer exists drops that binding at open and streams anonymously, so only the page guard and ``authorize`` decide.
An authenticated request without a live session at open counts as revoked straight away.

A recheck runs when a broker message or a heartbeat arrives after the interval has elapsed.
A revocation is therefore noticed within about two heartbeat intervals, plus the time the transform and the callback take.

Over SSE a topic denial by ``authorize`` ends the stream with a terminal ``next-end`` frame whose data is ``{"code":"auth"}``, and the browser stops reconnecting that stream.
A revoked session ends the SSE stream without that frame, so the ``EventSource`` reconnects with the current cookie and the page guard decides, a ``403`` stopping it for good.
Over WebSocket a denial sends ``{"type":"error","code":"auth"}`` and closes the socket with ``4401`` for a session problem or ``4403`` for a topic denial.
The client then falls back to SSE, where the same rules apply.

When the process mounts a ``WebSocketApp``, the client can negotiate a WebSocket through this same guarded stream URL.
The signed ticket binds the current session to the topics, transform, and importable ``authorize`` callback chosen by the page.
The client sends the ticket in its first WebSocket frame and never in the URL.
The socket checks the request Origin before it accepts, and it rechecks the session and topic access before joining the broker and during the connection.
WebSocket needs a session engine that stores sessions on the server, so the signed-cookie engine is unsupported and ``next.W142`` reports it.
The socket loads its session from the cookie at the handshake and on every recheck, so a session cache that evicts live entries closes sockets with ``4401``.
Size the session cache for the open connections, or use the ``db`` or ``cached_db`` engine.

WebSocket is optional and SSE remains the fallback.
A stream without a stable, importable ``authorize`` callback answers negotiation with ``406`` and stays on SSE.

Sequences and epochs
--------------------

The broker numbers each topic with a ``seq`` that grows by one per publication.
Every counter also carries an ``epoch``, a random token the broker draws whenever the counter starts again, so a ``seq`` is unique only within its epoch.
A counter starts again after a broker restart, an evicted key, or a flushed cache, and the new epoch tells the client that its old cursor means nothing.
A topic that was never published reports the empty epoch and ``seq`` zero.

Every stream opens with ``next-hello``, sent once the broker subscription is live.
It lists the current position of each subscribed topic.

.. code-block:: json
   :caption: next-hello data

   {"protocol": 2, "server_time": 1761300000000, "topics": {"room:42": {"epoch": "9f2c41d07a3be851", "seq": 17}}, "transports": ["sse"]}

A ``next-patches`` frame of a topic carries ``topic``, ``epoch``, ``seq``, and ``server_time`` beside the ordinary patch envelope.
A subscriber transform that returns ``None`` produces a ``next-skip`` acknowledgment with ``topic``, ``epoch``, and ``seq``, so filtered messages still advance that subscriber's cursor.
A stream whose topic grant was denied ends with ``next-end``, described in `Access while a stream is open`_.

.. code-block:: json
   :caption: next-skip data

   {"topic": "room:42", "epoch": "9f2c41d07a3be851", "seq": 18}

Delivery guarantees
-------------------

Delivery is at most once and best-effort.
The broker does not replay events, and bounded subscriber queues drop their oldest message when full.
The in-memory broker is process-local.
Use ``RedisBroker`` for multiple workers or hosts, so they share publication and sequence state.

The client keeps an ``(epoch, seq)`` cursor per topic.
On the first ``hello`` with a nonzero sequence, the client revalidates the page's zones because the HTML render may predate the subscription.
A frame whose ``seq`` skips ahead in the same epoch is a gap, and a frame or hello whose epoch differs from the cursor resets the cursor to the new position.
A change from the empty epoch of a topic that was never published at ``hello`` is contiguous when the frame's ``seq`` is ``1`` and a gap when it is higher.
Either case requests the page's bound zones in one guarded GET with a comma-separated ``X-Next-Zone`` header, and a newer gap cancels a GET already in flight for the same page.
The gap check runs before the client drops its own echo, so an echoed frame cannot hide a lost one.

Each page view applies its own guards and cookies.
A stream event is a hint that state may have changed, and the zone GET renders the current state.
When the broker loses its connection, the stream ends without ``next-end``.
The browser reconnects, receives a fresh ``hello``, and resynchronises from it.

If a broker loses a topic's whole record, epoch and counter together as an evicted Redis hash does, between the page render and the subscription, a publication made in that window can be missed until the next change.

``on_commit`` prevents subscribers from refreshing before a database commit.
It does not make broker delivery durable if the broker is unavailable after commit.
Client disconnect also does not roll back a completed mutation or stop a synchronous ORM operation that is already running.

Listen to topic events
----------------------

Applications can listen to a topic event without adding transport metadata to its detail.

.. code-block:: javascript
   :caption: rooms/static/rooms/phase.js

   Next.partial.stream.on("room:42", "room:phase", (detail, meta) => {
     console.log(detail.deadline, meta.epoch, meta.seq);
   });

``Next.partial.stream.meta()`` is available during synchronous event delivery.
``stream.now()`` estimates server epoch time in milliseconds.
``stream.offset()`` returns the median of the last five server clock samples.
``Next.partial.sse`` remains an alias.
The global event detail stays exactly as published.

Manual streams
--------------

``PatchEventStream`` remains available when an application needs a custom source.
Its source must match the server mode, a synchronous iterable under WSGI or an asynchronous iterable under ASGI.
The page view still owns access checks, and the author owns source cleanup.

See also
--------

.. seealso::

   :doc:`/content/howto/stream-live-updates-with-sse` for a short recipe.
   :doc:`/content/howto/fan-out-across-processes-with-redis` for shared broker setup.
   :doc:`/content/howto/stream-over-websockets` for the WebSocket transport.
   :doc:`/content/ref/realtime` for the broker contract and a custom backend.
   :doc:`/content/topics/partial-rendering/sse` for the underlying SSE transport.
