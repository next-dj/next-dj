.. _internals-component-pipeline:

Component pipeline
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
   ``discover`` is the eager pass the app-ready hook calls on every backend, a no-op for one that resolves names on demand.
   ``import_component_modules`` is the separate capability of executing those components' Python modules, which is why ``LAZY_COMPONENT_MODULES`` can populate the registry without running a single ``component.py``.
   It returns the paths it imported, and a backend whose components carry no module returns an empty tuple.
   ``register_walked_folder`` is the ownership hook the page-tree walk calls, and ``iter_components`` with ``global_component_roots`` is the enumeration the system checks read.
   Each of the three has a default that declines, so a backend that resolves names on demand implements only the two abstract render methods.

``next.components.manager``.
   ``ComponentsManager`` orchestrates the backends, shares one render pipeline between them, and builds the list with the shared ``load_backends`` helper.
   A ``settings_reloaded`` drops the cached backends, and the next access rebuilds them.
   ``next.components.watch`` resolves the same entries with ``resolve_backend_class`` and never instantiates them, because its scan is read-only.

``next.components.checks``.
   The components system checks, including ``next.E020`` and ``next.E034``.
   They read the per-run manager ``next.checks.common.get_components_manager`` builds, which registers the ``_components`` folders under the page trees itself instead of waiting for the router walk to reach them, so every check sees the same components whatever asked for the manager first.
   The checks enumerate through ``ComponentsBackend.iter_components``, so a custom backend joins the reports by implementing that hook and stays out of them by leaving it alone.

``next.components.watch``.
   Watch specs exposed to the autoreloader.

Resolution order
----------------

A component reference resolves through the visibility resolver.
The resolver collects every component visible from the template path, then scores each by scope specificity.
The highest score wins.
A component nested in a sub-folder of the template's own page tree outscores a same-named component contributed at a tree root or through a ``DIRS`` root.
A page-tree root and a ``DIRS`` root both score zero, so the tie breaks on origin, and the page-tree component wins.

The full sort key is ``(-score, dirs_origin, component.name, registration_position)``, where ``dirs_origin`` is ``0`` for a page-tree component and ``1`` for a ``DIRS`` component.
At equal score a page-tree component sorts before a ``DIRS`` one, so a project-local component shadows a shared ``DIRS`` entry.
A remaining same-origin tie breaks first by component name, then by registration order, so within one origin the component discovered first shadows a later same-named one.
Registration order operates inside a single ``FileComponentsBackend``.
``DIRS`` roots are scanned at app ready, before the URL router walk registers page-tree folders, but the origin dimension of the sort key makes the page-tree component win regardless of that order.
Across backends, the order of entries in ``COMPONENT_BACKENDS`` decides which backend is consulted first.

Two components sharing a name under one ``(scope_root, scope_relative)`` pair are reported by ``next.E020``, because nothing in the sort key above tells them apart.
``next.E034`` reports one name at the root scope of two roots the same template resolves against with neither taking precedence: two ``DIRS`` roots, which are visible everywhere, or one page tree nested inside another.
A page tree and a ``DIRS`` root sharing a name are decided by the origin dimension of the sort key, so that pair is silent.

Filter expression props
-----------------------

The ``{% component %}`` template tag accepts dynamic props through Django ``FilterExpression``.
A prop like ``title=note.title`` resolves against the surrounding template context at render time.
A prop like ``title="Hello"`` stays a literal string.

The renderer parses the props into a dict and forwards both the literal values and the surrounding scope into the component template.

Component context resolution
----------------------------

Each ``@component.context("key")`` function runs once per component render.
When a component's ``component.py`` fails to import, the renderer falls back to plain template rendering and the ``@component.context`` callables in that module do not run.
On the template render path the resolver shares the request-scoped dependency cache through ``get_request_dep_cache``.
Named ``Depends("name")`` values resolved earlier in the dispatch are reused inside the component callables.
Provider-resolved parameters are recomputed per call.
Page context values reach the component through the template scope, not through the DI cache.
A component whose ``component.py`` defines a ``render`` function uses a fresh ``DependencyCache`` for that call instead of the shared request cache.
The surrounding template scope (props and page context variables) is still forwarded to the resolver as DI parameters.
The lazy ``csrf_token`` and any ``@component.context`` callables are not run on this path.

Signals
-------

The pipeline fires four signals.

- ``component_registered`` once per component on startup or reload.
- ``components_registered`` once per bulk discovery cycle with the full list.
- ``component_backend_loaded`` once per backend instance, sent by the backend class with ``config`` and ``instance``.
- ``component_rendered`` after each render, carrying the ``ComponentInfo`` and its ``template_path``.

Extension points
----------------

- Subclass ``ComponentsBackend`` to serve components from another source.
- Define a ``render`` function in ``component.py`` for a non standard render path, for example a JSX bridge.
- Subscribe to ``components_registered`` to keep caches in sync with the registry.

``ComponentRenderStrategy`` is not an extension point.
``ComponentsManager._ensure_render_pipeline`` builds the strategy list from the composite and simple renderers itself, and no settings key feeds that list, so a third strategy cannot be reached.
A render path the two built-in strategies do not cover goes through a ``render`` function in ``component.py``, or through a ``ComponentsBackend`` that hands back records the composite renderer can serve.

See also
--------

.. seealso::

   :doc:`/content/topics/components` for the topic guide.
   :doc:`request-lifecycle` for where the component pipeline sits.
