.. _topics:

Topic Guides
============

Topic guides cover one subsystem at a time.
Read the topic that matches the part you are touching, then jump to the reference for the exact API.

.. rubric:: Routing

:doc:`file-router`
   File based routing, captured parameters, page roots, hot reload.

:doc:`url-reversing`
   ``page_reverse`` and ``with_query`` helpers.

.. rubric:: Pages and Templates

:doc:`pages`
   Page modules, body sources, render functions, template loaders.

:doc:`layouts`
   Layout discovery, composition, layout level context.

:doc:`context`
   ``@context`` and ``@component.context`` patterns and inheritance.

.. rubric:: Components

:doc:`components`
   Simple and composite components, props, slots, co-located assets.

.. rubric:: Forms

:doc:`forms/index`
   Actions, templates, modelforms, formsets, dispatch internals, signals.

.. rubric:: Static Assets

:doc:`static-assets/index`
   Co-located files, deduplication, asset kinds, backends, JS context.

.. rubric:: Cross Cutting

:doc:`dependency-injection`
   Markers, providers, the resolver, request scoped cache.

:doc:`signals`
   Every signal emitted by the framework, with payload tables.

:doc:`testing`
   ``NextClient``, ``SignalRecorder``, registry isolation.

:doc:`extending`
   Five extension mechanisms across the framework.

.. rubric:: Project Layout

:doc:`project-layout`
   Recommended single project tree and settings.

:doc:`multi-project`
   Shared UI kit and per project page DIRS.

.. toctree::
   :hidden:
   :maxdepth: 1

   file-router
   url-reversing
   pages
   layouts
   components
   context
   dependency-injection
   signals
   testing
   extending
   project-layout
   multi-project
   forms/index
   static-assets/index
