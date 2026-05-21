.. _ref-components:

Components Reference
====================

Module Summary
--------------

``next.components`` exposes the component discovery, registration, and rendering API.
The names in this reference are grouped by their intended audience.

.. note::

   The three API tiers discussed in :doc:`/content/faq/general` apply to this package.
   Underscore-prefixed render helpers under *Internal Infrastructure* are hooks for tests and framework code, not for everyday imports in applications.

Application Imports
-------------------

These are the names project code uses day-to-day.

.. autodata:: next.components.component
   :no-value:

   The component decorator namespace.
   Inside a ``component.py`` use ``@component.context("key")`` to publish a value for the component template.

.. autodata:: next.components.context
   :no-value:

   The ``@component.context`` decorator, bound from ``ComponentContextManager.context``. Registers a context function inside a ``component.py``.

.. autofunction:: next.components.get_component

.. autofunction:: next.components.load_component_template

.. autofunction:: next.components.render_component

Manager
~~~~~~~

.. autoclass:: next.components.ComponentsManager
   :members:

.. autodata:: next.components.components_manager

Framework Extension
-------------------

These names are used when writing a custom component backend or a custom renderer.

Backends
~~~~~~~~

.. autoclass:: next.components.ComponentsBackend
   :members:

.. autoclass:: next.components.FileComponentsBackend
   :members:

.. autoclass:: next.components.ComponentsFactory
   :members:

.. autofunction:: next.components.register_components_folder_from_router_walk

The URL router calls this during the page-tree walk and application code does not invoke it directly.

Context Pipeline
~~~~~~~~~~~~~~~~

.. autoclass:: next.components.ComponentContextManager
   :members:

.. autoclass:: next.components.ComponentContextRegistry
   :members:

Renderers
~~~~~~~~~

.. autoclass:: next.components.ComponentRenderer
   :members:

.. autoclass:: next.components.ComponentRenderStrategy
   :members:

.. autoclass:: next.components.SimpleComponentRenderer
   :members:

.. autoclass:: next.components.CompositeComponentRenderer
   :members:

.. autoclass:: next.components.ComponentTemplateLoader
   :members:

``ComponentsManager`` wires a single ``ComponentTemplateLoader`` into its render pipeline.
The loader is fixed and not pluggable, so a custom backend reads component template bodies through this class rather than substituting its own.

Internal Infrastructure
-----------------------

These classes are implementation details.
They are exported for testing and advanced instrumentation.
Prefer the Application Imports tier unless you are building framework tooling.

.. autoclass:: next.components.ComponentInfo
   :members:

``scope_key`` is the stable grouping tuple the duplicate-name check uses to detect same-scope collisions.

.. autoclass:: next.components.ContextFunction
   :members:

.. autoclass:: next.components.ComponentRegistry
   :members:

.. autoclass:: next.components.ComponentVisibilityResolver
   :members:

.. autoclass:: next.components.ModuleCache
   :members:

.. autoclass:: next.components.ModuleLoader
   :members:

.. autoclass:: next.components.ComponentScanner
   :members:

.. autofunction:: next.components.component_extra_roots_from_config

.. autofunction:: next.components.get_component_paths_for_watch

Test Doubles
~~~~~~~~~~~~

``DummyBackend`` and ``BoomBackend`` are minimal ``ComponentsBackend`` implementations kept in this module so that dotted-path resolution in tests works through the standard factory.
They are **not** intended for production use.

``DummyBackend`` accepts a config dict, stores it on ``self``, and resolves no components.
Use it to test factory wiring.

.. autoclass:: next.components.DummyBackend
   :members:

``BoomBackend`` raises ``RuntimeError`` from ``__init__`` so you can assert that ``ComponentsManager`` catches and logs a failed backend instantiation.

.. autoclass:: next.components.BoomBackend
   :members:

The underscore-prefixed render helpers exported from this module are internal hooks.
Do not use them in application code.

.. autofunction:: next.components._inject_component_context

.. autofunction:: next.components._merge_csrf_context

.. autofunction:: next.components._render_template_string

Signals
-------

See :doc:`signals` and :doc:`/content/topics/signals` for the components signals.

The module ``next.components.signals`` exposes four ``django.dispatch.Signal`` instances.

.. list-table::
   :header-rows: 1
   :widths: 30 25 45

   * - Signal
     - Sender
     - Payload
   * - ``component_registered``
     - ``ComponentRegistry``
     - ``info`` (``ComponentInfo``)
   * - ``components_registered``
     - ``ComponentRegistry``
     - ``infos`` (tuple of ``ComponentInfo``)
   * - ``component_backend_loaded``
     - ``ComponentsManager``
     - ``backend`` (``ComponentsBackend``), ``config`` (mapping)
   * - ``component_rendered``
     - ``ComponentsManager``
     - ``info`` (``ComponentInfo``), ``template_path`` (``Path`` or ``None``)

The package ``__init__`` re-exports ``next_framework_settings`` from :doc:`/content/ref/conf` as a convenience for backend code that reads ``LAZY_COMPONENT_MODULES``.

See Also
--------

.. seealso::

   :doc:`/content/topics/components` for the topic guide.
   :doc:`/content/topics/extending` for custom backends and render hooks.
   :doc:`/content/topics/testing` for rendering components in isolation.
   :doc:`/content/internals/component-pipeline` for the discovery and render pipeline.
