.. _ref-components:

Components Reference
====================

Module Summary
--------------

``next.components`` exposes the component discovery, registration, and rendering API.

Public API
----------

.. autodata:: next.components.component

.. autofunction:: next.components.context

.. autoclass:: next.components.ComponentsManager
   :members:

.. autoclass:: next.components.ComponentRegistry
   :members:

.. autoclass:: next.components.ComponentVisibilityResolver
   :members:

Info
~~~~

.. autoclass:: next.components.ComponentInfo
   :members:

Facade
~~~~~~

.. autofunction:: next.components.get_component

.. autofunction:: next.components.load_component_template

.. autofunction:: next.components.render_component

Backends
~~~~~~~~

.. autoclass:: next.components.ComponentsBackend
   :members:

.. autoclass:: next.components.FileComponentsBackend
   :members:

.. autoclass:: next.components.ComponentsFactory
   :members:

.. autofunction:: next.components.register_components_folder_from_router_walk

Context
~~~~~~~

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

Loading
~~~~~~~

.. autoclass:: next.components.ModuleCache
   :members:

.. autoclass:: next.components.ModuleLoader
   :members:

Scanner
~~~~~~~

.. autoclass:: next.components.ComponentScanner
   :members:

.. autofunction:: next.components.component_extra_roots_from_config

Watch
~~~~~

.. autofunction:: next.components.get_component_paths_for_watch

Signals
-------

See :doc:`signals` and :doc:`/content/topics/signals` for the components signals (``component_registered``, ``components_registered``, ``component_backend_loaded``, ``component_rendered``).

See Also
--------

.. seealso::

   :doc:`/content/topics/components` for the topic guide.
   :doc:`/content/internals/component-pipeline` for the discovery and render pipeline.
