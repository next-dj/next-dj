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
The result is a registry of pages keyed by directory path with the matching template loader, layout chain, and context registrations attached.

Pipeline
--------

.. mermaid::

   flowchart LR
       Walk[Filesystem walk] --> Loaders[Template loaders]
       Loaders --> Manager[PageManager]
       Manager --> Registry[Page registry]
       Manager --> ContextReg[Context registry]
       Manager --> Processors[Context processors]
       Registry --> Render[Render request]
       ContextReg --> Render
       Processors --> Render
       Render --> LayoutCompose[Layout composition]
       LayoutCompose --> Output[Final HTML body]

Modules
-------

``next.pages.loaders``.
   Registers template loaders such as ``DjxTemplateLoader``.
   Each loader recognises one extension and produces a body string for a given page directory.

``next.pages.manager``.
   Coordinates discovery, registration, and rendering.
   Holds the ``Page`` value object exposed through ``next.pages.Page``.

``next.pages.registry``.
   Stores ``PageContextEntry`` records and resolves context for a request.

``next.pages.context``.
   Implements the ``@context`` decorator and the ``Context`` marker.

``next.pages.processors``.
   Runs Django context processors plus framework processors before page level context functions.
   Merge precedence is global processors, layout context, page context, in that order.

``next.pages.watch``.
   Returns the watch specs that the autoreloader uses to track page directories.

Render Path
-----------

1. The view loads the page module.
2. The dependency resolver invokes every registered ``@context`` function for the page.
3. The body source produces the page body.
4. The framework walks the ancestor layout chain bottom up.
5. Each layout substitutes the wrapped content into ``{% block template %}``.
6. The framework runs ``{% collect_styles %}`` and ``{% collect_scripts %}`` against the request scoped collector.

Layout Composition
------------------

Layouts compose by string substitution, not Django template inheritance.
The framework reads each ``layout.djx`` from disk and replaces the ``{% block template %}{% endblock template %}`` region with the wrapped content.
The closest layout wraps the page body.
The farthest layout wraps everything.

This avoids the need for ``{% extends %}`` per page and keeps the chain explicit.

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

Context resolution happens in this order.

1. Context processors configured on the page backend.
2. Inherited context functions from every ancestor layout, evaluated from the page root downward.
3. Page level context functions declared in ``page.py``.
4. Component level context functions when ``{% component %}`` evaluates.

The dependency resolver shares its cache across the chain so a value asked for twice is computed once.

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
