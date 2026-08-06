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
The result is a set of Django URL patterns plus the context callables and layout chains attached to each ``page.py``.

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
   Defines the ``Page`` coordinator that loads templates, collects context, composes layouts, renders, and builds the page ``URLPattern``.
   The process-wide singleton is exposed as ``next.pages.page`` and the class as ``next.pages.Page``.
   The module also implements the ``@context`` decorator.

``next.pages.registry``.
   Stores ``PageContextEntry`` records and resolves context for a request.

``next.pages.context``.
   Defines the ``Context`` marker and the context-injection providers.

``next.pages.processors``.
   Discovers and imports the context processor callables listed in each page backend's ``OPTIONS.context_processors`` and in the first Django ``TEMPLATES`` entry.
   Processors are applied after all ``@context`` functions finish, so a processor that returns the same key as a context function overrides it.

``next.pages.watch``.
   Returns the watch specs that the autoreloader uses to track page directories.

Render path
-----------

1. The view loads the page module through the mtime-keyed module memo, reading from disk only when the file changed.
2. The body source produces the page body string.
3. The framework composes the ancestor layout chain, the innermost layout wrapping the page body first and each outer layout wrapping the result.
   Each layout substitutes the wrapped content into ``{% block template %}{% endblock template %}``.
4. ``Page.build_render_context`` assembles the template scope, see `Context resolution`_ below.
5. The composed template string renders against the assembled scope.
6. The static manager replaces the ``{% collect_styles %}`` and ``{% collect_scripts %}`` placeholder tokens with the rendered tags accumulated by the request-scoped ``StaticCollector``.

When the body source is a ``render`` function that returns an ``HttpResponseBase``, the response is returned verbatim and steps 3 through 6 do not run.

Composed-template cache
-----------------------

``Page`` keeps two parallel dicts that short-circuit layout composition for the callers of ``composed_template_for``.
Those callers are the form re-render after a validation failure, the standalone zone render, and direct ``Page.render`` calls such as ``next.testing.render_page``.
The canonical full-page path never consults the cache and recomposes the body and layout chain from the disk sources on each request.

``_template_registry``.
   Maps a ``page.py`` path to its already-composed template string.

``_template_source_mtimes``.
   Snapshots the modification time of every file that contributed to the composition, including the page body source and each ancestor ``layout.djx``.

On each cache read ``_is_template_stale`` compares the current mtimes against the snapshot.
A change to any contributing file evicts the entry, the composition step rebuilds the template string, and the new snapshot is stored.

``Page.clear_template_caches`` drops both dicts and the mtime snapshots in one call.
A rewrite landing on the same mtime tick is invisible to the staleness check, which is why ``next.testing.reset_page_cache`` calls it between renders of a file rewritten in place.

Layout composition
------------------

The framework reads each ancestor ``layout.djx`` from disk and replaces its ``{% block template %}{% endblock template %}`` region with the wrapped content.
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

3. Context processors merge ``OPTIONS.context_processors`` from each page backend entry with ``context_processors`` from the **first** ``TEMPLATES`` entry.
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
- Subclass ``Page`` to add metadata for rendering tools.
- Add a context processor for global template variables.

See also
--------

.. seealso::

   :doc:`/content/topics/pages` for the topic guide.
   :doc:`/content/topics/layouts` for layout composition.
   :doc:`request-lifecycle` for the surrounding request pipeline.
