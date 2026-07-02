.. _topics-extending:

Extending
=========

next.dj exposes five extension mechanisms.
Each section below states what its mechanism replaces and where to register it.

.. contents::
   :local:
   :depth: 2

The Five Mechanisms
-------------------

Backend.
   Replace or augment a complete subsystem.
   Used for URL routing, components, forms dispatch, and the :doc:`static pipeline <static-assets/index>`.

Registry.
   Add new entries to a global list at startup.
   Used for asset kinds, custom stems, :doc:`dependency injection <dependency-injection>` providers, and DI markers.

Protocol.
   Implement a runtime contract.
   Used for template loaders and JS context serializers.

Strategy.
   Swap an internal algorithm.
   Used for static deduplication and the JS context conflict policy.
   A strategy is selected by dotted path and implements a small protocol.

Signal.
   Observe a lifecycle event without changing it.
   Used for audit, observability, cache invalidation, and cross-app coordination.

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

A backend always implements the full contract.
A custom backend usually subclasses the default so it inherits every default behaviour.

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
   * - Template loaders
     - The ``TEMPLATE_LOADERS`` settings key.

The registry pattern is the right choice when the framework already knows how to consume the values and only needs to learn about a new entry.

The asset-stem registry is the extension point for teaching the static discovery scanner about a new asset filename next to a page, layout, or component.
``default_stems`` is not re-exported from the ``next.static`` package, so the registration requires the deep import ``from next.static.discovery import default_stems``.
Call ``default_stems.register(...)`` from ``AppConfig.ready`` so the new stem is known before the first component scan.

.. code-block:: python
   :caption: notes/apps.py

   from django.apps import AppConfig
   from next.static.discovery import default_stems

   class NotesConfig(AppConfig):
       name = "notes"

       def ready(self) -> None:
           default_stems.register("component", "theme")

Autoreload Watch Specs
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

Protocols and Abstract Base Classes
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

- **Add a new URL pattern source.** Subclass ``RouterBackend`` and register it under ``PAGE_BACKENDS``.
- **Recognise a new asset extension.** Register through the kind registry (``default_kinds``).
- **Recognise a new asset filename next to a page, layout, or component.** Register a custom stem (``default_stems``).
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
