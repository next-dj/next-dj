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
   ``ComponentTemplateLoader`` reads the template body and compiles it on every render.
   ``CachedComponentTemplateLoader``, the loader the manager wires in, keeps that compilation and revalidates it against the mtime of the file the body came from, so a repeated render costs one ``stat`` under ``DEBUG`` and nothing at all in production.
   The mtime is read before the body is, so a save that lands between the two reaches the next render rather than being filed under the text it replaced.

``next.components.facade``.
   Short helpers used from templates, including ``get_component``, ``load_component_template``, ``render_component``.

``next.components.info``.
   ``ComponentInfo`` value object.

``next.components.backends``.
   ``ComponentsBackend`` contract.
   ``FileComponentsBackend`` default implementation.
   Two abstract methods answer names, and six optional hooks with declining defaults carry everything else, which :doc:`/content/topics/components` covers one by one.
   ``discover`` and ``import_component_modules`` are two hooks rather than one because ``LAZY_COMPONENT_MODULES`` populates the registry without executing a single ``component.py``.

``next.components.manager``.
   ``ComponentsManager`` orchestrates the backends, shares one render pipeline between them, and builds the list with the shared ``load_backends`` helper.
   A ``settings_reloaded`` drops the cached backends, and the next access rebuilds them.
   A Django ``TEMPLATES`` change drops the render pipeline the same way, because a compiled component template carries the engine that built it.
   ``next.components.watch`` reads the loaded backends through the manager and asks each for ``watch_roots``, then walks those trees with a scanner of its own, so neither the component registries nor the router registry move.

``next.seeding``.
   ``RenderFrame`` carries the ambient values a component render inherits from the page around it, and its ``seed`` writes them into a context the caller is still building.
   ``next.forms.widgets`` and ``next.testing.rendering`` hold a frame each, because both build their context from scratch instead of copying a surrounding scope.
   ``ambient_frame`` publishes one for the span of a render, which is how a widget built after its form was bound still finds the anchors of that form.

``next.components.checks``.
   The components system checks, including ``next.E020`` and ``next.E034``.
   They read the per-run manager ``next.components.sources.get_components_manager`` builds, which registers the ``_components`` folders under the page trees itself instead of waiting for the router walk to reach them, so every check sees the same components whatever asked for the manager first.
   The checks enumerate through ``ComponentsBackend.iter_components``, so a custom backend joins the reports by implementing that hook and stays out of them by leaving it alone.

Resolution order
----------------

A component reference resolves through the visibility resolver.
The resolver collects every component visible from the template path, then scores each by scope specificity.
The highest score wins.
A component nested in a sub-folder of the template's own page tree outscores a same-named component contributed at a tree root or through a ``DIRS`` root.
A page-tree root and a ``DIRS`` root both score zero, so the tie breaks on origin, and the page-tree component wins.

The full sort key is ``(-score, dirs_origin, component.name, registration_position)``, where ``dirs_origin`` is ``0`` for a page-tree component and ``1`` for a ``DIRS`` component.
At equal score a page-tree component sorts before a ``DIRS`` one, so a project-local component shadows a shared ``DIRS`` entry.
A remaining same-origin tie is decided by registration order alone, so within one origin the component discovered first shadows a later same-named one.
The component name in the sort key only groups candidates of different names next to each other.
Registration order operates inside a single ``FileComponentsBackend``.
``DIRS`` roots are scanned at app ready, before the URL router walk registers page-tree folders, but the origin dimension of the sort key makes the page-tree component win regardless of that order.
Across backends, the order of entries in ``COMPONENT_BACKENDS`` decides which backend is consulted first.

Two components sharing a name under one ``(scope_root, scope_relative)`` pair are reported by ``next.E020``, because nothing in the sort key above tells them apart.
``next.E034`` reports one name at the root scope of two roots the same template resolves against with neither taking precedence, for example two ``DIRS`` roots, which are visible everywhere, or one page tree nested inside another.
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
An unkeyed callable's dict is checked before the merge, so a key naming a prop of the rendering ``{% component %}`` call site, a reserved render key, or anything carrying the ``slot_`` prefix raises ``ValueError`` rather than overwriting the entry.
When a component's ``component.py`` fails to import, the renderer falls back to plain template rendering and the ``@component.context`` callables in that module do not run.
On the template render path the resolver shares the request-scoped dependency cache through ``get_request_dep_cache``.
Named ``Depends("name")`` values resolved earlier in the dispatch are reused inside the component callables.
Only a form dispatch puts that cache on the request, so an ordinary GET leaves each component render building a ``DependencyCache`` of its own and two components asking for one name each pay for it.
Provider-resolved parameters are recomputed per call.
Page context values reach the component through the template scope, not through the DI cache.
A component whose ``component.py`` defines a ``render`` function uses a fresh ``DependencyCache`` for that call instead of the shared request cache.
The surrounding template scope (props and page context variables) is still forwarded to the resolver as DI parameters.
The lazy ``csrf_token`` and any ``@component.context`` callables are not run on this path.

Ambient render frame
~~~~~~~~~~~~~~~~~~~~

The ``{% component %}`` tag builds its child context from ``context.flatten()``, so the ambient keys of the surrounding render come along untouched.
A caller that builds its context from scratch carries a ``RenderFrame`` instead and seeds it, which is what ``ComponentWidget`` and ``render_component_by_name`` do.
The seed writes the anchor the lookup ran from, the path of the page module, the anchor the actions of the enclosing form resolve against, and the static collector, and it leaves the request to the render strategies that stamp it themselves.
The frame is plain data, so ``bind_component_widgets`` settles the anchor once and a render reuses the frame it was handed rather than copying it.
A widget no bind reached asks for the frame the ``{% form %}`` tag publishes around its body, which is what carries the anchors into the widgets of ``formset.empty_form``, and falls back to a synthetic name under ``BASE_DIR``, then under the working directory, so the walk starts at the project root rather than above it.
The four seeded keys are reserved render keys, so an unkeyed ``@component.context`` that returns one raises ``ValueError`` under either caller.
``render_component_by_name`` applies its ``context`` and ``props`` mappings over the seed, so a caller naming a seeded key replaces the seeded value.

Signals
-------

The pipeline fires four signals.

- ``component_registered`` fires only on a one-at-a-time ``ComponentRegistry.register`` call, which folder discovery never takes.
- ``components_registered`` fires once per bulk ``register_many`` call, carrying that call's batch in ``infos``.
- ``component_backend_loaded`` once per backend instance, sent by the backend class with ``config`` and ``instance``.
- ``component_rendered`` after each render, carrying the ``ComponentInfo`` and its ``template_path``.

Extension points
----------------

- Subclass ``ComponentsBackend`` to serve components from another source.
- Name a ``ComponentTemplateLoader`` subclass under ``COMPONENT_TEMPLATE_LOADER`` to decide where a component body is read from and how long a compiled template is reused.
  The manager builds one instance of it around the shared module loader, so the loader is the seam between a backend's records and the template engine.
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
