.. _howto-switch-boundaries-off-in-an-incident:

Switch boundaries off in an incident
====================================

Problem
-------

Pages with deferred boundaries hold a database under load, a pool thread count that crowds a connection limit, or a stream a proxy mishandles.
The project needs them to behave like plain Django pages at once, and ideally without a restart of every process.

Solution
--------

Two settings take the boundaries back to a plain page and take effect when the processes restart.
A ``DeliveryPolicy`` of the project that reads a flag the processes share switches streaming off without a restart.
Boundaries that the browser fetches on its own need the proxy or ``EPOCH``, since no server-side policy decides them.

Walkthrough
-----------

Stop streaming
~~~~~~~~~~~~~~

``DEFER["DELIVERY"]["OPTIONS"]["STREAM"]`` set to ``False`` assembles every page with ``stream`` boundaries inline.
The jobs still run on the pool, the response waits for the last of them or for ``DEFER["DEADLINE"]``, and it goes out whole with its own status code.
``page_stream_declined`` reports the reason ``option``.
A partial request is answered whole too, as the page tells the client runtime that no stream comes.

.. code-block:: python
   :caption: config/settings/prod.py

   NEXT_FRAMEWORK = {
       "DEFER": {"DELIVERY": {"OPTIONS": {"STREAM": False}}},
   }

Stop parallelism
~~~~~~~~~~~~~~~~

``DEFER["EXECUTOR"]["PARALLEL"]`` set to ``False`` runs every job on the request thread, one after another.
No pool thread starts and no database connection beyond that of the request opens.
A page then takes the sum of its boundaries, streamed or inline as the policy decides.

.. code-block:: python
   :caption: config/settings/prod.py

   NEXT_FRAMEWORK = {
       "DEFER": {"EXECUTOR": {"PARALLEL": False}},
   }

A project that keeps the pool but needs fewer connections lowers ``DEFER["EXECUTOR"]["OPTIONS"]["MAX_WORKERS"]`` instead.

Both settings together render a page the way a view without boundaries does.
``ROUTER["ENABLED"]`` and ``COMPRESSION["ENABLED"]`` are the switches of the other two features, and each is read from the settings like the two above.

Shed client-trip boundaries
~~~~~~~~~~~~~~~~~~~~~~~~~~~

A ``load``, ``visible``, or ``manual`` boundary is a separate ``GET`` to the page URL that carries the token in ``X-Next-Boundary``.
Neither setting above touches it, since the policy decides how one response delivers its boundaries and these requests are answered one by one.
The proxy sheds them by that header.

.. code-block:: nginx
   :caption: nginx.conf

   location / {
       if ($http_x_next_boundary != "") {
           return 503;
       }
       proxy_pass http://app;
   }

The client stops its retries of incomplete boundaries on a ``429`` or a ``5xx``, so the answer does not start a stampede.
A token the server already issued keeps working until ``MAX_AGE`` ends.
Raising ``DEFER["TOKENS"]["OPTIONS"]["EPOCH"]`` refuses every token issued so far, and each page that holds one reloads once.

Switch without a restart
~~~~~~~~~~~~~~~~~~~~~~~~

A policy that reads a flag the processes share, such as a cache key, answers ``INLINE`` while the flag is set.
The subclass keeps every row of the shipped policy and adds one.

.. code-block:: python
   :caption: config/policy.py

   from django.core.cache import cache
   from django.http import HttpRequest

   from next.defer import DefaultDeliveryPolicy, Delivery, DeliveryDecision, DeliveryFacts

   SWITCH = "incident:streams-off"


   class SwitchedDeliveryPolicy(DefaultDeliveryPolicy):
       def delivery(self, request: HttpRequest, facts: DeliveryFacts) -> DeliveryDecision:
           delivered = super().delivery(request, facts)
           if delivered.delivery is Delivery.STREAM and cache.get(SWITCH):
               return DeliveryDecision(Delivery.INLINE, "switch")
           return delivered

       def streams_partials(self, request: HttpRequest) -> bool:
           return not cache.get(SWITCH) and super().streams_partials(request)

.. code-block:: python
   :caption: config/settings/prod.py

   NEXT_FRAMEWORK = {
       "DEFER": {"DELIVERY": {"BACKEND": "config.policy.SwitchedDeliveryPolicy"}},
   }

The cache must be one every process reaches, such as Redis or Memcached, since a ``LocMemCache`` flag switches one process only.
``cache.set("incident:streams-off", True, None)`` from a shell turns streaming off on every process at its next request, and ``cache.delete("incident:streams-off")`` turns it back on.
The policy reads the cache once per page with boundaries.
Parallel jobs have no such seam, so ``PARALLEL`` takes a restart.

Verification
------------

Set the flag and request a page with a ``stream`` boundary.
The response arrives whole, ``page_stream_declined`` reports the reason ``switch``, and a receiver of that signal or the dashboard behind it counts the pages affected.

.. code-block:: bash
   :caption: shell

   curl -sN -o /dev/null -w "first byte %{time_starttransfer}s, total %{time_total}s\n" https://example.com/catalog/

A first byte that arrives with the total confirms the page does not stream, and a request after ``cache.delete`` streams again.

See also
--------

.. seealso::

   :doc:`/content/deployment/boundaries` for the topologies, the failure table, and the rolling deploy.
   :doc:`/content/topics/boundaries/streaming` for the rows of the delivery policy.
   :doc:`/content/deployment/checklist` for the checklist this recipe belongs to.
