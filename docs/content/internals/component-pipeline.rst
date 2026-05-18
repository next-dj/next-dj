.. _internals-component-pipeline:

Component Pipeline
==================

This page covers how the components backend discovers component folders, loads their Python modules, resolves their context, and renders the final HTML fragment.

.. contents::
   :local:
   :depth: 2

Overview
--------

The components pipeline runs once at startup and on every autoreload.
The result is a registry of ``ComponentInfo`` records keyed by name with the template path, the module, and the per-component context functions attached.

Pipeline
--------

.. mermaid::

   flowchart LR
       Scanner[Scanner] --> Registry[Registry]
       Registry --> Manager[ComponentsManager]
       Manager --> Resolve[Resolve name]
       Resolve --> Loading[Load module]
       Loading --> ContextReg[Component context]
       Resolve --> Renderer[Renderer]
       ContextReg --> Renderer
       Renderer --> FilterExpr[FilterExpression props]
       FilterExpr --> Template[Template engine]
       Template --> Output[HTML fragment]

Modules
-------

``next.components.scanner``.
   Walks every page root for folders matching ``COMPONENTS_DIR``.
   Emits one ``ComponentInfo`` per folder.

``next.components.registry``.
   ``ComponentRegistry`` stores entries in order.
   ``ComponentVisibilityResolver`` decides which entries are reachable from a given template path.

``next.components.loading``.
   ``ModuleLoader`` imports ``component.py``.
   ``ModuleCache`` keeps the imports between requests.

``next.components.context``.
   ``component`` (the decorator namespace), ``ComponentContextManager``, ``ComponentContextRegistry``, and ``ContextFunction``.

``next.components.renderers``.
   ``ComponentRenderStrategy`` plus the simple and composite implementations.
   ``ComponentTemplateLoader`` reads the template body.

``next.components.facade``.
   Short helpers used from templates, including ``get_component``, ``load_component_template``, ``render_component``.

``next.components.info``.
   ``ComponentInfo`` value object.

``next.components.backends``.
   ``ComponentsBackend`` contract.
   ``FileComponentsBackend`` default implementation.
   ``ComponentsFactory`` for dotted path lookup.

``next.components.watch``.
   Watch specs exposed to the autoreloader.

Resolution Order
----------------

A component reference resolves through the visibility resolver.

1. Look for a component folder under the page's own scope.
2. Look for a component folder inside any extra root directory.
   Each backend entry under ``DEFAULT_COMPONENT_BACKENDS`` reads its extra roots from the ``DIRS`` key.
   Within every root the scanner matches folders whose name equals the backend's ``COMPONENTS_DIR`` value.
3. Pick the first match in registration order, which matches the order of backend entries.

Two components in the same scope with the same name are reported by ``next.E020`` and ``next.E034``.

Filter Expression Props
-----------------------

The ``{% component %}`` template tag accepts dynamic props through Django ``FilterExpression``.
A prop like ``title=note.title`` resolves against the surrounding template context at render time.
A prop like ``title="Hello"`` stays a literal string.

The renderer parses the props into a dict and forwards both the literal values and the surrounding scope into the component template.

Component Context Resolution
----------------------------

Each ``@component.context("key")`` function runs once per component render.
The resolver shares its cache with the page render so values produced by page level context are reused inside the component.

Signals
-------

The pipeline fires four signals.

- ``component_registered`` once per component on startup or reload.
- ``components_registered`` once per bulk discovery cycle with the full list.
- ``component_backend_loaded`` once per backend instance.
- ``component_rendered`` after each render with the resulting HTML.

Extension Points
----------------

- Subclass ``ComponentsBackend`` to serve components from another source.
- Subclass ``ComponentRenderStrategy`` for non standard rendering, for example a JSX bridge.
- Subscribe to ``components_registered`` to keep caches in sync with the registry.

See Also
--------

.. seealso::

   :doc:`/content/topics/components` for the topic guide.
   :doc:`request-lifecycle` for where the component pipeline sits.
