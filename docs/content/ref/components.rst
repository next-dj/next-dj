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

.. autofunction:: next.components.collect_visible_components

.. autofunction:: next.components.load_component_template

.. autofunction:: next.components.render_component

Manager
~~~~~~~

``backends`` is the configured list in consultation order, and ``reload`` rebuilds it from the current ``NEXT_FRAMEWORK``.
Framework settings changes rebuild the manager on their own, so ``reload`` is for a caller that swaps ``COMPONENT_BACKENDS`` some other way, which is what ``next.testing.reset_components`` does.

.. autoclass:: next.components.ComponentsManager
   :members:

.. autodata:: next.components.components_manager

Framework extension
-------------------

These names are used when writing a custom component backend or a custom renderer.

Backends
~~~~~~~~

``ComponentsBackend.get_component`` and ``ComponentsBackend.collect_visible_components`` are the two abstract methods every backend implements, and the module-level helpers of the same name above delegate to them through the manager.
The rest of the contract has defaults that decline, so a backend implements only what its source can answer.
``discover`` is the eager population pass, ``import_component_modules`` executes the components' Python modules, ``register_walked_folder`` claims one components folder found during the page-tree walk, and ``iter_components`` with ``global_component_roots`` lets the system checks enumerate what the backend holds.

.. autoclass:: next.components.ComponentsBackend
   :members:

.. autoclass:: next.components.FileComponentsBackend
   :members:

.. autofunction:: next.components.register_components_folder_from_router_walk

The URL router calls this during the page-tree walk and application code does not invoke it directly.
It registers into the live ``components_manager``, which claims the folder on first registration, so a repeated walk over the same folder finds nothing left to do.
The manager offers the folder to each backend in configuration order and stops at the first whose ``register_walked_folder`` answers ``True``.
The system checks perform the same registration through ``ComponentsManager.register_router_walk_folder`` on the manager they read, so a check run sees every page-tree component without waiting for a router walk and without writing into the live manager.

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

.. autoclass:: next.components.CachedComponentTemplateLoader
   :members:

``ComponentsManager`` wires one ``CachedComponentTemplateLoader`` into its render pipeline, and the loader is fixed rather than configurable.
It keeps the compiled ``Template`` of each component, so a repeated render pays neither the read nor the parse.
Under ``DEBUG`` the entry is revalidated against the mtime of the file the body was read from, and an edit reaches the next render.
With ``DEBUG`` off nothing stats that file, and the compilation stands until eviction drops it.
The cache is bounded at 2048 compiled templates, the bound the visibility resolver also uses, and a ``TEMPLATES`` change drops it whole, because a compiled ``Template`` carries the engine that built it.
A component backend reads template bodies through that shared loader rather than substituting its own.

Internal infrastructure
-----------------------

These classes are implementation details.
They are exported for testing and advanced instrumentation.
Prefer the Application imports tier unless you are building framework tooling.

.. autoclass:: next.components.ComponentInfo
   :members:

The duplicate-name check groups by ``(scope_root, scope_relative, name)``, and the first two are exactly the scope the visibility resolver scores on, so only two components the resolver cannot tell apart collide under ``next.E020``.
The same name under a deeper route trail of one tree is the documented override and stays silent.

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
