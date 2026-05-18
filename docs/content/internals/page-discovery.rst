.. _internals-page-discovery:

Page Discovery
==============

This page covers how the framework discovers pages from the filesystem, registers them, evaluates context, and composes the final body with the ancestor layout chain.

.. contents::
   :local:
   :depth: 2

Overview
--------

Discovery runs once at startup and again whenever the autoreload watcher fires.
The result is a set of Django URL patterns plus the context callables and layout chains attached to each ``page.py``.

Pipeline
--------

.. mermaid::

   flowchart LR
       Walk[Filesystem walk] --> Loaders[Template loaders]
       Loaders --> Manager[Page]
       Manager --> TemplateCache[Template cache]
       Manager --> ContextReg[Context registry]
       TemplateCache --> Render[Render request]
       ContextReg --> Render
       Render --> InheritedCtx[Inherited page.py context]
       InheritedCtx --> PageCtx[Page @context functions]
       PageCtx --> Processors[Context processors]
       Processors --> LayoutCompose[Layout composition]
       LayoutCompose --> Output[Final HTML body]

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

Render Path
-----------

1. The view loads the page module through the mtime-keyed module memo, reading from disk only when the file changed.
2. The body source produces the page body string.
3. ``Page.build_render_context`` assembles the template scope, see `Context Resolution`_ below.
4. The framework composes the ancestor layout chain, the innermost layout wrapping the page body first and each outer layout wrapping the result.
5. Each layout substitutes the wrapped content into ``{% block template %}{% endblock template %}``.
6. ``StaticManager.inject`` replaces ``{% collect_styles %}`` and ``{% collect_scripts %}`` placeholder tokens with the rendered tags accumulated by the request-scoped ``StaticCollector``.

When the body source is a ``render`` function that returns an ``HttpResponseBase``, the response is returned verbatim and steps 3 through 6 do not run.

Layout Composition
------------------

The framework reads each ancestor ``layout.djx`` from disk and replaces its ``{% block template %}{% endblock template %}`` region with the wrapped content.
The innermost layout wraps the page body, the outermost layout wraps everything.
Composition is string substitution, not Django template inheritance, so no page needs an explicit ``{% extends %}``.

The user-facing rules for layout discovery, the placeholder contract, and layout-level context live in :doc:`/content/topics/layouts`.

Body Source Priority
--------------------

The framework picks the highest priority body source.

1. ``render`` function on the page module.
2. ``template`` attribute on the page module.
3. Sibling ``template.djx`` file.
4. Custom template loader match.

A page with more than one source is flagged by ``next.W043``.

Context Resolution
------------------

``Page.build_render_context`` assembles the template scope in this order.

1. URL kwargs from the matched route are seeded into the context dict.
2. ``PageContextRegistry.collect_context`` runs in two sub-steps.

   a. Inherited context.
      Every ``@context(..., inherit_context=True)`` callable registered in ancestor ``page.py`` files, walked from the root inward toward the current page.
   b. Page-level context.
      The ``@context`` callables declared in the current ``page.py``, evaluated after inherited values are in place so the page can shadow any inherited key.

3. Context processors merge ``OPTIONS.context_processors`` from each page backend entry with ``context_processors`` from the **first** ``TEMPLATES`` entry.
   The merge concatenates the page backend paths ahead of the Django paths and then deduplicates by dotted path.
   Distinct paths stay in that order and run once each.
   When the same dotted path appears in both sources only its first occurrence survives, so the page backend entry takes precedence over the Django entry.
   Each surviving processor returns a dict that updates the merged scope, so a processor running later overrides an earlier key on a value collision.
   Only the first ``TEMPLATES`` backend participates when several are configured.
4. Component-level context functions run on demand as each ``{% component %}`` tag is evaluated during rendering.

The dependency resolver shares a per-request cache across all four steps so a value resolved once (for example, the current user from ``Depends``) is not recomputed.

The canonical description is in :doc:`/content/topics/context`.
This page focuses on which module performs each step.

Extension Points
----------------

- Register a new template loader in ``NEXT_FRAMEWORK["TEMPLATE_LOADERS"]``.
- Subclass ``Page`` to add metadata for rendering tools.
- Add a context processor for global template variables.

See Also
--------

.. seealso::

   :doc:`/content/topics/pages` for the topic guide.
   :doc:`/content/topics/layouts` for layout composition.
   :doc:`request-lifecycle` for the surrounding request pipeline.
