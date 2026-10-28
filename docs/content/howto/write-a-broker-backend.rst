.. _howto-write-a-broker-backend:

Write a broker backend
======================

Problem
-------

Topic messages have to travel through a message system the shipped brokers do not speak, or reach Redis in a way ``RedisBroker`` does not cover, such as through Sentinel.

Solution
--------

Subclass ``BrokerBackend`` for a new transport.
Give it ``replay`` and ``areplay`` so reconnecting streams catch up from its log.
Give it the methods of ``PresenceBroker`` so streams can report who is attending.
For another way into Redis, subclass ``RedisBroker`` and override ``create_client`` and ``topic_key``.
Select the class in ``NEXT_FRAMEWORK['REALTIME']['BROKER']['BACKEND']``.

Walkthrough
-----------

Implement the broker contract
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``BrokerBackend`` is the abstract root of every broker.
A subclass implements ``publish``, ``apublish``, ``subscribe``, ``asubscribe``, ``positions``, and ``apositions``, and may override ``close`` and ``aclose``.
The constructor receives the whole ``BROKER`` entry and exposes its ``OPTIONS`` as ``self.options``, and ``manage.py check`` hands them to the ``check_options`` classmethod of the backend.
``check_options(options)`` receives the ``OPTIONS`` mapping and returns a list of Django system check messages, empty by default.
A third-party broker overrides it to report a bad or missing option as an error before the first request.
One instance serves the process, so every method runs from many threads and event loops at once.

The rules that matter most are these.

- ``publish`` calls ``validate_publication`` first, then assigns the next ``seq`` of the topic and fans the message out.
- The counter and its epoch live together, and the broker draws a new random epoch whenever the counter starts again.
- A subscription is live once its context is entered, and a wait longer than ``timeout`` yields ``None``.
- Every transport failure surfaces as ``BrokerConnectionError``.

:ref:`ref-realtime` carries the full contract and a skeleton ``QueueBroker`` built on a project message queue, which the next step extends.

Add a replay log
~~~~~~~~~~~~~~~~

``ReplayingBroker`` is a runtime-checkable ``Protocol``, so a broker supports replay by defining ``replay(topic, after)`` and ``areplay(topic, after)``.
Both return a ``Replay`` of the logged messages after the ``Position`` cursor, with ``complete`` set when they close the whole gap to the current position.
A stream discards an incomplete answer and resynchronises the client from ``hello`` instead.

``replay_window()`` in ``next.realtime.replay`` reads and validates ``REALTIME['REPLAY']`` as a ``(size, ttl)`` pair.
``replay_after(after, current, logged)`` cuts an ascending log down to the messages after the cursor, so a broker only has to read its counter and its log.
It answers ``INCOMPLETE`` unless the cut holds every ``seq`` from the cursor to the current position in the current epoch, one each and in order.
``INCOMPLETE`` is the shared ``Replay`` with no messages and ``complete`` unset, the answer of a broker whose log cannot close the gap.

.. code-block:: python
   :caption: realtime/brokers.py

   from collections.abc import Mapping

   from next.realtime import Message, Position, Replay
   from next.realtime.replay import INCOMPLETE, replay_after, replay_window

   class ReplayingQueueBroker(QueueBroker):
       """Fan topics out through the project queue and keep a log of each topic."""

       def __init__(self, config: Mapping[str, object] | None = None) -> None:
           super().__init__(config)
           self.replay_size, self.replay_ttl = replay_window()

       def replay(self, topic: str, after: Position) -> Replay:
           if not self.replay_size:
               return INCOMPLETE
           row, logged = self.store.read_counter_and_log(topic)
           return self._replay(after, row, logged)

       async def areplay(self, topic: str, after: Position) -> Replay:
           if not self.replay_size:
               return INCOMPLETE
           row, logged = await self.store.aread_counter_and_log(topic)
           return self._replay(after, row, logged)

       def _replay(self, after: Position, row, logged) -> Replay:
           current = Position("", 0) if row is None else Position(row.epoch, row.seq)
           messages = [Message.from_dict(entry) for entry in logged]
           return replay_after(after, current, messages)

The log has to be written in the same atomic step that numbers and fans out a message, the way the ``RedisBroker`` publish script does.
A log written afterwards can miss a message or hold two in the wrong order, and ``replay_after`` then reports the gap rather than replaying a broken run.
Keep at most ``replay_size`` entries per topic, expire the log ``replay_ttl`` seconds after its last write, and clear it when the topic starts a new epoch.
Read the counter and the log in one atomic read too, so the current position and the log describe the same moment.
A replay that fails on the transport raises ``BrokerConnectionError``, and the stream then skips replay for that topic.

Without these methods and with a nonzero ``REPLAY['SIZE']``, ``manage.py check`` warns with ``next.W143`` that every reconnect resynchronises.

Add presence
~~~~~~~~~~~~

``PresenceBroker`` is a runtime-checkable ``Protocol`` too, so a broker reports attendance by defining ``join``, ``leave``, ``touch``, ``presence``, and their ``ajoin``, ``aleave``, ``atouch``, and ``apresence`` twins.
``join(topic, connection, member, ttl)`` records a connection until ``ttl`` seconds pass without a touch.
``touch`` pushes the deadline out and returns ``False`` when the record is gone, and the stream then joins again.
``leave`` forgets the connection, and ``presence`` returns a ``Presence`` of the live connections without any expired one.

The helpers live in ``next.realtime.roster``.
``encode_member(member, limit)`` returns the canonical JSON to store, ``None`` for no member, and raises ``InvalidMemberError`` for a member that is not a mapping with string keys, holds a value JSON or UTF-8 cannot carry such as a lone surrogate, or encodes above ``limit`` bytes.
Store that string as the identity of the member, because connections with an equal string are one member.
``decode_member`` reads it back and skips a malformed row.

``PresenceWindow`` is the mixin both shipped brokers share.
``keep_presence(limits, schedule, clock)`` reads ``REALTIME['PRESENCE']`` into ``self.presence_limits`` when ``limits`` is ``None`` and builds ``self.presence_schedule``, the ``PresenceSchedule`` that calls ``announce_presence(topic)`` on a daemon thread once a window closes.
``schedule`` is ``None`` for that default, or a callable that receives the hook and returns a schedule, which is how a test runs windows by hand.
``announce_presence`` is the abstract hook that every presence broker implements, and ``announce(broker, topic)`` is its usual body, reading the current snapshot and sending it through ``broker.broadcast``.
``aannounce_presence`` is its asynchronous twin, which runs ``announce_presence`` on the shared thread pool by default, and a broker with an asynchronous client overrides it and awaits ``presence_schedule.aclose(self.aannounce_presence)`` in ``aclose``.
Override ``broadcast`` to deliver the snapshot to live subscribers without a sequence, since the default raises ``NotImplementedError`` and a snapshot never enters the replay log.

A change claims the coalescing window of its topic, and the claim has to be atomic across processes, as a ``SET NX PX`` is on Redis.
Only the process that wins the claim calls ``schedule_presence(topic)``, so one window produces one event.
A join of a new connection and a leave or expiry that removes a whole member are the changes that claim.
``touch`` prunes expired records as well, so it claims a window when its prune removed a member, and the store returns both answers from one atomic step.

A claim alone loses the snapshot when the process that won it dies before the window closes.
Keep a version per topic in the store, raise it on every change, and record the version a snapshot announced.
A join, a touch, or a leave that finds the version unannounced claims a free window without a change of its own, so the next refresh of any connection heals a lost window.
A leave that empties its topic sees no later refresh, so when another process holds the window the store answers how long it stays held and the broker schedules the snapshot for then.

``announce(broker, topic)`` reads the snapshot and broadcasts it in two steps, which orders the snapshots of one process only.
Two processes that announce one topic can interleave, and an older snapshot read first may leave last and stay on every page.
When more than one process announces, read the snapshot, record its version, and fan it out in one atomic step of the store, as the announce script of ``RedisBroker`` does.

.. code-block:: python
   :caption: realtime/brokers.py

   import time
   from collections.abc import Mapping

   from next.partial import Envelope
   from next.realtime import Message, Presence
   from next.realtime.roster import (
       PresenceWindow,
       Scheduler,
       announce,
       decode_member,
       encode_member,
   )

   class PresenceQueueBroker(ReplayingQueueBroker, PresenceWindow):
       """Keep the attendance of each topic in the project store."""

       def __init__(
           self,
           config: Mapping[str, object] | None = None,
           *,
           schedule: Scheduler | None = None,
       ) -> None:
           super().__init__(config)
           self.keep_presence(None, schedule, time.monotonic)

       def join(self, topic, connection, member, ttl) -> None:
           limits = self.presence_limits
           encoded = encode_member(member, limits.member_bytes)
           if self.store.put_attendee(topic, connection, encoded, ttl, limits.window):
               self.schedule_presence(topic)

       def touch(self, topic, connection, ttl) -> bool:
           window = self.presence_limits.window
           present, claimed = self.store.refresh_attendee(topic, connection, ttl, window)
           if claimed:
               self.schedule_presence(topic)
           return present

       def leave(self, topic, connection) -> None:
           window = self.presence_limits.window
           claimed, held = self.store.drop_attendee(topic, connection, window)
           if claimed:
               self.schedule_presence(topic)
           elif held:
               self.presence_schedule.schedule(topic, held)

       def presence(self, topic) -> Presence:
           count, rows = self.store.read_attendees(topic, self.presence_limits.size)
           members = (decode_member(row) for row in rows)
           return Presence(count, tuple(m for m in members if m is not None))

       async def ajoin(self, topic, connection, member, ttl) -> None:
           self.join(topic, connection, member, ttl)

       async def atouch(self, topic, connection, ttl) -> bool:
           return self.touch(topic, connection, ttl)

       async def aleave(self, topic, connection) -> None:
           self.leave(topic, connection)

       async def apresence(self, topic) -> Presence:
           return self.presence(topic)

       def broadcast(self, topic: str, envelope: Envelope) -> Message:
           envelope = self.validate_publication(topic, envelope)
           message = Message(topic, 0, envelope, time.time())
           self.store.fan_out(topic, message.as_dict())
           return message

       def announce_presence(self, topic: str) -> None:
           announce(self, topic)

       def close(self) -> None:
           self.presence_schedule.close()
           super().close()

The async twins call the synchronous methods, which suits a store that only touches memory, and a networked store awaits its own asynchronous client there instead.
``isinstance(broker, PresenceBroker)`` holds only once all eight methods exist, and a stream refuses presence on a broker without them.
The store methods above are the part a project writes, and each has to prune expired records and count each member once.
``Presence.count`` is the number of distinct members plus every connection without a member, and the list holds at most ``presence_limits.size`` of them.

A subclass of ``RedisBroker`` inherits presence whole.
An override of ``announce_presence`` observes or reroutes the snapshots the schedule publishes, and one of ``aannounce_presence`` those ``aclose`` flushes.
An override of ``topic_key`` has to return a distinct key for the kinds ``presence``, ``members``, ``window``, ``identified``, ``attendees``, ``holders``, ``state``, and ``order`` too, all under one hash tag, since the scripts touch them together.

Without these methods a stream that passes ``presence`` fails to start with ``ImproperlyConfigured``, and ``presence()`` and ``apresence()`` raise it too.
With an explicit ``REALTIME['PRESENCE']`` setting, ``manage.py check`` warns with ``next.W144`` that the broker keeps no presence.

Reach Redis another way
~~~~~~~~~~~~~~~~~~~~~~~

``RedisBroker`` needs the ``broker`` extra, which installs ``redis`` 8 or newer.
It builds every client through ``create_client(url, options, *, asynchronous)``, once for the synchronous pool and once per running event loop for the asynchronous client.
``options`` holds the merged ``CLIENT`` keywords, ``decode_responses`` forced on, and an override passes them on to whatever it builds.
``topic_key(kind, topic)`` names every key of a topic.
The kinds are ``seq`` for the counter and ``log`` for the replay log, and the presence kinds are listed in `Add presence`_.

The subclass below reaches the primary through Sentinel and names its keys without the hash tag.

.. code-block:: python
   :caption: realtime/brokers.py

   from collections.abc import Mapping

   from redis.asyncio.sentinel import Sentinel as AsyncSentinel
   from redis.sentinel import Sentinel

   from next.realtime import RedisBroker

   class SentinelBroker(RedisBroker):
       """Reach the Redis primary through Sentinel under the untagged key names."""

       def create_client(
           self, url: str, options: Mapping[str, object], *, asynchronous: bool
       ) -> object:
           del url
           sentinels = [tuple(address) for address in self.options["SENTINELS"]]
           service = str(self.options["SERVICE"])
           if asynchronous:
               return AsyncSentinel(sentinels).master_for(service, **options)
           return Sentinel(sentinels).master_for(service, **options)

       def topic_key(self, kind: str, topic: str) -> str:
           prefix = str(self.options.get("PREFIX", "next")).rstrip(":")
           return f"{prefix}:{kind}:{topic}"

The default ``topic_key`` returns ``<PREFIX>:seq:{<topic>}`` and ``<PREFIX>:log:{<topic>}``.
The braces are a Redis Cluster hash tag that keeps both keys of a topic in one slot, since the publish script and the replay transaction touch them together.
The override drops the tag.
That suits a single primary behind Sentinel and breaks the script on Cluster.
An override for Cluster keeps ``{<topic>}`` in both keys and returns a distinct key per ``kind``.
The broker refuses a ``PREFIX`` with ``{`` or ``}`` when it is created, since a brace would move the tag off the topic name.

The broker calls ``create_client`` twice per process and per event loop, once for commands and once for streams, and the stream call carries ``SUBSCRIBE_MAX_CONNECTIONS`` as ``max_connections`` when it is set.
Every stream holds one connection of the stream pool, so size ``SUBSCRIBE_MAX_CONNECTIONS`` for the open streams.
The ``200`` in the registration below is an example value to size from measurement, see :ref:`deployment-capacity`.
An exhausted pool ends the stream with ``BrokerConnectionError``, see :ref:`ref-realtime` for the warning it logs.

Register the backend
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "REALTIME": {
           "BROKER": {
               "BACKEND": "realtime.brokers.SentinelBroker",
               "OPTIONS": {
                   "PREFIX": "myapp:realtime",
                   "SENTINELS": [
                       ["sentinel-1.internal", 26379],
                       ["sentinel-2.internal", 26379],
                   ],
                   "SERVICE": "realtime",
                   "CLIENT": {"max_connections": 200},
               },
           },
           "REPLAY": {"SIZE": 100, "TTL": 300},
       },
   }

``RedisBroker.check_options`` validates the keys ``RedisBroker`` reads and reports no unknown key for a subclass.
A missing ``SENTINELS`` or ``SERVICE`` therefore raises ``KeyError`` when the broker is created, unless the subclass overrides ``check_options`` to report it.
A publisher outside Django passes ``replay={"SIZE": 100, "TTL": 300}`` to the constructor, which then reads no replay setting.

Verification
------------

Run ``uv run python manage.py check`` and confirm it reports neither ``next.E184`` for the class, nor ``next.W143`` for the replay window, nor ``next.W144`` for the presence limits.

Open a single-topic stream page with an old cursor in ``Last-Event-ID``, for example with ``curl -N -H "Last-Event-ID: <epoch>:<seq>"`` and the session cookie of a signed-in user.
The messages after that cursor arrive with their ``id:`` lines before ``next-hello``, and a cursor the log cannot cover yields ``next-hello`` alone.

See also
--------

.. seealso::

   :doc:`/content/ref/realtime` for the broker contract, the replay order, and the SSE event ids.
   :doc:`/content/howto/fan-out-across-processes-with-redis` for the stock Redis setup.
   :doc:`/content/topics/realtime` for the delivery guarantees replay works within and the page side of presence.
