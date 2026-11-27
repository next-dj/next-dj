.. _ref-settings:

Settings
========

Module summary
--------------

This page lists every key inside ``NEXT_FRAMEWORK`` with its framework default and a short description.
Set ``NEXT_FRAMEWORK`` in ``settings.py`` to override any of these values.

For production-specific recommendations (which values to change and why), see :doc:`/content/deployment/settings`.

Key naming
----------

Keys inside ``NEXT_FRAMEWORK`` carry no ``DEFAULT_`` prefix.
The dict itself is the framework defaults namespace.
A plural ``*_BACKENDS`` key holds an ordered list of sources the manager consults in order.
``PARTIAL_BACKENDS`` is the exception.
Partial rendering uses a single protocol backend, so only the first entry runs.

A singular ``*_BACKEND`` key holds the one engine for a concern.
A subsystem prefix (``PAGE_``, ``COMPONENT_``, ``STATIC_``, ``FORM_``, ``URL_``, ``TEMPLATE_``, ``JS_``, ``PARTIAL_``) groups related keys.
``NEXT_JS_OPTIONS`` stands outside the prefix scheme and configures the bundled client runtime.
``METADATA``, ``REALTIME``, ``SITE``, ``SEO``, ``CONSENT``, ``TRANSACTIONS``, ``BOUNDARIES``, ``LOADER``, ``DEFER``, ``COMPRESSION``, and ``ROUTER`` stand outside it as the scopes of one concern each, and ``CSRF_DELIVERY``, ``CSP_NONCE``, and ``DEV_SERVER`` as single values.

.. _ref-settings-merge:

How values merge
----------------

``NEXT_FRAMEWORK`` merges over the framework defaults one level deep, and one level only.
A key the project sets replaces the default for that key whole.
A key the project leaves out keeps its default.

A value of a type the key does not take leaves the key at its default, ``manage.py check`` reports it, and every request raises :exc:`~django.core.exceptions.ImproperlyConfigured` naming the key, the type found, and the type it takes until the value is fixed.
Nothing below the top level is combined, so a nested ``OPTIONS`` dict, an entry inside a backend list, and a sub-key of a single backend dict all come from the project alone once the top-level key is present.
The rule holds for every key, with no per-key exception.

This is what Django does with its own configuration mappings.
Defining :doc:`STORAGES <django:ref/settings>` overrides the default configuration rather than merging with it, and ``TEMPLATES``, ``DATABASES``, and ``CACHES`` each read the whole list or mapping the project wrote.
Filling in what a single entry leaves out belongs to the code that consumes the entry, the same place Django fills a ``DATABASES`` alias with ``ATOMIC_REQUESTS`` or a ``TEMPLATES`` entry with ``APP_DIRS``.

Every scope is such a consumer and answers the default of a sub-key the project leaves out, see :doc:`conf`.
``METADATA``, ``REALTIME``, ``SITE``, ``SEO``, and ``CONSENT`` are read through ``next.conf.scopes.scope_value``, ``DEFER`` through ``next.conf.defer.defer_option``, and ``TRANSACTIONS``, ``BOUNDARIES``, ``LOADER``, ``COMPRESSION``, and ``ROUTER`` through a reader that takes the whole scope at once.
A nested ``OPTIONS`` inside a scope still comes from the project alone once the project sets it.

A single backend dict follows the rule like any other key.
``FORM_WIZARD_BACKEND`` set to ``{"OPTIONS": {...}}`` alone carries no ``BACKEND``, which ``manage.py check`` reports as ``next.E069`` and the wizard manager answers with :exc:`~django.core.exceptions.ImproperlyConfigured` on first use.
Write the whole entry, ``BACKEND`` included.

Each key accepts one shape, and a value of any other type is never merged into it.

- The list keys (``PAGE_BACKENDS``, ``COMPONENT_BACKENDS``, ``STATIC_BACKENDS``, ``FORM_ACTION_BACKENDS``, ``PARTIAL_BACKENDS``, ``TEMPLATE_LOADERS``, ``FORM_ANCHOR_FILES``) accept a list.
- The mapping keys (``NEXT_JS_OPTIONS``, ``FORM_WIZARD_BACKEND``, ``METADATA``, ``REALTIME``, ``SITE``, ``SEO``, ``CONSENT``, ``TRANSACTIONS``, ``BOUNDARIES``, ``LOADER``, ``DEFER``, ``COMPRESSION``, ``ROUTER``) accept a dict.
- The dotted-path keys (``URL_RESOLVER``, ``DEPENDENCY_RESOLVER``, ``DEV_SERVER``, ``COMPONENT_TEMPLATE_LOADER``) accept a string naming an importable class, and ``JS_CONTEXT_SERIALIZER`` accepts such a dotted path or ``None``.
- ``CSRF_DELIVERY`` accepts a string naming a mode.
- ``STATIC_VERSION`` accepts a string or ``None``.
- ``URL_NAME_TEMPLATE`` also accepts a string, but a format template such as ``page_{name}`` rather than a dotted path.
- The bool flags (``LOGIN_REQUIRED``, ``STRICT_CONTEXT``, ``STRICT_LOADING``, ``LAZY_COMPONENT_MODULES``, ``FORM_AUTODISCOVER``, ``STATIC_DISCOVERY_CACHE``, ``CSP_NONCE``) accept any value and pass through ``bool()``.

A mistyped value is reported at ``manage.py check`` as ``next.E076``, or under the code the key owns where it carries one, and a bool flag holding a non-bool is reported as ``next.W072``.
The checks and the startup read the default in its place, so ``manage.py check``, ``runserver``, and ``migrate`` reach the report rather than a traceback, and the refusal waits for the first request.
``JS_CONTEXT_SERIALIZER`` is the one key a mistyped value never refuses requests for, since its reader falls back to the JSON serializer and ``next.W042`` warns about it.
A top-level key that is not in this catalog is reported as ``next.E035``, and the merged view raises ``AttributeError`` for it, so a typo never reaches a read site as a default.
See :doc:`system-checks` for the conditions.

To change one key of a default backend entry without writing the entry out, build the replacement value with ``next.conf.extend_default_backend``, described under `Patching defaults`_.

Backends
--------

PAGE_BACKENDS
~~~~~~~~~~~~~

List of page backend configurations.

Default value.

.. code-block:: python

   [
       {
           "BACKEND": "next.urls.FilePageBackend",
           "DIRS": [],
           "APP_DIRS": True,
           "PAGES_DIR": "pages",
           "OPTIONS": {"context_processors": []},
       }
   ]

Each entry is passed to the backend constructor.
Keys are ``BACKEND``, ``DIRS``, ``APP_DIRS``, ``PAGES_DIR``, and ``OPTIONS``.
A ``FilePageBackend`` entry may leave out ``OPTIONS``, which then reads as the empty mapping, and ``DIRS``, which then reads as the empty list.

``DIRS`` accepts two kinds of entry.
An absolute or project-relative path that resolves to an existing directory is added as an extra page root.
A plain string that does not resolve to a directory is treated as a skip name.
The router will not enter any directory with that name during the file walk.

See :doc:`/content/topics/file-router` for the full semantics including examples.

COMPONENT_BACKENDS
~~~~~~~~~~~~~~~~~~

List of component backend configurations.

Default value.

.. code-block:: python

   [
       {
           "BACKEND": "next.components.FileComponentsBackend",
           "DIRS": [],
           "COMPONENTS_DIR": "_components",
       }
   ]

STATIC_BACKENDS
~~~~~~~~~~~~~~~

List of static backend configurations.

Default value.

.. code-block:: python

   [
       {
           "BACKEND": "next.static.StaticFilesBackend",
           "OPTIONS": {},
       }
   ]

The first static backend's ``OPTIONS`` dict accepts ``JS_CONTEXT_POLICY``, a dotted path to a conflict-resolution class.
The static manager applies the policy when two context functions publish the same key for serialisation.
See :doc:`/content/topics/static-assets/js-context` under *Key conflict policy* for the available policies and an example.

The same ``OPTIONS`` dict accepts ``DEDUP_STRATEGY``, a dotted path to a dedup strategy class the collector instantiates once per request to drop assets several components register more than once.
See :doc:`/content/topics/static-assets/deduplication` for the bundled strategies and the custom-strategy protocol.

FORM_ACTION_BACKENDS
~~~~~~~~~~~~~~~~~~~~

List of form action backend configurations.

Default value.

.. code-block:: python

   [
       {
           "BACKEND": "next.forms.RegistryFormActionBackend",
           "OPTIONS": {"SHAPER": "next.partial.FormShaper"},
       }
   ]

``OPTIONS["SHAPER"]`` is the dotted path to the ``ResponseShaper`` class a backend asks before its default response envelope, and an entry without it keeps ``next.partial.FormShaper``.
The option applies only to a backend that passes its config to the base initialiser, so a subclass with its own ``__init__`` calls ``super().__init__(config)``, and one that does not keeps ``next.partial.FormShaper``.
The backend imports and instantiates the class the first time it shapes a response, and a path that does not import, a class that cannot be built without arguments, or one without ``shape`` and ``validate`` raises :class:`~django.core.exceptions.ImproperlyConfigured` there.
``manage.py check`` resolves the shaper of every loaded backend and reports the same failures as ``next.E241``.
See :doc:`forms` under *Backends* for the protocol.

FORM_ANCHOR_FILES
~~~~~~~~~~~~~~~~~

List of file basenames that receive ``page`` scope during auto-registration.
A form class declared in a file whose basename appears in this list is keyed to the path of that file inside its app or below ``BASE_DIR``.
All other files produce ``shared`` scope.

Default value ``None``, which uses the built-in set ``["page.py", "component.py"]``.
Set to a list of strings to replace the default set.
The configured list is used as is, so include ``"page.py"`` and ``"component.py"`` explicitly when they should stay anchors.

FORM_WIZARD_BACKEND
~~~~~~~~~~~~~~~~~~~

Single form wizard backend configuration.
The backend persists a wizard's per-step draft data between requests.

Default value.

.. code-block:: python

   {
       "BACKEND": "next.forms.SessionFormWizardBackend",
       "OPTIONS": {},
   }

The bundled ``SessionFormWizardBackend`` stores each step's cleaned data in the Django session through a typed value codec, so drafts share the durability of the session engine.
It reads no ``OPTIONS`` keys.
The bundled ``CacheFormWizardBackend`` stores drafts in the Django cache instead.
It reads two keys from ``OPTIONS``.
``CACHE_ALIAS`` names the cache to use, defaulting to ``"default"``, and ``TIMEOUT`` sets the draft expiry in seconds, defaulting to ``SESSION_COOKIE_AGE``.

Set ``BACKEND`` to a dotted path that subclasses ``FormWizardBackend`` to swap the persistence layer.
A project value replaces the default dict whole, as :ref:`ref-settings-merge` describes, so the key names its ``BACKEND`` even when the point of setting it is ``OPTIONS``.
``manage.py check`` reports a value that is no dict as ``next.E051``, a missing or non-string ``BACKEND`` as ``next.E069``, a path that does not import as ``next.E070``, and a class outside the family as ``next.E071``.
See :doc:`/content/topics/forms/wizard-backend` for the contract, the codec, and a custom backend.

PARTIAL_BACKENDS
~~~~~~~~~~~~~~~~

List of partial protocol backend configurations.
The first entry is active and owns the patch wire format that partial rendering serialises over HTTP and Server-Sent Events.
Entries after the first are ignored, and ``manage.py check`` reports them with ``next.W071``.
The value has to be a list, since any other shape leaves the default in place and every request raises :exc:`~django.core.exceptions.ImproperlyConfigured`.
``manage.py check`` reports it as ``next.E067`` from the partial checks, which own the shape probe for this key.

Default value.

.. code-block:: python

   [
       {
           "BACKEND": "next.partial.JsonPartialProtocolBackend",
           "OPTIONS": {
               "VERSION": None,
               "PUSH_WIZARD_STEPS": False,
               "SSE": {
                   "HEARTBEAT_SECONDS": 25,
                   "RETRY_MS": 3000,
               },
           },
       },
   ]

The ``OPTIONS`` keys tune the active backend.
``VERSION`` is the source of the ``X-Next-Version`` stamp and has three modes.
``None`` derives the version from the staticfiles manifest when the active storage hashes its files, and falls back to the stable string ``"0"`` when it does not, so a project needs no setting to get a hash out of a manifest storage.
The sentinel ``"manifest"`` takes the same runtime path and adds a declared requirement, which ``manage.py check`` enforces by reporting ``next.W069`` when the storage writes no manifest.
Any other string pins a release tag by hand.

The resolved string is memoised for the life of the configuration, so a manifest replaced under a running process keeps serving the version resolved before it until a settings reload or a restart.
A version that never changes leaves the version guard silent at runtime, so a deploy of new assets cannot ask a connected client to reload.

``PUSH_WIZARD_STEPS`` is the global default for pushing wizard steps to browser history, which a wizard's ``Meta.push_steps`` overrides per wizard.
``SSE.HEARTBEAT_SECONDS`` is the keepalive period in seconds of a ``PatchEventStream`` with an async source and of every ``TopicStream``, and ``SSE.RETRY_MS`` is the ``EventSource`` reconnect hint in milliseconds sent in the leading stream frame.
Under ASGI the same period bounds a blocked write, so a client whose send stays blocked for one to two heartbeats is dropped.

``SSE`` is a dict or ``None``, the heartbeat ``None`` or a finite number above ``0``, and the retry hint ``None`` or an integer from ``0`` that is not a bool.
``None`` takes the default, and a wrong value falls back to the default at runtime while ``next.E214`` reports it.
``REALTIME['HEARTBEAT']`` times the WebSocket ping and recheck instead, see `REALTIME`_.

See :doc:`/content/topics/partial-rendering/reference` for the wire protocol and :doc:`/content/topics/partial-rendering/sse` for the stream contract.

REALTIME
~~~~~~~~

Mapping for broker and stream settings.

Default value.

.. code-block:: python

   {
       "BROKER": {
           "BACKEND": "next.realtime.MemoryBroker",
           "OPTIONS": {"QUEUE_SIZE": 256},
       },
       "TRANSPORTS": None,
       "TICKET_MAX_AGE": 60,
       "TICKET_BINDING": "next.realtime.SessionBinding",
       "WS_PATH": "/_next/ws/",
       "HEARTBEAT": 25.0,
       "GATEWAY_TOKEN": None,
       "REPLAY": {"SIZE": 100, "TTL": 300},
       "PRESENCE": {"WINDOW": 1.0, "SIZE": 100, "MEMBER_BYTES": 512},
       "HANDLERS": None,
       "UPSTREAM": {
           "RATE": 10.0,
           "BURST": 20,
           "MAX_BYTES": 16384,
           "CONCURRENCY": 16,
           "TIMEOUT": 10.0,
       },
       "TUNNEL": {"CONCURRENCY": 4, "TIMEOUT": 30.0, "MAX_REPLY_BYTES": 1048576},
   }

``BROKER.BACKEND`` names a ``BrokerBackend`` subclass, and ``BROKER.OPTIONS`` belongs to that backend.

.. list-table:: Broker options per shipped backend
   :header-rows: 1
   :widths: 30 26 44

   * - Backend
     - Option and default
     - Meaning
   * - ``next.realtime.MemoryBroker``
     - ``QUEUE_SIZE``, ``256``
     - The bound of each subscriber queue, a positive integer.
       A full queue drops its oldest message.
   * - ``next.realtime.RedisBroker``
     - ``URL``, ``"redis://localhost:6379/0"``
     - The Redis connection URL, a non-empty string, and ``next.E213`` reports any other value.
   * - ``next.realtime.RedisBroker``
     - ``PREFIX``, ``"next"``
     - The prefix of every counter key, replay log key, and Pub/Sub channel.
       It needs a non-colon character and no ``{`` or ``}``, since a brace moves the Redis Cluster hash tag off the topic name, and ``next.E192`` reports any other value.
   * - ``next.realtime.RedisBroker``
     - ``HEALTH_CHECK_INTERVAL``, ``10.0``
     - Seconds a Redis connection may stay idle before the client pings it before its next command.
       Zero turns this client-side check off.
       Subscriptions run their own liveness ping regardless, see :doc:`/content/howto/fan-out-across-processes-with-redis`.
   * - ``next.realtime.RedisBroker``
     - ``CLIENT``, ``{}``
     - A mapping of keyword arguments passed whole to the Redis ``from_url`` call, for the synchronous and the asynchronous client alike.
       Its entries override the ``health_check_interval`` and ``socket_keepalive`` the broker sets, while ``decode_responses`` is always forced on, so a ``CLIENT`` that sets it to anything but ``True`` is ``next.E187``.
       ``max_connections`` is the usual entry, see :doc:`/content/howto/fan-out-across-processes-with-redis`.
   * - ``next.realtime.RedisBroker``
     - ``SUBSCRIBE_MAX_CONNECTIONS``, unset
     - The cap of the subscription pool, a positive integer, and ``next.E216`` reports any other value.
       Every open stream holds one connection of this pool, which stays apart from the command pool that publishing uses.
       Unset, the pool takes ``max_connections`` from ``CLIENT``, or the redis-py default of 100, and ``manage.py check --deploy`` reports ``next.W151`` when neither is set.

``MemoryBroker`` serves one process and does not coordinate sequence numbers across processes.
``manage.py check --deploy`` reports a ``MemoryBroker`` or a subclass of it as ``next.W149`` once realtime is in use, meaning ``NEXT_FRAMEWORK`` sets a non-empty ``REALTIME`` mapping, an ``@on`` handler is registered, the URLconf routes the ticket introspection view, or a routed ``page.py`` holds a ``TopicStream``.
A project that never touches realtime gets no warning, and a deployment that runs one process lists the code in ``SILENCED_SYSTEM_CHECKS``.
``RedisBroker`` needs the ``broker`` extra, which installs ``redis`` 8 or newer.
A custom backend validates its own ``OPTIONS`` keys in its ``check_options`` classmethod, which ``manage.py check`` calls, and reports its deployment warnings from ``check_deployment``, which ``manage.py check --deploy`` calls, see :doc:`realtime`.

``TRANSPORTS`` defaults to ``None`` and selects ``["ws", "sse"]`` in a process that mounts a ``WebSocketApp``, or ``["sse"]`` otherwise.
A process that serves pages without mounting the socket, such as a WSGI worker next to a separate ASGI deployment, needs an explicit ``["ws", "sse"]`` to offer it.
An explicit list that names ``ws`` offers it whether or not the process mounts the socket, so the first negotiation in a process without one logs a warning on the ``next.realtime.transports`` logger, once per process.
Set it to ``["sse"]`` to disable WebSocket negotiation.

``TICKET_MAX_AGE`` is the lifetime of a WebSocket ticket in seconds and accepts values from 1 to 600, which ``next.E182`` enforces.

``TICKET_BINDING`` is the dotted path of the ``TicketBinding`` subclass that decides what a WebSocket ticket is bound to, and ``next.E211`` reports any other value.
The default ``next.realtime.SessionBinding`` binds the session key.
Whatever the binding, the ticket is signed from ``SECRET_KEY`` and stays reusable for every handshake until ``TICKET_MAX_AGE`` elapses.

``next.realtime.SignedCookieBinding`` binds a signed-in user by its pk and session auth hash and an anonymous visitor by a ``next_visitor`` cookie, so it serves the signed-cookie ``SESSION_ENGINE``, a first visit without a session included.
The negotiation reply sets that cookie with ``HttpOnly`` and the path, domain, ``Secure``, ``SameSite`` and lifetime of the session cookie, and renews it on every negotiation.
Its socket rechecks the user by pk and auth hash without writing a cookie, so a password change or an inactive user closes it while a logout elsewhere does not, and ``next.W147`` warns when a server-side ``SESSION_ENGINE`` runs next to it.
A subclass changes ``cookie_name`` or ``salt``, and a project binding implements ``issue``, ``credential``, ``subject`` and ``live``, with ``live_session`` for SSE and ``respond`` for what the negotiation reply carries, see :doc:`realtime` and :doc:`/content/howto/bind-tickets-to-your-own-identity`.

``HANDLERS`` selects the modules that register ``@on`` upstream handlers.
``None`` imports the ``realtime`` module of every installed app, a list of dotted module names imports only those modules, and ``[]`` imports none.
Any other value is reported as ``next.E197``.

``WS_PATH`` sets the same-origin WebSocket route and defaults to ``/_next/ws/``, and it is the only place the route is configured.

``GATEWAY_TOKEN`` is the bearer secret of the ticket introspection endpoint, which stays off until a URLconf includes it.
It defaults to ``None``, and a routed endpoint with no token refuses every caller and is reported as ``next.E188``.
Read it from the environment rather than committing it.

``HEARTBEAT`` is the WebSocket ping and recheck interval in seconds, a positive finite number that ``next.E185`` enforces.
It drives every WebSocket served by a ``TopicSocket``, which sends a ``ping`` frame at that interval and rechecks its session and topic grants.
An SSE stream, a ``TopicStream`` or a manual ``PatchEventStream``, takes its heartbeat from the ``SSE.HEARTBEAT_SECONDS`` option of ``PARTIAL_BACKENDS`` instead.
``TopicStream(heartbeat_seconds=...)`` overrides that option for one SSE stream.
Keep both intervals below the shortest idle timeout of every proxy and load balancer in front of the streams.

``REPLAY`` sizes the per-topic log a reconnecting stream catches up from.
``SIZE`` is the number of newest messages kept per topic, an integer from ``0`` to ``10000`` that ``next.E189`` enforces, and ``0`` turns the log off.
``TTL`` is the number of seconds the log of a topic lives after its last publication, a positive number up to ``31536000``, one year, that ``next.E190`` enforces.

A missing key takes its default, any key besides the two is reported as ``next.E035``, and a ``REPLAY`` value that is not a mapping is reported as ``next.E191``.
A broker reads the window once when it is created, so a settings reload rebuilds the broker with it.
With a nonzero ``SIZE`` and a ``BACKEND`` that keeps no log, ``next.W143`` warns that every reconnect resynchronises.
See :doc:`realtime` for the replay order and the guarantees it gives.

``PRESENCE`` limits the attendance a stream reports when it passes ``presence`` to ``TopicStream``.
``WINDOW`` is the coalescing window in seconds, a positive number up to ``60`` that ``next.E194`` enforces.
Every change inside an open window joins the one ``next:presence`` event that the broker publishes when the window closes.

``SIZE`` is the number of members one event lists, an integer from ``0`` to ``1000`` that ``next.E195`` enforces, and ``0`` sends the count alone.
The ``count`` stays exact beyond ``SIZE``.
``MEMBER_BYTES`` is the largest compact JSON of one member in bytes, an integer from ``1`` to ``4096`` that ``next.E196`` enforces, and a larger member raises ``InvalidMemberError`` on its join.

A missing key takes its default, any key besides the three is reported as ``next.E035``, and a ``PRESENCE`` value that is not a mapping is reported as ``next.E193``.
A broker reads the limits once when it is created.
With an explicit ``PRESENCE`` and a ``BACKEND`` that keeps no presence, ``next.W144`` warns that every stream asking for presence fails to start.
See :doc:`realtime` for the snapshot semantics.

``UPSTREAM`` limits the frames a WebSocket client sends after ``sub``, the ``up`` events and the tunneled ``http`` requests alike.
They draw on one token bucket per connection that holds ``BURST`` tokens and refills at ``RATE`` tokens a second.
``RATE`` is a positive number up to ``1000`` that ``next.E202`` enforces, and ``BURST`` is an integer from ``1`` to ``1000`` that ``next.E203`` enforces.
The defaults suit a page that sends small, occasional events and a few form posts in a row, so raise them only for a page that streams input such as a cursor.

``MAX_BYTES`` caps one ``up`` frame in UTF-8 bytes as it arrives, an integer from ``1`` to ``65536`` that ``next.E204`` enforces, while a tunneled request is bounded by the 64 KiB frame limit alone.
``CONCURRENCY`` is the number of ``up`` events one connection runs at once, an integer from ``1`` to ``64`` that ``next.E209`` enforces, and an event past it is refused with ``limit`` without counting towards ``4429``.
``TIMEOUT`` is the seconds one ``up`` event waits for its handler before the ``ack`` answers ``timeout``, a positive number up to ``600`` that ``next.E210`` enforces.
A missing key takes its default, any key besides the five is reported as ``next.E035``, and an ``UPSTREAM`` value that is not a mapping is reported as ``next.E201``.

``TUNNEL`` configures the request tunnel that runs form actions over an open WebSocket, and ``False`` turns it off.
``CONCURRENCY`` is the number of requests one connection runs at once, an integer from ``1`` to ``64`` that ``next.E206`` enforces.
``TIMEOUT`` is the seconds one request may take, a positive number up to ``600`` that ``next.E207`` enforces.
``MAX_REPLY_BYTES`` caps the body of one reply, an integer from ``1`` to ``67108864`` that ``next.E208`` enforces.

A missing key takes its default, any key besides the three is reported as ``next.E035``, and a value that is neither ``False`` nor a mapping is reported as ``next.E205``.
``WebSocketApp`` reads both mappings once when it is created, and the ``hello`` frame tells the client ``MAX_BYTES`` and ``TIMEOUT``.
See :doc:`realtime` for the frames and the refusals.

See :doc:`realtime` for the broker API and :doc:`/content/howto/stream-over-websockets` for setup.

Application modules
-------------------

``NextFrameworkConfig.ready`` imports five conventional modules from every installed app, the third-party ones included, the way Django's admin imports every ``admin.py``.
All ``deps`` modules load first, then all ``permissions`` modules, then all ``forms`` modules, then all ``realtime`` modules, then all ``signals`` modules, so a form finds the providers, named dependencies, and object permissions of every app already registered.
An app without the module is skipped, and a module that fails to import raises its own error out of ``ready``.
The ``deps``, ``permissions``, ``realtime``, and ``signals`` sweeps always run, and ``FORM_AUTODISCOVER`` turns off the ``forms`` one.

FORM_AUTODISCOVER
~~~~~~~~~~~~~~~~~

Boolean that controls whether the ``forms`` submodule of every installed app is imported on startup.

Default value ``True``.

When ``True``, shared forms declared in ``app/forms.py`` register before the first request arrives.
``next.forms.autodiscover_forms`` runs the same sweep on a manual call and honours the key as well.
A project that sets it to ``False`` imports the ``forms`` modules it wants from ``AppConfig.ready``.

Routing
-------

URL_NAME_TEMPLATE
~~~~~~~~~~~~~~~~~

Template used to compute URL names from directory paths.

Default value ``"page_{name}"``.

The framework normalises the path through the parser and substitutes ``{name}`` with the result.
Slashes, square brackets, colons, hyphens, and underscores are collapsed to a single underscore, and the leading and trailing underscores are stripped.
The directory path ``notes/[id]`` becomes ``notes_id`` and produces the URL name ``page_notes_id``.

URL_RESOLVER
~~~~~~~~~~~~

Dotted path to the resolver class that wraps the framework urlpatterns.

Default value ``"next.urls.TrieURLResolver"``.

The class is instantiated with a root route pattern and a lazy pattern sequence, the CSRF, form-action, page, and SEO patterns of ``include("next.urls")`` or the patterns of one ``pages()`` tree, so it owns every resolution under both include points.
The default ``TrieURLResolver`` resolves a static route through a dictionary lookup and a parameterised route through a walk over a segment trie, with the final match delegated to standard Django pattern resolution.
Set the key to ``"django.urls.resolvers.URLResolver"`` to opt out of the trie and run every resolution through Django's plain linear scan.

A custom value must name a :class:`~django.urls.URLResolver` subclass whose constructor accepts the same pattern and pattern-sequence pair.
A path that fails to import, or one that names anything other than a ``URLResolver`` subclass, raises :exc:`~django.core.exceptions.ImproperlyConfigured` at startup.
The key is read through ``next.backends.resolve_setting_class``, documented in :doc:`backends`, which is also what raises those two errors.
The resolver is rebuilt on settings reload, so ``override_settings`` swaps it without a restart.

See :doc:`/content/internals/url-router` for the resolution algorithm.

Dependency injection
--------------------

DEPENDENCY_RESOLVER
~~~~~~~~~~~~~~~~~~~

Dotted path to the resolver class that fills dependency-injected parameters.

Default value ``"next.deps.DependencyResolver"``.

The class owns every injection the framework performs, from page views and ``@context`` callables to form actions and component renderers.
A custom value must name a ``next.deps.DependencyResolver`` subclass.
The framework ships a second one, ``next.deps.linear.LinearDependencyResolver``, which resolves each parameter by walking the providers instead of replaying a compiled plan.
It answers identically but pays the whole provider walk for every parameter of every call, where the default resolver compiles that work into a plan once and replays it, so it earns its place as a differential oracle in the test suite rather than as a production choice.

Widening the public ``skips`` predicate is the usual reason to subclass, because it decides which parameters a compiled plan carries at all.
A path that fails to import, or one that names anything other than a ``DependencyResolver`` subclass, raises :exc:`~django.core.exceptions.ImproperlyConfigured`.
The key is read through ``next.backends.resolve_setting_class``, documented in :doc:`backends`, the same helper ``URL_RESOLVER`` goes through.

The key is read at startup and again on every settings reload, never per request, so ``override_settings`` swaps the resolver without a restart.
The framework holds one resolver singleton behind a shared holder, so the named class is adopted by building an instance of it and putting it behind that holder, which every reference reads through.
The subclass is built like any other object, so its ``__init__`` runs and it is free to declare slots and bases of its own.
The object the swap replaces takes its state with it, the compiled plans as well as anything registered on it at runtime, so a dependency registered through ``resolver.dependency`` is registered again after a swap.

See :doc:`/content/internals/di-resolver` for the resolution algorithm and :doc:`deps` for the resolver API.

Development server
------------------

DEV_SERVER
~~~~~~~~~~

Dotted path to the ``ASGIServer`` class ``manage.py runserver`` serves the project on.

Default value ``"next.server.asgi.UvicornServer"``.

The class is built without arguments, and its ``run(app, **options)`` serves the app until it stops, the way ``uvicorn.run`` does.
The default runs uvicorn, and without uvicorn installed the command is Django's WSGI ``runserver``.
A project-named class is imported on every ``runserver`` after the system checks, and a path that fails to import or names no class with a ``run`` method is ``next.E212`` at ``manage.py check`` and at ``runserver``.
``manage.py runserver --wsgi`` skips the class and serves WSGI whatever the key names.
See :doc:`management` for the options ``run`` receives and an example server.

Templates
---------

TEMPLATE_LOADERS
~~~~~~~~~~~~~~~~

List of template loader dotted paths.

Default value.

.. code-block:: python

   ["next.pages.loaders.DjxTemplateLoader"]

Loaders are consulted in order, first match wins.

JavaScript context
------------------

NEXT_JS_OPTIONS
~~~~~~~~~~~~~~~~~

Dict passed to ``NextScriptBuilder.from_options`` for the bundled ``next.min.js`` runtime.
Keys are the injection ``policy`` (``auto``, ``disabled``, or ``manual``) and the optional string templates ``preload_template``, ``script_tag_template``, ``init_template``, and ``chunk_template``, the script a streamed page pushes each chunk with.
A ``policy`` no member names reads as ``auto``, and a template ``.format`` cannot fill is replaced by the default one.
The ``{payload}`` of ``chunk_template`` is escaped for the text of a ``<script>`` element alone, so a template that places it anywhere else, an attribute included, is replaced by the default one too.
``manage.py check`` reports both, and a render meets them under :ref:`topics-failure-containment`.

``script_tag_template`` also renders the tag of each bundle a page declares, such as ``boundaries``, ``reload``, ``more``, or ``router``, with its load attributes added after ``<script``, and the default tag serves a template that holds no ``<script>`` start tag, a custom element such as ``<script-x>`` included.
A bundle loads with ``async fetchpriority="low"``, with ``async`` on a streamed page for the ``boundaries`` bundle and for every bundle of a page that holds a ``load`` boundary, and with ``defer`` on a page that holds a ``load`` boundary and does not stream.
Under ``manual`` those bundle tags open the ``{% collect_scripts %}`` slot, so the tag that loads the runtime goes above it, and under ``disabled`` none renders, which ``next.W181`` reports.

Default value ``{}`` (automatic injection with default templates).

Serialisation of ``window.Next.context`` is controlled by ``JS_CONTEXT_SERIALIZER`` and by ``@context(..., serialize=True)``, not by this dict.

See :doc:`/content/topics/static-assets/js-context` under *Runtime script options* for the full table and examples.

JS_CONTEXT_SERIALIZER
~~~~~~~~~~~~~~~~~~~~~~~

Dotted path to a class that implements the ``JsContextSerializer`` protocol.
The class is instantiated with no arguments and its ``dumps`` method encodes every value bound for ``window.Next.context``.

Default value ``None``, which selects the built-in ``JsonJsContextSerializer``.

``resolve_serializer`` reads this setting on every call, so ``override_settings`` takes effect without a restart.
``manage.py check`` reports a value that is no dotted-path string as ``next.W042``, a path that does not import as ``next.W079``, a path naming something other than a class as ``next.W080``, a class that refuses to instantiate with no arguments as ``next.W081``, and an instance without a ``dumps`` method as ``next.W082``.
A value that cannot serialize falls back to ``JsonJsContextSerializer`` with an error in the log.
The same failure raises when ``DEBUG`` or ``STRICT_LOADING`` is on, see :ref:`topics-failure-containment`.

See :doc:`static` under *JS context serializer* for the protocol and the bundled serializers.

Access
------

LOGIN_REQUIRED
~~~~~~~~~~~~~~

When ``True``, every page, action, and zone request whose access rule declares nothing and is not public requires a logged-in user, and a component follows the page or the zone it renders in.
A ``page.public()`` lifts the requirement for its page, and ``page.public(inherit=True)`` for every page below it as well.
Listing ``LoginRequiredMiddleware`` or a subclass of it in ``MIDDLEWARE`` turns the same behaviour on with the key left out, and the framework then takes the login URL and the field name from the listed middleware.
The key closes the page trees alone, so a Django view of the project outside them needs the middleware or :func:`~django.contrib.auth.decorators.login_required` of its own.

Default value ``False``.
``manage.py check`` warns with ``next.W130`` about ``False`` written beside the middleware and with ``next.W131`` about ``True`` without ``django.contrib.auth`` and its ``AuthenticationMiddleware``.
:ref:`topics-access-default-login` describes the behaviour and how ``page.public()`` differs from :func:`~django.contrib.auth.decorators.login_not_required`.

Transactions
------------

TRANSACTIONS
~~~~~~~~~~~~

Dict holding the transaction rule of ``next.transactions``, which names per request the databases whose transaction wraps it.

Default value.

.. code-block:: python

   {"SAFE": "auto", "UNSAFE": "auto", "ALIASES": ("default",), "RULE": None}

``SAFE`` decides for ``GET``, ``HEAD``, ``OPTIONS``, and ``TRACE``, and ``UNSAFE`` for every other method.
``True`` means a transaction on each alias of ``ALIASES`` opened on entry, and ``False`` none.
``"writes"`` means the same transaction opened right before the first write or :func:`~django.db.transaction.on_commit` hook on each alias.
``"auto"`` follows Django's ``ATOMIC_REQUESTS``, so every alias that sets it gets a transaction opened on entry for every method, and an unsafe method also wraps ``ALIASES``, which keeps a project without ``ATOMIC_REQUESTS`` on a transaction for ``POST`` alone.

``ALIASES`` is a list of ``DATABASES`` aliases, or ``"all"`` for every alias ``DATABASES`` declares, nested the way ``ATOMIC_REQUESTS`` nests them, so the last alias holds the outermost transaction and commits last.
An alias ``DATABASES`` does not declare is skipped, so a project without a database opens no transaction.

``RULE`` is the dotted path of a callable taking the request and the view kind, and it replaces ``SAFE``, ``UNSAFE``, and ``ALIASES`` when set.
The kind is ``page`` for a page, ``zone`` for a zone request to a page, ``boundary`` for a boundary reload, ``navigate`` for a client router navigation, or ``action`` for a form action.
The callable returns the aliases to wrap on entry, or an ``AtomicOverride`` of ``next.transactions`` that names the mode too.
Its answer passes the same filter as ``ALIASES``, so a single alias name counts as a one-item tuple, an undeclared alias is skipped, and a repeated one opens once.
An ``AtomicOverride`` with a mode other than the three raises :exc:`~django.core.exceptions.ImproperlyConfigured`.

Under ``True`` the transaction opens eagerly, so a :func:`~django.db.transaction.on_commit` hook registered before the first query still waits for the commit, and a request that wraps no alias touches no connection.
Under ``"writes"`` the reads before the first write run in autocommit, a request that writes nothing touches no connection, and a hook registered before the first write opens the transaction and waits for its commit, see :ref:`howto-run-a-page-in-a-transaction`.
The aliases come from ``django.db.connections``, the source ``ATOMIC_REQUESTS`` reads, and the scope is read once and again after a settings reload or a change of the connection settings.

Every view next.dj mounts for pages and form actions, zone requests included, follows this rule in place of the ``ATOMIC_REQUESTS`` wrapping, which other Django views keep, and is marked with :func:`~django.db.transaction.non_atomic_requests` on every alias.
The access checks of a page and of a form action run before the transaction opens, so a refused request opens none, and the checks of a form action on its bound row run inside it, see :doc:`views` and :doc:`forms`.

``page.transaction()`` in a ``page.py`` overrides the rule for one page, and ``@action(atomic=...)`` or ``Meta.atomic`` on a form class for one action, see :doc:`pages` and :doc:`forms`.
``page.transaction()`` covers the full page, zone, boundary, and navigation requests of its page and none of the actions the page declares.
A form action answers only ``POST``, so under the default ``UNSAFE`` a :func:`~django.db.transaction.on_commit` hook registered inside it fires when the action's transaction commits, not when it is registered.
An action whose handler does slow work outside the database, such as a sleep, an outbound HTTP call, or sending mail, opts out with ``atomic=False`` or ``Meta.atomic = False``, so it holds no connection inside an open transaction while that work runs.

A missing key takes its default, any key besides the four is reported as ``next.E035``, and a value of the wrong type is reported as ``next.E076`` and raises :exc:`~django.core.exceptions.ImproperlyConfigured` at the first read of the scope.
``manage.py check`` reports a ``RULE`` that does not import or names no callable as ``next.E219``, the same path raises :exc:`~django.core.exceptions.ImproperlyConfigured` when the rule is first read, and an ``ALIASES`` entry missing from ``DATABASES`` is reported as ``next.W179``.
A database alias with ``ATOMIC_REQUESTS`` set under a ``SAFE``, ``UNSAFE``, or ``RULE`` that stops following it is reported as ``next.W154``.

Deferred work
-------------

DEFER
~~~~~

Dict holding the settings of deferred boundaries, see :doc:`/content/topics/boundaries/index`.

Default value.

.. code-block:: python

   {
       "EXECUTOR": {
           "BACKEND": "next.defer.LaneExecutor",
           "OPTIONS": {
               "MAX_WORKERS": 8,
               "MAX_QUEUE": 64,
               "CONN_IDLE": 60.0,
               "SHUTDOWN_TIMEOUT": 5.0,
           },
           "PER_REQUEST": 4,
           "QUEUE_TIMEOUT": 0.05,
           "SHED_DELAY": 1.0,
           "MAX_REQUEST_JOBS": 16,
           "PARALLEL": True,
           "SQLITE": "request",
           "THREAD_LOCAL_MIDDLEWARE": (),
       },
       "DELIVERY": {
           "BACKEND": "next.defer.DefaultDeliveryPolicy",
           "OPTIONS": {"STREAM": True, "BOTS": None, "SYNC_WORKERS": "inline"},
       },
       "SERVER": {
           "BACKEND": "next.defer.DefaultServerProbe",
           "OPTIONS": {
               "INCREMENTAL": None,
               "CONCURRENT": None,
               "BUFFERED_MARKS": None,
           },
       },
       "TOKENS": {
           "BACKEND": "next.defer.StackedTokenBackend",
           "OPTIONS": {
               "STORAGE": "next.defer.InlineStorage",
               "SEAL": "next.defer.SignedSeal",
               "BINDING": "auto",
               "CARRIER": "header",
               "MAX_AGE": 600,
               "MAX_BYTES": 2048,
               "MAX_STORED_BYTES": 65536,
               "CACHE_ALIAS": "default",
               "EPOCH": 1,
           },
       },
       "CONNECTION_STATE": [],
       "THREAD_STATE": [],
       "ADMISSION": {"BACKEND": "next.defer.QueueDelay", "OPTIONS": {}},
       "DEADLINE": 10.0,
       "DEADLINE_TO_DB": "always",
       "WRITE_GRACE": 2.0,
       "WRITE_TIMEOUT": 10.0,
       "REQUEST_CONCURRENCY": 1,
       "MAX_BOUNDARIES": 64,
       "MAX_PER_SITE": 4,
       "MAX_DEPTH": 4,
       "MAX_INSTANCES": 1000,
       "MAX_STREAMS": "auto",
       "NOSCRIPT": True,
   }

``EXECUTOR`` names the ``BoundaryExecutor`` that runs deferred jobs on the pool lane, ``next.defer.LaneExecutor`` by default.
Its ``OPTIONS`` belong to that backend, and an option the project leaves out keeps its default.
The keys beside ``BACKEND`` and ``OPTIONS`` belong to the framework, which reads them whatever the backend.

The ``OPTIONS`` of ``LaneExecutor`` size a pool that resets each Django connection between jobs.
``MAX_WORKERS`` is the most pool threads the process starts, on demand, and a thread idle for ``CONN_IDLE`` seconds closes its connections and later exits.
Every pool thread holds a database connection of its own, so one process may hold ``REQUEST_CONCURRENCY`` plus ``MAX_WORKERS`` connections per alias, see ``next.W189``.
``MAX_QUEUE`` is the most jobs the queue of the pool holds.

As the interpreter exits, the pool refuses new jobs, fails the queued ones, and gives the running ones ``SHUTDOWN_TIMEOUT`` seconds before their daemon threads are dropped.
A settings reload stops the old pool the same way.

``PER_REQUEST`` is the most pool jobs one response holds at once, and the size of a group the later calls of one site run in.
A pool job waits at most ``QUEUE_TIMEOUT`` in the queue before its request thread takes it, and ``MAX_REQUEST_JOBS`` bounds the request-lane work of the process under ASGI.
Once the oldest job in the pool queue has waited ``SHED_DELAY``, the process is behind, and a new pool job settles as ``overloaded`` at once rather than loading a request thread.

``PARALLEL`` set to ``False`` keeps every job on its request thread.
``SQLITE`` of ``"request"`` keeps the jobs of a project with a SQLite alias on the request thread, and ``"parallel"`` lets a file database use the pool.
``THREAD_LOCAL_MIDDLEWARE`` lists the dotted paths of middleware that keep request state on the thread, which pins jobs to the request thread until a ``THREAD_STATE`` backend covers them.

``DELIVERY`` names the ``DeliveryPolicy`` that decides how a response delivers its boundaries.
The default one takes three ``OPTIONS``.
``STREAM`` set to ``False`` assembles every response inline.
``BOTS`` is the case-insensitive pattern of the user agents assembled inline, and ``None`` names the common crawlers.
``SYNC_WORKERS`` is ``"inline"`` or ``"stream"`` for a server that serves one request at a time.

``SERVER`` names the ``ServerProbe`` that tells once per request whether the response reaches its client write by write and whether the server serves other requests meanwhile, see :doc:`/content/topics/boundaries/streaming`.
The default one takes ``INCREMENTAL`` and ``CONCURRENT``, each ``True`` or ``False`` to state the fact over every detection and ``None`` to detect it.
Its ``BUFFERED_MARKS`` lists the request keys and key prefixes that the buffering adapter of the project leaves, as ``None`` for no marks or a dict with a ``WSGI`` and an ``ASGI`` entry, each a dict of ``KEYS`` and ``PREFIXES`` lists of strings.
A transport that is not incremental assembles every page inline, and one that is not concurrent follows ``SYNC_WORKERS``, see :doc:`/content/deployment/platforms`.

``TOKENS`` names the token backend of client-trip boundaries.
The ``OPTIONS`` of the default one name the ``STORAGE``, ``SEAL``, and ``BINDING`` classes and the ``CARRIER`` place.
``MAX_BYTES`` is the largest token, ``MAX_STORED_BYTES`` the largest claims a cache storage keeps, and a change of ``EPOCH`` refuses every token minted before it.
Tokens are signed with ``SECRET_KEY``, and ``SECRET_KEY_FALLBACKS`` keeps the tokens minted before a rotation valid.

``BINDING`` takes one of three forms, see :doc:`/content/topics/boundaries/loading`.

- A short name, ``"auto"``, ``"none"``, ``"session"``, or ``"user"``, where the default ``"auto"`` binds the user ``AuthenticationMiddleware`` puts on the request and nothing without one.
- The dotted path of a concrete ``SubjectBinding`` subclass that takes no arguments.
- The dotted path of a callable that takes the request and returns the subject as text or ``None``.

``CARRIER`` names where the browser sends the token on a reload.
``"header"`` sends it in the ``X-Next-Boundary`` header, and ``"body"`` in the ``_nb`` field of a ``POST`` body.
``"query"`` sends it in the ``_nb`` query field, which access logs and ``Referer`` headers see.

``CONNECTION_STATE`` and ``THREAD_STATE`` list the dotted paths of ``ConnectionState`` and ``ThreadState`` subclasses, which replay per-connection and thread-local state on a pool thread, see :doc:`/content/howto/replay-request-state-on-a-pool-thread`.

``ADMISSION`` names the ``Admission`` that decides for each pool job between the pool queue, the request thread, and shedding.
The default ``next.defer.QueueDelay`` takes no ``OPTIONS`` and reads ``QUEUE_TIMEOUT``, ``SHED_DELAY``, and ``OPTIONS["MAX_QUEUE"]`` of ``EXECUTOR``.
It hands a job to its request thread once the queue lags past ``QUEUE_TIMEOUT`` or holds ``MAX_QUEUE`` jobs.

Time limits
^^^^^^^^^^^

``DEADLINE`` is the seconds every job of a response has from the moment the response opened, after which a job settles as ``timeout``.
``WRITE_GRACE`` is the seconds past it a stream still writes settled jobs before it ends.
``WRITE_TIMEOUT``, above ``0``, is the seconds one write of a stream under ASGI may wait for the client before the stream closes, freeing its slot and its jobs.
A WSGI server writes on its own thread beyond the reach of that setting.

``DEADLINE_TO_DB`` hands the remaining time of a job to PostgreSQL as its statement timeout, at least 50 milliseconds.
On the request lane it applies only to a connection outside a transaction of the request.
``"always"`` sets it in a transaction opened for that alone before the first query, ``"state"`` only inside the transaction a ``ConnectionState`` replay opens, and ``False`` never.
The timeout is set with ``set_config(..., true)``, local to that transaction, so it ends with it and holds under PgBouncer transaction pooling.
Other database vendors get no timeout.

Every duration of the scope is at most one day, ``86400`` seconds or ``"86400s"``, and a longer or non-finite one is outside its shape.

Response limits
^^^^^^^^^^^^^^^

``REQUEST_CONCURRENCY`` is the requests one process serves at once, the threads of a WSGI worker or the concurrency limit of an ASGI one.
It sizes the connections ``next.W162`` compares a pool against, and it is the floor of the request threads ``MAX_STREAMS`` of ``"auto"`` counts under WSGI.
``MAX_STREAMS`` is the most open streams of a process.
Its ``"auto"`` means four per pool worker under ASGI, and otherwise half the larger of ``REQUEST_CONCURRENCY`` and the live request threads that served a page with deferred boundaries, at least one.

Four keys bound the jobs of one response.
``MAX_BOUNDARIES`` bounds the jobs that run on their own, and ``MAX_PER_SITE`` those of one call site that do.
``MAX_DEPTH`` bounds the nesting depth, and ``MAX_INSTANCES`` the calls that queue at all.

``NOSCRIPT`` sends a reader without scripts from a streamed page to the page assembled whole, the same address with ``_nojs=1``, and hides the loading UI meanwhile.

Token checks
^^^^^^^^^^^^

``TOKENS["OPTIONS"]`` of the shipped token backend checks its static values together.
``MAX_AGE`` is an integer from ``1`` or the dotted path of a callable taking the request and the claims, and another value is reported as ``next.E233`` and raises :exc:`~django.core.exceptions.ImproperlyConfigured` at the first read of ``DEFER``.
A token seals its expiry at mint, and a redeem accepts it only before the earlier of that second and its mint second plus the lifetime ``MAX_AGE`` answers then, so a callable can shorten the life of a token and never extend it.
A ``MAX_AGE`` above one day draws the ``next.W175`` warning.
A ``SECRET_KEY`` or an entry of ``SECRET_KEY_FALLBACKS`` shorter than 32 characters is reported as ``next.E231`` while a component can mint a token.

``SpillStorage`` keeps the claims inside the token while the whole token fits ``MAX_BYTES`` and stores larger claims in the cache, so it has no threshold of its own.
``CacheStorage`` and ``SpillStorage`` write to ``CACHE_ALIAS``, and Django unpickles a cache entry before the token's MAC is checked, so the alias must name a cache whose writers are trusted exactly like those of the session cache.
Leaving ``CACHE_ALIAS`` unset with either storage draws the ``next.W180`` warning, and an alias that stores where the session engine keeps sessions is reported as ``next.E234``.
``SignedSeal`` signs a token without hiding it, so a client reads its props, the component name, the scope, the URL, and the permission codenames the boundary renders under, while ``EncryptedSeal`` hides them all.

Missing and unknown keys
^^^^^^^^^^^^^^^^^^^^^^^^

A missing key takes its default, nested keys included, so a project that sets ``DELIVERY["OPTIONS"]["STREAM"]`` keeps the defaults of the other ``DELIVERY`` keys.
An unknown key at any level of the mapping is reported as ``next.E035``, and a value outside its shape is reported as ``next.E076`` and raises :exc:`~django.core.exceptions.ImproperlyConfigured` at the first read of ``DEFER``, one message naming every such value with the shape it takes.
The ``OPTIONS`` of a backend other than the default one belong to that backend and are not checked.
A server runs no system checks, so the error stops a misconfigured process at its first request rather than serving it on defaults.
A backend path of the scope that does not import or names no subclass of its base is reported as ``next.E219``.

BOUNDARIES
~~~~~~~~~~

Dict holding the settings of every error boundary and the default loading and error templates, see :doc:`/content/topics/boundaries/errors`.

Default value.

.. code-block:: python

   {
       "LOADING_TEMPLATE": None,
       "ERROR_TEMPLATE": None,
       "RAISE": False,
       "REPORTER": None,
       "ATOMIC": "writes",
       "KINDS": "detailed",
       "DEBUG_DETAIL": True,
   }

``LOADING_TEMPLATE`` names the template a deferred component without a ``loading.djx`` renders, ``None`` for none, which ``next.W157`` reports.
``ERROR_TEMPLATE`` names the template a deferred component without an ``error.djx`` renders, ``None`` for the built-in error UI, a neutral sentence with a retry button.
``RAISE`` re-raises each contained failure, so every boundary is off.
``REPORTER`` names a callable that reports a failure and returns its digest, or ``None`` for the framework log.

``ATOMIC`` chooses ``"writes"``, ``True``, or ``False`` for what a failed subtree rolls back in autocommit.
``KINDS`` of ``"opaque"`` reads every kind with a status as ``error``, and ``DEBUG_DETAIL`` shows the traceback under ``DEBUG``.
The keys apply to every error boundary, inline ones included, whether or not the page defers anything.
A missing key takes its default, an unknown key is reported as ``next.E035``, and a value outside its shape as ``next.E076``, which also raises :exc:`~django.core.exceptions.ImproperlyConfigured` at the first boundary.

LOADER
~~~~~~

Dict holding the batch limits of every ``next.deps.Loader`` built without its own, see :doc:`/content/howto/batch-lookups-with-a-loader`.

Default value.

.. code-block:: python

   {"WINDOW": 0.001, "MAX_BATCH": 500}

``MAX_BATCH`` is the most keys one call of a batch function receives, an integer from ``1``.
A batch that reaches it closes at once and the next key opens another one.
``WINDOW`` is the longest a batch stays open after its first key so that concurrent callers join it, a duration from ``0`` written as a number of seconds or as a literal such as ``"1ms"`` or ``"2s"``.
The batch closes earlier once every deferred job of the response that runs or is about to run waits on a loader or has ended, see :doc:`/content/topics/boundaries/async`.

A ``Loader`` built with ``max_batch=`` or ``window=`` keeps its own limit, and one built without them reads the setting each time it opens or fills a batch, so a settings reload reaches loaders built before it.
A missing key takes its default, an unknown key is reported as ``next.E035``, and a value outside its shape as ``next.E076``, which also raises :exc:`~django.core.exceptions.ImproperlyConfigured` at the first batch.

Compression
-----------

COMPRESSION
~~~~~~~~~~~

Dict holding the settings of response compression, read by ``next.middleware.CompressionMiddleware`` and by the streams that compress themselves, see :doc:`compression`.

Default value.

.. code-block:: python

   {
       "ENABLED": True,
       "BACKENDS": [
           {
               "BACKEND": "next.compression.ZstdBackend",
               "OPTIONS": {"LEVEL": 3, "WINDOW": 17},
           },
           {
               "BACKEND": "next.compression.BrotliBackend",
               "OPTIONS": {"LEVEL": 4, "WINDOW": 18},
           },
           {"BACKEND": "next.compression.GzipBackend", "OPTIONS": {"LEVEL": 4}},
       ],
       "ORDER": ("zstd", "br", "gzip"),
       "MIN_SIZE": 1024,
       "CONTENT_TYPES": (
           "text/*",
           "application/json",
           "application/*+json",
           "application/x-ndjson",
           "application/javascript",
           "application/xml",
           "application/*+xml",
           "image/svg+xml",
       ),
       "STREAMS": "self",
       "SSE": True,
       "SSE_TOPICS": "off",
       "PADDING": 32,
       "COALESCE": 0.002,
       "RESPECT_NO_TRANSFORM": True,
       "EXCLUDE": None,
   }

``ENABLED`` switches compression on and off for the middleware and for every self-compressing stream at once.
``ORDER`` is the server preference among the codings, and a coding whose library does not import is left out of it at runtime.
``MIN_SIZE`` is the smallest buffered body in bytes that is compressed.

Codec backends
^^^^^^^^^^^^^^

``BACKENDS`` lists the codecs the process loads, each a dict with the dotted path of a ``CompressionBackend`` class in ``BACKEND`` and an optional ``OPTIONS``.
The first entry of a coding answers it.
``OPTIONS`` takes two integer keys, both optional, and any other key is reported as ``next.E076``.
``LEVEL`` is the compression level, and ``WINDOW`` the stream window as a base-2 logarithm.
A key left out, or a value outside the range of the codec, takes the default of the codec.

gzip takes no window, its window is fixed at 32 KB.

What is compressed
^^^^^^^^^^^^^^^^^^

``CONTENT_TYPES`` lists the lower-case media types that are compressed, matched in one of three ways.
An entry matches exactly, as a ``type/*`` family, or as a ``type/*+suffix`` structured suffix.
A structured suffix matches every subtype ending in it, such as ``application/vnd.next.patches+json`` for ``application/*+json``.
``RESPECT_NO_TRANSFORM`` leaves a response with ``Cache-Control: no-transform`` uncompressed.
``EXCLUDE`` is ``None`` or the dotted path of a callable taking the request and the response that returns ``True`` to keep the response uncompressed.

Compressed streams
^^^^^^^^^^^^^^^^^^

``STREAMS`` names who compresses a next.dj document stream.
``"self"`` leaves it to the stream itself, and ``"middleware"`` to the middleware, the choice for a project whose other middleware rewrites streamed bodies.
The choices of ``STREAMS`` are the members of ``StreamOwner``, and those of ``SSE_TOPICS`` the members of ``TopicMode``, both in ``next.conf.compression``.
``SSE`` decides whether ``text/event-stream`` responses are compressed.
``SSE_TOPICS`` decides how a realtime topic stream is compressed, ``"off"`` for not at all, ``"reset"`` for a fresh window per event on a codec that can drop its window, and ``"shared"`` for one window across the stream.

``COALESCE`` is how long a streamed page writer waits for more ready output before it compresses and flushes, in seconds or as ``"2ms"``.
The middleware applies it to an async stream it wraps other than an event stream, which flushes on its first chunk, once 16 KB of input gathers, or after a pause of ``COALESCE``.
``0`` flushes every chunk.
A sync stream the middleware wraps is compressed and flushed per non-empty chunk.
Under ASGI a write larger than 16 KB is compressed in a worker thread rather than on the event loop.

``PADDING`` is the most random characters of the filler that ends each compressed body and each flushed write of a self-compressing stream.
It is also the most bytes of the random file name in a gzip header, and ``0`` turns the padding off.

Compression checks
^^^^^^^^^^^^^^^^^^

A missing key takes its default.
An unknown key is reported as ``next.E035``, and a value outside its shape is reported as ``next.E076`` and raises :exc:`~django.core.exceptions.ImproperlyConfigured` at the first read of the scope.
A ``BACKENDS`` or ``EXCLUDE`` path that names nothing usable is reported as ``next.E219``, which names the failing entry.
An ``ORDER`` coding no backend serves, a ``LEVEL`` or ``WINDOW`` outside the range of its codec, and a codec the project names in ``ORDER`` or ``BACKENDS`` without its library are reported as ``next.E228``.
``STREAMS`` of ``"middleware"`` without ``next.middleware.CompressionMiddleware`` in ``MIDDLEWARE`` is reported as ``next.W193``.

Client router
-------------

ROUTER
~~~~~~

Dict holding the settings of the client router, which answers a same-document navigation with only the slot of the deepest layout the old and the new page share.

Default value.

.. code-block:: python

   {
       "ENABLED": True,
       "SCOPE": "pages",
       "EXCLUDE": (),
       "FOREIGN": "hard",
       "PREFETCH": "intent",
       "PREFETCH_DELAY": 0.065,
       "PREFETCH_TTL": 30.0,
       "PREFETCH_SHELL_TTL": 300.0,
       "PREFETCH_ENTRIES": 16,
       "PRESS": "mouse",
       "SNAPSHOTS": 10,
       "PROGRESS_DELAY": 0.15,
       "PROGRESS": True,
       "TRANSITIONS": False,
       "INTERCEPT": "opt_in",
       "SPECULATION": None,
       "CUT": "next.router.VerifiedCut",
       "DIGEST": "next.router.ChromeDigest",
       "SHED": "next.router.LoadShed",
       "ACTIVE_LINKS": {
           "ENABLED": True,
           "PASS": "template",
           "CONTAINERS": ("nav", "[role=navigation]", "[data-next-nav]"),
       },
   }

The browser reads every key but ``ENABLED``, ``SPECULATION``, ``CUT``, ``DIGEST``, ``SHED``, and ``ACTIVE_LINKS["PASS"]`` from the ``$router`` payload entry.
The string choices of ``SCOPE``, ``FOREIGN``, ``PREFETCH``, ``PRESS``, ``INTERCEPT``, ``SPECULATION``, and ``ACTIVE_LINKS["PASS"]`` are the members of ``RouterScope``, ``ForeignMode``, ``PrefetchMode``, ``PressMode``, ``InterceptMode``, ``Eagerness``, and ``LinkPass`` of ``next.conf.router``, which equal the strings, see :doc:`conf`.

``ENABLED``, default ``True``
   Switches the router on.
   With it off, composition writes no slot marker, and no document carries ``$router``, the layouts block, the router bundle, or Speculation Rules.
   A request carrying the navigation headers is then answered as a plain page, and active links keep working.

``SCOPE``, default ``"pages"``
   ``"pages"`` takes over a link only when a page of the file router answers its URL, which the router reads from a route table the server serves, and a link inside an element carrying ``data-next-router``.
   ``"all"`` takes over every same-origin link and draws ``next.W191`` while the URLconf serves routes that are no page, and ``"opt_in"`` takes over only the links inside an element carrying ``data-next-router``.
   See :ref:`topics-router-scope`.

``EXCLUDE``, default ``()``
   Path prefixes, each starting with ``/``, that the router never takes over, after the prefixes derived from ``STATIC_URL``, ``MEDIA_URL``, and the admin site.
   A prefix matches the start of the URL path, the script prefix included, as plain text, so ``"/docs"`` also covers ``/docs-archive/`` and ``"/docs/"`` covers only that directory.
   A bare ``"/"``, the script prefix, and a repeat of a derived prefix are dropped, and the browser sees the result as ``exclude`` in ``$router``.
   A link under an excluded prefix loads as the browser would load it, and ``SPECULATION`` hands it to the browser's own prefetch.
   A prefix that starts no path of the URLconf draws ``next.W184``.

``FOREIGN``, default ``"hard"``
   What the browser does with an answer that is no navigation envelope.
   ``"hard"`` loads the URL as a new document and remembers its path for the tab, and ``"extract"`` reads a ``text/html`` answer in the browser and swaps it in like a navigation.

``PREFETCH``, default ``"intent"``
   ``"intent"`` prefetches a link the pointer or the focus rests on, or one pressed, and ``PRESS`` decides how deep a press goes.
   ``"viewport"`` also prefetches every owned link once as it scrolls into view, at the price of one render per link in view, and ``"off"`` prefetches nothing on its own.
   ``data-next-prefetch`` set to ``"viewport"``, ``"intent"``, or ``"off"`` on an element overrides the mode for the links inside it, see :doc:`/content/topics/router/prefetch`.

``PREFETCH_DELAY``, default ``0.065``
   The seconds, above ``0``, the pointer or the focus rests on a link before it is prefetched.

``PREFETCH_TTL``, default ``30.0``
   The seconds, above ``0``, a prefetched answer marked ``complete`` stays usable, and the age up to which Back or Forward shows a history snapshot without fetching the page again.

``PREFETCH_SHELL_TTL``, default ``300.0``
   The seconds, above ``0``, a prefetched shell, an answer not marked ``complete``, stays usable.
   A navigation that applies a shell always fetches the page beside it, so a shell can live longer than a complete answer.

``PREFETCH_ENTRIES``, default ``16``
   The prefetched answers the browser keeps, an integer from ``1``, the oldest dropped first.
   A link in view waits while the kept answers and the prefetches in flight reach this number, until a shell older than ``PREFETCH_TTL`` gives way, the oldest first.

``PRESS``, default ``"mouse"``
   Which pointers send the full page request, ``X-Next-Prefetch: full``, at the press of a link, so the click adopts the response.
   ``"mouse"`` does it for a mouse press only and keeps the shell prefetch for a touch or a pen, ``"all"`` does it for every pointer, and ``"off"`` keeps every press at the shell depth.
   ``data-next-press`` set to ``"mouse"``, ``"all"``, or ``"off"`` on an element overrides the value for the links inside it.
   A full request runs the deferred jobs of the page, see :doc:`/content/topics/router/prefetch`.

``SNAPSHOTS``, default ``10``
   The history snapshots a tab keeps in memory and the scroll positions it saves in ``sessionStorage``, an integer from ``1``.

``PROGRESS_DELAY``, default ``0.15``
   The wait in seconds, above ``0``, before a navigation marks ``<html>`` with ``data-next-navigating``.

``PROGRESS``, default ``True``
   Has the client router put a ``<style>`` with the default progress bar, keyed on ``html[data-next-navigating]``, in the head of every routed document as it boots, with the CSP nonce of the runtime script.
   ``False`` leaves the progress UI to the project, see :doc:`/content/topics/router/browser`.

``TRANSITIONS``, default ``False``
   Runs each swap of a slot or the body inside a view transition, where the browser supports one and the visitor has not asked for reduced motion.

``INTERCEPT``, default ``"opt_in"``
   ``"opt_in"`` opens the layer of an intercepted page only from a ``data-next-layer`` link, and ``"links"`` answers a plain navigation to such a page with its layer.

``SPECULATION``, default ``None``
   ``None`` emits no Speculation Rules.
   ``"conservative"``, ``"moderate"``, or ``"eager"``, as a string or as ``{"eagerness": ...}``, makes every routed document carry a prefetch rule for the same-origin links the router does not own.
   A value that names another eagerness, asks for ``prerender``, or holds another key emits nothing and is reported as ``next.E242``.

``CUT``, default ``"next.router.VerifiedCut"``
   The ``NavigationCut`` subclass that decides how far a navigation keeps the layouts.

``DIGEST``, default ``"next.router.ChromeDigest"``
   The ``ChromeDigest`` subclass that normalises and digests their chrome.

``SHED``, default ``"next.router.LoadShed"``
   The ``PrefetchShed`` subclass that decides whether a prefetch answers ``429`` to spare the server.
   The default sheds while the pool queue of deferred boundaries waits past ``DEFER["EXECUTOR"]["QUEUE_TIMEOUT"]``, and sheds nothing in a process whose pool never started, see :doc:`router`.

``ACTIVE_LINKS``, default ``{"ENABLED": True, "PASS": "template", "CONTAINERS": ("nav", "[role=navigation]", "[data-next-nav]")}``
   The settings of active links, ``True`` for the defaults, ``False`` to switch the pass off, or a dict whose missing keys take their defaults.
   ``ACTIVE_LINKS["ENABLED"]`` switches the server pass and the client pass together, independently of ``ENABLED``.
   ``ACTIVE_LINKS["PASS"]`` is ``"template"`` to mark each container where a template writes it, the whole document only for a page whose containers the source cannot show, or ``"document"`` to mark every rendered document, see :doc:`/content/topics/router/active-links`.
   ``ACTIVE_LINKS["CONTAINERS"]`` lists the navigation containers whose links are marked, each a tag name, ``[attr]``, or ``[attr=value]`` with the value bare or quoted.

A missing key takes its default, nested ``ACTIVE_LINKS`` keys included, an unknown key is reported as ``next.E035``, and a value outside its shape is reported as ``next.E076`` and raises :exc:`~django.core.exceptions.ImproperlyConfigured` at the first read of the scope.
A ``CUT``, ``DIGEST``, or ``SHED`` path that names no usable class is reported as ``next.E219``.
See :doc:`/content/topics/router/index`, :doc:`/content/topics/router/prefetch`, and :doc:`router`.

Strictness
----------

STRICT_CONTEXT
~~~~~~~~~~~~~~

When ``True``, a ``TypeError``, ``ValueError``, ``AttributeError``, or ``KeyError`` raised by a context processor listed under a page backend ``OPTIONS["context_processors"]`` is re-raised.
When ``False``, it is logged as a warning and the processor is skipped.
Exceptions from ``@context`` callables always propagate.

Default value ``False``.
:doc:`/content/deployment/settings` explains when to turn this on in production.

STRICT_LOADING
~~~~~~~~~~~~~~

The one switch for a loud failure.
When ``True``, a failure the framework would log and degrade past is raised at its caller instead.
A broken body source fails the request, and a ``{% component %}`` name that does not resolve raises ``TemplateSyntaxError`` with a did-you-mean hint.
The same switch decides for the user callables of a render, the compression paths, the SEO backends, the metadata chain, the consent backends, the site configuration, and the ``{% more %}`` options.
When ``False``, the default, the framework logs and keeps rendering, so a missed component renders as an empty string with a warning log.

A ``page.py`` that raised while importing is outside this flag.
Every request to that page re-raises the recorded ``PageModuleImportError``, so it answers 500 on either setting of the flag and of ``DEBUG``, and Django's request logger records the traceback.
The failure is scoped to the broken page and the pages below it, which answer to what it would have declared with ``inherit=True``, and sibling pages keep serving.

Every fail-loud path reads the flag through the ``next.conf.fail_loudly`` predicate, which combines it with ``settings.DEBUG`` so the component tag and the other fail-loud paths cannot drift apart.
The same predicate lets a failure of any other user callable propagate, see :ref:`topics-failure-containment`.

Default value ``False``.
See :doc:`pages` for the page-load contract, :doc:`template-tags` for the component-miss outcomes, :doc:`conf` for ``fail_loudly``, and :doc:`/content/deployment/settings` for the production recommendation.

Loudness axes
~~~~~~~~~~~~~

Several independent switches decide how loudly a broken piece fails.

.. list-table::
   :header-rows: 1
   :widths: 26 40 34

   * - Axis
     - Covers
     - When loud
   * - ``settings.DEBUG``
     - Django development mode as a whole
     - A broken ``page.py`` shows the technical 500 page instead of the generic one, and a component miss renders a visible HTML comment.
   * - ``STRICT_LOADING``
     - Body sources, ``{% component %}`` misses, and the failures of user callables, compression paths, SEO backends, metadata, consent, site configuration, and ``{% more %}`` options
     - Raises regardless of ``DEBUG``.
   * - ``STRICT_CONTEXT``
     - Django context processor exceptions
     - Raises regardless of ``DEBUG``.
   * - ``next.E076``
     - A ``NEXT_FRAMEWORK`` value outside the shape its key takes, at the top level or inside a scope
     - Always, on ``manage.py check``, and a mistyped top-level value also refuses every request while a value inside a scope raises at the first read of the scope.
   * - ``next.E077``
     - A ``NEXT_FRAMEWORK`` that is not a dict at all, which the settings layer ignores entirely
     - Always, on ``manage.py check``.
   * - ``next.W072``
     - Every ``NEXT_FRAMEWORK`` bool key, where ``bool()`` coercion can invert the intent
     - Always, on ``manage.py check``, as a warning rather than an error.

``DEBUG=True`` turned on temporarily, for serving static files or profiling, also changes the error semantics of pages.
A missed component that rendered as an empty string starts rendering an HTML comment, so the switch flips more than the error page and the toolbar.
The configuration checks stay independent of every flag above, so ``manage.py check`` reports ``next.E076``, ``next.E077``, and ``next.W072`` in any combination of ``DEBUG`` and the strict flags.
See :doc:`system-checks` for each check condition.

Component loading
-----------------

COMPONENT_TEMPLATE_LOADER
~~~~~~~~~~~~~~~~~~~~~~~~~

Dotted path to the loader class that reads and compiles component template bodies.

Default value ``"next.components.CachedComponentTemplateLoader"``.

The class is instantiated once with the shared module loader and handed to both render strategies, so one instance answers every component read in the process.
The default ``CachedComponentTemplateLoader`` keeps a compiled ``Template`` per component and revalidates it against the modification time of the file the body came from, which costs a warm render one ``stat`` instead of a read plus a parse.
Set the key to ``"next.components.ComponentTemplateLoader"`` to drop that cache and read and parse the body on every render.
Both shipped classes produce the same HTML for the same sources, so the choice is a cost, not a behaviour.

A custom value must name a ``next.components.ComponentTemplateLoader`` subclass.
A path that fails to import, or one that names anything other than such a subclass, raises :exc:`~django.core.exceptions.ImproperlyConfigured`.
The key is read through ``next.backends.resolve_setting_class``, documented in :doc:`backends`, the same helper ``URL_RESOLVER`` goes through.

The key is read when the render pipeline is built, never per render.
A settings reload drops the pipeline, so ``override_settings`` swaps the loader without a restart.

See :doc:`components` for the loader API.

LAZY_COMPONENT_MODULES
~~~~~~~~~~~~~~~~~~~~~~

Controls bulk import of ``component.py`` modules in configured component roots during ``next.apps.components.install``.
When ``True``, each ``component.py`` is imported on demand the first time ``get_component`` resolves it.
Components discovered through ``_components`` directories beside page files are imported by the file router as it walks the page tree, regardless of this flag.

Default value ``False``.
See :doc:`/content/deployment/settings` for production defaults and :doc:`/content/topics/testing` for the ``eager_load_components`` helper.

Static assets
-------------

STATIC_DISCOVERY_CACHE
~~~~~~~~~~~~~~~~~~~~~~

Controls whether asset discovery keeps the plan it built for a page or a component.

Default value ``True``.

A plan records the co-located files a page or component directory holds and the module-level ``styles`` and ``scripts`` URLs it declares.
With the key on, discovery keeps one plan per page file and one per component, each bounded at 2048 entries, and rebuilds a plan once a watched directory moves or an asset registry changes.
Every render still hands the planned files to the backend and the planned URLs to the collector, so a warm render collects what a cold one collected.

When ``False``, both plan caches are bypassed and every render walks the role directories and reads the module lists again.
The collected assets are the same, only the walk is paid on each render.
Turn the key off when a deployment suspects a stale plan, because the uncached path reads the disk with no freshness heuristic in front of it.

The key is read when the discovery instance is built, never per render.
A settings reload drops the static manager and the discovery behind it, so ``override_settings`` takes effect without a restart.
The page-root lookup that maps a page file to its tree is memoised separately and is not affected, because it is a pure function of the path and the configured roots and it is dropped whole whenever those roots move.

See :doc:`/content/topics/static-assets/index` for the discovery rules.

STATIC_VERSION
~~~~~~~~~~~~~~

The version every rendered asset URL carries as a ``v`` query parameter.

Default value ``None``.

With the key unset no URL is touched.
With a string in it, every URL the pipeline renders gains the parameter, because every such URL passes the one manager hook that sets it.
That covers co-located files, module-list assets, tag assets, ``{% asset %}`` values, and the ``next.min.js`` runtime alike.
The parameter is set after the backend ``asset_url`` hook, so a rewritten URL keeps the version, and a URL that already carries a query keeps every other pair while a ``v`` pair already in it is replaced rather than doubled.
An opaque URI such as ``data:`` or ``blob:`` owns no query string, so it reaches the document exactly as it was written.

A value of any type other than a string or ``None`` is reported at ``manage.py check`` as ``next.E076``, and every request raises :exc:`~django.core.exceptions.ImproperlyConfigured` until it is fixed.
Read the value from the environment or from a build artefact so every worker of a deployment renders the same URL.

A project on :doc:`ManifestStaticFilesStorage <django:ref/contrib/staticfiles>` leaves the key unset, because the manifest already versions each file by its content.

The key is also the deploy stamp the partial asset version derives from, so one value moves both the asset URLs and the ``X-Next-Version`` guard that asks an open client to reload.
A project that wants the two to differ pins ``VERSION`` in its ``PARTIAL_BACKENDS`` entry, which wins over this key, and a project that names neither while running no manifest earns ``next.W083`` on ``manage.py check --deploy``.
See :doc:`/content/deployment/static-files` for the trade-off, :doc:`/content/topics/partial-rendering/reference` for the ``VERSION`` option, and :doc:`/content/topics/static-assets/template-tags` for the per-URL ``version`` argument of ``{% asset %}``.

Site and SEO
------------

SITE
~~~~

Dict naming the public origin of the site, its name, and whether search engines may index it.

Default value ``{"URL": None, "NAME": None, "INDEXABLE": "auto"}``.

``URL`` is an ``http`` or ``https`` origin with no path, a callable taking the request, or the dotted path of one, and every absolute URL of the head, the sitemap, and the robots file is built on it, the ``Site`` row of ``django.contrib.sites`` and then the request host standing in without it.
A callable answering ``None`` falls through to the same fallbacks.
A callable that raises or answers no origin makes the SEO routes answer 503.

``NAME`` seeds the ``site_name`` metadata key when ``DEFAULTS`` sets none.
``INDEXABLE`` is ``"auto"``, which follows ``not DEBUG``, a bool, or a callable taking the request or ``None``.
Every robots meta, ``X-Robots-Tag``, sitemap, and robots file follows its answer.
A callable that raises reads as not indexable.
See :doc:`/content/topics/seo/site` for what a closed site serves and the checks that read the scope.

METADATA
~~~~~~~~

Dict holding the page metadata scope, the site-wide defaults of the metadata chain and the options beside them.

Default value.

.. code-block:: python

   {
       "RENDERER": "next.pages.HtmlMetadataRenderer",
       "DEFAULTS": {},
       "CANONICAL_QUERY": [],
   }

A key the project leaves out of its ``METADATA`` reads the default above, so a scope naming only ``DEFAULTS`` keeps the other two.
The scope takes three upper-case options, and any other key is reported as ``next.E035``.

.. code-block:: python
   :caption: config/settings.py

   from django.utils.translation import gettext_lazy as _

   NEXT_FRAMEWORK = {
       "SITE": {"URL": "https://notes.example", "NAME": _("Notes")},
       "METADATA": {
           "DEFAULTS": {
               "title": {"template": _("{title} · {site_name}"), "default": _("Notes")},
               "description": _("A notebook that lives in the browser."),
               "og": {"type": "website"},
               "twitter": {"card": "summary_large_image", "site": "@notes"},
               "jsonld": [{"@type": "Organization", "@id": "#org", "name": "Notes", "url": "https://notes.example/"}],
           },
           "CANONICAL_QUERY": ("page",),
           "RENDERER": "next.pages.HtmlMetadataRenderer",
       },
   }

``DEFAULTS`` is the outermost segment of every page's metadata chain and takes the lower-case keys a ``metadata`` dict in a ``page.py`` takes, less ``breadcrumb``.
Its ``title`` is the ``{"template": ..., "default": ...}`` form alone, because the settings tier has no page of its own to title, and its template therefore applies to every page, the root included.
The value is normalised once per settings reload.

A key or a value the schema refuses renders every page under ``noindex`` with the site name alone, and ``manage.py check`` reports it.
A ``Replace`` or ``RESET`` there has no effect.
See :doc:`/content/topics/seo/metadata` for the declaration forms and :doc:`/content/topics/seo/merge` for the merge.

``CANONICAL_QUERY`` is the list or tuple of query parameter names a self canonical keeps, in the order it lists them.
Every other parameter is dropped from the canonical URL, and a ``page=1`` pair is dropped even when ``page`` is listed.
It defaults to an empty list.

``RENDERER`` is the dotted path to the class ``{% metadata %}`` renders through.
A custom value names a ``next.pages.MetadataRenderer`` subclass, whose ``render`` takes the ``ResolvedMetadata`` of one response and answers the head markup, see :doc:`metadata` for the contract.
The key is read through ``next.backends.resolve_setting_class``, documented in :doc:`backends`, and the renderer is built once and built again on ``settings_reloaded``.
A path that fails to import or names anything else is reported as ``next.E107``, and :doc:`metadata` describes how a render falls back to ``HtmlMetadataRenderer``.

A ``METADATA`` value that is not a dict is reported as ``next.E076``, and every request raises :exc:`~django.core.exceptions.ImproperlyConfigured` until it is fixed.

SEO
~~~

Dict holding the sitemap backends.

Default value ``{"SITEMAP_BACKENDS": [{"BACKEND": "next.seo.PageTreeSitemapBackend", "OPTIONS": {}}]}``.

``SITEMAP_BACKENDS`` lists the sources of sitemap sections in merge order, each an entry of ``BACKEND`` naming a ``next.seo.SitemapBackend`` subclass and ``OPTIONS``.
A backend that raises while serving makes the sitemap answer 503.
See :doc:`/content/topics/seo/sitemap-sections`.

CSRF_DELIVERY
~~~~~~~~~~~~~

String naming where a rendered page puts its CSRF token.

Default value ``"auto"``.

``"eager"`` embeds the token in every form and init payload, ``"lazy"`` never does and lets the runtime fetch it from ``/_next/csrf/``, and ``"auto"`` is lazy on a page whose ``cache`` lets a shared cache keep it.
On any other page ``"auto"`` embeds the token in the init payload only when the render already read one, through a ``{% form %}`` or ``{% csrf_token %}``, or when the page streams, so a page without forms sets no CSRF cookie and no ``Vary: Cookie``, as a Django view that never calls ``get_token`` does.
A value outside the three reads as ``"auto"`` and is ``next.E132``.
See :doc:`/content/security/csrf-and-forms`.

CSP and consent
---------------

CSP_NONCE
~~~~~~~~~

Boolean that controls whether every tag the framework writes carries the CSP nonce of the request.

Default value ``True``.

The nonce is the one django-csp or Django's own CSP middleware minted, read without a setting of its own, see :doc:`/content/security/csp-and-nonce`.
A response whose render reads a nonce is sent with ``Cache-Control: private``, so ``manage.py check`` warns with ``next.W120`` while a nonce is active beside a page a shared cache may keep.
``False`` suits a site that allows its scripts by hash or by source.
A value that is no bool passes through ``bool()`` and is reported as ``next.W072``.

CONSENT
~~~~~~~

Dict holding the consent categories, the backend that reads the visitor's choice, and where gated content renders.

Default value.

.. code-block:: python

   {
       "BACKEND": "next.consent.CookieConsentBackend",
       "CATEGORIES": ["necessary"],
       "SERVER_RENDER": "auto",
       "OPTIONS": {
           "cookie_name": "next_consent",
           "max_age": 15552000,
           "samesite": "Lax",
           "secure": None,
           "domain": None,
           "path": "/",
       },
   }

The presence of the key switches consent on.
A project that sets ``CONSENT``, even to an empty dict, gives every page its consent state, so the banner works on pages without a gated script.
Without it, only a page whose tree declares scripts or whose render meets a ``{% #consented %}`` block carries the state.

``BACKEND`` names a ``next.consent.ConsentBackend`` subclass, ``CATEGORIES`` the categories the project declares with ``necessary`` always among them, and ``SERVER_RENDER`` is ``"auto"``, ``True``, or ``False``.
``OPTIONS`` belongs to the backend, and the cookie backend reads the cookie name, lifetime, ``SameSite``, domain, path, and ``secure`` flag from it.
``manage.py check`` reports a value the scope cannot use, see :ref:`ref-system-checks`.
See :doc:`/content/topics/scripts/consent`.

Patching defaults
-----------------

Use ``next.conf.extend_default_backend`` to patch one key of a default backend entry without copying the whole default.

.. code-block:: python
   :caption: config/settings.py

   from next.conf import extend_default_backend

   NEXT_FRAMEWORK = {
       "PAGE_BACKENDS": extend_default_backend(
           "PAGE_BACKENDS",
           PAGES_DIR="routes",
       )
   }

The helper returns a deep copy of the default list with the entry at ``index`` (default ``0``) patched by the keyword overrides.
Nested dicts such as ``OPTIONS`` are merged.

The helper accepts five backend-list keys.

- ``PAGE_BACKENDS``
- ``COMPONENT_BACKENDS``
- ``STATIC_BACKENDS``
- ``FORM_ACTION_BACKENDS``
- ``PARTIAL_BACKENDS``

The helper raises ``ImproperlyConfigured`` when ``key`` is not one of these settings.
It raises ``IndexError`` when ``index`` is out of range for the default list.

See :doc:`conf` for the helper API and :doc:`/content/howto/extend-a-default-backend` for the recipe.

See also
--------

.. seealso::

   :doc:`/content/topics/extending` for the broader picture.
   :doc:`/content/deployment/settings` for production tuned values.
   :doc:`/content/topics/static-assets/js-context` for ``NEXT_JS_OPTIONS``.
   :doc:`/content/topics/seo/metadata` for ``METADATA`` and :doc:`/content/topics/seo/site` for ``SITE``.
   :doc:`/content/topics/scripts/index` for ``CONSENT`` and :doc:`/content/security/csp-and-nonce` for ``CSP_NONCE``.
   :class:`next.static.runtime.ScriptInjectionPolicy` for the policy enum.
