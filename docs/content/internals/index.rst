.. _internals:

Internals
=========

The internals section explains how next.dj works under the hood.
Each page traces one pipeline with a mermaid diagram, lists the modules involved, and points at the public hooks used to extend it.

.. rubric:: Entry points

:doc:`overview`
   Map of every subsystem with a signals fan out diagram.

:doc:`request-lifecycle`
   End to end path of an HTTP request.

.. rubric:: Subsystem pipelines

:doc:`page-discovery`
   Page modules, layouts, context.

:doc:`url-router`
   URL parsing, dispatch, reload.

:doc:`component-pipeline`
   Component discovery, loading, rendering.

:doc:`di-resolver`
   Parameter resolution, providers, cache.

:doc:`static-pipeline`
   Asset discovery, collector, backends, injection.

:doc:`action-dispatch`
   Form dispatch, validation, re-render.

:doc:`autoreload`
   Watchers, route reload, signals.

.. rubric:: For contributors

:doc:`contributing-notes`
   Internal guidelines for the framework itself.

.. toctree::
   :hidden:
   :maxdepth: 1

   overview
   request-lifecycle
   page-discovery
   url-router
   component-pipeline
   di-resolver
   static-pipeline
   action-dispatch
   autoreload
   contributing-notes
