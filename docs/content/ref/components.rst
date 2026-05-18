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

.. autofunction:: next.components.context

.. autofunction:: next.components.get_component

.. autofunction:: next.components.load_component_template

.. autofunction:: next.components.render_component

Manager
~~~~~~~

.. autoclass:: next.components.ComponentsManager
   :members:

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

Context Pipeline
~~~~~~~~~~~~~~~~

.. autoclass:: next.components.ComponentContextManager
   :members:

.. autoclass:: next.components.ComponentContextRegistry
   :members:

.. autoclass:: next.components.ContextFunction
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

Internal Infrastructure
-----------------------

These classes are implementation details.
They are exported for testing and advanced instrumentation.
Prefer the Application Imports tier unless you are building framework tooling.

.. autoclass:: next.components.ComponentInfo
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

``BoomBackend`` raises ``RuntimeError`` from ``__init__`` so you can assert that ``ComponentsManager`` catches and logs a failed backend instantiation.

The underscore-prefixed render helpers exported from this module (``_inject_component_context``, ``_merge_csrf_context``, ``_render_template_string``) are internal hooks.
Do not use them in application code.

Signals
-------

See :doc:`signals` and :doc:`/content/topics/signals` for the components signals (``component_registered``, ``components_registered``, ``component_backend_loaded``, ``component_rendered``).

See Also
--------

.. seealso::

   :doc:`/content/topics/components` for the topic guide.
   :doc:`/content/topics/extending` for custom backends and render hooks.
   :doc:`/content/topics/testing` for rendering components in isolation.
   :doc:`/content/internals/component-pipeline` for the discovery and render pipeline.
