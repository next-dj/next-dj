.. _internals-request-lifecycle:

Request lifecycle
=================

This page traces an HTTP request from the Django entry point through next.dj to the rendered response.
It covers the regular page request flow, the zone branch for partial requests, and the parallel path used for form submissions.

.. contents::
   :local:
   :depth: 2

Overview
--------

A request enters through Django middleware as in any Django project.
Once the request reaches ``next.urls`` the framework takes over for resolution, context evaluation, layout composition, asset collection, and response building.

Pipeline
--------

.. mermaid::

   flowchart TB
       Browser(["Browser"]) -- HTTP request --> Django["Django middleware"]
       Django --> Resolver["Django URL resolver"]
       Resolver -- form dispatch path --> FormDispatch["Form dispatcher"]
       Resolver -- file routed path --> PageView["Page view"]
       PageView --> Loader["Page loader"]
       Loader --> BodySource{"Body source"}
       BodySource -- "render() function" --> RenderFn["Call render(), resolve its arguments"]
       BodySource -- "template / template.djx" --> StaticBody["Read static body string"]
       RenderFn --> ZoneIntent{"Partial intent"}
       StaticBody --> ZoneIntent
       ZoneIntent -- "zone request" --> ZoneResp["Zone response"]
       ZoneResp --> Response
       ZoneIntent -- "full page" --> LayoutChain["Compose layout chain"]
       LayoutChain --> ContextCtx["Run context functions"]
       ContextCtx --> CollectAssets["Static collector"]
       CollectAssets --> InjectTags["Emit collected tags"]
       InjectTags --> Response(["HTTP response"])
       FormDispatch --> Validation{"Form valid"}
       Validation -- yes --> Handler["Run handler"]
       Handler --> Response
       Validation -- no --> Loader

Implementation notes
--------------------

Django middleware
~~~~~~~~~~~~~~~~~

Django middleware runs first.
Authentication, sessions, CSRF, common middleware, and any project specific middleware all see the request before the framework does.

URL resolver
~~~~~~~~~~~~

The framework registers its URL patterns through ``include("next.urls")`` in ``config/urls.py``, which mounts the framework's ``TrieURLResolver``.
The resolver narrows the request path to a few candidate patterns and matches them with standard Django pattern resolution, as :doc:`url-router` describes.
A file routed match dispatches to the page view.
A match on ``/_next/form/<str:uid>/`` dispatches to the form dispatcher instead.

Page view
~~~~~~~~~

The page view loads the page module and resolves the body source first.
When the module exposes a ``render`` function the view calls it before context runs, resolving its arguments through the dependency resolver.
``render`` may return a string body or an ``HttpResponseBase`` that short-circuits the layout and static pipelines.
When the body comes from the ``template`` attribute or a ``template.djx`` file the view reads that source as a plain string.
After the body is in hand the view builds the render context and runs every ``@context`` function in order.
Captured URL kwargs from the matched route are seeded into the context dict before any ``@context`` function runs.

Zone requests
~~~~~~~~~~~~~

After the body source resolves, the view inspects the request for a partial intent.
A request that targets named zones receives a zone response instead of the full page render.
See :doc:`/content/topics/partial-rendering/how-it-works` for the zone request wire format and the patch envelope.

Layout chain
~~~~~~~~~~~~

The framework collects every ancestor ``layout.djx`` walking upward from the page directory through every ancestor, bounded at 64 levels.
Each layout substitutes the wrapped content into its ``{% block template %}`` placeholder.
The innermost layout wraps the page body, the outermost layout wraps everything.

Static collector
~~~~~~~~~~~~~~~~

The collector accumulates assets touched during the render.
Components contribute when they render through ``{% component %}``.
The collector finalises before the template tags emit their slot.

Tag injection
~~~~~~~~~~~~~

``{% collect_styles %}`` and ``{% collect_scripts %}`` emit placeholder tokens during template rendering.
After the layout chain finishes, the static manager replaces every placeholder token with the rendered tags accumulated by the request-scoped ``StaticCollector``.
The framework injects the ``Next`` JS context script before any other script in the page.

Form submission path
--------------------

A form submission enters at ``/_next/form/<str:uid>/``.
The dispatcher resolves the UID to the registered handler and form class.
On valid form the handler runs and returns a response that goes back to the browser.
On invalid form the dispatcher loads the origin page and re-renders it through the same pipeline used for a fresh page request, with the bound form in the template scope.

The dependency cache is reused across the failure path so context functions and providers run at most once per request.

Extension points
----------------

- Add an entry to ``MIDDLEWARE`` to intercept the request before next.dj sees it.
- Subscribe to ``page_rendered`` to observe render duration, asset counts, and the context keys of each response.
- Subclass ``StaticBackend`` to change how the collector renders.
- Subclass ``RouterBackend`` to feed the resolver from a different source.

See also
--------

.. seealso::

   :doc:`page-discovery` for how the page is resolved.
   :doc:`url-router` for the URL dispatcher.
   :doc:`action-dispatch` for the form submission path.
   :doc:`/content/topics/file-router` for the URL semantics.
