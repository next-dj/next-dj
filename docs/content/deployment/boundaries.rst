.. _deployment-boundaries:

Deferred boundaries in production
=================================

A page with deferred boundaries runs jobs on threads the framework starts, streams its body, and carries tokens that outlive a request.
Each of the three depends on the server, the database, the proxy, and the rolling release around the process.
This page gathers the facts an operator needs in one place, and each section links to the page that owns the detail.

.. contents::
   :local:
   :depth: 2

.. _deployment-topologies:

Server topologies
-----------------

The server decides whether a page streams and how many threads and database connections a process holds.
Every row below holds per process.

.. list-table::
   :header-rows: 1
   :widths: 18 22 36 24

   * - Topology
     - The page
     - Threads and pools
     - What to set
   * - ``gunicorn`` sync workers, ``uwsgi`` without threads
     - Assembled whole, reason ``sync_worker``, since the worker serves one request at a time
     - The request thread, and pool threads for the jobs unless ``PARALLEL`` is ``False``, up to ``PER_REQUEST`` for one response
     - ``PARALLEL``, ``MAX_WORKERS``, or ``SYNC_WORKERS`` of ``"stream"`` to stream and hold the worker for the whole stream
   * - ``gunicorn`` with ``gthread`` or ``--threads`` above one, ``uwsgi --threads``
     - Streams while a stream slot is free, else assembled whole, reason ``capacity``
     - One request thread per worker thread, a pool of at most ``MAX_WORKERS`` threads started on demand
     - ``REQUEST_CONCURRENCY`` equal to the threads, ``MAX_STREAMS``, ``MAX_WORKERS``
   * - ``uvicorn``, ``daphne``, ``hypercorn``, ``granian`` and other ASGI servers
     - Streams while a stream slot is free
     - A thread per synchronous request from ``sync_to_async``, the pool, and one background loop thread started on first use
     - ``MAX_STREAMS``, ``MAX_REQUEST_JOBS``, ``MAX_WORKERS``, ``WRITE_TIMEOUT``
   * - ``manage.py runserver``
     - As the ASGI server of ``DEV_SERVER`` when it is installed, else as a threaded WSGI server
     - As the server it runs
     - Nothing for development
   * - Serverless, a function per request
     - As the stated ``SERVER``, assembled whole where the platform buffers
     - Pool threads of one instance, each with a connection
     - ``SERVER`` options and ``PARALLEL``, see :doc:`platforms`

The framework reads no server name.
It reads whether the interpreter runs the request under an event loop, and ``wsgi.multithread`` of a WSGI request, which ``gunicorn`` sets from ``--threads`` and ``uwsgi`` from its thread options.
A server that reports the fact wrongly is corrected with ``DEFER["SERVER"]["OPTIONS"]``, see :doc:`platforms`.
Workers of ``gevent`` and ``eventlet`` patch the threading module, and the framework has not been verified under them.

Three limits bound the concurrency of one process.

- ``MAX_STREAMS`` is the most open streams.
  ``"auto"`` is four per pool worker under ASGI, and under WSGI half of the larger of ``REQUEST_CONCURRENCY`` and the live request threads seen, at least one.
  A stream past the limit is assembled whole with the reason ``capacity``.
- ``REQUEST_CONCURRENCY`` is the requests the process serves at once, the threads of a WSGI worker or the concurrency limit of an ASGI one.
  It defaults to ``1``, so set it to the thread count of the worker, since the stream limit and the connection budget below both start from it.
- ``MAX_WORKERS`` is the most pool threads of the default ``LaneExecutor``, set with ``CONN_IDLE`` and ``MAX_QUEUE`` in ``DEFER["EXECUTOR"]["OPTIONS"]``.
  A pool thread idle for ``CONN_IDLE`` closes its connections and exits later.
  ``MAX_QUEUE`` bounds the waiting jobs, and a job that waits past ``QUEUE_TIMEOUT`` runs on its request thread.
  ``SHED_DELAY`` is the wait of the oldest queued job after which a new pool job settles as ``overloaded`` instead of loading a request thread.

The first time a reason of the deployment holds a page back, the process logs one ``WARNING`` on ``next.defer`` naming the setting, and ``page_stream_declined`` reports every such response.
``manage.py check --deploy`` does not know the server, so a project that expects streaming confirms it from outside with ``curl -N``, see :doc:`platforms`.

.. _deployment-connections:

Database connections
--------------------

Each pool thread holds a connection of its own per database alias, and so does each request thread.
The most connections one process holds on an alias is the sum below, and it is the figure ``next.W189`` prints under ``--deploy`` for a PostgreSQL alias without ``OPTIONS["pool"]``.

.. code-block:: text

   per process = REQUEST_CONCURRENCY + MAX_WORKERS
   per host    = per process * worker processes
   total       = per host * hosts

Compare the total with ``max_connections`` of the server, less the connections reserved for administration.
With ``gthread`` at eight threads, ``REQUEST_CONCURRENCY`` of ``8``, and the default ``MAX_WORKERS`` of ``8``, one process may hold ``16``, four processes ``64``, and eight ``128``.

The figure is a ceiling.
Pool threads start on demand, so the connections open at the peak of load rather than at startup, and a job that outran its deadline keeps its thread and connection until it returns.
``PARALLEL`` set to ``False`` keeps every job on the request thread, and a smaller ``MAX_WORKERS`` lowers the ceiling.

``CONN_MAX_AGE`` decides how long a pool thread keeps a connection.
At ``0`` the thread closes it after each job, so each boundary pays a new connection.
Above ``0`` the connection lives until the thread has been idle for ``CONN_IDLE``, which saves the reconnect and makes the ceiling above real.
Put the connections behind a pooler when the total does not fit, or set ``OPTIONS["pool"]`` on the alias, which ``next.W162`` and ``next.W172`` check against the same budget.

PgBouncer in transaction pooling mode suits the framework when two rules hold.
Set ``DISABLE_SERVER_SIDE_CURSORS`` to ``True`` on the alias, see :doc:`django:ref/databases`.
Keep state that a job needs in transaction-local form.

The statement timeout that ``DEADLINE_TO_DB`` sets and the replay of a ``ConnectionState`` both use ``set_config(..., true)``, which ends with the transaction and stays on the server connection that runs it.
A ``SET`` without ``LOCAL`` in project code, or an advisory lock held across transactions, leaks to the next client of the server connection.
See :ref:`deployment-pooler` for the serverless case and :doc:`/content/howto/replay-request-state-on-a-pool-thread` for the replay.

``DEADLINE_TO_DB`` defaults to ``"always"``, so a job on PostgreSQL runs under a statement timeout equal to the time left of ``DEADLINE``, and a hung query stops holding its thread.
Other database vendors get no timeout, and the job that outran its deadline keeps its thread until the driver returns.
A SQLite project keeps its jobs on the request thread unless ``SQLITE`` is ``"parallel"``, and a ``DEBUG`` run logs a warning when that default serialised two boundaries.

Proxy and CDN
-------------

A streamed page asks the proxy to stay out of the way in the response itself.
It sends ``X-Accel-Buffering: no``, so nginx forwards each write, and ``Cache-Control: private, no-cache``, so no cache keeps it.
It drops ``ETag``, ``Last-Modified``, and ``Content-Length``, and adds ``Vary: Accept-Encoding, Accept``.
The proxy still needs timeouts above the longest quiet gap of a stream.

.. code-block:: nginx
   :caption: nginx.conf

   upstream app {
       server 127.0.0.1:8000;
       keepalive 32;
   }

   server {
       location / {
           proxy_pass http://app;
           proxy_http_version 1.1;
           proxy_set_header Connection "";
           proxy_read_timeout 30s;
           send_timeout 15s;
       }
   }

``proxy_read_timeout`` counts the time between two reads from the application, so it needs to exceed the longest pause between writes, which ``DEADLINE`` plus ``WRITE_GRACE`` bounds.
``send_timeout`` counts the time between two writes to the client, and a value a few seconds above ``DEADLINE`` closes a connection whose client reads nothing.
That closes the case the application cannot reach under WSGI, where a client that stops reading holds the request thread, the stream slot, and the connections of the request, see :ref:`deployment-slow-clients`.
Under ASGI ``WRITE_TIMEOUT`` closes such a stream first, and the proxy timeout is the second line.
A proxy that ignores ``X-Accel-Buffering``, or a ``proxy_ignore_headers`` line that names it, buffers the stream, and a CDN that buffers is stated through ``"INCREMENTAL": False``, see :doc:`platforms`.

One layer compresses.
The application compresses its own responses and flushes each write, and a proxy that finds ``Content-Encoding`` passes the body through.
When the proxy compresses instead, set ``COMPRESSION["ENABLED"]`` to ``False``, since two layers repeat the work and a layer that buffers a streamed page to compress it removes the early shell.
See :ref:`deployment-compression`.

Caching follows from the headers above.
A streamed page is private, so no cache keeps it.
A page that declares a shared ``cache`` is assembled whole with the reason ``cache`` and stays cacheable, and the framework makes it private again when its render follows the visitor, see :doc:`/content/topics/caching`.
The fetch of a client-trip boundary and every zone request answer ``private, no-store``, and the CDN bypasses them by ``X-Next-Request``, see :doc:`/content/howto/cache-pages-on-a-cdn`.

The default token binding is the one setting that changes a shared page.
``"auto"`` binds the token to the signed-in user, which reads the session while the page renders, so a shared shell with a client-trip boundary leaves the shared cache.
``DEFER["TOKENS"]["OPTIONS"]["BINDING"]`` set to ``"none"`` keeps it shared, and :doc:`/content/howto/cache-pages-on-a-cdn` explains the trade.

Process lifecycle
-----------------

A server that forks workers after importing the application, such as ``gunicorn --preload``, forks before any deferred response has run.
Pool threads, the background loop, and the stream registry start lazily, so the parent holds none.
A fork hook also resets them in the child, together with the stream slots, the request-thread count, and the once-per-process log marks, so a child never inherits a parent's thread or slot.
Close the database connections the import opened before the fork, as Django asks of any forking server.

A termination signal stops the server first.
The server stops accepting requests and lets the running ones finish for its own grace period, and a stream ends by itself within ``DEADLINE`` plus ``WRITE_GRACE``.
Set the grace period of the server above ``DEADLINE`` plus ``WRITE_GRACE`` plus ``SHUTDOWN_TIMEOUT``, for example ``--graceful-timeout`` of ``gunicorn`` or ``--timeout-graceful-shutdown`` of ``uvicorn``.
As the interpreter exits, the pool refuses new jobs, fails the queued ones, and gives the running ones ``SHUTDOWN_TIMEOUT`` seconds, then its daemon threads are dropped and the background loop stops.
A job that outran its deadline and still runs is therefore cut at exit, and its connection closes with the process.

``max_requests`` of ``gunicorn`` and the same limit of other servers recycle a worker after a number of requests, after the current request ends.
The new worker starts without pool threads, and the first deferred responses start them again on demand.
``max_requests_jitter`` spreads the recycles, so the workers of one host do not restart their pools together.

Rolling deploy
--------------

Old and new processes serve the same page for a while, so a token or a runtime minted by one meets the other.

A boundary token is sealed with ``SECRET_KEY``, so every process of both releases must share it.
It carries a claims version, the ``EPOCH``, a profile of the token options its component declares, its component, props, URL, and permissions.
A token that the other release cannot open is refused in one of two ways.

- A stale one, an expired or version-mismatched token or one whose storage entry is gone, answers ``409`` with ``X-Next-Reload: token_expired``, and the page reloads once.
- A refused one, a token with another signature, profile, or component, answers ``403``.
  A ``load`` or ``visible`` boundary then reloads the page once, and a ``manual`` boundary or one refused again settles as ``forbidden``.

The reload is bounded by a flag in ``sessionStorage``, so a client does not loop between two releases.
A release that keeps the claims format, the key, and ``EPOCH`` opens tokens of the other in both directions, so an ordinary release shows no refusal.
Both kinds go to ``boundary_token_rejected`` and to ``next.defer``, stale ones at ``INFO``, so a rolling release shows as a short burst of ``409``.

The runtime and the assets carry versions as well.
Every partial request sends ``X-Next-Version`` and ``X-Next-Protocol``, and a process that serves another asset version or runtime protocol answers ``409``.
The client reloads the page once, and a second mismatch ends in a ``partial:error`` event instead of a loop, so a stale CDN cannot loop a page.
Run ``collectstatic`` before the first process of the new release accepts traffic, and keep the files of the previous release available until the old pages are gone, see :doc:`checklist`.

Rotate ``SECRET_KEY`` in two releases.
Add the new key to ``SECRET_KEY_FALLBACKS`` of every process first, while ``SECRET_KEY`` stays the old one, so each process verifies tokens sealed under either key.
Then promote the new key to ``SECRET_KEY`` and move the old one into ``SECRET_KEY_FALLBACKS``, and drop it after ``MAX_AGE`` has passed.
Promoting in one step during a rolling release makes the processes that still hold the old key refuse the tokens of the new ones as forged.
``EncryptedSeal`` rotates the same way, encrypting with the current key and decrypting with every fallback.

``DEFER["TOKENS"]["OPTIONS"]["EPOCH"]`` revokes every token issued before it.
Raise it on every process to cut off tokens at once, for example after a leak of props.
A process that still holds the old value refuses the new tokens as stale, so a client reloads once more during the roll.

Failure table
-------------

Each row names what the visitor sees, what the logs hold, and the signal that carries the event.
A response with a streamed body has sent its ``200`` before a boundary fails, so a failure inside a boundary never changes the status.

.. list-table::
   :header-rows: 1
   :widths: 20 28 30 22

   * - Failure
     - The visitor sees
     - The log
     - The signal
   * - An exception inside a boundary that has an ``error.djx``
     - The error UI in place of the boundary, status unchanged
     - ``next.boundaries`` at ``ERROR`` once per call site and quiet period, then ``DEBUG``, and nothing when ``REPORTER`` returned a digest
     - ``boundary_failed``, ``boundary_settled``
   * - An exception outside every boundary, before the headers, in the view or a guard
     - The usual Django error response
     - The usual Django records on ``django.request``
     - ``got_request_exception``
   * - An exception outside every boundary of a job, after the headers
     - The default error UI of the boundary, status unchanged
     - ``django.request`` at ``ERROR`` once per call site and quiet period
     - ``got_request_exception``, ``boundary_failed``, ``boundary_settled``
   * - A job that raises ``Http404`` or ``PermissionDenied`` after the headers
     - The error UI of kind ``not_found`` or ``forbidden`` with status ``200`` and ``noindex``
     - ``next.defer`` at ``WARNING`` that names ``guard=True``
     - ``boundary_settled``
   * - A job past its timeout or ``DEADLINE``
     - The error UI of kind ``timeout``
     - ``next.defer`` at ``WARNING`` once per call site and quiet period
     - ``boundary_settled``, ``page_stream_closed`` with ``timeouts`` and ``late``
   * - A job shed as ``overloaded``
     - The error UI of kind ``overloaded``
     - ``next.defer`` at ``WARNING``
     - ``boundary_settled``, ``page_stream_closed`` with ``shed``
   * - A router prefetch while the queue is behind
     - Nothing, the click loads the page
     - None
     - ``prefetch_shed``
   * - A stream that outran ``DEADLINE`` plus ``WRITE_GRACE``
     - The boundaries not yet written wait, and the client fetches each once more after a short random delay, only where a token or a zone lets it, so a streaming boundary outside a zone shows its error UI until the page reloads
     - ``next.defer`` at ``WARNING``
     - ``page_stream_closed`` with ``cut``, ``abandoned``, and ``dropped``
   * - A token storage that is down
     - A boundary minted without a token streams instead, and a reload answers ``409`` and reloads the page once
     - ``next.defer`` at ``ERROR`` with the traceback
     - ``boundary_token_rejected`` with reason ``storage_down`` on a reload
   * - A stale token
     - One page reload
     - ``next.defer`` at ``INFO`` at a bounded rate
     - ``boundary_token_rejected``
   * - A refused token
     - One page reload, then ``forbidden``
     - ``next.defer`` and ``django.security.BoundaryTokenError`` at ``WARNING`` at a bounded rate
     - ``boundary_token_rejected``
   * - A stream cut before its end marker, by ``SIGTERM``, ``WRITE_TIMEOUT`` or a dropped connection
     - Each boundary still ``loading`` turns ``error`` of kind ``incomplete`` once the document or the partial response ends, and the client fetches each one with a token or a zone once more after a short random delay, while a streaming boundary with no token outside a zone is not fetched again and needs a page reload, see :ref:`topics-boundaries-streaming-limits`
     - As the cause logs
     - ``page_stream_closed`` with ``aborted``
   * - A client that left
     - Nothing
     - None
     - ``page_stream_closed`` with ``aborted``, the queued jobs cancelled and the running ones finished
   * - A slow client under ASGI
     - The stream ends after ``WRITE_TIMEOUT``
     - ``next.defer`` at ``WARNING``
     - ``page_stream_closed`` with ``aborted``
   * - A slow client under WSGI
     - The stream continues at the pace of the client
     - None from the application
     - None until the proxy or the server closes the connection
   * - A page assembled whole instead of streamed
     - The whole page after its slowest boundary
     - ``next.defer`` once per process and reason, at ``WARNING`` for the reasons of the deployment
     - ``page_stream_declined``

The recipes for reading these into an error tracker and a tracer are in :doc:`/content/howto/send-boundary-failures-to-sentry-and-tracing`.
:doc:`/content/howto/switch-boundaries-off-in-an-incident` covers the switches for an incident.

See also
--------

.. seealso::

   :doc:`wsgi-asgi` for the server choice and capacity.
   :doc:`platforms` for the two transport facts and serverless hosts.
   :doc:`checklist` for the checklist before a release.
   :doc:`settings` for the production values of the keys named here.
