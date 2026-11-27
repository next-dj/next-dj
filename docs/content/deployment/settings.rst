.. _deployment-settings:

Production settings
===================

This page lists recommended ``NEXT_FRAMEWORK`` values for production.
Each entry explains why the production value differs from the development default.
For the full list of available keys, their defaults, and their semantics, see :doc:`/content/ref/settings`.

Each snippet below sets one key on an existing ``NEXT_FRAMEWORK`` dict.
Declare ``NEXT_FRAMEWORK = {}`` once before the first override, or merge the keys into a single literal as shown under :ref:`combining-keys`.
Keys left unset keep their framework default, because the project dict merges over the framework defaults one level deep and a key the project leaves out is never replaced, see :ref:`ref-settings-merge`.

Strict context
--------------

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {}
   NEXT_FRAMEWORK["STRICT_CONTEXT"] = True

Set ``STRICT_CONTEXT`` to ``True`` in production so a misconfigured context processor fails loudly.
See :ref:`ref-settings` for behaviour and exception types.

Strict loading
--------------

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK["STRICT_LOADING"] = True

Set ``STRICT_LOADING`` to ``True`` in production so a ``{% component %}`` name that does not resolve fails the request instead of serving a silently degraded page.
With ``DEBUG=False`` the client sees the generic 500 page, and the traceback appears only in the server log.
Without the flag a missed component renders as an empty string, which monitoring rarely catches.
A ``page.py`` that fails to import answers 500 with or without the flag, so monitoring on 5xx sees a broken deploy.
See :ref:`ref-settings` for the loudness table across ``DEBUG`` and the strict flags.

Component module loading
------------------------

``LAZY_COMPONENT_MODULES`` already defaults to ``False``, which is the production value, so a deployment writes the key only to turn lazy loading on.
The framework discovers the component tree eagerly in both modes, so the registry knows every component name before traffic.
The flag controls only when each ``component.py`` module is imported.

With the default ``False``, every ``component.py`` is imported during startup, so any import-time error surfaces before the first request.
With ``True``, a ``component.py`` is imported on the first render that resolves the component rather than during startup.
See :ref:`ref-settings` and :doc:`/content/topics/testing` for lazy behaviour and testing helpers.

Static backend
--------------

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK["STATIC_BACKENDS"] = [
       {"BACKEND": "notes.backends.TenantPrefixStaticBackend", "OPTIONS": {}},
   ]

Point at a custom backend where the asset URL varies per request, for example a per-tenant prefix.
A single CDN host in front of the static origin belongs in ``STATIC_URL`` instead, where every rendered path agrees with it, see :doc:`static-files`.
The default ``StaticFilesBackend`` is appropriate for single host deployments where the same process serves both HTML and static files.

Asset version
-------------

.. code-block:: python
   :caption: config/settings.py

   import os

   NEXT_FRAMEWORK["STATIC_VERSION"] = os.environ["BUILD_ID"]

Set a version only for a deployment that cannot run a hashed staticfiles manifest.
The value sets a ``v`` query parameter on every rendered asset URL, so a deploy invalidates the whole asset set at once, while a manifest reissues the URL of a changed file alone.
Read the value from the environment, because a value generated at startup differs per worker and makes a client refetch one file once per process.

JS context serializer
---------------------

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK["JS_CONTEXT_SERIALIZER"] = "next.static.PydanticJsContextSerializer"

Set the serializer when context values include types beyond the standard JSON set.
``PydanticJsContextSerializer`` handles Pydantic models and falls back to the Django JSON encoder for plain values.

.. note::

   ``PydanticJsContextSerializer`` requires the ``pydantic`` package, which is not a dependency of next.dj.
   Install it with ``uv add pydantic`` before enabling this serializer.
   Without ``pydantic`` the serializer fails to build, so a production render falls back to the JSON serializer and logs the error, while ``DEBUG`` or ``STRICT_LOADING`` raises ``ImportError``.

Page backends with context processors
-------------------------------------

.. code-block:: python
   :caption: config/settings.py

   from next.conf import extend_default_backend

   NEXT_FRAMEWORK["PAGE_BACKENDS"] = extend_default_backend(
       "PAGE_BACKENDS",
       OPTIONS={"context_processors": [
           "notes.context_processors.csp_nonce",
           "notes.context_processors.tenant",
       ]},
   )

Use ``extend_default_backend`` to patch the default page backend entry with production context processors.
The ``OPTIONS`` dict is merged, so the other default keys survive.

Form action backend
-------------------

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK["FORM_ACTION_BACKENDS"] = [
       {"BACKEND": "notes.backends.RateLimitedFormActionBackend"},
   ]

Register a custom backend that subclasses ``RegistryFormActionBackend`` and rate limits dispatch for endpoints exposed to anonymous users.
See :doc:`/content/howto/write-a-form-action-backend`.

Realtime
--------

.. code-block:: python
   :caption: config/settings.py

   import os

   NEXT_FRAMEWORK["REALTIME"] = {
       "BROKER": {
           "BACKEND": "next.realtime.RedisBroker",
           "OPTIONS": {
               "URL": os.environ["REDIS_URL"],
               "CLIENT": {"max_connections": 200},
               "SUBSCRIBE_MAX_CONNECTIONS": 1000,
           },
       },
   }

Set ``BROKER`` to ``RedisBroker``, because the default ``MemoryBroker`` reaches subscribers of one process only.
Until it does, ``manage.py check --deploy`` reports ``next.W149`` for a project that uses realtime.
The ``max_connections`` and ``SUBSCRIBE_MAX_CONNECTIONS`` values are placeholders.
The subscription pool needs one connection per open stream of the process, and the command pool one per Redis call the process runs at once, see :doc:`/content/howto/fan-out-across-processes-with-redis` and :ref:`deployment-capacity`.

Heartbeats, the gateway token, and the transports are covered in :doc:`checklist` and :doc:`/content/ref/realtime`.

.. _combining-keys:

Combining keys
--------------

When several recommendations apply at once, merge them into a single ``NEXT_FRAMEWORK`` literal.

.. code-block:: python
   :caption: config/settings.py

   from next.conf import extend_default_backend

   NEXT_FRAMEWORK = {
       "STRICT_CONTEXT": True,
       "STRICT_LOADING": True,
       "JS_CONTEXT_SERIALIZER": "next.static.PydanticJsContextSerializer",
       "PAGE_BACKENDS": extend_default_backend(
           "PAGE_BACKENDS",
           OPTIONS={"context_processors": [
               "notes.context_processors.csp_nonce",
               "notes.context_processors.tenant",
           ]},
       ),
       "FORM_ACTION_BACKENDS": [
           {"BACKEND": "notes.backends.RateLimitedFormActionBackend"},
       ],
   }

Keep only the keys the deployment changes.
The framework supplies the default for every key left out, so there is no need to duplicate the full default structures documented on :doc:`/content/ref/settings`.

Deferred boundaries
-------------------

The keys below decide capacity, time limits, token binding, compression, and transactions of a process that serves pages with deferred boundaries.
Each one has a default that suits a single process, and the page :doc:`boundaries` explains how they combine under each server.
:doc:`/content/ref/settings` holds the full shape of every scope.

``DEFER`` sets capacity.

- ``REQUEST_CONCURRENCY`` is the requests a process serves at once and sizes the stream limit and the connection budget, so set it to the threads of a WSGI worker.
- ``MAX_STREAMS`` caps the open streams of a process, and ``"auto"`` derives the cap from the server.
- ``EXECUTOR["OPTIONS"]["MAX_WORKERS"]`` caps the pool threads of the default ``LaneExecutor`` and with them the database connections beyond the request threads.
- ``EXECUTOR["PARALLEL"]`` set to ``False`` keeps every job on the request thread.
- ``EXECUTOR["OPTIONS"]["MAX_QUEUE"]``, ``EXECUTOR["QUEUE_TIMEOUT"]``, and ``EXECUTOR["SHED_DELAY"]`` bound the queue of the pool, and the default ``ADMISSION`` reads them to decide when a job moves to its request thread or is shed.
- ``MAX_REQUEST_JOBS`` caps the request-lane work of a process under ASGI.

``DEFER`` sets time limits.

- ``DEADLINE`` is the seconds every job of a response has before it settles as ``timeout``, and ``WRITE_GRACE`` the seconds past it a stream keeps writing.
- ``WRITE_TIMEOUT`` closes an ASGI stream whose client takes no write for that long.
- ``DEADLINE_TO_DB`` passes the remaining time to PostgreSQL as a statement timeout and defaults to ``"always"``.
- ``EXECUTOR["OPTIONS"]["SHUTDOWN_TIMEOUT"]`` is the grace a pool gives running jobs as the process exits.

``DEFER["TOKENS"]["OPTIONS"]`` sets the tokens of client-trip boundaries.

- ``BINDING`` ties a token to its visitor and defaults to ``"auto"``, which binds the signed-in user, and ``"none"`` keeps a shared page shared.
- ``MAX_AGE`` is the life of a token in seconds, and ``EPOCH`` revokes every token issued before its last change.
- ``SEAL`` of ``EncryptedSeal`` hides the props in the token, and ``STORAGE`` and ``CACHE_ALIAS`` move the claims to a cache every process reaches.

``BOUNDARIES`` sets what the visitor and the error tracker see.

- ``REPORTER`` receives every failure an error boundary contained, and ``KINDS`` hides the cause of a failure from the visitor when set to ``"opaque"``.
- ``ATOMIC`` sets when a boundary opens a savepoint, and ``"writes"`` opens one at the first write.

``LOADER`` sets batching.

- ``WINDOW`` is the seconds a loader waits to gather lookups, and ``MAX_BATCH`` the most lookups one batch holds.

``COMPRESSION`` decides which layer compresses.

- ``ENABLED`` set to ``False`` leaves compression to the proxy, and ``STREAMS`` and ``PADDING`` shape how streams compress and how much random padding blunts BREACH.

``TRANSACTIONS`` decides which requests run in a transaction.

- ``SAFE`` and ``UNSAFE`` default to ``"auto"``, which wraps each alias with ``ATOMIC_REQUESTS`` and nothing else, and a database held in a transaction keeps the jobs of that request on its thread.

.. code-block:: python
   :caption: config/settings/prod.py

   NEXT_FRAMEWORK = {
       "DEFER": {
           "REQUEST_CONCURRENCY": 8,
           "EXECUTOR": {"OPTIONS": {"MAX_WORKERS": 4}},
       },
   }

The values above are placeholders, and a load run against the target hardware sets the real ones, see :ref:`deployment-capacity`.

Runtime script overrides
------------------------

With ``CSP_NONCE`` on, every tag the framework writes carries the nonce the CSP middleware minted, the ``next.min.js`` shell included, so a strict policy needs no manual injection, see :doc:`/content/security/csp-and-nonce`.
A nonce belongs to one response and makes a shared page private, so a site a CDN serves sets ``CSP_NONCE`` to ``False`` and allows its scripts by hash or by source.
``NEXT_FRAMEWORK["NEXT_JS_OPTIONS"]`` accepts template overrides, which keep the ``{nonce_attr}`` placeholder, and ``ScriptInjectionPolicy`` values described on :ref:`ref-settings` and in :doc:`/content/topics/static-assets/js-context`.

Site, CSRF, and consent
-----------------------

Production names its origin in ``SITE["URL"]`` and keeps ``SITE["INDEXABLE"]`` at ``"auto"``, and a staging deployment sharing the settings answers ``INDEXABLE`` through a callable that names the production hosts, see :doc:`/content/topics/seo/site`.
``CSRF_DELIVERY`` stays at ``"auto"`` behind a CDN, so a shared page carries no token, see :doc:`/content/howto/cache-pages-on-a-cdn`.
``CONSENT["OPTIONS"]["secure"]`` stays ``None`` or ``True`` under HTTPS, which ``next.W119`` checks.

Template and asset staleness
----------------------------

``settings.DEBUG`` controls this behaviour rather than a ``NEXT_FRAMEWORK`` key.
It is the only behaviour difference between a warm cache and a cold one.

The composed-template cache, the compiled component template cache, and the co-located asset plans each hold a snapshot of what they read from disk.
Every probe that compares that snapshot against the disk is gated on ``settings.DEBUG`` through the ``next.utils.template_edits_watched`` predicate.
With ``DEBUG`` off none of them runs, and a warm request issues no ``stat`` call at all.

The consequence is that a running production process never notices a file changed underneath it.
An edited ``template.djx``, an edited or newly added ``layout.djx``, and a ``template.css`` created next to a page are all invisible until the process restarts.
Only a registration still invalidates an asset plan, because a stem or a kind registered at startup moves no file and is compared through a generation counter rather than a ``stat``.

This is a deliberate trade of edit visibility for a syscall-free hot path.
A deployment publishes assets through ``collectstatic`` and restarts its workers, so the alternative buys nothing a release does not already do.
Read :doc:`/content/internals/page-discovery` and :doc:`/content/internals/static-pipeline` for the snapshots and the checks that read them.

See also
--------

.. seealso::

   :doc:`checklist` for the full pre-flight list.
   :doc:`/content/ref/settings` for every available key.
