Stream updates over WebSockets with Channels
============================================

Use this guide when an application already renders an authorized ``TopicStream`` and wants to add WebSocket delivery for deployments that run under ASGI.
The same guarded page endpoint negotiates the transport, and an importable topic authorizer is checked before subscription and throughout the connection.

Install the optional dependency and add Channels to the application registry.

.. code-block:: bash
   :caption: shell

   uv add "next.dj[channels]"

.. code-block:: python
   :caption: config/settings.py

   INSTALLED_APPS = [
       # Django and project applications
       "channels",
   ]

The socket rechecks its session on the server while it stays open, so ``SESSION_ENGINE`` must store sessions on the server.
The ``db``, ``cache``, ``cached_db``, and ``file`` engines work, while the signed-cookie engine is unsupported and ``manage.py check`` reports it as ``next.W145``.

Wrap Django's HTTP ASGI application with the Channels integration.
The helper applies Django session authentication and validates WebSocket Origin against the project's concrete ``ALLOWED_HOSTS``.
Projects whose public origins differ from those hosts pass an explicit ``origins`` list.

.. code-block:: python
   :caption: config/asgi.py

   import os

   from django.core.asgi import get_asgi_application

   from next.contrib.channels import application as realtime_application

   os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
   django_application = get_asgi_application()
   application = realtime_application(django_application)

Each ``ALLOWED_HOSTS`` entry becomes a scheme-less Origin pattern that ignores the port.
An entry beginning with ``.``, such as ``.example.com``, allows ``example.com`` and every subdomain, as it does in Django.
``ALLOWED_HOSTS = ["*"]`` cannot protect a cookie-authenticated socket from cross-site WebSocket requests, so the wrapper raises ``ValueError`` unless an explicit ``origins`` list is supplied.
Pass that list when production policy requires an exact scheme and port as well.

The existing guarded ``TopicStream`` page remains the subscription endpoint.
The browser first performs its normal GET with ``X-Next-Transport: ws``.
The response is ``application/json`` and contains a short-lived, session-bound ticket scoped to that stream's topics, transform, and authorizer.
The client sends the ticket in its first WebSocket frame and never places it in the URL.
A reply of any other content type, or no reply within five seconds, sends the client to SSE.
When the cookie names a session that no longer exists, negotiation issues the ticket against a fresh session and the response rewrites the session cookie.

Once the socket opens, its ``hello`` must arrive within five seconds, or the client closes the socket as a transient failure and retries.
The server sends ``hello`` only after the broker confirms the subscription, and ``RedisBroker`` waits up to five seconds for that confirmation.
A degraded broker can therefore push clients through repeated reconnects and, after three quick failures, onto SSE.

.. code-block:: python
   :caption: polls/pages/polls/[int:id]/stream/page.py

   from django.http import HttpRequest
   from polls.models import Poll
   from polls.realtime import can_subscribe_topic

   from next.realtime import TopicStream

   def render(request: HttpRequest, poll: Poll) -> TopicStream:
       return TopicStream(
           request,
           f"poll:{poll.pk}",
           authorize=can_subscribe_topic,
       )

The authorizer is a synchronous, importable function accepting ``(user, topic)`` and returning a boolean.
The consumer calls it before joining the broker and then at most once per heartbeat interval, together with a recheck of the session.
A revocation closes the socket within about two heartbeat intervals plus the callback's own latency, with ``4401`` for a session problem and ``4403`` for a topic denial, each after an ``auth`` error frame.
The client then falls back to SSE, where a revoked session reconnects through the page guard and a topic denial ends the stream for good.
The checks run on the shared thread pool rather than the single sync thread, so keep the callback's database work small.
If no stable authorizer reference is available, WebSocket negotiation returns ``406`` and the client uses SSE.

Keep sensitive page data out of published topic payloads.
Publish a ``refresh`` operation for private zones.
Each subscriber then loads current HTML through its own guarded HTTP request and cookies.

The default realtime configuration tries WebSocket when Channels is installed and keeps SSE as fallback.
Set ``NEXT_FRAMEWORK["REALTIME"]["TRANSPORTS"]`` to ``["sse"]`` to disable WebSocket negotiation.
A ``TRANSPORTS`` list that names ``"ws"`` while ``channels`` is installed but missing from ``INSTALLED_APPS`` is reported as ``next.E189``.
The browser renews a ticket when it reconnects.
The socket sends a ``ping`` frame every ``NEXT_FRAMEWORK["REALTIME"]["HEARTBEAT"]`` seconds, 25 by default, so keep that value below the idle timeout of every proxy in front of it.

For a single process, the default ``MemoryBroker`` works.
A multi-worker deployment needs both a shared channel layer and an atomic shared Django cache for ``ChannelsLayerBroker`` sequence numbers.
Install the Redis channel layer and configure both shared services.

.. code-block:: bash
   :caption: shell

   uv add channels-redis

.. code-block:: python
   :caption: config/settings.py

   import os

   REDIS_URL = os.environ["REDIS_URL"]
   CHANNEL_LAYERS = {
       "default": {
           "BACKEND": "channels_redis.core.RedisChannelLayer",
           "CONFIG": {"hosts": [REDIS_URL]},
       },
   }
   CACHES = {
       "default": {
           "BACKEND": "django.core.cache.backends.redis.RedisCache",
           "LOCATION": REDIS_URL,
       },
   }

Then select the broker explicitly.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "REALTIME": {
           "BROKER": {
               "BACKEND": "next.contrib.channels.ChannelsLayerBroker",
           },
       },
   }

``cache.incr`` allocates topic sequence numbers before ``group_send``, and the two operations are not atomic together.
Delivery is best-effort, and clients recover gaps by requesting their guarded zones over HTTP.
Use a durable outbox if the application needs guaranteed delivery after a database commit.
``manage.py check`` reports an in-process channel layer as ``next.W143`` and a cache without an atomic ``incr()`` as ``next.W144`` whenever this broker is selected, SSE-only deployments included.

The counter cache must never evict realtime keys.
Django's ``RedisCache.incr`` is not atomic against an eviction between its read and its write, so point ``CACHE_ALIAS`` at a dedicated cache or at a Redis instance configured with ``maxmemory-policy noeviction``.

The same broker also serves SSE streams under WSGI.
Their blocking subscriptions run every layer receive on one background event loop, which is what ``channels_redis`` needs, so a WSGI process can keep serving SSE with this broker.
The broker registers an :func:`os.register_at_fork` hook, so a pre-forking server starts a fresh loop in each worker instead of inheriting the parent's.
A failed layer or cache call, Redis and Memcached errors from ``pymemcache`` or ``pylibmc`` included, ends the affected stream with ``BrokerConnectionError``, and the browser reconnects and resynchronises from a fresh ``hello``.

The topic counter lives under a key namespaced by its epoch.
A counter the cache lost, through eviction or a restart, therefore rotates the epoch on the next publication rather than restarting the numbers inside the old one, and clients reset their cursors.
Until then ``positions`` reports the stored epoch with ``seq`` zero.

See :doc:`/content/topics/realtime` for topic delivery and recovery guarantees, :doc:`/content/ref/realtime` for the complete API, and :doc:`/content/deployment/wsgi-asgi` for ASGI deployment.
