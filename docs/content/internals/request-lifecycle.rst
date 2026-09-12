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

What is cached between requests
-------------------------------

Every stage above keeps a structure that outlives the response it served, and each of those structures carries the key that invalidates it.

Route index.
   The resolver holds a static route map and a segment trie built from the concatenated router and form-action pattern list, versioned by the counter pair those two managers bump.
   See :doc:`url-router` for the index layout and for what a reload does to it.

Page modules.
   An executed ``page.py`` is memoised by the nanosecond mtime of its file, so the pattern build, the loader probe, and the view share one module object instead of executing the file again.
   See :doc:`page-discovery` for the render path that reads the memo.

Layout compositions.
   A page keeps the composed template string, the compiled ``Template`` built from it, and, for a page whose body comes from ``render()``, the layout skeleton whose body slot is filled per request.
   Each entry is keyed by the page path and carries an mtime snapshot of the sources and the directories the composition read.
   See :doc:`page-discovery` for that snapshot and the staleness check around it.

Dependency introspection.
   The signature and the type hints of a callable are read once per process rather than once per call.

Injection plans.
   The plan the resolver replays for a callable is compiled once and stamped with the providers version it was built against, so only a change to the provider list recompiles it.
   See :doc:`di-resolver` for both memos and the bounds they are held under.

Component lookup.
   The component registry holds an ordered list with a name index and a version counter, the visibility resolver derives its scope index from that counter, and the module cache keeps the imported ``component.py`` modules.

Compiled component templates.
   A component template is parsed once and kept under the files that define the component, revalidated against the mtime of the file the body was read from.
   See :doc:`component-pipeline` for the loader and the visibility resolver.

Asset plans.
   Asset discovery remembers, per page path and per component, what the filesystem walk found, together with the generations of the registries that decide which filenames count as an asset.
   See :doc:`static-pipeline` for the contents of a plan and for what invalidates it.

The caches hold structures and compilations, never a rendered answer.
Every request still runs each ``@context`` function in order, resolves the parameters the compiled plan left as runtime candidates, renders the body and the layout chain against a freshly assembled scope, and fills a ``StaticCollector`` created for that request alone.
The dependency cache lives for a single resolution pass, and the form dispatch path shares one such cache across the stages of a single POST.

An edit to a source file reaches the next request rather than the next restart.
The module memo compares mtimes on every load, the version counters behind the route index, the component registry, and the asset plans are compared on every read, and under ``DEBUG`` the composition, component-template, and asset-plan caches re-stat their sources before a hit counts.

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
