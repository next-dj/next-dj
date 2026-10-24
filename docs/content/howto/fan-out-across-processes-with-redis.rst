.. _howto-fan-out-across-processes-with-redis:

Fan out realtime updates across processes with Redis
====================================================

Problem
-------

The in-memory realtime broker cannot deliver a publication from one worker to a subscriber connected to another worker.

Solution
--------

Install the optional broker dependency and select ``RedisBroker`` in the framework settings.

.. code-block:: bash
   :caption: shell

   uv add "next.dj[broker]"

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "REALTIME": {
           "BROKER": {
               "BACKEND": "next.realtime.RedisBroker",
               "OPTIONS": {
                   "URL": "redis://redis.internal:6379/0",
                   "PREFIX": "myapp:realtime",
                   "HEALTH_CHECK_INTERVAL": 10.0,
                   "CLIENT": {"max_connections": 200},
               },
           },
       },
   }

Run ``manage.py check`` in each deployment environment before serving requests.
The check validates the configured backend and its options, reports a missing Redis library as ``next.E179``, a negative, infinite, or non-numeric ``HEALTH_CHECK_INTERVAL`` as ``next.E186``, and a ``CLIENT`` option that is not a mapping of keyword names or that turns ``decode_responses`` off as ``next.E187``.

Tune the Redis client
---------------------

``OPTIONS['CLIENT']`` is a mapping of keyword arguments that the broker passes whole to the Redis ``from_url`` call, for the synchronous and the asynchronous client alike.
The broker builds the options in three layers.
``health_check_interval`` from ``HEALTH_CHECK_INTERVAL`` and ``socket_keepalive`` set to ``True`` come first, then ``CLIENT`` overrides them, and ``decode_responses`` is forced to ``True`` last because the broker reads text.
Use it for ``max_connections``, timeouts, or TLS settings.

Every open WebSocket and SSE stream holds one Redis connection for its subscription.
The default pool size is the one redis-py picks, so a process with many open streams raises ``max_connections`` before it runs out.
An exhausted pool is logged as a warning on the ``next.realtime.redis`` logger and ends the affected stream with ``BrokerConnectionError``, so the browser reconnects.

A project that reaches Redis through Sentinel or Cluster overrides ``RedisBroker.create_client(url, options, *, asynchronous)`` in a subclass and selects the subclass as ``BACKEND``.
The method returns the synchronous client when ``asynchronous`` is false and the asynchronous one otherwise.

How the broker behaves
----------------------

Redis Pub/Sub supplies cross-process fan-out, and one Lua script assigns each topic's ``seq`` and ``epoch`` atomically with the publication.
The counter and its epoch live in one hash under the prefix, so an evicted or flushed key restarts the counter with a new epoch and clients resynchronise.
A subscription waits until Redis confirms every channel before the stream reads the topic positions and sends ``hello``, and it gives up with ``BrokerConnectionError`` after five seconds without that confirmation.

A quiet subscription pings Redis on its own connection at every heartbeat interval.
A ping left unanswered until the next interval ends the stream, so a half-open TCP connection cannot leave a page heartbeating on stale data.
``HEALTH_CHECK_INTERVAL`` is separate and sets the idle time after which the Redis client checks a pooled connection before reusing it, zero turning that check off.
When the Redis client reconnects a subscription on its own, messages may have been lost in between, so the broker ends the stream rather than resuming it silently.
The browser then reconnects, receives a fresh ``hello``, and requests its zones again.

Under ASGI the broker keeps one asynchronous Redis client per running event loop, so a client never crosses loops.
Synchronous publishers and WSGI streams share one blocking client pool.
Messages published while a subscriber is disconnected are not replayed.
The client recovers current state by refreshing the affected zones through its own page view.
See :doc:`/content/topics/realtime` for delivery semantics and topic payload rules.
