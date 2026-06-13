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
       Registry --> Backend[FileComponentsBackend]
       Backend --> Manager[ComponentsManager]
       Manager --> Visibility[ComponentVisibilityResolver]
       Visibility --> Resolve[Resolve name]
       Resolve --> Loading[Load module]
       Loading --> ContextReg["Run @component.context"]
       Resolve --> Renderer[Renderer]
       ContextReg --> Renderer
       Renderer --> FilterExpr["Props, slots, children"]
       FilterExpr --> Template[Template engine]
       Template --> Output[HTML fragment]

Modules
-------

``next.components.scanner``.
   Scans one component directory.
   Emits one ``ComponentInfo`` per ``.djx`` file for simple components, and one per sub-directory holding ``component.djx`` or ``component.py`` for composite components.

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
   ``ComponentsFactory`` instantiates a backend from its ``BACKEND`` dotted path.

``next.components.watch``.
   Watch specs exposed to the autoreloader.

Resolution Order
----------------

A component reference resolves through the visibility resolver.
The resolver collects every component visible from the template path, then scores each by scope specificity.
The highest score wins.
A component nested in a sub-folder of the template's own page tree outscores a same-named component contributed at a tree root or through a ``DIRS`` root.
A page-tree root and a ``DIRS`` root both score zero, so the tie breaks on registration order.

The full sort key is ``(-score, component.name, registration_position)``.
Equal scores break first by component name, then by registration order, so the component discovered first shadows a later same-named one.
Registration order operates inside a single ``FileComponentsBackend``.
The page-tree backend records its roots during the URL router walk before ``DIRS`` roots are scanned, so its components shadow same-name entries from ``DIRS``.
Across backends, the order of entries in ``COMPONENT_BACKENDS`` decides which backend is consulted first.

Two components in the same scope with the same name are reported by ``next.E020``.
``next.E034`` reports one component name used at the root route scope of more than one page tree.

Filter Expression Props
-----------------------

The ``{% component %}`` template tag accepts dynamic props through Django ``FilterExpression``.
A prop like ``title=note.title`` resolves against the surrounding template context at render time.
A prop like ``title="Hello"`` stays a literal string.

The renderer parses the props into a dict and forwards both the literal values and the surrounding scope into the component template.

Component Context Resolution
----------------------------

Each ``@component.context("key")`` function runs once per component render.
When a component's ``component.py`` fails to import, the renderer falls back to plain template rendering and the ``@component.context`` callables in that module do not run.
On the template render path the resolver shares the request-scoped dependency cache through ``get_request_dep_cache``, so DI parameters resolved earlier in the request are reused inside the component callables.
Page context values reach the component through the template scope, not through the DI cache.
A component whose ``component.py`` defines a ``render`` function uses a fresh ``DependencyCache`` for that call instead of the shared request cache.
The surrounding template scope (props and page context variables) is still forwarded to the resolver as DI parameters.
The lazy ``csrf_token`` and any ``@component.context`` callables are not run on this path.

Signals
-------

The pipeline fires four signals.

- ``component_registered`` once per component on startup or reload.
- ``components_registered`` once per bulk discovery cycle with the full list.
- ``component_backend_loaded`` once per backend instance.
- ``component_rendered`` after each render, carrying the ``ComponentInfo`` and its ``template_path``.

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
