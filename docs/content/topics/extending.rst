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
   Used for URL routing, components, forms dispatch, static pipeline, and JS context serialization.

Registry.
   Add new entries to a global list at startup.
   Used for asset kinds, custom stems, dependency providers, and DI markers.

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
     - ``next.static.serializers.JsContextSerializer``

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
     - ``next.static.default_kinds.register`` in ``AppConfig.ready``.
   * - Asset stems
     - ``next.static.discovery.default_stems.register`` in ``AppConfig.ready``.
   * - Placeholder slots
     - ``next.static.default_placeholders.register`` in ``AppConfig.ready``.
   * - Dependency providers
     - Subclass ``RegisteredParameterProvider``, imported in ``AppConfig.ready``.
   * - Named dependencies
     - ``next.deps.resolver.dependency`` decorator.
   * - Template loaders
     - The ``TEMPLATE_LOADERS`` settings key.

The registry pattern is the right choice when the framework already knows how to consume the values and just needs to learn about a new entry.

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
     - ``LastWinsPolicy``
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
The decision tree below covers the common cases.

I need to add a new URL pattern source.
   Use a backend that subclasses ``RouterBackend``.

I need to recognise a new asset extension.
   Use the kind registry.

I need to recognise a new filename inside a component.
   Use the stem setting.

I need to validate every dispatch.
   Use a form action backend.

I need to log every dispatch.
   Use the ``action_dispatched`` signal.

I need to change how URLs land in HTML.
   Use a static backend.

I need to vary URLs by request.
   Use a request-aware static backend.

I need to inspect every rendered page.
   Use the ``page_rendered`` signal.

Worked Examples
---------------

See ``examples/`` in the repository for one project per mechanism.

- ``examples/audit-forms`` shows a custom form action backend.
- ``examples/kanban`` shows a custom static backend and a custom asset kind.
- ``examples/live-polls`` shows a custom stem and a custom asset kind for Vue SFCs.
- ``examples/wiki`` shows a hybrid router backend that reads from the database.
- ``examples/feature-flags`` shows signal driven cache invalidation.
- ``examples/observability`` covers every signal of the framework.

See Also
--------

.. seealso::

   :doc:`signals` for the observation points.
   :doc:`/content/howto/extend-a-default-backend` for the helper details.
   :doc:`/content/ref/conf` for the configuration surface.
   :doc:`/content/internals/index` for how each subsystem composes.
