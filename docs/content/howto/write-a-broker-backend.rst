.. _howto-write-a-broker-backend:

Write a broker backend
======================

Problem
-------

Topic messages have to travel through a message system the shipped brokers do not speak, or reach Redis in a way ``RedisBroker`` does not cover, such as through Sentinel.

Solution
--------

Subclass ``BrokerBackend`` for a new transport and give it ``replay`` and ``areplay`` so reconnecting streams catch up from its log.
For another way into Redis, subclass ``RedisBroker`` and override ``create_client`` and ``topic_key``.
Select the class in ``NEXT_FRAMEWORK['REALTIME']['BROKER']['BACKEND']``.

Walkthrough
-----------

Implement the broker contract
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``BrokerBackend`` is the abstract root of every broker.
A subclass implements ``publish``, ``apublish``, ``subscribe``, ``asubscribe``, ``positions``, and ``apositions``, and may override ``close`` and ``aclose``.
The constructor receives the whole ``BROKER`` entry and exposes its ``OPTIONS`` as ``self.options``, and ``manage.py check`` leaves those options to the backend.
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

Reach Redis another way
~~~~~~~~~~~~~~~~~~~~~~~

``RedisBroker`` needs the ``broker`` extra, which installs ``redis`` 8 or newer.
It builds every client through ``create_client(url, options, *, asynchronous)``, once for the synchronous pool and once per running event loop for the asynchronous client.
``options`` holds the merged ``CLIENT`` keywords, ``decode_responses`` forced on, and an override passes them on to whatever it builds.
``topic_key(kind, topic)`` names the ``seq`` key of the counter and the ``log`` key of the replay log.

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
The override drops the tag, which suits a single primary behind Sentinel and breaks the script on Cluster.
An override for Cluster keeps ``{<topic>}`` in both keys and returns a distinct key per ``kind``.
The broker refuses a ``PREFIX`` with ``{`` or ``}`` when it is created, since a brace would move the tag off the topic name.

Every stream holds one pooled Redis connection, so size ``CLIENT['max_connections']`` for the open streams.
The default pool raises ``MaxConnectionsError`` when it runs out, and the broker logs a warning on ``next.realtime.redis`` for it.
A ``BlockingConnectionPool`` raises a plain ``ConnectionError`` on timeout, so its exhaustion ends the stream with ``BrokerConnectionError`` and logs no warning.

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
                   "SENTINELS": [["sentinel-1.internal", 26379], ["sentinel-2.internal", 26379]],
                   "SERVICE": "realtime",
                   "CLIENT": {"max_connections": 200},
               },
           },
           "REPLAY": {"SIZE": 100, "TTL": 300},
       },
   }

``manage.py check`` leaves the options of a ``RedisBroker`` subclass alone, so a missing ``SENTINELS`` or ``SERVICE`` raises ``KeyError`` when the broker is created.
A publisher outside Django passes ``replay={"SIZE": 100, "TTL": 300}`` to the constructor, which then reads no replay setting.

Verification
------------

Run ``uv run python manage.py check`` and confirm it reports neither ``next.E184`` for the class nor ``next.W143`` for the replay window.

Open a single-topic stream page with an old cursor in ``Last-Event-ID``, for example with ``curl -N -H "Last-Event-ID: <epoch>:<seq>"`` and the session cookie of a signed-in user.
The messages after that cursor arrive with their ``id:`` lines before ``next-hello``, and a cursor the log cannot cover yields ``next-hello`` alone.

See also
--------

.. seealso::

   :doc:`/content/ref/realtime` for the broker contract, the replay order, and the SSE event ids.
   :doc:`/content/howto/fan-out-across-processes-with-redis` for the stock Redis setup.
   :doc:`/content/topics/realtime` for the delivery guarantees replay works within.
