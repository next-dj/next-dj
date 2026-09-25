.. _internals:

Internals
=========

The internals section explains how next.dj works under the hood.
Each page traces one pipeline with a mermaid diagram, lists the modules involved, and points at the public hooks used to extend it.
The pages read top to bottom, from a whole-framework map down to individual subsystem pipelines.

The shape every one of them fits into is the request pipeline below.

.. mermaid::

   flowchart LR
       Request[HTTP request] --> Router["next.urls<br/>file router"]
       Router --> View["next.pages<br/>page view"]
       View --> Deps["next.deps<br/>fill parameters"]
       Deps --> Compose["next.pages + next.components<br/>compose and render"]
       Compose --> Static["next.static<br/>collect and inject assets"]
       Static --> Response[HTML response]
       View -- "zones named" --> Partial["next.partial<br/>render zones, build patches"]
       Partial --> Envelope[Patch envelope]
       Request -- "POST /_next/form/uid/" --> Forms["next.forms<br/>guard, bind, dispatch"]
       Forms -- "valid" --> Redirect[Redirect or handler response]
       Forms -- "invalid" --> Compose

:doc:`request-lifecycle` expands that into the full account, and :doc:`overview` maps the subsystems it names.

:doc:`overview`
   Map of every subsystem with a signals fan-out diagram.

:doc:`request-lifecycle`
   End to end path of an HTTP request.

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

:doc:`partial-pipeline`
   Zones, zone render, patch envelopes, registries.

:doc:`seo-pipeline`
   Sitemap and robots discovery, the items registry, the URL slot, resets.

:doc:`autoreload`
   Watchers, route reload, signals.

:doc:`contributing-notes`
   Conventions the framework code follows.

:doc:`adding-an-area`
   Backbone contract for a new subsystem package.

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
   partial-pipeline
   seo-pipeline
   autoreload
   contributing-notes
   adding-an-area
