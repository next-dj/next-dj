.. _topics-extending:

Extending
=========

next.dj exposes five extension mechanisms.
Each section below states what its mechanism replaces and where to register it.

.. contents::
   :local:
   :depth: 2

The five mechanisms
-------------------

Backend.
   Replace or augment a complete subsystem.
   Used for URL routing, components, forms dispatch, the :doc:`static pipeline <static-assets/index>`, and the :doc:`partial patch protocol <partial-rendering/index>`.

Registry.
   Add new entries to a global list at startup.
   Used for asset kinds, custom stems, and :doc:`dependency injection <dependency-injection>` providers.

Protocol.
   Implement a runtime contract.
   Used for template loaders and JS context serializers.

Strategy.
   Swap an internal algorithm.
   Used for static deduplication, the JS context conflict policy, and the URL resolver.
   A strategy is selected by dotted path and satisfies the contract its slot names.

Signal.
   Observe a lifecycle event without changing it.
   Used for audit, observability, cache invalidation, and cross-app coordination.

Backends
--------

A backend implements a complete subsystem.
Subclass the base class listed for its family and register the dotted path in ``NEXT_FRAMEWORK``.

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * - Subsystem
     - Setting
     - Base class
   * - URL routing
     - ``PAGE_BACKENDS``
     - ``next.urls.backends.RouterBackend``
   * - Components
     - ``COMPONENT_BACKENDS``
     - ``next.components.backends.ComponentsBackend``
   * - Forms dispatch
     - ``FORM_ACTION_BACKENDS``
     - ``next.forms.backends.FormActionBackend``
   * - Static pipeline
     - ``STATIC_BACKENDS``
     - ``next.static.backends.StaticBackend``
   * - Partial protocol
     - ``PARTIAL_BACKENDS``
     - ``next.partial.PartialProtocolBackend``
   * - Form wizard drafts
     - ``FORM_WIZARD_BACKEND``
     - ``next.forms.FormWizardBackend``

A backend always implements the full contract.
Every family checks the configured class against the base named above, so a class that does not subclass it is rejected with :class:`~django.core.exceptions.ImproperlyConfigured`.
A custom backend usually subclasses the default so it inherits every default behaviour.

Not every base is abstract.
``RouterBackend``, ``ComponentsBackend``, ``FormActionBackend``, ``StaticBackend``, and ``FormWizardBackend`` are abstract base classes that declare methods a subclass must implement.
``PartialProtocolBackend`` is a plain concrete class with no abstract methods, so a subclass overrides only the parts of the wire format it changes.
Its override points are ``serialize_envelope``, which returns the HTTP response body for one patch envelope, ``sse_event``, which returns the same envelope as a server-sent-events frame, and the ``content_type`` class attribute that names the media type of the body.
The ``_dumps`` helper the two methods share is internal and carries no stability promise, so override both public methods rather than reaching through it.

``PARTIAL_BACKENDS`` differs from the other backend lists in that only its first entry is active.
``FORM_WIZARD_BACKEND`` is singular rather than a list, so the key holds one configuration dict instead of a list of them.
That shape puts it outside ``extend_default_backend``, which patches an entry of a backend list and rejects any other key.
The settings merge overlays a user dict on the default dict key by key, so an entry that names only ``BACKEND`` keeps the default ``OPTIONS``.
``manage.py check`` reports a bad shape, an unimportable path, or a class that does not subclass ``FormWizardBackend`` as ``next.E051``, and a misconfiguration that survives to the first wizard request raises :class:`~django.core.exceptions.ImproperlyConfigured` there rather than being skipped.

Beyond the abstract methods, a base class carries optional hooks whose defaults decline.

``ComponentsBackend`` carries the widest set of optional hooks.
``discover`` populates the registry and ``import_component_modules`` executes each discovered module.
``register_walked_folder`` claims a components folder the page-tree walk found.
``iter_components`` and ``global_component_roots`` let the system checks enumerate what the backend holds.
Leaving a hook alone is a supported answer, and it keeps the backend out of the diagnostics that hook feeds.
The full recipe with a worked backend is in :doc:`components`.

.. code-block:: python
   :caption: registering a custom backend

   NEXT_FRAMEWORK = {
       "FORM_ACTION_BACKENDS": [
           {"BACKEND": "notes.backends.AuditedFormActionBackend"},
       ]
   }

To patch one key of a default backend entry rather than replace it, use ``extend_default_backend``.

.. code-block:: python
   :caption: patching a default entry

   from next.conf import extend_default_backend

   NEXT_FRAMEWORK = {
       "PAGE_BACKENDS": extend_default_backend(
           "PAGE_BACKENDS",
           PAGES_DIR="routes",
       )
   }

Registries
----------

A registry is a process wide map populated at startup.
Register entries in ``AppConfig.ready`` or through a settings key.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Target
     - How to register
   * - Asset kinds
     - ``default_kinds.register(...)`` in ``AppConfig.ready``. Import ``default_kinds`` from ``next.static`` (top-level re-export).
   * - Asset stems
     - ``default_stems.register(...)`` in ``AppConfig.ready``. Import ``default_stems`` from ``next.static.discovery`` (deep import).
   * - Placeholder slots
     - ``default_placeholders.register(...)`` in ``AppConfig.ready``. Import ``default_placeholders`` from ``next.static`` (top-level re-export).
   * - Dependency providers
     - Subclass ``RegisteredParameterProvider``, imported in ``AppConfig.ready``.
   * - Named dependencies
     - ``@resolver.dependency("name")`` decorator. Import ``resolver`` from ``next.deps``.
   * - Patch verbs
     - ``register_patch_op("name")`` in ``AppConfig.ready``. Import ``register_patch_op`` from ``next.partial``.
   * - Template loaders
     - The ``TEMPLATE_LOADERS`` settings key.

The registry pattern is the right choice when the framework already knows how to consume the values and only needs to learn about a new entry.

The asset-stem registry is the extension point for teaching the static discovery scanner about a new asset filename next to a page, layout, or component.
Call ``default_stems.register(...)`` from ``AppConfig.ready`` so the new stem is known before the first render discovers assets.

.. code-block:: python
   :caption: notes/apps.py

   from django.apps import AppConfig
   from next.static.discovery import default_stems

   class NotesConfig(AppConfig):
       name = "notes"

       def ready(self) -> None:
           default_stems.register("component", "theme")

App order in ``INSTALLED_APPS``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Django runs every ``AppConfig.ready`` during application population, in ``INSTALLED_APPS`` order, so an app listed above ``next`` registers before ``NextFrameworkConfig.ready`` runs and an app listed below it registers after.
For the registries on this page that ordering does not change the outcome.
``NextFrameworkConfig.ready`` installs the autoreload, template, staticfiles, and component hooks and runs component discovery, and none of those steps read the kind, stem, placeholder, provider, named-dependency, or patch-verb registries.
Each of those registries is read on the first render, the first dispatch, or the first ``manage.py check``, and the watch specs are resolved when the autoreload watcher starts, all of which happen after application population finishes.
What matters is that the call sits in ``ready`` rather than in request-time code, not where the app sits in the list.

One conflict does depend on the order.
``default_kinds`` and ``default_placeholders`` reject a second registration of an existing name with different parameters, and the framework registers ``css``, ``js``, ``module``, ``styles``, and ``scripts`` from its own ``ready``.
An app that reuses one of those names with different parameters fails at startup either way, and its position in ``INSTALLED_APPS`` only decides which ``ready`` call raises.

Autoreload watch specs
~~~~~~~~~~~~~~~~~~~~~~

The development reloader watches the page and component trees by default.
Call ``register_autoreload_watch_spec`` from ``next.server`` to add a directory of your own.
It takes a ``path``, the filesystem root to watch, and a ``glob``, a pattern relative to that root that selects the files whose changes trigger a reload.
See :doc:`/content/ref/server` for the full signature.
Register the spec from ``AppConfig.ready`` so it is in place before the watcher starts.

.. code-block:: python
   :caption: notes/apps.py

   from pathlib import Path
   from django.apps import AppConfig
   from next.server import register_autoreload_watch_spec

   class NotesConfig(AppConfig):
       name = "notes"

       def ready(self) -> None:
           register_autoreload_watch_spec(
               Path(__file__).resolve().parent / "rules",
               "**/*.yaml",
           )

Edits to any ``*.yaml`` file under ``notes/rules`` now restart the development server.
Duplicate ``(path, glob)`` pairs are dropped, so registering the same spec twice is safe.

``register_autoreload_watch_spec`` is the only way to add extra trees to the watcher.
``iter_all_autoreload_watch_specs`` from ``next.server`` resolves the final spec set and sends the ``watch_specs_ready`` signal with ``sender`` set to the function itself.
Subscribe to that signal to observe or audit the resolved spec set.
See :doc:`/content/internals/autoreload` for the full watcher pipeline.

Protocols and abstract base classes
-----------------------------------

A protocol is a structural contract.
Implement the methods listed in the protocol and pass the class to the framework.

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Protocol
     - Defined in
   * - Dedup strategy
     - ``next.static.collector.DedupStrategy``
   * - JS context serializer
     - ``next.static.JsContextSerializer``

Select a serializer implementation with the ``JS_CONTEXT_SERIALIZER`` setting.
The default is ``JsonJsContextSerializer``.

``next.pages.loaders.TemplateLoader`` is an abstract base class rather than a protocol.
Subclass it explicitly and register the subclass through ``TEMPLATE_LOADERS``.

Protocols differ from backends in that they implement a single hook.
A template loader handles file discovery for one extension.
A backend coordinates an entire subsystem.

Strategies
----------

A strategy is a swappable algorithm.
The framework calls the strategy at a well known point in the pipeline.

.. list-table::
   :header-rows: 1
   :widths: 35 35 30

   * - Strategy
     - Configured through
     - Default
   * - Static dedup
     - ``DEDUP_STRATEGY`` in the first static backend ``OPTIONS``
     - ``UrlDedup``
   * - JS context conflict policy
     - ``JS_CONTEXT_POLICY`` in the first static backend ``OPTIONS``
     - ``FirstWinsPolicy``
   * - URL resolver
     - ``URL_RESOLVER`` at the top level of ``NEXT_FRAMEWORK``
     - ``next.urls.TrieURLResolver``

Use a strategy when the customisation is a single algorithm rather than a complete subsystem.

The URL resolver is the one strategy configured at the top level of ``NEXT_FRAMEWORK`` rather than inside a backend ``OPTIONS`` mapping.
Its value is a dotted path to a ``django.urls.resolvers.URLResolver`` subclass, and the framework builds one instance of that class around the lazy list of page and form-action patterns.
Swap it to change how a request path is matched against those patterns, for example to trade the default trie for a different index.
A path that cannot be imported, and a class that is not a ``URLResolver`` subclass, both raise :class:`~django.core.exceptions.ImproperlyConfigured` while the URL configuration is built.
A value that is not a string is dropped by the settings merge, which leaves the default in place with no error.

Signals
-------

A signal is an observation point.
Connect a receiver to react to a framework event.

The signal catalog lives in :doc:`signals`.
The patterns are uniform across the framework.

Choosing between mechanisms
---------------------------

Picking the right mechanism saves work.
Use the entries below as a quick map.

- **Add a new URL pattern source.** Subclass ``RouterBackend`` and register it under ``PAGE_BACKENDS``.
- **Change how a path is matched against the built patterns.** Name a ``URLResolver`` subclass under ``URL_RESOLVER``.
- **Recognise a new asset extension.** Register through the kind registry (``default_kinds``).
- **Recognise a new asset filename next to a page, layout, or component.** Register a custom stem (``default_stems``).
- **Validate every dispatch.** Implement a form action backend.
- **Log every dispatch.** Subscribe to the ``action_dispatched`` signal.
- **Change the patch wire format.** Register a partial protocol backend under ``PARTIAL_BACKENDS``.
- **Add a custom patch verb.** Call ``register_patch_op``. See :doc:`/content/topics/partial-rendering/extending`.
- **Persist wizard drafts elsewhere.** Subclass ``FormWizardBackend`` and name it under ``FORM_WIZARD_BACKEND``.
- **Change how URLs land in HTML.** Customise a static backend.
- **Vary URLs by request.** Use a request-aware static backend.
- **Inspect every rendered page.** Subscribe to the ``page_rendered`` signal.
- **Watch extra directories during development.** Call ``register_autoreload_watch_spec``. See *Autoreload watch specs* above.

Worked examples
---------------

The repository ``examples/`` tree ships complete projects for every major extension mechanism.
:doc:`/content/misc/examples` lists each folder, a one-line focus, links to GitHub, and the sections of this manual that explain the same techniques.

See also
--------

.. seealso::

   :doc:`signals` for the observation points.
   :doc:`dependency-injection` for the provider registry and custom markers.
   :doc:`/content/howto/extend-a-default-backend` for the helper details.
   :doc:`/content/ref/conf` for the configuration surface.
   :doc:`/content/internals/index` for how each subsystem composes.
