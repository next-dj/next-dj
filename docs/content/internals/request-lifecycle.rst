.. _internals-request-lifecycle:

Request Lifecycle
=================

This page traces an HTTP request from the Django entry point through next.dj to the rendered response.
It covers the regular page request flow plus the parallel path used for form submissions.

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
       RenderFn --> ContextCtx["Run context functions"]
       StaticBody --> ContextCtx
       ContextCtx --> LayoutChain["Compose layout chain"]
       LayoutChain --> CollectAssets["Static collector"]
       CollectAssets --> InjectTags["Emit collected tags"]
       InjectTags --> Response(["HTTP response"])
       FormDispatch --> Validation{"Form valid"}
       Validation -- yes --> Handler["Run handler"]
       Handler --> Response
       Validation -- no --> Loader

Implementation Notes
--------------------

Django Middleware
~~~~~~~~~~~~~~~~~

Django middleware runs first.
Authentication, sessions, CSRF, common middleware, and any project specific middleware all see the request before the framework does.

URL Resolver
~~~~~~~~~~~~

The framework registers its URL patterns through ``include("next.urls")`` in ``config/urls.py``.
The Django URL resolver matches the request path against those patterns.
A file routed match dispatches to the page view.
A match on ``/_next/form/<uid>/`` dispatches to the form dispatcher instead.

Page View
~~~~~~~~~

The page view loads the page module and resolves the body source first.
When the module exposes a ``render`` function the view calls it before context runs, resolving its arguments through the dependency resolver.
``render`` may return a string body or an ``HttpResponseBase`` that short-circuits the layout and static pipelines.
When the body comes from the ``template`` attribute or a ``template.djx`` file the view reads that source as a plain string.
After the body is in hand the view builds the render context and runs every ``@context`` function in order.

Layout Chain
~~~~~~~~~~~~

The framework collects every ancestor ``layout.djx`` walking from the page directory up to the page root.
Each layout substitutes the wrapped content into its ``{% block template %}`` placeholder.
The innermost layout wraps the page body, the outermost layout wraps everything.

Static Collector
~~~~~~~~~~~~~~~~

The collector accumulates assets touched during the render.
Components contribute when they render through ``{% component %}``.
The collector finalises before the template tags emit their slot.

Tag Injection
~~~~~~~~~~~~~

``{% collect_styles %}`` and ``{% collect_scripts %}`` ask the collector for the appropriate slot and emit the HTML.
The framework injects the ``Next`` JS context script before any other script in the page.

Form Submission Path
--------------------

A form submission enters at ``/_next/form/<uid>/``.
The dispatcher resolves the UID to the registered handler and form class.
On valid form the handler runs and returns a response that goes back to the browser.
On invalid form the dispatcher loads the origin page and re-renders it through the same pipeline used for a fresh page request, with the bound form in the template scope.

The dependency cache is reused across the failure path so context functions and providers run at most once per request.

Extension Points
----------------

- Add an entry to ``MIDDLEWARE`` to intercept the request before next.dj sees it.
- Subscribe to ``page_rendered`` to inspect the final HTML.
- Subclass ``StaticBackend`` to change how the collector renders.
- Subclass ``FileRouterBackend`` to feed the resolver from a different source.

See Also
--------

.. seealso::

   :doc:`page-discovery` for how the page is resolved.
   :doc:`url-router` for the URL dispatcher.
   :doc:`action-dispatch` for the form submission path.
   :doc:`/content/topics/file-router` for the URL semantics.
