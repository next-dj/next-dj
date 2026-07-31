.. _ref-components:

Components reference
====================

Module summary
--------------

``next.components`` exposes the component discovery, registration, and rendering API.
The names in this reference are grouped by their intended audience.

.. note::

   The Application imports, Framework extension, and Internal infrastructure tiers follow the public-surface rules in :ref:`faq-safe-symbols`.

Application imports
-------------------

These are the names project code uses day-to-day.

.. autodata:: next.components.component
   :no-value:

   The component decorator namespace.
   Inside a ``component.py`` use ``@component.context("key")`` to publish a value for the component template.

.. autodata:: next.components.context
   :no-value:

   The ``@component.context`` decorator, bound from ``ComponentContextManager.context``.
   It registers a context function inside a ``component.py``.

.. autofunction:: next.components.get_component

.. autofunction:: next.components.load_component_template

.. autofunction:: next.components.render_component

Manager
~~~~~~~

.. autoclass:: next.components.ComponentsManager
   :members:

.. autodata:: next.components.components_manager

Framework extension
-------------------

These names are used when writing a custom component backend or a custom renderer.

Backends
~~~~~~~~

.. autoclass:: next.components.ComponentsBackend
   :members:

.. autoclass:: next.components.FileComponentsBackend
   :members:

.. autofunction:: next.components.register_components_folder_from_router_walk

The URL router calls this during the page-tree walk and application code does not invoke it directly.

Context pipeline
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

Internal infrastructure
-----------------------

These classes are implementation details.
They are exported for testing and advanced instrumentation.
Prefer the Application imports tier unless you are building framework tooling.

.. autoclass:: next.components.ComponentInfo
   :members:

The duplicate-name check groups by ``(scope_root, name)`` and ignores ``scope_relative``, so two same-named components anywhere in one tree collide under ``next.E020``.

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

Test doubles
~~~~~~~~~~~~

``DummyBackend`` and ``BoomBackend`` are minimal ``ComponentsBackend`` implementations kept in this module so that dotted-path resolution in tests works through the standard loader.
They are **not** intended for production use.

``DummyBackend`` accepts a config dict, stores it on ``self``, and resolves no components.
Use it to test backend wiring.

.. autoclass:: next.components.DummyBackend
   :members:

``BoomBackend`` raises ``RuntimeError`` from ``__init__`` so you can assert that a backend bug reaches the caller instead of being logged as a configuration error.

.. autoclass:: next.components.BoomBackend
   :members:

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
     - The component backend class
     - ``instance`` (``ComponentsBackend``), ``config`` (mapping)
   * - ``component_rendered``
     - ``ComponentsManager``
     - ``info`` (``ComponentInfo``), ``template_path`` (``Path`` or ``None``)

See also
--------

.. seealso::

   :doc:`/content/topics/components` for the topic guide.
   :doc:`/content/topics/extending` for custom backends and render hooks.
   :doc:`/content/topics/testing` for rendering components in isolation.
   :doc:`/content/internals/component-pipeline` for the discovery and render pipeline.
