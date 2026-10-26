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
Both shipped brokers keep a bounded log of the newest messages per topic, so a reconnecting subscriber receives what it missed when the log covers the whole gap.
Consumers detect any other gap or an epoch change from the stream metadata and recover current state through their own guarded HTTP request.

``MemoryBroker`` is the default and shares messages among subscribers in one process.
For deployments with multiple workers or hosts, select ``RedisBroker`` so they share publication and sequence state.

API tier
--------

Advanced.
   ``BrokerBackend``, ``MemoryBroker``, ``RedisBroker``, ``ReplayingBroker``, ``Replay``, ``Message``, ``Position``, ``TopicStream``, ``publish``, ``apublish``, ``Presence``, ``PresenceBroker``, ``presence``, ``apresence``, ``BrokerConnectionError``, ``InvalidTopicError``, ``InvalidMemberError``, and ``TopicPayloadError`` are imported from ``next.realtime``.
   The WebSocket side adds ``WebSocketApp``, ``application``, ``TopicSocket``, ``SocketPort``, ``Handshake``, ``TicketClaims``, ``verify_ticket``, ``InvalidTicketError``, ``Close``, and ``TicketIntrospectionView``.
   Upstream events add ``on`` and ``Upstream``.
   ``DjangoTunnel``, ``Tunnel``, and ``TunnelHints`` live in ``next.realtime.tunnel`` for gateway and tunnel authors.
   ``signals``, ``checks``, and ``frames`` are also available as submodules.
   ``TicketBinding``, ``SessionBinding``, and ``SignedCookieBinding`` choose what a ticket is bound to, and ``LiveIdentity``, ``stored_session``, ``session_user``, ``session_alive``, and ``ticket_binding`` are the helpers a binding author uses.
   ``resolve_claims`` and ``resolve_identify`` live in ``next.realtime.tickets`` for gateway authors.
   ``replay_window``, ``replay_after``, and ``INCOMPLETE`` live in ``next.realtime.replay`` for broker authors.

Topic names contain 1 to 200 ASCII letters, digits, underscores, dots, colons, or hyphens.
The ``next:`` prefix belongs to the framework and is rejected.

Configuration
-------------

The ``REALTIME`` key is a mapping in ``NEXT_FRAMEWORK``.
See :doc:`settings` for the complete default, the ``HEARTBEAT`` interval, and the options of every shipped backend.

``REALTIME.BROKER.BACKEND`` names the backend class.
``MemoryBroker`` accepts ``OPTIONS.QUEUE_SIZE``, a positive integer that bounds each subscriber queue.
``RedisBroker`` accepts ``OPTIONS.URL``, ``OPTIONS.PREFIX``, ``OPTIONS.HEALTH_CHECK_INTERVAL``, and ``OPTIONS.CLIENT`` and requires the Redis optional dependency from the ``broker`` extra, ``redis`` 8 or newer.
Without it, selecting the backend is reported by ``manage.py check`` as ``next.E179``.
``REALTIME.REPLAY`` sizes the replay log of both brokers, see `Replay`_.
``REALTIME.PRESENCE`` limits the attendance snapshots of both brokers, see `Presence`_.

The in-memory backend is process-local.
A multi-process production deployment must use a shared backend such as Redis, because the framework does not infer process count from the server configuration.

``OPTIONS.CLIENT`` is a mapping of keyword arguments passed whole to the Redis ``from_url`` call, for the synchronous and the asynchronous client.
It overrides the defaults ``health_check_interval`` and ``socket_keepalive``, while ``decode_responses`` is always ``True``.
A subclass that overrides ``RedisBroker.create_client(url, options, *, asynchronous)`` reaches Redis through Sentinel or Cluster.
A pool that raises ``redis.exceptions.MaxConnectionsError`` logs a warning on the ``next.realtime.redis`` logger.
A ``BlockingConnectionPool`` raises a plain ``ConnectionError`` on timeout instead, so its exhaustion ends the stream with ``BrokerConnectionError`` and logs no warning.

``RedisBroker.topic_key(kind, topic)`` names the keys of a topic as ``<PREFIX>:<kind>:{<topic>}``.

.. list-table::
   :header-rows: 1
   :widths: 22 78

   * - Kind
     - Holds
   * - ``seq``
     - The counter of the topic and its epoch.
   * - ``log``
     - The replay log.
   * - Presence kinds
     - Eight more kinds name the attendance keys, see `Presence`_.

The braces are a Redis Cluster hash tag that puts the keys of a topic in one slot.
The publish script, the replay transaction, and the presence scripts touch those keys together.
An override keeps that property and returns a distinct key for each ``kind``.
``PREFIX`` therefore needs a non-colon character and no ``{`` or ``}``, because a brace in it would move the hash tag off the topic name.
The broker raises ``ImproperlyConfigured`` for any other value, which ``manage.py check`` reports earlier as ``next.E192``.
The Pub/Sub channel stays ``<PREFIX>:topic:<topic>``.

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
   * - ``broadcast(topic, envelope)``
     - Delivers the envelope to live subscribers only, unnumbered and never logged.
       The default raises ``NotImplementedError``, and a broker that keeps presence overrides it.
   * - ``positions(topics)`` and ``apositions(topics)``
     - Return a ``Position`` per topic in one batched read.
       A topic never published answers ``Position(epoch="", seq=0)``.
   * - ``close()`` and ``aclose()``
     - Release connections and threads.
       Both default to doing nothing, and ``aclose`` calls ``close``.
       The broker manager calls ``close`` when the framework settings reload.

A stream enters the subscription first, reads the positions second, replays what its cursor missed third, and then sends ``hello``.
A message published after the subscription appears in the iterator and may also be counted in the positions or replayed, which the client treats as a duplicate rather than as a gap.
A subscription that cannot guarantee delivery any more, after a reconnect or an unanswered liveness ping, raises ``BrokerConnectionError`` from its iterator.
A backend also translates its own transport failures into ``BrokerConnectionError``, so a stream catches one error type.
The SSE stream then ends without a terminal frame so the browser reconnects and receives a fresh ``hello``, and the WebSocket closes with ``1013``.

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
The skeleton keeps no replay log, so with a nonzero ``REPLAY['SIZE']`` the check warns with ``next.W143``.
:doc:`/content/howto/write-a-broker-backend` adds the log and walks through a ``RedisBroker`` subclass.

Replay
------

A reconnecting subscriber states the cursor it stopped at, and the stream sends the messages after it before ``hello``.
Replay is a bounded best-effort catch-up rather than a delivery guarantee, so ``hello`` and the zone resync it triggers stay the source of truth.

The replay log
~~~~~~~~~~~~~~

``REALTIME['REPLAY']`` holds ``SIZE``, the number of newest messages kept per topic, and ``TTL``, the seconds the log of a topic lives after its last publication.
The defaults are ``100`` and ``300``.
``SIZE`` set to ``0`` turns the log off.
``SIZE`` is capped at ``10000`` messages, and ``TTL`` is capped at ``31536000`` seconds, one year.
A replay reads and copies the whole log of a topic.
That is ``LRANGE`` over every entry on Redis and a copy under the broker lock in memory, so the size cap bounds that read and the memory a topic holds.
A broker reads the window once when it is created, so a settings reload rebuilds the broker with the new values.
A ``replay`` keyword on either broker constructor takes the same mapping and wins over the setting, so a process without Django settings can build a broker.
A window that is not a mapping, carries another key, or holds an invalid value raises ``ImproperlyConfigured`` when the broker is created.
``manage.py check`` reports it earlier.
A value that is not a mapping is ``next.E191``, another key is ``next.E035``, and an invalid ``SIZE`` or ``TTL`` is ``next.E189`` or ``next.E190``.

``MemoryBroker`` keeps the newest ``SIZE`` messages of each topic in the process and drops the whole log once ``TTL`` seconds pass without a publication on the topic.
A publication on a topic whose log has already expired starts a new log, as ``PEXPIRE`` does on Redis, and the first publication a ``TTL`` after the last sweep drops the expired logs of every topic.
An idle log therefore holds its memory for two ``TTL`` periods at most while the broker keeps publishing.
A topic idle for ``TTL`` seconds reports the position ``("", 0)``, and its next publication opens a new epoch at ``seq`` ``1``.
A live client adopts the first message of that new epoch without a resync, while a client that reconnects with a cursor that no longer matches the position resyncs its zones.
Its ``clock`` keyword, ``time.time`` by default, stamps ``published_at`` and times the expiry from the same reading, so a test passes a fake one.
``RedisBroker`` writes the log in the same Lua script that numbers and publishes a message, so the three steps are atomic, and the script publishes only after the log write.
The log is a list trimmed to ``SIZE`` entries whose expiry each publication pushes out by ``TTL``, the sequence key expires with it, and the script deletes the log when the topic starts a new epoch.
With ``SIZE`` set to ``0`` the script never touches the log key.
``replay`` reads the counter and the log in one ``MULTI`` transaction.

The resume order
~~~~~~~~~~~~~~~~

SSE and WebSocket run the same order for every connection.

#. The stream subscribes to the broker.
#. It reads the positions of its topics, the snapshot that ``hello`` announces.
#. For each topic whose cursor trails the snapshot in the same epoch, it replays the messages after the cursor up to the snapshot position.
   Every replayed message passes through the same transform and the same access check as a live one, so a filtered message becomes a ``skip`` and a revoked grant ends the stream.
#. It sends ``hello``.
#. It sends live messages.
   A message published after the subscription waits in the subscription queue, and one already replayed arrives again for the client to drop by ``seq``.

A topic replays nothing when its cursor names another epoch or runs ahead of the snapshot.
It replays nothing either when the log lacks any message between the cursor and the snapshot, because it was trimmed, expired, or lost with a broker restart.
The same holds for a broker that is no ``ReplayingBroker``, for ``SIZE`` set to ``0``, and for a replay that raises ``BrokerConnectionError``.
Replay is all or nothing per topic, so the client never receives a partial run that looks contiguous.
In each case the client compares ``hello`` with its cursor and resynchronises its zones as it does for any gap.

The replaying broker contract
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``ReplayingBroker`` is a runtime-checkable ``Protocol`` with two methods, so a broker supports replay by defining them rather than by subclassing.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Member
     - Contract
   * - ``replay(topic, after)`` and ``areplay(topic, after)``
     - Take a ``Position`` cursor and return a ``Replay`` of the logged messages numbered after it, in ascending order.
       ``areplay`` runs on the event loop, so a broker that only reads memory calls ``replay`` from it.
       A transport failure raises ``BrokerConnectionError``, and the stream then skips replay for that topic.
   * - ``Replay.messages``
     - The tuple of messages after the cursor.
   * - ``Replay.complete``
     - Whether the messages close the whole gap from the cursor to the current position of the topic.
       A stream discards an incomplete answer and leaves the gap to the resync.

The stream cuts a complete answer at its position snapshot, since later messages already wait in the subscription.

Resuming over SSE
~~~~~~~~~~~~~~~~~

A ``TopicStream`` with exactly one topic writes ``id: <epoch>:<seq>`` before every message frame and every ``skip`` frame, replayed ones included, and before ``hello`` with the position of the topic.
A reconnect before the first message therefore resumes from the position ``hello`` announced.
A topic that was never published has no position to name, so its ``hello`` carries no ``id``, and ``end`` frames never carry one.
When ``EventSource`` reconnects on its own, it sends the last id back as ``Last-Event-ID`` and the stream replays from that cursor.
The header is parsed strictly as sixteen lower-case hex digits, a colon, and a decimal ``seq`` without leading zeros, at most 40 characters in all.
Any other value counts as no cursor, and the stream resynchronises from ``hello``.
Both shipped brokers draw epochs in that form, and a broker whose epochs take another form gets no ids.

A stream over several topics writes no ``id``, because one id cannot hold a cursor per topic, so it resumes through the resync alone.

Three methods are the override points.
``PatchStreamWriter.event_id(frame)`` in ``next.partial.sse`` receives a message or ``skip`` frame as a ``StreamPosition``, or the ``StreamHello``, and returns its id, ``None`` by default, and the writer drops an id holding CR, LF, or NUL.
``TopicStream.event_id(frame)`` returns the ``<epoch>:<seq>`` form for a single-topic stream.
``TopicStream.replay_cursors(request)`` returns the mapping of topic to ``Position`` the stream replays from, so a subclass that writes its own ids reads them back there.
The stream calls it once the instance is built, so an override can read ``self.topics``.

Resuming over WebSocket
~~~~~~~~~~~~~~~~~~~~~~~

The ``last_seq`` mapping of the ``sub`` frame is the cursor.
An entry in ``{"epoch": "...", "seq": 7}`` form replays, while a bare integer ``seq`` names no epoch and replays nothing.
The browser client sends its cursor in the object form on every reconnect.
A subclass changes the replay step through the ``TopicSocket.catch_up`` hook that `Serving the socket`_ describes.

Presence
--------

A stream that passes ``presence`` to ``TopicStream`` records its connection on each of its topics while it is open, and the broker publishes the attendance of a topic as a message on the topic itself.
:doc:`/content/topics/realtime` explains the feature from the page author's side.

The ``presence`` parameter
~~~~~~~~~~~~~~~~~~~~~~~~~~

``TopicStream(request, *topics, transform=None, authorize=None, presence=False, heartbeat_seconds=None)`` takes ``presence`` as one of three values.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Value
     - Meaning
   * - ``False``
     - The stream takes no part in presence.
   * - ``True``
     - Count mode.
       Every connection counts, and no data about people is stored or sent.
   * - ``identify`` or its dotted path
     - Member mode.
       ``identify(user, topic, subject)`` returns a mapping of JSON values or ``None``, and the mapping is shown to every subscriber of the topic that identifies its own members too.
       ``identify(user, topic)`` without ``subject`` works as well, see `The identify callable`_.

``transform``, ``authorize``, and ``presence`` accept a callable or its dotted path, and a path is imported when the stream is built.
A path that does not import or does not name a callable raises ``ImproperlyConfigured``, and so does a ``presence`` that is no bool, callable, or string, such as ``None`` or ``0``.
A stream that asks for presence on a broker without it raises ``ImproperlyConfigured`` when it is built, under WSGI and ASGI alike.
A callable passed directly reaches a WebSocket ticket through its import path, and one without a stable path such as a lambda keeps the stream on SSE with the reason ``transform``, ``authorization``, or ``presence``.

The stream joins after it read the topic positions and before it sends the replayed messages and ``hello``, and right after ``hello`` it sends the current snapshot of every topic.
It refreshes its record once per heartbeat and leaves in the ``finally`` of the generator, on a disconnect, a cancellation, and a revoked grant alike.
A broker failure on leave is logged on ``next.realtime.attendance``, the other topics are still left, and the time to live removes the record.
An asynchronous leave waits at most one heartbeat, so a broker that hangs never holds the close, and the time to live removes what it did not confirm.

The identify callable
~~~~~~~~~~~~~~~~~~~~~

``subject`` is the opaque id of the connection's user or visitor, the ``Handshake.subject`` and ``Upstream.subject`` of a WebSocket and the same digest an SSE stream derives from the cookies of its request.
It is a keyed digest that is stable across reconnects and across both transports of one browser, never yields the credential back, and is empty when the cookies prove no subject.
Under ``SessionBinding`` it follows the session, so it changes when the session key rotates, at login for one.
Under ``SignedCookieBinding`` a signed-in user keeps one subject on every device and an anonymous browser keeps the visitor cookie's subject, which a WebSocket negotiation sets, so a guest is the same member after a reload.
A stream whose request carries no session or visitor cookie gets an empty ``subject``, and an ``identify`` returns ``None`` for it to count that connection without a member.

The signature of a hashable callable is read once for all streams and sockets, ``next.realtime.attendance.Identifier`` holds the result, and a callable that accepts a third positional argument gets the subject.
A ``*args`` signature or an optional third parameter counts as accepting it, and a decorator should apply ``functools.wraps`` so the read sees the wrapped signature.
An SSE stream derives the subject only for such a callable, so a two-argument ``identify`` costs no extra session read.

.. code-block:: python
   :caption: rooms/realtime.py

   def identify(user, topic, subject):
       """Name signed-in users and give each guest a short stable tag."""
       if user.is_authenticated:
           return {"name": user.get_full_name()}
       return {"name": f"Guest {subject[:4]}"} if subject else None

``TopicStream.attend(backend)``, ``TopicStream.aattend(backend)``, and ``TopicSocket.attend(broker, claims)`` are the hooks that build the ``Attendance`` of a connection from ``next.realtime.attendance``.
An override returns another ``Attendance``, such as a subclass with its own ``ttl_heartbeats``, or ``None`` to keep the connection out of the roster.

The attendance snapshot
~~~~~~~~~~~~~~~~~~~~~~~

``Presence(count, members=())`` is the frozen snapshot a broker returns.
``count`` is the number of distinct members in member mode plus every connection without a member, and the number of connections in count mode.
Connections whose members have the same canonical JSON, with keys sorted and compact separators, are one member.
``members`` holds at most ``REALTIME['PRESENCE']['SIZE']`` members in the order of their first join, and ``count`` stays exact beyond that cap.
A member that leaves entirely and comes back joins the end of the order.
``Presence.as_detail()`` returns the detail of the event, ``{"count": n}`` without members and ``{"count": n, "members": [...]}`` with them, so the key is absent when no identified member attends or ``SIZE`` is ``0``.

The event is an ``event`` operation named ``next:presence`` that the broker broadcasts unnumbered through ``BrokerBackend.broadcast``.
It takes no ``seq``, never enters the replay log, and fires ``message_published`` with ``ephemeral=True`` and a message whose ``seq`` is ``0``.
It passes the access check but skips the subscriber ``transform``.
The stream cuts ``members`` from every ``next:presence`` event, numbered or not and wherever it sits among the operations, for every subscriber that does not identify its own.
An SSE frame of it has no ``id`` line and a WebSocket ``patches`` frame of it has no ``seq`` or ``epoch``, so a client applies it without moving its cursor or counting a gap.
A client listens with ``Next.partial.stream.on(topic, "next:presence", callback)``.

``presence(topic)`` and ``await apresence(topic)`` read the current ``Presence`` of a topic from the configured broker, never an expired record.
Both validate the topic name and raise ``ImproperlyConfigured`` when the broker has no presence.

The coalescing window
~~~~~~~~~~~~~~~~~~~~~

A change that matters claims a window of ``REALTIME['PRESENCE']['WINDOW']`` seconds on its topic, and the broker publishes one snapshot when the window closes.
A change inside an open window claims nothing, and its effect is in the snapshot because the snapshot is read at the close.
A join of a connection that is new, or that moved to another member, claims a window.
A leave or an expiry claims one only when the member left entirely.
One daemon thread per broker times the windows for WSGI and ASGI alike, starts on the first window, and exits when none is pending.
Every change raises a version that the published snapshot records, and a join, a refresh, or a leave that finds the version unannounced claims a free window, so a snapshot outlives the death of the process that held the window.
``RedisBroker`` reads and publishes a snapshot in one script, so snapshots of one topic leave in the order of their changes.
A leave that empties its topic while another process holds the window schedules the snapshot for the close of that window, since no later refresh would claim it.
A failure to publish a snapshot is logged on ``next.realtime.roster`` and the topic is tried again a window later.
``close()`` publishes the pending snapshots at once and stops the thread, and a forked child starts with a fresh schedule.

The record of a connection lives ``Attendance.ttl_heartbeats`` heartbeat intervals, three by default, and every refresh pushes the deadline out.
The scripts and the memory roster prune the expired records on a join, a refresh, and a leave, and a prune that removes a member claims a window.
A refresh that finds its record pruned joins again.

The ``PresenceBroker`` contract
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``PresenceBroker`` is a runtime-checkable ``Protocol`` like ``ReplayingBroker``, so a broker supports presence by defining its methods.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Member
     - Contract
   * - ``join(topic, connection, member, ttl)`` and ``ajoin``
     - Record a connection until ``ttl`` seconds pass without a touch.
       ``member`` is a mapping or ``None``, and a broker encodes it with ``encode_member`` from ``next.realtime.roster``, which raises ``InvalidMemberError`` for a member that is no mapping with string keys, holds a value JSON cannot carry, or exceeds ``MEMBER_BYTES``.
       Joining a known connection replaces its member.
   * - ``touch(topic, connection, ttl)`` and ``atouch``
     - Push the deadline out and return ``False`` when the connection holds no record, so the caller joins again.
   * - ``leave(topic, connection)`` and ``aleave``
     - Forget the connection.
   * - ``presence(topic)`` and ``apresence``
     - Return the attendance snapshot as a ``Presence`` and never an expired attendee.

A transport failure raises ``BrokerConnectionError``.
A broker keeps the coalescing itself with the helpers of ``next.realtime.roster``.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Helper
     - Role
   * - ``PresenceWindow``
     - A mixin that holds ``presence_limits`` and ``presence_schedule``.
       Its schedule calls the ``announce_presence(topic)`` hook once a window closes.
   * - ``presence_limits()``
     - Reads the ``PRESENCE`` limits.
   * - ``PresenceSchedule``
     - Coalesces the changes of a topic into one window.
   * - ``announce(broker, topic)``
     - Sends the snapshot through ``broker.broadcast``, because a snapshot must reach live subscribers unnumbered.
   * - ``encode_member()`` and ``decode_member()``
     - Convert a member mapping to and from its stored form.

:doc:`/content/howto/write-a-broker-backend` builds one on top of ``RedisBroker``.

``MemoryBroker`` keeps a roster per topic and prunes by the earliest deadline, so a refresh costs the same on a large roster.
``RedisBroker`` keeps eight keys per topic under the hash tag.

.. list-table::
   :header-rows: 1
   :widths: 22 78

   * - Kind
     - Holds
   * - ``presence``
     - The set of connections without a member.
   * - ``members``
     - The members hash.
   * - ``window``
     - The coalescing lock.
   * - ``state``
     - The hash of the change version.
   * - ``identified``, ``attendees``, ``holders``, ``order``
     - The indexes for members.

A script extends a key to twice the time to live only once less than one time to live is left.
Every script path leaves each of the eight keys with a time to live, so the keys of an emptied topic expire.
Its Lua scripts read the Redis ``TIME``, so hosts with different clocks agree on every deadline.
Both constructors take a ``presence`` keyword with the same mapping as the setting, and a broker reads it once when it is created.
Both take a ``schedule`` keyword too.
It is a callable that receives the ``announce_presence`` hook and returns the ``PresenceSchedule``.
An override of ``announce_presence`` observes or reroutes every snapshot.

The limits
~~~~~~~~~~

``REALTIME['PRESENCE']`` holds ``WINDOW``, ``SIZE``, and ``MEMBER_BYTES``.
The defaults are ``1.0``, ``100``, and ``512``.
``WINDOW`` is a positive number of seconds up to ``60``, ``SIZE`` an integer from ``0`` to ``1000``, and ``MEMBER_BYTES`` an integer from ``1`` to ``4096``.
A mapping with another key or an invalid value raises ``ImproperlyConfigured`` when the broker is created.
``manage.py check`` reports a value that is not a mapping as ``next.E193`` and another key as ``next.E035``.
An invalid ``WINDOW``, ``SIZE``, or ``MEMBER_BYTES`` is ``next.E194``, ``next.E195``, or ``next.E196``.
An explicit ``PRESENCE`` with a broker that is no ``PresenceBroker`` is ``next.W144``.

Public API
----------

.. autoclass:: next.realtime.BrokerBackend
   :members:

.. autoclass:: next.realtime.MemoryBroker
   :members:

.. autoclass:: next.realtime.RedisBroker
   :members:

.. autoclass:: next.realtime.ReplayingBroker
   :members:

.. autoclass:: next.realtime.Replay
   :members:

.. autoclass:: next.realtime.Message
   :members:

.. autoclass:: next.realtime.Position
   :members:

.. autoclass:: next.realtime.PresenceBroker
   :members:

.. autoclass:: next.realtime.Presence
   :members:

.. autoclass:: next.realtime.TopicStream
   :members:

.. autofunction:: next.realtime.publish

.. autofunction:: next.realtime.apublish

.. autofunction:: next.realtime.presence

.. autofunction:: next.realtime.apresence

.. autofunction:: next.realtime.on

.. autoclass:: next.realtime.Upstream
   :members:

.. autoexception:: next.realtime.BrokerConnectionError
   :members:

.. autoexception:: next.realtime.InvalidTopicError
   :members:

.. autoexception:: next.realtime.InvalidMemberError
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
#. The server verifies the ticket against the credential of the handshake and imports the transform and authorizer it names.
   A ticket that is forged, expired, bound to another session, or names a callable that no longer imports ends in an ``auth`` error and ``4401``.
#. The server checks the session and the authorizer for every topic.
   A revoked session ends in an ``auth`` error and ``4401``, and a denied topic ends in an ``auth`` error and ``4403``.
#. The server subscribes to the broker, reads the positions of the topics, sends the messages the ``last_seq`` cursor missed, and sends ``hello``, see `Replay`_.
   A message published after the subscription arrives after ``hello`` as well, and the client treats it as a duplicate rather than a gap.

From then on the server sends ``patches`` and ``skip`` frames as messages arrive, and a ``ping`` once a whole heartbeat passes with nothing to send or with no frame from the client, however busy the topic.
Any frame from the client answers the ping, and a peer that sent nothing for more than two heartbeats after an unanswered ping is dropped without a close frame, leaves presence, and fires ``stream_unsubscribed`` with the reason ``client``.
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
   * - ``up``
     - ``{"type": "up", "topic": "room:42", "name": "typing", "data": {...}, "id": "7"}``
     - A client event for the handler of ``topic`` and ``name``, see `Upstream events`_.
       ``topic`` and ``name`` are strings, ``data`` is any JSON value, and ``id`` is an optional string of 1 to 64 characters.
       Only a frame with an ``id`` gets an ``ack``.
       Any other key or type closes with ``4400``.
   * - ``http``
     - ``{"type": "http", "id": "8", "method": "POST", "path": "/_next/form/<uid>/", "headers": {...}, "body": "..."}``
     - A form action request the server runs as an HTTP request, see `Request tunnel`_.
       ``id`` is a required string of 1 to 64 characters, ``method`` and ``path`` are strings, ``headers`` is an optional object of string values, and ``body`` is an optional string.
       Any other key or type closes with ``4400``, since the server cannot answer a frame without its ``id``.
   * - any other ``type``
     - a JSON object with an unknown or missing ``type``
     - The server answers ``{"type": "error", "code": "unsupported"}`` and keeps the socket open.
       This includes a second well-formed ``sub``.

Frames that are not JSON objects close with ``4400``.
That covers an empty text frame, invalid JSON, a JSON value that is not an object, and every binary frame.
A frame over 64 KiB closes with ``1009``.

The server replays what the ``last_seq`` cursor missed when the replay log covers the gap, and then sends the current positions in ``hello``.
The client compares the positions with its own cursor and resynchronises through the guarded zone GET whenever replay left a gap, see :doc:`/content/topics/realtime`.

Server frames
~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 14 46 40

   * - Frame
     - Shape
     - Meaning
   * - ``hello``
     - ``{"type": "hello", "protocol": 2, "server_time": 1761300000000, "topics": {"room:42": {"epoch": "9f2c41d07a3be851", "seq": 17}}, "transports": ["ws", "sse"], "heartbeat": 25.0, "connection": "kq3Vx0b9cE2mTnYfJw1sPA"}``
     - The subscription is live.
       It carries the keys of the SSE ``next-hello`` frame plus ``type`` and ``heartbeat``, the server interval in seconds, ``connection``, and the ``upstream`` and ``tunnel`` limits below.
       A client sizes its missed-ping watchdog from ``heartbeat``.
       ``connection`` is the ``TopicSocket.connection_id`` of the socket, the ``Upstream.connection`` its handlers see, see `The connection id`_.
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
     - A heartbeat on an idle subscription, or a probe of a client silent for a whole heartbeat.
       The client answers with ``pong``.
   * - ``ack``
     - ``{"type": "ack", "id": "7", "ok": true}`` or ``{"type": "ack", "id": "7", "ok": false, "error": "topic"}``
     - The outcome of an ``up`` frame with an ``id``, see `Upstream events`_.
   * - ``http``
     - ``{"type": "http", "id": "8", "status": 200, "headers": {...}, "body": "..."}`` or ``{"type": "http", "id": "8", "status": 0, "error": "limit", "retry": true}``
     - The answer to an ``http`` frame, an HTTP reply or a refusal with ``status`` ``0``, see `Request tunnel`_.
   * - ``error``
     - ``{"type": "error", "code": "protocol"}``
     - ``code`` is ``protocol``, ``auth``, ``unsupported``, or ``limit``.
       ``protocol`` and ``auth`` precede a close with ``4400``, ``4401``, or ``4403``, while ``unsupported`` leaves the socket open.
       ``limit`` refuses an ``up`` frame without an ``id`` and precedes the close with ``4429``.
       Every frame after ``sub`` draws a token, so a ``pong`` or an unsupported frame that finds the bucket empty gets no answer and counts towards ``4429`` as well.

The connection id
~~~~~~~~~~~~~~~~~

``connection`` in the WebSocket ``hello`` is ``TopicSocket.connection_id``, 16 random bytes in URL-safe base64 drawn when the socket is built.
It names one socket and nothing else, it derives from no session, user, or ticket, and a reconnect draws a new one.
The same string is ``Upstream.connection`` in every handler of the socket, the ``connection`` of ``upstream_rejected``, and the key of its presence record.
A page reads it from ``Next.partial.stream.meta().connection``, so an upstream handler that copies it into the data it publishes lets the sending tab drop its own echo, see :doc:`/content/topics/realtime`.
Knowing another socket's id grants nothing, as no frame or endpoint takes a connection id from a client.
An SSE stream has no ``hello`` key of that name, because it carries no upstream events.

The WebSocket ``hello`` tells the client the limits it builds its frames by.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Key
     - Value
   * - ``upstream.frame``
     - ``UPSTREAM['MAX_BYTES']``, the largest ``up`` frame in UTF-8 bytes.
       Always present.
   * - ``tunnel.timeout``
     - ``TUNNEL['TIMEOUT']`` in seconds, after which the server answers ``timeout``.
   * - ``tunnel.frame``
     - ``frames.MAX_INBOUND_BYTES``, the largest ``http`` frame in UTF-8 bytes.
   * - ``tunnel.headers``
     - The sorted request header names the tunnel keeps, lowercase, ``DjangoTunnel.request_headers`` by default.
   * - ``tunnel.prefixes``
     - The header name prefixes the tunnel keeps, ``DjangoTunnel.request_prefixes`` by default.
   * - ``tunnel.csrf``
     - The lowercase header ``CSRF_HEADER_NAME`` stands for, such as ``x-csrftoken``, or ``null`` when the setting names a plain ``META`` key.

``tunnel`` is present only when the socket has a tunnel and ``REALTIME['TUNNEL']`` is not ``False``, so its absence means every ``http`` frame answers ``disabled``.
With the defaults the frame ends in ``"upstream":{"frame":16384},"tunnel":{"timeout":30.0,"frame":65536,"headers":["accept","content-type"],"prefixes":["x-next-"],"csrf":"x-csrftoken"}``.
A client sends a header outside ``headers``, ``prefixes``, and ``csrf`` over HTTP, since the tunnel drops it.

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
     - An ``OSError`` or an unexpected error ends the stream.
       The client reconnects with backoff.
   * - ``1013``
     - ``TRY_AGAIN``
     - The broker subscription is lost.
       The client reconnects with backoff.
       A gateway may send it to a client that stopped reading, which the built-in socket drops without a close frame.
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
     - Three frames in a row found the token bucket empty, see `Upstream events`_.
       An ``error`` frame with the ``limit`` code precedes it.

The browser client decides by the close code rather than the ``error`` frame before it, falling back to SSE for the life of the page on ``4403`` and negotiating a fresh ticket once on ``4401`` before it does.
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
   * - Frames after ``sub``
     - 10 a second with a burst of 20 by default
     - ``REALTIME['UPSTREAM']['RATE']`` and ``['BURST']``
   * - One ``up`` frame
     - 16 KiB of UTF-8 by default
     - ``REALTIME['UPSTREAM']['MAX_BYTES']``
   * - Concurrent ``up`` events
     - 16 by default
     - ``REALTIME['UPSTREAM']['CONCURRENCY']``
   * - Wait for a handler
     - 10 seconds by default
     - ``REALTIME['UPSTREAM']['TIMEOUT']``
   * - ``id`` of an ``up`` or ``http`` frame
     - 64 characters
     - ``frames.MAX_ID_LENGTH``
   * - Tunneled requests at once
     - 4 by default
     - ``REALTIME['TUNNEL']['CONCURRENCY']``
   * - Tunneled request time
     - 30 seconds by default
     - ``REALTIME['TUNNEL']['TIMEOUT']``
   * - Tunneled reply body
     - 1 MiB by default
     - ``REALTIME['TUNNEL']['MAX_REPLY_BYTES']``

Upstream events
~~~~~~~~~~~~~~~

Every frame after ``sub`` draws one token from a bucket per connection, which holds ``UPSTREAM['BURST']`` tokens and refills at ``UPSTREAM['RATE']`` a second.
A frame that finds the bucket empty is refused at once, an ``up`` with ``{"type": "ack", "ok": false, "error": "limit"}`` or, without an ``id``, with an ``error`` frame of the ``limit`` code, and an ``http`` with a ``limit`` refusal.
Three refusals in a row with no admitted frame between them close the socket with ``4429``, ``TopicSocket.strikes`` in a subclass.
A reconnect starts a fresh bucket, so a client that backs off before reconnecting gets its burst again.
A frame sent before ``hello`` waits until the ticket is verified and its topics granted.

An admitted ``up`` frame is checked in this order, and the first failure is the ``error`` of its ``ack``.

.. list-table::
   :header-rows: 1
   :widths: 16 84

   * - ``error``
     - When
   * - ``too_big``
     - The frame exceeds ``UPSTREAM['MAX_BYTES']`` bytes of UTF-8.
   * - ``topic``
     - ``topic`` is not a topic of the ticket.
   * - ``denied``
     - The session or the authorizer refuses the ticket on the throttled access recheck.
       The socket then closes as on any failed recheck, with ``4401`` or ``4403``.
   * - ``unknown``
     - No ``@on`` handler serves the topic and the name.
   * - ``timeout``
     - The handler ran past ``UPSTREAM['TIMEOUT']``.
       The event may still take effect, since a sync handler keeps running in its thread, so a client does not resend it blindly.
   * - ``internal``
     - The handler raised, which is logged on ``next.realtime.socket``.
   * - ``limit``
     - The bucket was empty, or ``UPSTREAM['CONCURRENCY']`` events of the connection are still running.
       Only the bucket counts towards ``4429``.

Every refusal in the table, and the frame that closes the socket with ``4429``, fires ``upstream_rejected`` with the ``error`` as ``reason``, see :doc:`signals`.
``{"type": "ack", "id": ..., "ok": true}`` means the handler ran to completion.
Each admitted frame runs as its own task, so acks may arrive in another order than the frames.
The recheck runs at most once per heartbeat, so an ``up`` frame may still reach its handler up to one heartbeat after the session or the grant is revoked.
``TopicSocket.access_check(claims, authorizer)`` builds that recheck, and an override with a shorter interval tightens it.
A sync handler runs on a thread pool of its own, sized like the default executor, so a slow handler never queues the session loads and transforms of other sockets.
``TopicSocket.handle(upstream)`` routes a granted event and returns whether anything served it, and an override replaces the ``@on`` registry with routing of its own.
``Upstream.user`` is the user the last access recheck loaded, not the one of the handshake, so a revoked permission reaches the handler after the next recheck.

Request tunnel
~~~~~~~~~~~~~~

An ``http`` frame runs a form action through the same ASGI application ``application()`` serves, as a ``POST`` with the cookies, host, origin, and proxy headers of the WebSocket handshake.
Middleware, CSRF, sessions, guards, and ``FormActionDispatch`` see an ordinary request, and ``request.scope["next.transport"]`` is ``"ws"``.
The session keeps its engine and class, and during the action only ``cycle_key()``, ``flush()``, and their async forms are replaced on the instance with a refusal.
The refusal starts at the dispatch, because ``SessionMiddleware`` builds the session inside the Django handler, out of the tunnel's reach.
Middleware that reads ``request.user`` before the view therefore still rotates a session whose hash only matches a ``SECRET_KEY_FALLBACKS`` entry.
That reply sets a new session cookie, so it becomes the ``http_only`` refusal after the action has run, and the browser keeps the cookie of the replaced session.
An action behind such middleware keeps ``transport="http"`` while ``SECRET_KEY_FALLBACKS`` holds the previous key.
Only an action declared with ``transport="any"`` runs, and the other actions answer ``421`` with ``X-Next-Transport: http`` before their guards.
The envelope of a reply is the one HTTP gets, so a zone morph carries the zone's ``context`` op and the page applies it through ``context-updated``, see :doc:`/content/topics/partial-rendering/extending`.

The frame contributes only ``content-type``, ``accept``, the header ``CSRF_HEADER_NAME`` names, and ``x-next-*`` headers, and every other header of the frame is dropped.
A request may carry at most 32 headers, each name an RFC 7230 token and each value at most 4096 Latin-1 characters without CR, LF, or NUL.
``DjangoTunnel.max_headers``, ``max_header_value``, ``max_path``, and ``grace`` are class attributes a subclass changes.
The body is sent as UTF-8 with a computed ``content-length``.
The server takes from the handshake every header except the hop-by-hop and ``sec-websocket-*`` headers, the ones the frame supplies, ``content-length``, ``accept-encoding``, ``if-*``, ``range``, ``expect``, and ``referer``.
The scheme is ``https`` for a ``wss`` socket, so ``SECURE_PROXY_SSL_HEADER`` and ``USE_X_FORWARDED_HOST`` behave as over HTTP.

A reply carries its status, the ``content-type``, ``location``, and ``x-next-*`` headers with repeated values joined by ``", "``, and the body decoded as UTF-8 with replacement.
Only a reply to a request that reached ``FormActionDispatch`` passes, so a middleware that swaps ``request.urlconf`` or answers on its own never leaks another view through the socket.
Such a reply is the answer of the action, so a ``PermissionDenied`` or ``Http404`` raised by a guard or a handler arrives with its ``403`` or ``404`` as over HTTP.
A redirect is not followed and arrives as its ``3xx`` with ``location``.
A ``Set-Cookie`` that repeats a cookie of the handshake with the same value and no deletion is dropped, and any other ``Set-Cookie`` turns the reply into the ``http_only`` refusal, logged as a warning that names the cookies and never their values.
A ``set_expiry()`` call in a tunneled action moves the expiry the store keeps, while the browser cookie keeps its lifetime, so an action that changes how long the browser holds the session keeps ``transport="http"``.
``DjangoTunnel.cookie_allowed(name, morsel)`` decides each cookie, and an override admits a cookie the client does not need, such as an analytics marker.

A refusal has ``status`` ``0``, an ``error`` code, and ``retry``.
``retry`` is ``true`` only when the request never reached the action dispatch, or reached it only to get the ``421`` of an HTTP-only action, so a client may send it again over HTTP.

.. list-table::
   :header-rows: 1
   :widths: 16 10 74

   * - ``error``
     - ``retry``
     - When
   * - ``disabled``
     - ``true``
     - The socket has no application to run requests through, or ``REALTIME['TUNNEL']`` is ``False``.
   * - ``limit``
     - ``true``
     - The bucket was empty, or ``TUNNEL['CONCURRENCY']`` requests of the connection are still running.
       Only the bucket counts towards ``4429``.
   * - ``method``
     - ``true``
     - The method is not ``POST``.
   * - ``path``
     - ``true``
     - The path is not ASCII up to 2048 characters starting with a single ``/`` and free of spaces, quotes, ``#``, and ``\``, or it falls outside the ``root_path`` of the socket.
   * - ``headers``
     - ``true``
     - A header breaks the rules above.
   * - ``body``
     - ``true``
     - The body is not valid UTF-8, or ``content-type`` is neither ``application/x-www-form-urlencoded`` nor ``application/json``, multipart included.
   * - ``stale``
     - ``true``
     - The handshake carried no ``CSRF_COOKIE_NAME`` cookie while ``CSRF_USE_SESSIONS`` is off, or a ``403`` came before the request reached the dispatch, which is how the CSRF check refuses a token rotated after the handshake.
       The client then reconnects the socket for fresh cookies and sends the request over HTTP.
   * - ``transport``
     - ``true``
     - The path does not resolve to the form action dispatch, the action answered ``421`` with ``X-Next-Transport: http``, or another reply below ``500`` came before the request reached the dispatch.
   * - ``http_only``
     - ``false``
     - The reply sets a cookie the socket cannot deliver, such as a flashed message in a cookie or a new cookie.
       Or the action rotated or flushed the session, a login or a logout, which the dispatch refuses before the store writes and answers with ``X-Next-Transport: session``.
   * - ``too_big``
     - ``false``
     - The reply body exceeds ``TUNNEL['MAX_REPLY_BYTES']``.
   * - ``timeout``
     - ``false``
     - The request ran past ``TUNNEL['TIMEOUT']``.
   * - ``encoding``
     - ``false``
     - The reply carries ``content-encoding``.
   * - ``internal``
     - ``false``
     - The application raised or broke the ASGI protocol, which is logged on ``next.realtime.tunnel``, or answered ``500`` or above before the request reached the dispatch.

A timeout or a closing socket ends the request with ``http.disconnect`` and waits ``DjangoTunnel.grace``, one second, for the application.
The application is never cancelled, because a cancel inside Django's thread-sensitive context blocks the event loop until the view thread returns.
A synchronous view keeps running in its thread after the disconnect, so a ``timeout`` or ``http_only`` request may have committed, and Django still sends ``request_finished`` once it ends.
Such a request keeps its ``TUNNEL['CONCURRENCY']`` slot until the view returns, so a socket never runs more views at once than the limit.
``WebSocketApp(http_app=...)`` runs the requests through another application, and a subclass that overrides ``WebSocketApp.tunnel(scope)`` returns its own ``Tunnel``, an object whose ``async __call__(request, *, connection)`` returns a ``Reply`` or a ``Refusal`` and whose ``hints()`` returns the ``TunnelHints`` of the ``hello`` frame.
``DjangoTunnel`` keeps its header lists in class attributes, so a subclass extends them without copying the class, and ``hello`` reports the extended lists.
With messages kept in a cookie, ``next.W145`` warns that a flashed message turns a tunneled reply into ``http_only``.

Origin and session
~~~~~~~~~~~~~~~~~~

A cookie-authenticated socket needs the same cross-site protection as a form post, so the default Origin rule mirrors Django's CSRF check.
The Origin is accepted when it equals the scheme and ``Host`` of the socket, or when it matches ``CSRF_TRUSTED_ORIGINS``, wildcards such as ``https://*.example.com`` included.
The ``Host`` is validated against ``ALLOWED_HOSTS`` and honours ``USE_X_FORWARDED_HOST``, and the scheme is ``https`` for a ``wss`` scope or when ``SECURE_PROXY_SSL_HEADER`` says so.
A TLS-terminating proxy hands the socket a ``ws`` scope, so it needs ``SECURE_PROXY_SSL_HEADER``, as Django's CSRF check does, or the ``https`` Origin is refused.
A connection with no ``Origin`` header is refused, because only a browser carries ambient cookies.
``WebSocketApp(origins=[...])`` replaces the rule with an explicit list in the ``CSRF_TRUSTED_ORIGINS`` format and raises ``ValueError`` for an empty or malformed list.
A ``*`` entry in ``ALLOWED_HOSTS`` is not an error, because the check compares the Origin with the validated ``Host``.

The credential comes from the cookies of the handshake through the ticket binding.
The default binding reads the session named by the ``SESSION_COOKIE_NAME`` cookie through the configured ``SESSION_ENGINE`` on the shared thread pool.
The load is read-only.
A session whose hash only matches a ``SECRET_KEY_FALLBACKS`` entry keeps working.
Rotating the key belongs to the next HTTP request, and the socket has no response to carry a new cookie.
The ``db``, ``cache``, ``cached_db``, and ``file`` engines work.
The signed-cookie engine re-signs its cookie on every session save.
A ticket issued before the save then no longer matches the handshake cookie, and the socket closes with ``4401``, which ``next.W142`` reports.
``SignedCookieBinding`` removes that limit and needs no server-side session engine, see `Ticket binding`_.
The ``file`` engine replaces an expired session with a new file when it loads one, and the socket turns that write into a no-op.
Under the default binding the socket loads the session at the handshake and on each recheck, so a cache-backed store that evicts live sessions closes sockets with ``4401``.

A recheck runs on every heartbeat and at the session's deadline, and it closes the socket only when the session is gone.
A session renewed over HTTP, by ``SESSION_SAVE_EVERY_REQUEST`` or ``set_expiry``, moves the deadline and keeps its socket open.
The deadline is the ``expire_date`` row of the ``db`` and ``cached_db`` engines, or an expiry date passed to ``set_expiry``.
The ``cache`` and ``file`` engines keep no deadline the socket can read otherwise.
For them the heartbeat recheck alone notices an expired session.

Ticket binding
~~~~~~~~~~~~~~

A ticket proves that the browser which negotiated it is the one that opens the socket.
``NEXT_FRAMEWORK['REALTIME']['TICKET_BINDING']`` names the ``TicketBinding`` subclass that decides what the ticket is bound to, as a dotted path.
The default ``next.realtime.SessionBinding`` binds the Django session key.
``next.realtime.SignedCookieBinding`` binds a signed-in user and a visitor cookie, and serves the signed-cookie ``SESSION_ENGINE``.
A project class serves any other identity, see :doc:`/content/howto/bind-tickets-to-your-own-identity`.

A ticket is a signed token bound to the session.
The first handshake does not consume it, so it stays valid for every handshake until ``REALTIME['TICKET_MAX_AGE']`` elapses.
It is signed from ``SECRET_KEY``, so rotating the key invalidates outstanding tickets unless the old key stays in ``SECRET_KEY_FALLBACKS``.

A binding answers six questions.
The framework calls the first four in this order for a WebSocket, and ``live_session`` serves SSE instead of ``live``.

.. list-table::
   :header-rows: 1
   :widths: 22 78

   * - Method
     - Contract
   * - ``issue(request)``
     - Runs while the ticket is negotiated and returns the subject string the ticket binds.
       It raises ``InvalidTicketError`` when the request has nothing to bind, which answers the negotiation with ``503`` and ``session_unavailable``.
   * - ``respond(request, response)``
     - Sets the cookie a binding needs on a negotiation reply and does nothing by default.
       It reads what ``issue`` left on the request and is idempotent, so a view that issues tickets without ``TopicStream`` calls ``ticket_binding().respond(request, response)`` on its own response.
   * - ``credential(cookies)``
     - Reads the credential from the cookies of the handshake or of an introspection call and returns ``None`` when they carry none.
       It runs off the event loop, so it may read the session store.
   * - ``subject(credential)``
     - Returns the subject the credential proves, which ``verify_ticket`` compares with the one the ticket binds, or ``None`` when it proves none.
       It runs on the event loop of the socket, so it is pure and never blocks, with no database, cache, or network access.
   * - ``live(credential)``
     - Returns a ``LiveIdentity`` of the user and the seconds left, or ``None`` once the credential is gone.
       The seconds are ``None`` when the store keeps no deadline.
       The handshake calls it, and so does every recheck, which is where a logout or a password change closes the socket.
       It serves WebSockets only.
   * - ``live_session(session_key)``
     - Returns a ``LiveIdentity`` for the session key of an SSE request, or ``None`` once the session is gone.
       It calls ``stored_session`` by default and serves SSE only.

The ticket carries a digest of the subject under ``SECRET_KEY``, and ``verify_ticket`` accepts a digest made under any of ``SECRET_KEY_FALLBACKS`` as well.
``Handshake.credential`` holds the credential of the binding and stays out of ``repr``.
It is the session key only under ``SessionBinding``.
A handler never sees it.
``Handshake.subject`` and ``Upstream.subject`` hold a keyed digest of the subject the credential proves.
The digest is stable across reconnects of one user or visitor and never yields the credential back.
It is empty when the credential proves no subject.
The digest changes when ``SECRET_KEY`` rotates, and under ``SessionBinding`` it follows the session rather than the user, so a new login yields a new one.
``stored_session(session_key)`` returns the ``LiveIdentity`` of a stored session, ``session_user(store)`` loads the user of a session without writing to it, and ``session_alive(session_key)`` tells whether the stored session is live.
A binding class is built once per dotted path and shared by every connection, so it keeps no per-connection state.

``SignedCookieBinding`` binds a signed-in user as ``user:<auth hash>:<pk>`` and an anonymous visitor as ``visitor:<id>``.
The visitor id lives in the signed ``next_visitor`` cookie.
The negotiation reply sets and renews it with the path, domain, ``Secure``, ``SameSite``, and lifetime of the session cookie, and always ``HttpOnly``.
``issue`` leaves the visitor id on ``request.META``, and ``respond`` writes the cookie from there, so a custom view calls ``ticket_binding().respond(request, response)`` on its reply.
A view that forgets the call sets no cookie, and the handshake closes with ``4401``.
A signed-in negotiation makes ``respond`` delete the ``next_visitor`` cookie.
A visitor that never negotiated while signed in survives a login and a logout.
The first anonymous negotiation after one that did issues a fresh visitor.
A negotiation whose session holds a user that no longer loads, after a password change elsewhere, binds a visitor the same way.
``live`` loads the user by pk and compares the auth hash.
A password change or an inactive user closes the socket, while a logout in another tab does not, because the binding keeps no stored session to delete.
``live`` for a credential it does not recognise returns ``None``.
A subclass changes ``cookie_name`` or ``salt``.
The binding works under any ``SESSION_ENGINE``.
``next.W147`` warns when a server-side engine runs next to it, because ``SessionBinding`` would recheck the stored session there.

.. autoclass:: next.realtime.TicketBinding
   :members:

.. autoclass:: next.realtime.SessionBinding
   :members:

.. autoclass:: next.realtime.SignedCookieBinding
   :members:

.. autoclass:: next.realtime.LiveIdentity

.. autofunction:: next.realtime.stored_session

.. autofunction:: next.realtime.session_user

.. autofunction:: next.realtime.session_alive

.. autofunction:: next.realtime.ticket_binding

Serving the socket
~~~~~~~~~~~~~~~~~~

``WebSocketApp`` is a plain ASGI application for WebSocket scopes, so it mounts under any ASGI router.
``application(http_app, *, websocket=None)`` is the ready default for a Django project.
It builds a ``WebSocketApp`` unless ``websocket`` is given, so a project that overrides the extension points below passes an instance of its own ``WebSocketApp`` subclass.
It sends HTTP to ``http_app``, serves the socket at ``REALTIME['WS_PATH']``, and closes any other WebSocket path before accepting it, since Django's own ASGI handler rejects WebSocket scopes.
The path is compared below ``FORCE_SCRIPT_NAME`` or the ASGI ``root_path``, the way Django computes ``path_info``, and the ticket URL carries the same prefix.
``REALTIME['WS_PATH']`` is the only place the route is configured, because the ticket tells the client the same URL.

Django's ASGI handler does not speak lifespan, so ``application`` answers it and closes the broker on shutdown.
Another HTTP application gets the lifespan events, and the broker closes before its shutdown complete goes out.
An application that fails or returns before sending a lifespan event, such as a middleware around Django's handler, leaves the answer to ``application``.
A broker that fails to close is logged and reported as ``lifespan.shutdown.failed``.
Creating a ``WebSocketApp`` marks the process as serving ``ws``, which switches the default ``TRANSPORTS`` to ``["ws", "sse"]``.
It also logs the session warnings of ``manage.py check`` as ``WARNING`` records on the ``next.realtime.asgi`` logger, and ``next.W146`` for an explicit origin CSRF refuses.
It reads ``UPSTREAM`` and ``TUNNEL`` once, so a bad value raises ``ImproperlyConfigured`` when the application is built rather than on every connection, and ``WebSocketApp.limits`` holds the result.

Three methods of ``WebSocketApp`` are the extension points of a subclass.
``allow_origin(origin, scope)`` decides the Origin, ``authenticate(scope)`` returns the ``Handshake`` of the connection or ``None`` for a refusal, and ``topic_socket(port, handshake, scope)`` returns the ``TopicSocket`` that serves it.
An overridden ``topic_socket`` passes ``tunnel=self.tunnel(scope)`` and ``limits=self.limits`` to its socket, since a socket built without ``tunnel=`` answers every ``http`` frame with ``disabled``.
An ``authenticate`` that raises is logged and closes the socket with ``1011``.

``TopicSocket`` knows nothing of ASGI.
It reads frames from an async iterable and writes through a ``SocketPort``, which is two coroutines, ``send(text)`` and ``close(code)``.
``send`` raises ``OSError`` once the peer is gone, and ``close`` tolerates a peer that already left.
A gateway on another server loads the ``Handshake`` of the connection cookies with ``await Handshake.from_cookies(cookies)``, which ``WebSocketApp.authenticate`` uses too, and runs ``TopicSocket(port, handshake).serve(frames)``.
The mapping comes from ``django.http.parse_cookie`` on the ``cookie`` header, and the method returns ``None`` for an empty mapping or a binding that finds no live credential.
A gateway that tunnels form actions passes ``tunnel=`` as well, a ``DjangoTunnel`` or its own ``Tunnel``.
A gateway that takes the identity from elsewhere builds ``Handshake(credential, user, expires_at)`` itself, with ``expires_at`` on the ``time.monotonic()`` clock or ``None``.
``expires_at`` is the moment of a forced session recheck, not of a close.
The ``Handshake.credential`` has to be the credential the ticket was issued under, and ``Handshake.subject`` is derived from it.
``TopicSocket.catch_up(subscription, positions)`` and ``TopicSocket.tick`` are the hooks a subclass overrides, the first between the position read and ``hello`` and the second on each idle heartbeat.
``TopicSocket.handle(upstream)`` and ``TopicSocket.access_check(claims, authorizer)`` are the hooks of `Upstream events`_.

``verify_ticket(token, credential)`` returns the ``TicketClaims`` of a valid ticket and raises ``InvalidTicketError`` otherwise.
``resolve_claims(claims)`` imports the transform and the authorizer the claims name.
``TicketClaims.presence`` is ``False`` for a stream outside presence, ``True`` for a count, or the dotted path of the ``identify`` callable, and ``resolve_identify(claims)`` imports that callable or returns ``None``.
A ``TopicSocket`` joins the presence of its ticket after the position read and before ``hello``, under its ``connection_id``, and leaves it on every way out, a cancellation and a watchdog drop included.
A ticket without a ``presence`` key verifies as ``False``.
The ``count`` of a snapshot follows the rules of `Presence`_, one member for every group of equal mappings in member mode and one per connection in count mode.

.. autoclass:: next.realtime.WebSocketApp
   :members:

.. autofunction:: next.realtime.application

.. autoclass:: next.realtime.tunnel.DjangoTunnel
   :members:

.. autoclass:: next.realtime.tunnel.Tunnel
   :members:

.. autoclass:: next.realtime.tunnel.TunnelHints
   :members:

.. autoclass:: next.realtime.TopicSocket
   :members:

.. autoclass:: next.realtime.attendance.Attendance
   :members:

.. autoclass:: next.realtime.SocketPort
   :members:

.. autoclass:: next.realtime.Handshake
   :members:

.. autoclass:: next.realtime.TicketClaims
   :members:

.. autofunction:: next.realtime.verify_ticket

.. autofunction:: next.realtime.tickets.resolve_claims

.. autofunction:: next.realtime.tickets.resolve_identify

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
       ``presence`` is ``"count"`` or ``"members"`` for a stream that takes part in presence and is absent otherwise.
       The ``identify`` path stays out of the body, and a ticket whose ``identify`` no longer imports gets the ``403``.
   * - ``400``
     - ``{"error": "invalid"}``.
       The caller is authorized and the body is not JSON or lacks a string ``ticket`` or ``cookie``.
   * - ``403``
     - ``{"error": "denied"}``.
       The bearer is wrong, the ticket is forged or expired, the session is gone or foreign, an authorizer refuses a topic, or the ticket names a transform and ``grant_transformed`` returns ``None``.
       One body covers every case so a caller learns nothing of the reason, and the reason is logged at ``INFO`` on ``next.realtime.views``.
   * - ``405``
     - Any method but ``POST``, with an ``Allow: POST`` header.

A ticket that names a transform is refused by default, because a gateway cannot run Python.
Responses carry never-cache headers, and the view is exempt from CSRF and from the default login requirement.
A subclass overrides ``authorize(request)`` for a caller check of its own, such as a client certificate header, and ``grant(claims, user, expires_in)`` to change the body.
``grant_transformed(claims, user, expires_in)`` answers a ticket with a transform, and a subclass returns a body for a gateway that applies the transform itself.
It returns ``None`` by default, which keeps the ``403``.
A routed endpoint with the stock ``authorize`` and no ``GATEWAY_TOKEN`` is reported as ``next.E188``.

.. autoclass:: next.realtime.TicketIntrospectionView
   :members:

Signals
-------

``broker_backend_loaded`` fires when the lazy manager creates the configured backend, after the manager lock is released.
``message_published`` fires after the broker accepts a publication, with ``ephemeral`` true for an unnumbered presence snapshot.
``upstream_received`` fires before an upstream handler runs, with the ``Upstream`` it gets.
``upstream_rejected`` fires for every ``up`` frame that is not delivered, with the ``error`` of its ack as ``reason``.
All of them go out robust, so a raising receiver is logged and never fails the publication or the event.
``stream_subscribed`` and ``stream_unsubscribed`` bracket the life of every ``TopicStream`` and ``TopicSocket``, the second with the ``duration`` and an ``UnsubscribeReason``.
See :doc:`signals` for their payloads and :doc:`/content/topics/signals` for receiver usage.

Management command
------------------

``manage.py realtime`` publishes to a topic and reads its positions, presence, and replay log from the shell, and ``manage.py runserver`` serves the project on uvicorn with the WebSocket mounted.
See :doc:`management` for both.

System checks
-------------

Realtime checks validate the ``REALTIME`` keys, the transport list, the ticket age, the ticket binding, the WebSocket path, the heartbeat, the replay window, the presence limits, and the broker backend and its options, and report a missing ``redis`` dependency.
A warning flags a nonzero ``REPLAY['SIZE']`` with a broker that keeps no replay log, and another flags an explicit ``PRESENCE`` with a broker that keeps no presence.
Deployment warnings flag an explicit ``"ws"`` transport without ``SessionMiddleware`` or with signed-cookie sessions under the default ticket binding.
A ``WebSocketApp`` logs the session warnings itself when it is created, since ``manage.py check`` never sees a mounted socket.
An error flags a routed ticket introspection endpoint that has no ``GATEWAY_TOKEN``.
The checks run against the defaults when ``NEXT_FRAMEWORK`` has no ``REALTIME`` key.
Errors flag invalid ``UPSTREAM`` and ``TUNNEL`` limits and upstream handlers that can never match.
See :doc:`system-checks` for the ids ``next.E177`` to ``next.E199``, ``next.E201`` to ``next.E211``, and ``next.W141`` to ``next.W144``, ``next.W146``, and ``next.W147``.

See also
--------

.. seealso::

   :doc:`settings` for the ``REALTIME`` settings mapping.
   :doc:`backends` for the shared backend loading conventions.
   :doc:`/content/topics/realtime` for publication, access, and recovery.
   :doc:`/content/howto/write-a-broker-backend` for a custom broker.
   :doc:`/content/howto/bind-tickets-to-your-own-identity` for a ticket binding of your own.
   :doc:`/content/howto/deliver-events-durably` for events that must not be lost.
   :doc:`management` for the ``realtime`` and ``runserver`` commands.
   :doc:`testing` for the realtime test helpers.
   :doc:`/content/topics/partial-rendering/sse` for the existing stream transport.
