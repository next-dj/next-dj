.. _topics-extending:

Extending
=========

next.dj exposes five extension mechanisms that you compose freely.
Each mechanism has a different purpose, scope, and lifecycle.
This page covers all five so you can pick the right one for the customisation you have in mind.

.. contents::
   :local:
   :depth: 2

The Five Mechanisms
-------------------

Backend.
   Replace or augment a complete subsystem.
   Used for URL routing, components, forms dispatch, the :doc:`static pipeline <static-assets/index>`, and JS context serialization.

Registry.
   Add new entries to a global list at startup.
   Used for asset kinds, custom stems, :doc:`dependency injection <dependency-injection>` providers, and DI markers.

Protocol.
   Implement a runtime contract.
   Used for template loaders, dedup strategies, and JS context serializers.

Strategy.
   Swap an internal algorithm.
   Used for static deduplication and the JS context conflict policy.

Signal.
   Observe a lifecycle event without changing it.
   Used for audit, observability, cache invalidation, and cross-app coordination.

Use the first mechanism that fits.
A signal that can replace a backend keeps the framework defaults intact and the project surface small.

Backends
--------

A backend implements a complete subsystem.
Subclass an abstract base class and register the dotted path in ``NEXT_FRAMEWORK``.

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * - Subsystem
     - Setting
     - Base class
   * - URL routing
     - ``DEFAULT_PAGE_BACKENDS``
     - ``next.urls.backends.RouterBackend``
   * - Components
     - ``DEFAULT_COMPONENT_BACKENDS``
     - ``next.components.backends.ComponentsBackend``
   * - Forms dispatch
     - ``DEFAULT_FORM_ACTION_BACKENDS``
     - ``next.forms.backends.FormActionBackend``
   * - Static pipeline
     - ``DEFAULT_STATIC_BACKENDS``
     - ``next.static.backends.StaticBackend``
   * - JS context serializer
     - ``JS_CONTEXT_SERIALIZER``
     - ``next.static.JsContextSerializer`` protocol, defined in ``next.static.serializers``

A backend always implements the full contract.
A custom backend usually subclasses the default so it inherits every default behaviour.

.. code-block:: python
   :caption: registering a custom backend

   NEXT_FRAMEWORK = {
       "DEFAULT_FORM_ACTION_BACKENDS": [
           {"BACKEND": "notes.backends.AuditedFormActionBackend"},
       ]
   }

To patch one key of a default backend entry rather than replace it, use ``extend_default_backend``.

.. code-block:: python
   :caption: patching a default entry

   from next.conf import extend_default_backend

   NEXT_FRAMEWORK = {
       "DEFAULT_PAGE_BACKENDS": extend_default_backend(
           "DEFAULT_PAGE_BACKENDS",
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
     - ``default_kinds.register(...)`` in ``AppConfig.ready``. Import ``default_kinds`` from ``next.static``.
   * - Asset stems
     - ``default_stems.register(...)`` in ``AppConfig.ready``. Import ``default_stems`` from ``next.static.discovery`` (deep import).
   * - Placeholder slots
     - ``default_placeholders.register(...)`` in ``AppConfig.ready``. Import ``default_placeholders`` from ``next.static``.
   * - Dependency providers
     - Subclass ``RegisteredParameterProvider``, imported in ``AppConfig.ready``.
   * - Named dependencies
     - ``@resolver.dependency("name")`` decorator. Import ``resolver`` from ``next.deps``.
   * - Template loaders
     - The ``TEMPLATE_LOADERS`` settings key.

The registry pattern is the right choice when the framework already knows how to consume the values and just needs to learn about a new entry.

The asset-stem registry is the extension point for teaching the static discovery scanner about a new filename inside a component.
``default_stems`` is not re-exported from the ``next.static`` package, so the registration requires the deep import ``from next.static.discovery import default_stems``.
Call ``default_stems.register(...)`` from ``AppConfig.ready`` so the new stem is known before the first component scan.

.. code-block:: python
   :caption: notes/apps.py

   from django.apps import AppConfig

   from next.static.discovery import default_stems


   class NotesConfig(AppConfig):
       name = "notes"

       def ready(self) -> None:
           default_stems.register("styles", "theme")

Autoreload Watch Specs
~~~~~~~~~~~~~~~~~~~~~~

The development reloader watches the page and component trees by default.
Call ``register_autoreload_watch_spec`` from ``next.server`` to add a directory of your own.

.. code-block:: python

   register_autoreload_watch_spec(path: Path, glob: str) -> None

``path`` is the filesystem root to watch.
``glob`` is a pattern relative to that root that selects the files whose changes trigger a reload.
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

A subsystem that produces watch specs of its own implements the ``FilesystemWatchContributor`` Protocol from ``next.server.watcher``.
The Protocol declares a single ``iter_watch_specs()`` method that yields ``(root, glob)`` pairs for the file watcher.
When the watcher collects the final spec list it sends the ``watch_specs_ready`` signal with ``sender`` set to the ``iter_all_autoreload_watch_specs`` function itself.
Subscribe to that signal to observe or audit the resolved spec set.
See :doc:`/content/internals/autoreload` for the full watcher pipeline.

Protocols
---------

A protocol is a structural contract.
Implement the methods listed in the protocol and pass the class to the framework.

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Protocol
     - Defined in
   * - Template loader
     - ``next.pages.loaders.TemplateLoader``
   * - Dedup strategy
     - ``next.static.collector.DedupStrategy``
   * - JS context serializer
     - ``next.static.JsContextSerializer``

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
     - ``DEDUP_STRATEGY`` in static backend ``OPTIONS``
     - ``UrlDedup``
   * - JS context conflict policy
     - ``JS_CONTEXT_POLICY`` in static backend ``OPTIONS``
     - ``FirstWinsPolicy``
   * - JS context serializer
     - ``JS_CONTEXT_SERIALIZER`` settings key
     - ``JsonJsContextSerializer``

Use a strategy when the customisation is a single algorithm rather than a complete subsystem.

Signals
-------

A signal is an observation point.
Connect a receiver to react to a framework event.

The signal catalog lives in :doc:`signals`.
The patterns are uniform across the framework.

Choosing Between Mechanisms
---------------------------

Picking the right mechanism saves work.
Use the entries below as a quick map.

- **Add a new URL pattern source.** Subclass ``RouterBackend`` and register it under ``DEFAULT_PAGE_BACKENDS``.
- **Recognise a new asset extension.** Register through the kind registry (``default_kinds``).
- **Recognise a new filename inside a component.** Register a custom stem (``default_stems``).
- **Validate every dispatch.** Implement a form action backend.
- **Log every dispatch.** Subscribe to the ``action_dispatched`` signal.
- **Change how URLs land in HTML.** Customise a static backend.
- **Vary URLs by request.** Use a request-aware static backend.
- **Inspect every rendered page.** Subscribe to the ``page_rendered`` signal.
- **Watch extra directories during development.** Call ``register_autoreload_watch_spec``. See *Autoreload Watch Specs* above.

Worked Examples
---------------

The repository ``examples/`` tree ships complete projects for every major extension mechanism.
:doc:`/content/misc/examples` lists each folder, a one-line focus, links to GitHub, and the sections of this manual that explain the same techniques.

See Also
--------

.. seealso::

   :doc:`signals` for the observation points.
   :doc:`dependency-injection` for the provider registry and custom markers.
   :doc:`/content/howto/extend-a-default-backend` for the helper details.
   :doc:`/content/ref/conf` for the configuration surface.
   :doc:`/content/internals/index` for how each subsystem composes.
