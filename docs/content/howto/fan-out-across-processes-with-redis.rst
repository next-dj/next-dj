.. _howto-fan-out-across-processes-with-redis:

Fan out realtime updates across processes with Redis
====================================================

Problem
-------

The default ``MemoryBroker`` keeps topics, sequence numbers, the replay log, and presence inside one process.
A deployment with several workers or hosts needs a broker every process shares, or a publication from one worker never reaches a subscriber connected to another.

Solution
--------

Install the optional broker dependency, which needs ``redis`` 8 or newer, and select ``RedisBroker`` in the framework settings.

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
                   "SUBSCRIBE_MAX_CONNECTIONS": 1000,
               },
           },
       },
   }

The ``max_connections`` and ``SUBSCRIBE_MAX_CONNECTIONS`` values are examples to size from measurement, see :ref:`deployment-capacity`.

Run ``manage.py check`` in each deployment environment before serving requests.
The check validates the configured backend and its options.
It reports a missing Redis library as ``next.E179`` and a negative, infinite, or non-numeric ``HEALTH_CHECK_INTERVAL`` as ``next.E186``.
It reports a ``CLIENT`` option that is not a mapping of keyword names, or that turns ``decode_responses`` off, as ``next.E187``.
It reports an empty or non-string ``URL`` as ``next.E213``.
It reports a ``SUBSCRIBE_MAX_CONNECTIONS`` that is not a positive integer as ``next.E216``.
``manage.py check --deploy`` reports a broker with neither ``SUBSCRIBE_MAX_CONNECTIONS`` nor ``CLIENT['max_connections']`` as ``next.W151``.

.. _howto-realtime-several-workers:

Run several workers or hosts
----------------------------

One process
~~~~~~~~~~~

``MemoryBroker`` is the default because one process needs nothing more.
``manage.py runserver``, a single uvicorn process, or gunicorn with one worker holds every stream and every socket itself, so a publication reaches all of them.
The state lives in memory, so a restart empties the replay log and presence, and reconnecting clients resynchronise from ``hello``.

Several workers
~~~~~~~~~~~~~~~

A ``--workers`` value above one starts that many processes, and each builds a ``MemoryBroker`` of its own.
Every connection lands on one of them, and a ``publish()`` in one worker reaches only the SSE streams and WebSockets that worker holds.
Each worker also keeps its own counters, replay log, and presence, so presence counts the attendees of one worker and a reconnect that lands on another worker cannot replay what it missed.
A process outside the server, such as a task queue worker or a cron job, reaches no subscriber at all, and ``manage.py realtime publish`` refuses ``MemoryBroker`` for that reason.
The framework does not count the server processes, so it never switches brokers on its own.

The usual multi-process commands all need the shared broker.

.. code-block:: bash
   :caption: shell

   uv run gunicorn config.asgi:application -k uvicorn_worker.UvicornWorker --workers 4
   uv run uvicorn config.asgi:application --workers 4
   uv run gunicorn config.wsgi:application --workers 4

The first two serve ASGI, with WebSocket and async SSE streams.
``uvicorn_worker.UvicornWorker`` comes from the ``uvicorn-worker`` package, which replaces the deprecated ``uvicorn.workers`` module.
The third serves WSGI with gunicorn sync workers, where SSE is the only transport and each open stream holds one worker, or one thread under ``--threads``, for its whole lifetime, see :ref:`deployment-wsgi-asgi`.
The worker counts are placeholders to size from a load run, see :ref:`deployment-capacity`.

Select ``RedisBroker`` in the settings every worker and every publishing process reads.
The settings in `Solution`_ list every option, and a minimal entry needs only the URL.

.. code-block:: python
   :caption: config/settings.py

   import os

   NEXT_FRAMEWORK = {
       "REALTIME": {
           "BROKER": {
               "BACKEND": "next.realtime.RedisBroker",
               "OPTIONS": {"URL": os.environ["REDIS_URL"]},
           },
       },
   }

All processes of one project share the same ``URL`` and ``PREFIX``, since a different prefix puts them on separate topics.
Two projects on one Redis database keep apart by giving each its own ``PREFIX``.

Preload and fork
~~~~~~~~~~~~~~~~

``gunicorn --preload`` imports the application in the master process before it forks the workers.
The broker is built on the first publish or subscription, not at import, so each worker normally builds its own.
A ``RedisBroker`` the master already built stays usable in the children.
The redis-py pool opens fresh connections in a forked process, and the framework drops the event-loop clients, the presence scheduler, and the handler thread pool a child inherits.
``--preload`` therefore needs no extra setting.
It does not make ``MemoryBroker`` shared either, because each child receives a copy of the master memory that diverges at the fork.

Several hosts
~~~~~~~~~~~~~

Several hosts or containers behind a load balancer point at the same Redis ``URL`` and ``PREFIX``.
Fan-out needs no sticky sessions, because Redis carries every publication to the subscribers of every process.
The topic counters, the replay log, and presence live in Redis as well.
A client that reconnects to another host therefore resumes from the same ``seq``, replays from the same log, and sees the same attendance.
The hosts also share ``SECRET_KEY`` and the session store, as any Django deployment behind a load balancer does, because a WebSocket ticket and its session recheck may reach another host than the page that issued them.
See :ref:`deployment-capacity` for the Redis pool and connection limits across processes.

Check the deployment
~~~~~~~~~~~~~~~~~~~~

``manage.py check --deploy`` reports ``next.W149`` while the configured broker is ``MemoryBroker`` or a subclass of it.
A plain ``manage.py check`` stays quiet, because a development server runs one process.
A deployment that truly runs one process silences the warning.

.. code-block:: python
   :caption: config/settings.py

   SILENCED_SYSTEM_CHECKS = ["next.W149"]

A project whose infrastructure offers another shared message system, such as NATS or a database queue, writes a ``BrokerBackend`` for it instead of running Redis, see :doc:`/content/howto/write-a-broker-backend`.
The warning clears for any broker that is not ``MemoryBroker``.

Tune the Redis client
---------------------

``OPTIONS['CLIENT']`` is a mapping of keyword arguments that the broker passes whole to the Redis ``from_url`` call, for the synchronous and the asynchronous client alike.
The broker builds the options in three layers.
``health_check_interval`` from ``HEALTH_CHECK_INTERVAL`` and ``socket_keepalive`` set to ``True`` come first, then ``CLIENT`` overrides them, and ``decode_responses`` stays ``True``, since a ``CLIENT`` that turns it off is refused as ``next.E187``.
Use it for ``max_connections``, timeouts, or TLS settings.

The broker keeps two connection pools per process and per event loop.
The command pool serves publish, positions, replay, and presence, and each call returns its connection when it finishes.
The subscription pool serves streams, and every open WebSocket and SSE stream holds one of its connections for as long as it stays open.
Open streams therefore never take a connection from publishing, however many of them a process holds.
``SUBSCRIBE_MAX_CONNECTIONS`` caps the subscription pool, and without it the subscription pool takes ``CLIENT['max_connections']``, or the redis-py default of 100 when that is unset too.
Size it to the open streams one process serves at its peak, with headroom for reconnects, since a stream that reconnects opens its new subscription before Redis drops the old one.
A full subscription pool refuses the new stream with ``BrokerConnectionError``, so the WebSocket closes with ``1013`` and the SSE stream ends, and the browser reconnects later while the open streams keep running.
The broker logs a warning that names ``SUBSCRIBE_MAX_CONNECTIONS`` each time, see :ref:`ref-realtime`.

Secure the broker
-----------------

Redis is a trust boundary.
Anything able to write the broker keys can publish to any topic, and a published patch reaches every subscriber of that topic.
Use a ``rediss://`` URL for TLS, so traffic to Redis is encrypted.
Put a password or an ACL user in the same ``URL``, such as ``rediss://user:password@redis.internal:6379/0``, and read it from the environment.
Keep Redis on a private network that only the web and worker processes reach.
The ``CLIENT`` mapping carries any further redis-py connection keyword, such as the certificate options of the TLS connection.

Use another client
------------------

A project that reaches Redis through Sentinel or Cluster overrides ``RedisBroker.create_client(url, options, *, asynchronous)`` in a subclass and selects the subclass as ``BACKEND``.
The method returns the synchronous client when ``asynchronous`` is false and the asynchronous one otherwise, see :doc:`/content/howto/write-a-broker-backend`.

How the broker behaves
----------------------

Redis Pub/Sub supplies cross-process fan-out, and one Lua script assigns each topic's ``seq`` and ``epoch`` atomically with the publication.
The counter and its epoch live in one hash at ``<PREFIX>:seq:{<topic>}``, so an evicted or flushed key restarts the counter with a new epoch and clients resynchronise.
The same script writes the replay log at ``<PREFIX>:log:{<topic>}``, a list trimmed to ``REPLAY['SIZE']`` entries that expires ``REPLAY['TTL']`` seconds after the last publication.
The braces are a Cluster hash tag that keeps both keys of a topic in one slot, and the Pub/Sub channel stays ``<PREFIX>:topic:<topic>``.
A ``PREFIX`` with ``{`` or ``}`` would move that tag off the topic name, so the broker refuses it and ``manage.py check`` reports it as ``next.E192``.
The script publishes only after it writes the log, so every message a subscriber receives is already in the log.
A subscription waits until Redis confirms every channel before the stream reads the topic positions and sends ``hello``, and it gives up with ``BrokerConnectionError`` after five seconds without that confirmation.

A quiet subscription pings Redis on its own connection at every heartbeat interval.
A ping left unanswered until the next interval ends the stream, so a half-open TCP connection cannot leave a page heartbeating on stale data.
``HEALTH_CHECK_INTERVAL`` is separate and sets the idle time after which the Redis client checks a pooled connection before reusing it, zero turning that check off.
When the Redis client reconnects a subscription on its own, messages may have been lost in between, so the broker ends the stream rather than resuming it silently.
The browser then reconnects, receives a fresh ``hello``, and requests its zones again.

Under ASGI the broker keeps one asynchronous Redis client per running event loop, so a client never crosses loops.
Synchronous publishers and WSGI streams share one blocking client pool.
A reconnecting stream replays what it missed from the log when the log still covers the gap.
Otherwise the client recovers current state by refreshing the affected zones through its own page view.
See :doc:`/content/topics/realtime` for delivery semantics and topic payload rules.
