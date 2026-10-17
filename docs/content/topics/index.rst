.. _topics:

Topic guides
============

Read the topic that matches the part you are touching, then jump to the reference for the exact API.

.. rubric:: Routing

:doc:`file-router`
   File based routing, captured parameters, page roots, hot reload.

:doc:`url-reversing`
   ``page_reverse`` and ``with_query`` helpers.

.. rubric:: Pages, layouts, and components

:doc:`pages`
   Page modules, body sources, render functions, template loaders.

:doc:`layouts`
   Layout discovery and composition of the ``layout.djx`` chain.

:doc:`proxy`
   ``proxy.py``, the access of a directory and its subtree, inheritance, and ``public()``.

:doc:`context`
   ``@context`` and ``@component.context`` patterns and inheritance.

:doc:`components`
   Simple and composite components, props, slots, co-located assets.

:doc:`seo/index`
   Page metadata and its merge, the site scope, head tags, structured data, the sitemap and robots files, and the checks.

:doc:`caching`
   ``cache`` and ``headers`` in ``page.py``, what passes down the tree, and the rules that keep a shared page anonymous.

:doc:`scripts/index`
   Third-party scripts, consent, and page views.

.. rubric:: Forms and static assets

:doc:`forms/index`
   Actions, templates, modelforms, field components, formsets, multi-step wizards, dispatch internals, signals.

:doc:`static-assets/index`
   Co-located files, deduplication, asset kinds, backends, JS context.

:doc:`partial-rendering/index`
   Zones, patches, forms, the modal wizard, lazy zones, co-located JavaScript, and the SSE bridge.

.. rubric:: Cross-cutting concerns

:doc:`dependency-injection`
   Markers, providers, the resolver, request scoped cache.

:doc:`permissions`
   Object permissions in ``permissions.py``, the backend, the ``{% has_perm %}`` tag, and form guards.

:doc:`signals`
   Every signal emitted by the framework, with payload tables.

:doc:`testing`
   ``NextClient``, ``SignalRecorder``, registry isolation.

:doc:`extending`
   Six extension mechanisms across the framework.

.. rubric:: Project layout

:doc:`project-layout`
   Application composition, the page tree as delivery, project sizes, and settings.

:doc:`application-layout`
   Where models, querysets, services, and signals hold the logic of an application.

:doc:`boundaries`
   The graph of edges between applications, the rules it breaks, the ``boundaries.toml`` ratchet, and the strict mode.

:doc:`multi-project`
   Shared UI kit and per project page DIRS.

.. toctree::
   :hidden:
   :maxdepth: 1

   file-router
   url-reversing
   pages
   layouts
   proxy
   context
   components
   seo/index
   caching
   scripts/index
   forms/index
   static-assets/index
   partial-rendering/index
   dependency-injection
   permissions
   signals
   testing
   extending
   project-layout
   application-layout
   boundaries
   multi-project

.. seealso::

   :doc:`/content/security/index` for the guard model, the trust boundary, and the obligations a custom backend takes on.
