.. _topics-realtime:

Realtime topics
===============

Realtime topics fan out small patch instructions to connected pages.
The browser loads current HTML through the page's normal guarded HTTP request.

.. contents::
   :local:
   :depth: 1

What realtime is for
--------------------

A page that stays open goes stale when another user or a worker changes the data behind it.
Realtime lets the server tell every open page of a topic that a part of it changed, in the form of small patch instructions such as a refresh of one zone.
Each page then reloads that zone through its own guarded request, so a subscriber never receives HTML rendered for someone else.
Over a WebSocket a page also sends small events and form submissions back to the server.

How one update travels
----------------------

1. The page renders a ``{% zone %}`` and a ``data-next-stream`` element that points at a stream page.
2. The client runtime requests the stream page, whose guard runs and whose ``render`` returns a ``TopicStream``.
   A process that mounts ``WebSocketApp`` answers with a signed ticket and the client opens a WebSocket.
   Otherwise, or when the socket fails, the same URL serves Server-Sent Events.
3. The stream subscribes to the broker and sends ``next-hello`` with the current position of each topic.
4. A mutation calls ``publish(topic, Patches(request).refresh(zone=...))``, and the broker receives the message once the transaction commits.
5. The broker numbers the message and hands it to every stream subscribed to the topic, in one process with ``MemoryBroker`` or across processes with ``RedisBroker``.
6. The client checks the message against its cursor, drops the echo of its own request, and applies the patches.
   A ``refresh`` makes it fetch the zone through the page view with its own cookies and morph the fresh HTML in place.

The reverse path exists over a WebSocket only.
``Next.partial.stream.send`` sends an upstream event that a handler registered with ``@on`` receives, as `Upstream events`_ describes.
A form whose action opts in with ``transport="any"`` submits through the same socket and runs the ordinary HTTP pipeline, as `Form actions over WebSocket`_ describes.

.. mermaid::

   sequenceDiagram
       participant Tab as Browser tab
       participant Page as Page view
       participant Stream as TopicStream
       participant Broker
       Tab->>Stream: GET stream page, WebSocket or SSE
       Stream->>Broker: subscribe to topic
       Stream-->>Tab: next-hello
       Page->>Broker: publish refresh after commit
       Broker-->>Stream: numbered message
       Stream-->>Tab: next-patches
       Tab->>Page: zone GET with own cookies
       Page-->>Tab: zone HTML
       Tab->>Stream: upstream event or tunneled form, WebSocket only

Terms
-----

.. list-table::
   :header-rows: 1
   :widths: 18 82

   * - Term
     - Meaning
   * - topic
     - A named channel such as ``room:42`` that a mutation publishes to and a stream subscribes to, see `Publish after a mutation`_.
   * - broker
     - The backend that numbers, stores, and fans out topic messages, ``MemoryBroker`` by default and ``RedisBroker`` across processes, see `Delivery guarantees`_.
   * - stream
     - One open connection from a page to its topics, over WebSocket or SSE, see `Subscribe from an authorized page`_.
   * - ``TopicStream``
     - The response a stream page returns to open a stream after its guard, see `Subscribe from an authorized page`_.
   * - ticket
     - A signed token that carries the stream's grant from the guarded page to the WebSocket, see `Access while a stream is open`_.
   * - binding
     - The rule that ties a ticket to the identity of the browser, ``SessionBinding`` by default, see `The signed-cookie session engine`_.
   * - replay
     - The bounded catch-up of messages a reconnecting stream missed, see `Replay after a reconnect`_.
   * - presence
     - The live count or member list of the streams open on a topic, see `Presence`_.
   * - upstream
     - A small event a page sends to the server over its WebSocket, see `Upstream events`_.
   * - tunnel
     - The path that carries a form action over the open WebSocket, see `Form actions over WebSocket`_.
   * - zone
     - A named region of a page template that the server renders on its own, see :doc:`/content/topics/partial-rendering/zones`.
   * - patch
     - One instruction of an envelope, such as ``refresh`` or ``event``, that the client applies, see :doc:`/content/topics/partial-rendering/how-it-works`.

SSE, WebSocket, and Django Channels
-----------------------------------

.. list-table::
   :header-rows: 1
   :widths: 22 39 39

   * - Aspect
     - SSE
     - WebSocket
   * - Server
     - WSGI or ASGI, the development server included.
     - ASGI with ``WebSocketApp`` mounted, for example through ``next.realtime.application``.
   * - Direction
     - Server to page.
     - Both ways, with upstream events and tunneled form actions.
   * - Requirements
     - None beyond the stream page.
     - An importable ``authorize`` callback, and with the default binding a session engine that stores sessions on the server, as `The signed-cookie session engine`_ explains.
   * - Role
     - The fallback every stream keeps.
     - Preferred when the process mounts it, with SSE taking over when negotiation or the socket fails.

Both transports carry the same frames, apply the same access rules, and share one broker, so a page does not choose between them in code.

next.dj neither uses nor requires Django Channels.
Its WebSocket runs on plain ASGI and loads the Django session itself, and topics travel through the next.dj broker rather than a channel layer.
A project that already routes WebSockets with Channels mounts ``WebSocketApp`` as one route of its own router, as :ref:`howto-stream-over-websockets-router` shows.
It can instead wrap its whole router in ``next.realtime.application``, which serves ``WS_PATH`` itself and passes every other WebSocket path and the lifespan to the router.

Where to start
--------------

1. :doc:`/content/howto/stream-live-updates-with-sse` builds a complete live page with a model, a vote form, a zone, and a stream page.
   ``examples/live-polls`` is the same idea as a full project, see :doc:`/content/misc/examples`.
2. `Access while a stream is open`_ covers the ``authorize`` callback, tickets, and revocation.
3. :doc:`/content/howto/stream-over-websockets` mounts the socket and runs the server.
4. :doc:`/content/howto/fan-out-across-processes-with-redis` shares topics between workers and hosts.
5. `Presence`_ and `Upstream events`_ add who-is-watching counts and page-to-server events.

Publish after a mutation
------------------------

``publish`` accepts a ``Patches`` builder or a partial ``Envelope``.
The default ``on_commit=True`` delays sync publication until the selected database connection commits.
Pass ``using="analytics"`` when the mutation belongs to a non-default database, otherwise the default connection is used.
The callback is process-local and is not a durable outbox.
An ``apublish`` call publishes immediately through the backend's asynchronous API.
Both return the numbered ``Message`` once they have published, and a deferred ``publish`` returns ``None``.
A payload the topics reject raises ``TopicPayloadError``.
A malformed topic name or a topic in the reserved ``next:`` namespace raises ``InvalidTopicError``.

.. code-block:: python
   :caption: rooms/signals.py

   from django.http import HttpRequest
   from rooms.models import Room

   from next.partial import Patches
   from next.realtime import publish

   def announce(request: HttpRequest, room: Room) -> None:
       publish(f"room:{room.code}", Patches(request).refresh(zone="scoreboard"))

The request ID from a request-bound ``Patches`` builder is attached automatically.
This lets the browser ignore the update produced by its own successful action.
An explicit ``echo_of`` value on the builder takes precedence.

Topics accept only ``refresh``, ``event``, ``toast``, and ``remove`` operations.
The framework rejects HTML operations, custom operations, assets, form metadata, CSRF state, and values that cannot be serialized as JSON.
Use ``refresh(zone=...)`` when a subscriber must load private or request-specific HTML.
The browser then fetches that zone through its own guarded page request and session cookies.

A deferred publication runs as a robust ``on_commit`` callback.
When the broker fails after the commit, Django logs the exception under the callback's qualified name, which names the topic, and the committed write still answers normally.
``publish(..., on_commit=False)`` and ``apublish`` call the broker at once, return the ``Message``, and raise the broker error to the caller.

Subscribe from an authorized page
---------------------------------

Return ``TopicStream`` from a page's ``render`` function.
The page's regular access checks and dependency resolution run before the stream opens.

.. code-block:: python
   :caption: rooms/pages/rooms/[slug:code]/stream/page.py

   from django.http import HttpRequest

   from next.realtime import TopicStream
   from next.urls import DUrl

   def render(request: HttpRequest, code: DUrl[str]) -> TopicStream:
       return TopicStream(request, f"room:{code}")

``DUrl[str]`` reads the ``code`` segment of the URL, see :doc:`/content/topics/dependency-injection`.
The backend subscription is selected from the request's WSGI or ASGI type.
The broker wait timeout is the ``SSE.HEARTBEAT_SECONDS`` option of the partial backend, 25 seconds by default, and each timeout writes a heartbeat comment under WSGI and ASGI alike.
A WebSocket times its pings, access rechecks, and presence refreshes by ``REALTIME['HEARTBEAT']`` instead, 25 seconds by default, and every later mention of the heartbeat interval means the one of the stream's transport.
Under WSGI, disconnect cleanup is best-effort at the next yield, write, or close.
Under ASGI, a client that stops reading is dropped one to two heartbeats after a write to it blocks.
Closing the stream releases the broker subscription.

An optional ``transform(message)`` runs for each subscriber and can return a safe ``Patches`` builder or ``Envelope``.
``transform``, ``authorize``, and ``presence`` each take a callable or its dotted path, such as ``"rooms.access.can_watch"``.
A dotted path is imported when the stream is built and reaches a WebSocket ticket as written.
Returning ``None`` suppresses the message for that subscriber.
A transform that raises or returns an unsafe payload suppresses the message the same way and logs the topic and the ``seq`` on ``next.realtime.delivery``, on a live message and on a replayed one alike.
``transform`` may be a plain function, an ``async def`` function, or an object whose ``__call__`` is either.
An async stream or a WebSocket awaits an async transform on the event loop and runs a sync one on the shared thread pool.
A sync stream under WSGI runs an async transform to completion through ``async_to_sync``.

Access while a stream is open
-----------------------------

The page guard runs once, when the stream opens.
An optional ``authorize(user, topic)`` callback on ``TopicStream`` keeps watching the grant afterwards on both transports.
``authorize`` is a synchronous callable that returns ``True`` to grant the topic, and any other value, a truthy one or an ``async def`` coroutine included, denies it.
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
The client then falls back to SSE, where the same rules apply, after one fresh negotiation when the close was ``4401``.

When the process mounts a ``WebSocketApp``, the client can negotiate a WebSocket through this same guarded stream URL.
The signed ticket binds the current session to the topics, transform, and importable ``authorize`` callback chosen by the page.
The client sends the ticket in its first WebSocket frame and never in the URL.
The first handshake does not consume the ticket, so it stays valid for every handshake until ``REALTIME['TICKET_MAX_AGE']`` elapses.
It is signed from ``SECRET_KEY`` and also verifies under ``SECRET_KEY_FALLBACKS``, so a rotation that keeps the old key as a fallback leaves outstanding tickets valid, and dropping the old key invalidates them.
The socket checks the request Origin before it accepts, and it rechecks the session and topic access before joining the broker and during the connection.
WebSocket works best with a session engine that stores sessions on the server, and ``next.W142`` flags the signed-cookie engine under the default ticket binding, see `The signed-cookie session engine`_.
The socket loads its session from the cookie at the handshake and on every recheck, so a session cache that evicts live entries closes sockets with ``4401``.
Size the session cache for the open connections, or use the ``db`` or ``cached_db`` engine.

WebSocket is optional and SSE remains the fallback.
A stream without a stable, importable ``authorize`` callback answers negotiation with ``406`` and stays on SSE.

.. _topics-realtime-signed-cookie:

The signed-cookie session engine
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The signed-cookie session engine works with the default ticket binding only in part, and ``next.W142`` reports it.

- A first visit without a session cookie cannot negotiate a ticket, since the engine has no session key before it saves one, and neither can a forged or expired signed cookie.
  The negotiation answers ``503`` with ``session_unavailable`` and the client falls back to SSE after its retries.
- Any HTTP response that saves the session re-signs the cookie, so a ticket issued before that save no longer matches the handshake cookie.
  The socket closes with ``4401`` and the client continues over SSE.
- A signed cookie stays valid after logout until ``SESSION_COOKIE_AGE`` passes, so logging out leaves an open socket running, and only a password change or an inactive user revokes its session.

Setting ``NEXT_FRAMEWORK['REALTIME']['TICKET_BINDING']`` to ``"next.realtime.SignedCookieBinding"`` removes the first two limits.
It binds a signed-in user by primary key and auth hash and an anonymous visitor by a signed cookie.
Neither changes when the engine re-signs the session.
The third limit stays, because the signed cookie holds nothing on the server to delete.
The binding needs no server-side session engine.
Next to one it never rechecks the stored session, so ``next.W147`` suggests ``SessionBinding`` there.
A project with another source of identity writes a binding of its own, see :doc:`/content/howto/bind-tickets-to-your-own-identity`.

The ``db``, ``cache``, ``cached_db``, and ``file`` engines avoid all three with the default binding.

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
Bounded subscriber queues drop their oldest message when full, and a reconnect recovers only what the replay log still holds, see `Replay after a reconnect`_.
The in-memory broker is process-local, and ``manage.py check --deploy`` reports ``MemoryBroker`` as ``next.W149``.
Use ``RedisBroker`` for multiple workers or hosts, so they share publication and sequence state.
An event that must not be lost goes through an outbox table, see :doc:`/content/howto/deliver-events-durably`.

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

Replay after a reconnect
------------------------

Both shipped brokers keep the newest messages of each topic in a bounded log, sized by ``NEXT_FRAMEWORK['REALTIME']['REPLAY']``.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "REALTIME": {
           "REPLAY": {"SIZE": 100, "TTL": 300},
       },
   }

``SIZE`` caps the messages kept per topic and ``TTL`` is how many seconds the log lives after the last publication on the topic.
``SIZE`` set to ``0`` turns the log off, so every reconnect resynchronises.

A reconnecting stream subscribes, reads the topic positions, and sends the messages its cursor missed up to those positions before ``hello``.
Replayed messages pass through the subscriber transform and the access check like live ones.
A topic replays only when the log holds every message from the cursor to the current position in the same epoch.
A trimmed or expired log, a broker restart, and a new epoch replay nothing for that topic, and ``hello`` then shows the gap so the client resynchronises its zones.

A WebSocket client sends its cursor in the ``sub`` frame on every reconnect.
An SSE stream over one topic tags ``hello`` and each message and ``skip`` frame with an ``id`` that ``EventSource`` sends back as ``Last-Event-ID`` when it reconnects.
An SSE stream over several topics carries no ``id`` and resumes through the resync alone.
:doc:`/content/ref/realtime` describes the order, the id format, and the override points.

An event is not state
~~~~~~~~~~~~~~~~~~~~~

Replay is a bounded best-effort catch-up, not a record of what happened.
The log forgets messages past ``SIZE`` and ``TTL``, a broker restart drops it, and the at-most-once rule still holds for a live subscriber whose queue overflows.
Build pages so that a topic event only says that state may have changed, and let ``hello`` and the zone GET it triggers stay the source of truth.
A page that renders correctly after a resync renders correctly after any replay too.

Known limitations
~~~~~~~~~~~~~~~~~

The browser client closes its streams while the tab is hidden and opens them again on return.
A WebSocket closes after a brief drain, once the tunneled requests and acknowledged ``up`` frames already sent on it get their replies.
A page loaded in a hidden tab opens its streams on the first show.
Over SSE that is a new ``EventSource``, which sends no ``Last-Event-ID``, so a stream resumed after a visibility change resynchronises instead of replaying.

.. _topics-realtime-presence:

Presence
--------

Presence tells a page who is watching a topic.
A stream that opts in is recorded on its topics while it is open, and the broker publishes the live attendance as an ordinary topic message.
Pass ``presence=True`` for a counter or an ``identify`` callable for a list of members.

.. code-block:: python
   :caption: rooms/pages/rooms/[slug:code]/stream/page.py

   from django.http import HttpRequest

   from next.realtime import TopicStream
   from next.urls import DUrl

   def render(request: HttpRequest, code: DUrl[str]) -> TopicStream:
       return TopicStream(request, f"room:{code}", presence=True)

Every attendance snapshot of the topic reaches the page as the ``next:presence`` event, and the page subscribes to it like to any topic event.

.. code-block:: javascript
   :caption: rooms/static/rooms/online.js

   Next.partial.stream.on("room:42", "next:presence", (detail) => {
     document.querySelector("#online").textContent = detail.count;
   });

The detail always holds ``count``.
It holds ``members`` only in the member mode.
It also needs at least one identified member and a ``SIZE`` above ``0``, so a page reads it as ``detail.members ?? []``.
The client needs no new code, because the event is a plain ``event`` operation.

A snapshot is not an ordinary publication.
It travels unnumbered, so it takes no ``seq``, never enters the replay log, and skips the subscriber ``transform``, which therefore cannot hide or edit it for one user.
A reconnecting page receives the current snapshot right after ``hello`` instead of a replayed one.
The access check still gates the stream, so a subscriber that loses its grant stops receiving snapshots with everything else.

Members and privacy
~~~~~~~~~~~~~~~~~~~

``identify(user, topic, subject)`` returns a mapping of JSON values that describes the user to the other subscribers, or ``None`` to attend without a description.
It runs once per topic when the connection opens, with the session user of the request, and under ASGI it runs on the shared worker pool, so it may query the database.
``subject`` is the opaque id of the browser's user or visitor, the same string ``Upstream.subject`` holds, so an anonymous visitor can be named too.
It stays the same across reconnects and on both transports, and under ``SignedCookieBinding`` a guest keeps the visitor cookie's subject across reloads.
It is empty when the request carries no session or visitor cookie, and an ``identify`` that takes only ``(user, topic)`` is called without the subject.

.. code-block:: python
   :caption: rooms/presence.py

   from collections.abc import Mapping

   def identify(user, topic: str, subject: str) -> Mapping[str, object] | None:
       if user.is_authenticated:
           return {"handle": user.username, "name": user.get_full_name()}
       if not subject:
           return None
       return {"handle": subject[:4], "name": f"Guest {subject[:4]}"}

.. code-block:: python
   :caption: rooms/pages/rooms/[slug:code]/stream/page.py

   def render(request: HttpRequest, code: DUrl[str]) -> TopicStream:
       return TopicStream(request, f"room:{code}", presence="rooms.presence.identify")

Privacy rests on two rules, because no transform touches a snapshot.
The ``members`` list reaches only subscribers that identify their own members, and the stream cuts it from every ``next:presence`` event it sends to any other subscriber, a counter stream or one without presence alike.
Whatever ``identify`` returns is exactly what those identifying subscribers see, so it is the privacy boundary of presence.
Return only what each watcher may see of the others, and leave out e-mail addresses, primary keys, and anything private.
A stream with ``presence=True`` sends no data about people, only a number.

The mapping needs string keys and JSON values, and its compact JSON may not exceed ``REALTIME['PRESENCE']['MEMBER_BYTES']`` bytes.
A larger or non-JSON member raises ``InvalidMemberError`` when the connection joins.

How the count is made
~~~~~~~~~~~~~~~~~~~~~

The member mode counts unique participants.
The canonical JSON of the mapping, with sorted keys, is the identity of a member, so two tabs of one user that return an equal mapping are one member and show once.
Two different people who return the same mapping merge too, so add a distinguishing public key such as a handle when names can repeat.
A connection whose ``identify`` returned ``None`` counts alone.
The counter mode counts connections, so two tabs of one user count twice.
A topic that mixes both kinds of stream adds the counters of the two.

``REALTIME['PRESENCE']['SIZE']`` caps how many members one event lists, ``100`` by default, and the ``count`` stays exact beyond it.
A ``SIZE`` of ``0`` sends the count alone.
Members are listed in the order of their first join, and a member that leaves entirely and comes back joins the end of the list.

The coalescing window
~~~~~~~~~~~~~~~~~~~~~

A change in the attendance does not publish at once.
The first change claims a window of ``REALTIME['PRESENCE']['WINDOW']`` seconds, one second by default, and the broker publishes one snapshot of the attendance when the window closes.
Every change inside the window joins that snapshot, so a crowd that opens a page together produces one event instead of one per person.
A new connection always claims a window, because it needs the snapshot to learn the starting state, even when its member is already present through another tab.
A connection that leaves, or expires, claims one only when its member goes away entirely.
When the last connection of a topic leaves while another process holds the window, the leaving process schedules the snapshot for the moment that window closes, so the final count arrives even if the holder died.

A page that must show the count on first paint reads it on the server with ``presence(topic)``, or ``await apresence(topic)`` in async code.
Both return a ``Presence`` snapshot and raise ``ImproperlyConfigured`` on a broker without presence.

Crashed connections
~~~~~~~~~~~~~~~~~~~

An open connection refreshes its record once per heartbeat, and the record lives three heartbeat intervals unless an ``Attendance`` subclass sets another ``ttl_heartbeats``.
A process that dies without a goodbye therefore drops out of the attendance after at most three intervals, 75 seconds at the default heartbeat.
Any later join, refresh, or leave on the topic prunes the expired records and publishes the new snapshot.
A read through ``presence(topic)`` never counts an expired record.

Presence needs a broker that implements ``PresenceBroker``.
``MemoryBroker`` and ``RedisBroker`` do, and a stream that asks for presence on another broker fails to start.
Over WebSocket the ``identify`` callable travels in the signed ticket as a dotted path, so a callable without an importable path, such as a lambda, answers the negotiation with ``406`` and ``reason`` set to ``presence``, and the stream stays on SSE.
:doc:`/content/ref/realtime` carries the contract and :doc:`/content/ref/settings` the limits.

Listen to topic events
----------------------

Applications can listen to a topic event without adding transport metadata to its detail.

.. code-block:: javascript
   :caption: rooms/static/rooms/phase.js

   Next.partial.stream.on("room:42", "room:phase", (detail, meta) => {
     console.log(detail.deadline, meta.epoch, meta.seq);
   });

A ``next:presence`` event carries no position, so its ``meta.seq`` is ``undefined`` and its ``meta.epoch`` is an empty string.
``Next.partial.stream.meta()`` is available during synchronous event delivery.
``stream.now()`` estimates server epoch time in milliseconds.
``stream.offset()`` returns the median of the last five server clock samples.
The global event detail stays exactly as published.

.. _topics-realtime-upstream:

Upstream events
---------------

A topic stream is one-way by default, so the page learns what the server publishes and tells the server nothing back.
Upstream events let a page send a small event to the server over the WebSocket it already holds.
Typing indicators, cursor positions, and "I pulled this card" signals fit, because they are ephemeral and do not deserve an HTTP request and a database write.
Over SSE there is no upstream channel, so a page treats an upstream event as an optional enhancement.

Register a handler
~~~~~~~~~~~~~~~~~~

``@on(pattern, name)`` from ``next.realtime`` registers a synchronous or asynchronous function for the events called ``name`` on the topics ``pattern`` matches.
The function takes one ``Upstream`` argument.

.. code-block:: python
   :caption: rooms/realtime.py

   from next.partial import Patches
   from next.realtime import Upstream, apublish, on

   @on("room:{code}", "typing")
   async def typing(upstream: Upstream) -> None:
       who = str(upstream.user)
       await apublish(upstream.topic, Patches(None).event("room:typing", {"who": who}))

``pattern`` is a topic name in which a ``{placeholder}`` stands for exactly one segment of letters, digits, ``_``, and ``-``.
A placeholder never crosses a colon or a dot, so ``room:{code}`` matches ``room:42`` and not ``room:42:chat``.
The matched values reach the handler as the read-only mapping ``upstream.params``, here ``{"code": "42"}``.
A pattern without placeholders matches one topic.
When several patterns match, the one with the fewest placeholders wins, then the one registered first.
A pattern may not start with ``next:`` and a name is 1 to 100 topic characters.
``manage.py check`` reports a pattern or a name that can never match.

``Upstream`` is a frozen dataclass with these fields.

.. list-table::
   :header-rows: 1
   :widths: 22 78

   * - Field
     - Value
   * - ``topic``
     - The topic the event arrived on, always one of the topics in the connection's ticket.
   * - ``name``
     - The event name.
   * - ``data``
     - The decoded JSON the page sent, untrusted like any request input.
   * - ``user``
     - The user of the session behind the connection, possibly anonymous.
   * - ``subject``
     - An opaque id of the identity behind the connection, stable across its reconnects and empty when the credential proves none.
       It is a keyed digest and never the session key, because a handler never sees the credential of the socket.
       It changes when ``SECRET_KEY`` rotates, and under ``SessionBinding`` it follows the session rather than the user, so a new login yields a new one.
   * - ``connection``
     - An identifier of the sending connection, the same id the WebSocket ``hello`` frame carries in ``connection``.
       `Drop your own echo`_ shows how a handler uses it.
   * - ``params``
     - What the placeholders of the pattern matched.

A synchronous handler runs on a thread pool of its own and may use the ORM.
An asynchronous handler runs on the event loop.
A handler returns nothing, and an exception it raises is logged and reaches the sender as a failed acknowledgment.
The ``upstream_received`` signal fires before each handler runs, with the handler as its sender, and ``upstream_rejected`` fires for an event refused before a handler finished.
``stream_subscribed``, ``stream_unsubscribed``, and ``message_published`` report the life of every stream and publication, see :ref:`ref-signals-stream-lifecycle`.

Discovery
~~~~~~~~~

Django imports the ``realtime`` module of every installed application at startup, the way the admin finds ``admin.py``, so handlers register by being defined there.
``NEXT_FRAMEWORK["REALTIME"]["HANDLERS"]`` replaces that search with an explicit list of dotted module names, and an empty list imports nothing.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "REALTIME": {
           "HANDLERS": ["rooms.realtime", "chat.events"],
       },
   }

Registering a second function under the same pattern and name is reported by ``manage.py check``, and only the first one runs.
A function counts as itself only at the same file, first line, and qualified name, so a reload that keeps the ``def`` in place replaces its own entry without the report, and a reload that moves the ``def`` is reported.
:doc:`/content/ref/settings` lists the keys and :doc:`/content/ref/system-checks` the check ids.

What a handler may rely on
~~~~~~~~~~~~~~~~~~~~~~~~~~

An event reaches a handler only on a topic of the ticket that the page's own stream request issued, so a page cannot address a topic it was never granted.
The ``authorize`` callback of the stream applies to upstream events too.
Its recheck is throttled to once per heartbeat interval, so a revoked grant stops upstream events within one heartbeat.
A failed recheck acknowledges the frame as ``denied`` and closes the socket like any other failed recheck.
A handler still validates ``data`` and checks what the user may do, because the grant says only that the user may watch the topic.

Each connection draws a token per frame from one bucket, set by ``REALTIME["UPSTREAM"]``.
The defaults are ``10`` frames a second, a burst of ``20``, ``16384`` bytes per ``up`` frame, ``16`` handlers running at once per connection under ``CONCURRENCY``, and ``10`` seconds for a handler before the ``ack`` answers ``timeout``.
``hello`` carries that handler timeout as ``upstream.timeout``, so the client waits for an ``ack`` as long as the server lets the handler run.
The same bucket serves the tunneled requests of `Form actions over WebSocket`_.
A frame over the budget is refused with ``limit``, and three refusals in a row close the socket with ``4429``.
An event past ``CONCURRENCY`` is refused with ``limit`` as well and never counts toward ``4429``.
A client that throttles its own sends, a typing indicator at most every few hundred milliseconds, never reaches that code.

What the framework does not cap
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Frame size, frame count, upstream rate, and tunnel limits apply per connection.
The number of open WebSocket and SSE connections per user, address, or process is not capped by next.dj.
The proxy, the operating system limit on open file descriptors, and the connection limit of the broker own that bound.
See :doc:`/content/security/overview` and :doc:`/content/deployment/wsgi-asgi`.

Send from the page
~~~~~~~~~~~~~~~~~~

``Next.partial.stream.send(topic, name, data, options?)`` returns a promise of a boolean.

.. code-block:: javascript
   :caption: rooms/static/rooms/typing.js

   const input = document.querySelector("#message");
   let last = 0;

   input.addEventListener("input", () => {
     const now = Date.now();
     if (now - last < 400) return;
     last = now;
     Next.partial.stream.send("room:42", "typing", { typing: true }, { ack: false });
   });

   Next.partial.stream.on("room:42", "room:typing", (detail) => {
     document.querySelector("#typing").textContent = `${detail.who} is typing`;
   });

By default the frame carries an id and the promise resolves to ``true`` when the handler ran to completion.
It resolves to ``false`` when the handler is missing or failed, the bucket was empty, the topic is not open on a socket, no acknowledgment arrived within the ``upstream.timeout`` of ``hello`` plus a short margin, or the socket dropped first.
``{ack: false}`` sends the frame without an id and resolves to ``true`` as soon as it is queued, which suits events whose loss is harmless.
A page on SSE has no WebSocket, so the promise resolves to ``false`` and nothing is sent.
A frame over the ``upstream.frame`` limit of ``hello``, 16 KiB by default, resolves to ``false`` as well.

Drop your own echo
~~~~~~~~~~~~~~~~~~

A publication reaches every subscriber of the topic, the sending tab included.
The WebSocket ``hello`` names the socket in ``connection``, the same id a handler reads from ``upstream.connection``, so a handler copies it into what it publishes and the sender skips the event.
The id names one socket only and reveals nothing about the session or the user.

.. code-block:: python
   :caption: rooms/realtime.py

   @on("room:{code}", "typing")
   async def typing(upstream: Upstream) -> None:
       detail = {"who": str(upstream.user), "from": upstream.connection}
       await apublish(upstream.topic, Patches(None).event("room:typing", detail))

.. code-block:: javascript
   :caption: rooms/static/rooms/typing.js

   Next.partial.stream.on("room:42", "room:typing", (detail) => {
     if (detail.from === Next.partial.stream.meta()?.connection) return;
     document.querySelector("#typing").textContent = `${detail.who} is typing`;
   });

Each tab and each reconnect gets its own id, so a second tab of the same user still sees the event.

.. _topics-realtime-tunnel:

Form actions over WebSocket
---------------------------

A page that already holds a WebSocket can submit its forms through it instead of opening an HTTP request each time.
The client sends the request as an ``http`` frame.
The server runs it through the same ASGI application that serves HTTP.
Middleware, CSRF, sessions, and the action's guards run as they do for a browser request.
The reply goes back as a frame, and the page applies the envelope it carries exactly as it applies a fetched one.

The tunnel is opt-in per action.

.. code-block:: python
   :caption: rooms/pages/rooms/[slug:code]/page.py

   from django.http import HttpRequest
   from rooms.models import ChatMessage

   import next.forms
   from next.forms import redirect_to_origin

   class MessageForm(next.forms.Form):
       body = next.forms.CharField()

       class Meta:
           transport = "any"

       def on_valid(self, request: HttpRequest):
           ChatMessage.objects.create(body=self.cleaned_data["body"])
           return redirect_to_origin(request)

The default is ``"http"``.
``@action(..., transport="any")`` sets the flag for a form-less action, and a ``FormWizard`` sets ``Meta.transport`` like a form, see :doc:`/content/topics/forms/actions`.
The ``{% form %}`` tag marks an opted-in form with ``data-next-transport="any"``, and the client sends only marked submissions through the socket.
An unknown value registers the action for HTTP alone, and ``manage.py check`` reports it.

What goes through the tunnel
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The tunnel carries a ``POST`` with a ``application/x-www-form-urlencoded`` or JSON body.
The request is built from the handshake of the socket, so the cookies, host, origin, and proxy headers are the ones the browser sent when the socket opened.
The page contributes only ``content-type``, ``accept``, the CSRF header, and ``x-next-*`` headers.
A request that carries any other header goes by ``fetch`` instead.

These requests go by ``fetch`` and never reach the socket.

- A multipart body, so every upload.
  The tunnel takes text only.
- A request while no socket is open, such as on a page served over SSE.
- A body over the 64 KiB frame limit.
- A request to an action that did not opt in.
  The server answers ``421`` with ``X-Next-Transport: http`` before any guard runs, nothing executes, and the client repeats the same request over ``fetch``.

The reply is the response Django produced, except for ``Set-Cookie``, because a script cannot apply a cookie that came through a frame.
A cookie that repeats a request cookie with the same value is dropped silently.
That covers the CSRF cookie that ``{% form %}`` re-emits and a session cookie that saves unchanged.
Any other ``Set-Cookie`` replaces the reply with the ``http_only`` refusal.
That covers a new cookie, a changed value, a deletion, a message stored in a cookie, and a language cookie, and the action has already run by then.

A login, a logout, or any other rotation or flush of the session is refused earlier, when the action calls ``cycle_key()`` or ``flush()`` on ``request.session``.
The call raises before the session store writes, so the stored session and its signed-in user stay intact, and the reply is ``http_only`` as well.
With ``ATOMIC_REQUESTS`` the database work of the action rolls back, and without it whatever ran before the rotation stays, a ``user_logged_out`` receiver included.

On ``http_only`` the client fires ``partial:error`` of kind ``undelivered`` with the reason ``http_only`` and then reloads the page, so the post is not repeated and the page shows the state the server holds.
The event cannot be cancelled, and its listeners run before the reload.
The other refusals that may follow a run, a dropped socket, and a client timeout fire ``undelivered`` without a reload, so a listener reads the state again rather than resending.
An action whose page must not reload keeps the default ``transport="http"``, and so do login and logout.
For flashed messages, set ``MESSAGE_STORAGE`` to the session storage with a server-side session engine, and ``next.W145`` flags a cookie-based storage next to an opted-in action.
The signed-cookie engine rewrites its cookie on every session write, so a tunneled action that writes to the session ends in ``http_only`` there.

The socket loads its cookies once, at the handshake.
A request that finds no CSRF cookie in them is refused with ``stale`` before it runs.
The client sends it over ``fetch`` and reconnects the socket with the fresh cookie.
The client also reconnects after it mints a new CSRF token.
A second ``stale`` on the reconnected socket makes the client stop tunneling on that connection.
When the tunnel is off, the refusal is ``disabled``, and the client stops tunneling on that connection too.
Both refusals mean the action did not run.
A ``403`` or ``404`` that a guard or the action raises arrives as the reply, as over HTTP, and is not repeated.
The refusals ``http_only``, ``too_big``, ``timeout``, ``encoding``, and ``internal`` mean it may have.
:doc:`/content/ref/realtime` lists every refusal with its ``retry`` flag.

``REALTIME["TUNNEL"]`` sets the limits of the tunnel.

- ``CONCURRENCY``, 4 requests at once per connection by default, and a fifth is refused with ``limit`` and sent over ``fetch``.
- ``TIMEOUT``, 30 seconds per request by default, after which the request is ended with ``http.disconnect`` and answered with ``timeout``, while a sync view keeps its slot until it returns.
- ``MAX_REPLY_BYTES``, 1 MiB for the reply body by default.

The frame bucket of `Upstream events`_ applies as well.
``REALTIME["TUNNEL"] = False`` turns the tunnel off, and every submission goes by ``fetch``.

Replace the tunnel
~~~~~~~~~~~~~~~~~~

``application()`` hands the tunnel the HTTP application it wraps.
``WebSocketApp(http_app=...)`` runs the requests through another ASGI application.
A subclass that overrides ``WebSocketApp.tunnel(scope)`` returns its own object with ``async __call__(request, *, connection)`` that answers with a ``Reply`` or a ``Refusal`` from ``next.realtime.frames`` and ``hints()`` that names the headers it keeps.
``next.realtime.tunnel.DjangoTunnel`` keeps its header lists and limits in class attributes and decides each ``Set-Cookie`` in ``cookie_allowed``, so a subclass extends them without copying the class, and the ``hello`` frame tells the client the extended header list.
The frame fields, the refusal table, and the signatures are in :doc:`/content/ref/realtime`.

Manual streams
--------------

Use ``PatchEventStream`` when an application needs a custom source.
Its source must match the server mode, a synchronous iterable under WSGI or an asynchronous iterable under ASGI.
The page view still owns access checks, and the author owns source cleanup.
A manual stream always travels over SSE, since only ``TopicStream`` answers WebSocket negotiation with a ticket.

See also
--------

.. seealso::

   :doc:`/content/howto/stream-live-updates-with-sse` for a short recipe.
   :doc:`/content/howto/fan-out-across-processes-with-redis` for shared broker setup.
   :doc:`/content/howto/stream-over-websockets` for the WebSocket transport.
   :doc:`/content/ref/realtime` for the broker contract and the replay order.
   :doc:`/content/howto/write-a-broker-backend` for a custom backend with replay and presence.
   :doc:`/content/topics/partial-rendering/sse` for the underlying SSE transport.
