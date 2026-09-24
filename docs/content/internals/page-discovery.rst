.. _internals-page-discovery:

Page discovery
==============

This page covers how the framework discovers pages from the filesystem, registers them, evaluates context, and composes the final body with the ancestor layout chain.

.. contents::
   :local:
   :depth: 2

Overview
--------

Discovery runs lazily on the first URL access and again whenever the autoreload watcher fires.
The result is a set of Django URL patterns plus the context functions and layout chains attached to each ``page.py``.

Pipeline
--------

.. mermaid::

   flowchart LR
       Walk[Filesystem walk] --> Dispatcher[FilesystemTreeDispatcher]
       Dispatcher --> Pairs["(url_path, page.py) pairs"]
       Pairs --> Manager[Page]
       Manager --> ContextReg[Context registry]
       Manager --> RenderReq[Render request]
       ContextReg --> RenderReq
       RenderReq --> Loaders[Template loaders]
       Loaders --> LayoutCompose[Layout composition]
       LayoutCompose --> InheritedCtx[Inherited page.py context]
       InheritedCtx --> PageCtx[Page @context functions]
       PageCtx --> Processors[Context processors]
       Processors --> Output[Final HTML body]

Modules
-------

``next.pages.loaders``.
   Registers template loaders such as ``DjxTemplateLoader``.
   Each loader recognises one extension and produces a body string for a given page directory.

``next.pages.manager``.
   A package whose ``__init__`` defines the ``Page`` coordinator that loads templates, collects context, composes layouts, renders, and builds the page ``URLPattern``.
   The process-wide singleton is exposed as ``next.pages.page`` and the class as ``next.pages.Page``.
   The package also implements the ``@context`` decorator.

``next.pages.manager.templates``.
   Holds ``PageTemplateCache``, the per-page composed source, compiled ``Template``, and layout skeleton layers, each with the mtime snapshot that dates it.
   See `Composed-template cache`_ below for the layers and their staleness rule.

``next.pages.manager.views``.
   Builds the two routed views, the static one that serves a compiled composed template and the resolving one that runs ``render()`` per request, and the ``URLPattern`` the file router mounts either under.
   Both carry ``next_page_path`` and both stamp the partial ``Vary`` set on the full-page branch.

``next.pages.placeholder``.
   Owns the ``{% template %}`` grammar, the single and the paired ``{% #template %}...{% /template %}`` spellings, and the Django-lexer scan that locates the region in a layout source.
   Composition and the layout checks read the same spellings from here, which is why a placeholder inside ``{% verbatim %}`` or ``{% comment %}`` is left alone.

``next.pages.registry``.
   Stores ``PageContextEntry`` records and resolves context for a request.

``next.pages.context``.
   Defines the ``Context`` marker and the context-injection providers.

``next.pages.processors``.
   Discovers and imports the context processor callables listed in each page backend's ``OPTIONS.context_processors`` and in the first Django ``TEMPLATES`` entry.
   Processors are applied after all ``@context`` functions finish, so a processor that returns the same key as a context function overrides it.

``next.pages.scan``.
   Walks the routed page tree once per check run and yields the existing ``page.py`` paths, plus the keyed ``serialize=True`` context keys the static reserved-key check reads.

``next.pages.paths``.
   Memoises the path facts of one ``page.py``, its module path, its template path, and its ancestor chain, in a bounded cache the composition lifecycle clears.

``next.pages.errors``.
   Defines the two exceptions the area raises.
   ``PageModuleImportError`` is what a broken ``page.py`` raises on the request path, and ``PageContextShapeError`` is what a keyless ``@context`` answering a non-mapping raises during the context merge.

``next.pages.ports``.
   Holds ``PageScanImpl``, which binds the page-tree scan to the ``PageScan`` port so discovery reaches the scan without an import that would close the cycle.

``next.pages.checks``.
   Registers the Django system checks for the pages subsystem, listed with their identifiers in :doc:`/content/ref/system-checks`.

``next.pages.watch``.
   Reports the page roots of each router backend and the components folder name each root carries, which ``next.server.watcher`` turns into the ``(path, glob)`` watch specs the autoreloader consumes.

Render path
-----------

1. The router loads the page module through the mtime-keyed memo once, while it builds the URL pattern, and the generated view closes over that module.
   A page whose module raised at build time is the exception, and its view re-reads the memo on every request so a fixed file recovers without a restart.
2. The body source produces the page body string.
3. The framework composes the ancestor layout chain, the innermost layout wrapping the page body first and each outer layout wrapping the result.
   Each layout substitutes the wrapped content into ``{% template %}``.
4. ``Page.build_render_context`` assembles the template scope, see `Context resolution`_ below.
5. The composed template string renders against the assembled scope.
6. The static manager replaces the ``{% collect_styles %}`` and ``{% collect_scripts %}`` placeholder tokens with the rendered tags accumulated by the request-scoped ``StaticCollector``.

When the body source is a ``render`` function that returns an ``HttpResponseBase``, the response is returned verbatim and steps 3 through 6 do not run.

Composed-template cache
-----------------------

``Page`` holds a ``PageTemplateCache`` whose layers short-circuit layout composition.
Two of them back ``composed_template_for``, read by the canonical full-page render of a page without ``render()``, by the form re-render after a validation failure, by the standalone zone render, and by direct ``Page.render`` calls such as ``next.testing.render_page``.
A page whose body comes from ``render()`` resolves that body per request and caches only the layout chain around it.

``composed``.
   Maps a ``page.py`` path to its already-composed template string.

``compiled``.
   Maps a ``page.py`` path to the compiled Django ``Template`` built from the composed source, carrying an ``Origin`` so a compile error names the page path.
   Writing the source registry drops the compiled entry with it.

``composed_sources``.
   Snapshots the modification time of every file that contributed to the composition, including the page body source and each ancestor ``layout.djx``, and of every directory the layout walk visited.
   The directories are tracked because a ``layout.djx`` that appears or disappears moves no mtime of a file the snapshot already holds.
   Only the directories inside the page tree are tracked, so an unrelated write to a shared parent such as the home directory evicts nothing, and a ``layout.djx`` created above the tree joins the chain on the next composition instead.

``skeleton``.
   Maps a ``page.py`` path whose body comes from ``render()`` to its layout chain with an empty body slot, filled with the resolved body on each request.
   No dynamic body enters the composed-source registry.
   Its own snapshot lives in ``skeleton_sources``, so an eviction on the composed side never reads as freshness here.

The snapshot is taken on every composition, and only the check reading it is gated on ``DEBUG`` through ``next.utils.template_edits_watched``, read per call so an override takes effect at once and sees the sources of a composition built before it.
The dev watcher deliberately ignores ``.djx``, so under ``DEBUG`` this is the only mechanism that makes a template edit visible without a restart, and with ``DEBUG`` off a warm read performs no stat and holds the composition for the life of the process.

On each cache read ``composed_is_stale`` or ``skeleton_is_stale`` compares the current mtimes against the snapshot.
A change to any contributing path evicts the entry, the composition step rebuilds the template string, and the new snapshot is stored.
A tracked path that no longer stats counts as a change, so a deleted ``layout.djx`` evicts the entry exactly like an edited one.

``Page.clear_template_caches`` drops all of them in one call.
A rewrite landing on the same mtime tick is invisible to the staleness check, which is why ``next.testing.reset_page_cache`` calls it between renders of a file rewritten in place.

Layout composition
------------------

The framework reads each ancestor ``layout.djx`` from disk and replaces its ``{% template %}`` region with the wrapped content.
Django's own lexer locates that region, so a placeholder written inside ``{% verbatim %}`` or ``{% comment %}`` is text the composition leaves alone.
The innermost layout wraps the page body, the outermost layout wraps everything.
Composition is string substitution, not Django template inheritance, so no page needs an explicit ``{% extends %}``.

The user-facing rules for layout discovery, the placeholder contract, and layout-level context live in :doc:`/content/topics/layouts`.

Body source priority
--------------------

``Page._resolve_page_body`` and ``_load_static_body`` in ``next.pages.manager`` pick the highest priority body source.
See :doc:`/content/topics/pages` for the full priority order and the ``next.W043`` conflict warning.

Context resolution
------------------

``Page.build_render_context`` assembles the template scope in this order.

1. URL kwargs from the matched route are seeded into the context dict.
2. ``PageContextRegistry.collect_context`` runs in two sub-steps.

   a. Inherited context.
      Every ``@context(..., inherit_context=True)`` callable registered in ancestor ``page.py`` files, walked from the current page upward through every ancestor directory, bounded at 64 levels.
   b. Page-level context.
      The ``@context`` callables declared in the current ``page.py``, evaluated after inherited values are in place so the page can shadow any inherited key.

3. Context processors merge ``OPTIONS.context_processors`` from each page backend entry with ``OPTIONS.context_processors`` from the **first** ``TEMPLATES`` entry.
   The page backend paths are concatenated ahead of the Django paths.
   Deduplication by dotted path keeps the first occurrence, so a path shared by both sources runs once with the page backend taking precedence.
   Each surviving processor returns a dict that updates the merged scope, so a later processor overrides an earlier key on a collision.
4. Component-level context functions run on demand as each ``{% component %}`` tag is evaluated during rendering.

The dependency resolver shares one cache between the inherited and page-level sub-steps of a single ``collect_context`` call, so a value resolved at an ancestor is not recomputed for the page.
On a regular GET that cache does not extend to components, and each component render starts its own cache.
A cache that spans the page and its components exists only on the form-dispatch re-render after a validation failure, where the dispatcher attaches its cache to the request.

The canonical description is in :doc:`/content/topics/context`.
This page focuses on which module performs each step.

Extension points
----------------

- Register a new template loader in ``NEXT_FRAMEWORK["TEMPLATE_LOADERS"]``.
- Subscribe to ``page_rendered`` to observe every render, and to ``template_loaded`` to observe every composed template written to the registry.
- Add a context processor for global template variables.

``Page`` is not an extension point.
``next.pages.manager`` builds the ``page`` singleton at import time and no settings key swaps the class, so a subclass has no registration path and nothing would route requests through it.

See also
--------

.. seealso::

   :doc:`/content/topics/pages` for the topic guide.
   :doc:`/content/topics/layouts` for layout composition.
   :doc:`request-lifecycle` for the surrounding request pipeline.
