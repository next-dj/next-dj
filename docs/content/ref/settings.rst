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

.. _ref-settings-merge:

How values merge
----------------

``NEXT_FRAMEWORK`` merges over the framework defaults one level deep, and one level only.
A key the project sets replaces the default for that key whole.
A key the project leaves out keeps its default.
Nothing below the top level is combined, so a nested ``OPTIONS`` dict, an entry inside a backend list, and a sub-key of a single backend dict all come from the project alone once the top-level key is present.
The rule holds for every key, with no per-key exception.

This is what Django does with its own configuration mappings.
Defining :doc:`STORAGES <django:ref/settings>` overrides the default configuration rather than merging with it, and ``TEMPLATES``, ``DATABASES``, and ``CACHES`` each read the whole list or mapping the project wrote.
Filling in what a single entry leaves out belongs to the code that consumes the entry, the same place Django fills a ``DATABASES`` alias with ``ATOMIC_REQUESTS`` or a ``TEMPLATES`` entry with ``APP_DIRS``.

A single backend dict follows the rule like any other key.
``FORM_WIZARD_BACKEND`` set to ``{"OPTIONS": {...}}`` alone carries no ``BACKEND``, which ``manage.py check`` reports as ``next.E051`` and the wizard manager answers with :exc:`~django.core.exceptions.ImproperlyConfigured` on first use.
Write the whole entry, ``BACKEND`` included.

Each key accepts one shape, and a value of any other type is dropped in favour of the default rather than merged into it.

- The list keys (``PAGE_BACKENDS``, ``COMPONENT_BACKENDS``, ``STATIC_BACKENDS``, ``FORM_ACTION_BACKENDS``, ``PARTIAL_BACKENDS``, ``TEMPLATE_LOADERS``, ``FORM_ANCHOR_FILES``) accept a list.
- The mapping keys (``NEXT_JS_OPTIONS``, ``FORM_WIZARD_BACKEND``) accept a dict.
- The dotted-path keys (``URL_RESOLVER``, ``DEPENDENCY_RESOLVER``, ``COMPONENT_TEMPLATE_LOADER``) accept a string naming an importable class, and ``JS_CONTEXT_SERIALIZER`` accepts such a dotted path or ``None``.
- ``URL_NAME_TEMPLATE`` also accepts a string, but a format template such as ``page_{name}`` rather than a dotted path.
- The bool flags (``STRICT_CONTEXT``, ``STRICT_LOADING``, ``LAZY_COMPONENT_MODULES``, ``FORM_AUTODISCOVER``, ``STATIC_DISCOVERY_CACHE``) accept any value and pass through ``bool()``.

A dropped value is reported at ``manage.py check`` as ``next.E076``, or under the code the key owns where it carries one, and a bool flag holding a non-bool is reported as ``next.W072``.
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
           "BACKEND": "next.urls.FileRouterBackend",
           "DIRS": [],
           "APP_DIRS": True,
           "PAGES_DIR": "pages",
           "OPTIONS": {"context_processors": []},
       }
   ]

Each entry is passed to the backend constructor.
Keys are ``BACKEND``, ``DIRS``, ``APP_DIRS``, ``PAGES_DIR``, and ``OPTIONS``.

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
           "OPTIONS": {},
       }
   ]

FORM_AUTODISCOVER
~~~~~~~~~~~~~~~~~

Boolean that controls whether ``NextFrameworkConfig.ready`` imports the ``forms`` submodule of every installed app on startup.

Default value ``True``.

When ``True``, shared forms declared in ``app/forms.py`` register before the first request arrives.
Set to ``False`` to disable the automatic import and manage registration manually.

FORM_ANCHOR_FILES
~~~~~~~~~~~~~~~~~

List of file basenames that receive ``page`` scope during auto-registration.
A form class declared in a file whose basename appears in this list is keyed to the absolute path of that file.
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
See :doc:`/content/topics/forms/wizard-backend` for the contract, the codec, and a custom backend.

PARTIAL_BACKENDS
~~~~~~~~~~~~~~~~

List of partial protocol backend configurations.
The first entry is active and owns the patch wire format that partial rendering serialises over HTTP and Server-Sent Events.
Entries after the first are ignored, and ``manage.py check`` reports them with ``next.W071``.
The value has to be a list, since any other shape is dropped in favour of the default.
``manage.py check`` reports the drop as ``next.E067`` from the partial checks, which own the shape probe for this key.

Default value.

.. code-block:: python

   [
       {
           "BACKEND": "next.partial.JsonPartialProtocolBackend",
           "OPTIONS": {
               "VERSION": "manifest",
               "PUSH_WIZARD_STEPS": False,
               "SSE": {
                   "HEARTBEAT_SECONDS": 25,
                   "RETRY_MS": 3000,
               },
           },
       },
   ]

The ``OPTIONS`` keys tune the active backend.
``VERSION`` is the source of the ``X-Next-Version`` stamp.
The sentinel ``"manifest"`` hashes the staticfiles manifest when the active storage hashes its files, and an explicit string pins the version by hand.
The resolved string is memoised for the life of the configuration, so a manifest replaced under a running process keeps serving the version resolved before it until a settings reload or a restart.
Without a manifest storage the version guard stays silent at runtime, and ``manage.py check`` reports ``next.W069``.
``PUSH_WIZARD_STEPS`` is the global default for pushing wizard steps to browser history, which a wizard's ``Meta.push_steps`` overrides per wizard.
``SSE.HEARTBEAT_SECONDS`` is the keepalive period in seconds for an async stream source, and ``SSE.RETRY_MS`` is the ``EventSource`` reconnect hint in milliseconds sent in the leading stream frame.

See :doc:`/content/topics/partial-rendering/reference` for the wire protocol and :doc:`/content/topics/partial-rendering/sse` for the stream contract.

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

The class is instantiated with a root route pattern and the lazy sequence of router and form-action patterns, so it owns every resolution under the ``include("next.urls")`` mount.
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
The framework holds one resolver singleton that the rest of the code binds by reference, so the named class is adopted by retyping that object in place rather than by building a new one.
Two constraints on the subclass follow from that.
Its ``__init__`` never runs, because the state it would build already lives on the singleton the base class initialised.
It adds no instance slots and no second base, because either changes the object layout and the retype then raises :exc:`~django.core.exceptions.ImproperlyConfigured`.
An empty ``__slots__ = ()`` leaves the layout alone and is fine.

See :doc:`/content/internals/di-resolver` for the resolution algorithm and :doc:`deps` for the resolver API.

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
Keys are the injection ``policy`` (``auto``, ``disabled``, or ``manual``) and the optional string templates ``preload_template``, ``script_tag_template``, and ``init_template``.

Default value ``{}`` (automatic injection with default templates).

Serialisation of ``window.Next.context`` is controlled by ``JS_CONTEXT_SERIALIZER`` and by ``@context(..., serialize=True)``, not by this dict.

See :doc:`/content/topics/static-assets/js-context` under *Runtime script options* for the full table and examples.

JS_CONTEXT_SERIALIZER
~~~~~~~~~~~~~~~~~~~~~~~

Dotted path to a class that implements the ``JsContextSerializer`` protocol.
The class is instantiated with no arguments and its ``dumps`` method encodes every value bound for ``window.Next.context``.

Default value ``None``, which selects the built-in ``JsonJsContextSerializer``.

``resolve_serializer`` reads this setting on every call, so ``override_settings`` takes effect without a restart.
A value that does not resolve to a usable serializer triggers the ``next.W042`` warning during ``manage.py check``.
At render time such a value raises ``ImportError`` or ``TypeError`` on first use, so fix the dotted path rather than rely on a fallback.
The built-in ``JsonJsContextSerializer`` steps in only when the setting is unset.

See :doc:`static` under *JS context serializer* for the protocol and the bundled serializers.

Strictness
----------

STRICT_CONTEXT
~~~~~~~~~~~~~~

When ``True``, any ``TypeError``, ``ValueError``, ``AttributeError``, or ``KeyError`` raised by a Django context processor is re-raised immediately.
The default behaviour is to log a warning and swallow the exception.
The check applies only to processors listed under a page backend ``OPTIONS["context_processors"]``.
Context callables registered with ``@context`` always propagate their exceptions regardless of this setting.
When ``False``, the default, a failing processor is skipped so local development keeps rendering.

Default value ``False``.
:doc:`/content/deployment/settings` explains when to turn this on in production.

STRICT_LOADING
~~~~~~~~~~~~~~

When ``True``, a broken body source fails the request instead of degrading silently.
A ``page.py`` that raised while importing re-raises the recorded ``PageModuleImportError`` on every request to that page.
A ``{% component %}`` name that does not resolve raises ``TemplateSyntaxError`` with a did-you-mean hint.
When ``False``, the default, the framework logs and keeps rendering.
A broken ``page.py`` answers 404 while ``logger.exception`` records the full traceback, and a missed component renders as an empty string with a warning log.
``settings.DEBUG`` raises the same page-load error without this flag, so the flag matters for a ``DEBUG=False`` deployment.
In every mode the failure is scoped to the broken page, sibling pages keep serving.

Every fail-loud path reads the flag through the ``next.conf.fail_loudly`` predicate, which combines it with ``settings.DEBUG`` so the page loader and the component tag cannot drift apart.

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
     - A broken ``page.py`` raises with the standard technical 500 page, and a component miss renders a visible HTML comment.
   * - ``STRICT_LOADING``
     - ``page.py`` import failures and ``{% component %}`` misses
     - Raises regardless of ``DEBUG``.
   * - ``STRICT_CONTEXT``
     - Django context processor exceptions
     - Raises regardless of ``DEBUG``.
   * - ``next.E076``
     - The ``NEXT_FRAMEWORK`` keys whose mistyped value the settings merge silently drops
     - Always, on ``manage.py check``.
   * - ``next.E077``
     - A ``NEXT_FRAMEWORK`` that is not a dict at all, which the settings layer ignores entirely
     - Always, on ``manage.py check``.
   * - ``next.W072``
     - Every ``NEXT_FRAMEWORK`` bool key, where ``bool()`` coercion can invert the intent
     - Always, on ``manage.py check``, as a warning rather than an error.

``DEBUG=True`` turned on temporarily, for serving static files or profiling, also changes the error semantics of pages.
A broken ``page.py`` that answered 404 starts raising, so the switch flips more than the error page and the toolbar.
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
   :class:`next.static.scripts.ScriptInjectionPolicy` for the policy enum.
