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
       ContextCtx --> Metadata["Fold page metadata as the head renders"]
       Metadata --> CollectAssets["Static collector"]
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
The render context also carries a deferred fold of the page metadata, which ``{% metadata %}`` in the root layout resolves as the composed template renders, so a ``@page.metadata`` callable runs after every ``@context`` function and reads the same dependency cache.

Zone requests
~~~~~~~~~~~~~

After the body source resolves, the view inspects the request for a partial intent.
A request that targets named zones receives a zone response instead of the full page render.
A zone body carries no head, so the metadata chain of the page is never folded and a ``@page.metadata`` callable never runs on this path.
See :doc:`/content/topics/partial-rendering/how-it-works` for the zone request wire format and the patch envelope.

Layout chain
~~~~~~~~~~~~

The framework collects every ancestor ``layout.djx`` walking upward from the page directory through every ancestor, bounded at 64 levels.
Each layout substitutes the wrapped content into its ``{% template %}`` placeholder.
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

Both routed views then stamp the partial ``Vary`` set on the finished response, whether or not the project declares a single zone.
The header names ``X-Next-Request``, ``X-Next-Zone``, ``X-Next-Merge``, and ``X-Next-Version``, because a full page and a zone envelope answer the same URL and a shared cache that ignores those headers would serve one where the other belongs.
A project using no partial rendering therefore still ships the four names on every file-routed HTML response, which narrows what a shared cache may reuse across clients that send different values.
The one response that escapes the stamp is the one a ``render`` function returns itself, because that branch leaves the view before the port is reached.
See :doc:`/content/topics/partial-rendering/reference` for the request headers behind that set and what a shared cache does with them.

Form submission path
--------------------

A form submission enters at ``/_next/form/<str:uid>/``.
The dispatcher resolves the UID to the registered handler and form class.
It then resolves the posted origin and asks the page that origin names to authorize the request, so a submission never reaches a handler on behalf of a visitor that page would refuse.
On valid form the handler runs and returns a response that goes back to the browser.
On invalid form the dispatcher loads the origin page and re-renders it through the same pipeline used for a fresh page request, with the bound form in the template scope.

The dependency cache is reused across the failure path so context functions and providers run at most once per request.

.. _internals-request-lifecycle-render-paths:

Render paths and what each one runs
-----------------------------------

Six paths produce HTML from a page, and they do not run the same steps.
The table below is the reference for deciding where an access check belongs.

.. list-table::
   :header-rows: 1
   :widths: 26 12 22 12 28

   * - Path
     - Runs ``render()``
     - Composes the body from ``render()``
     - Runs ``@context``
     - Runs the page's own guard
   * - Full page GET
     - Yes
     - Yes, when it returns a body
     - Yes
     - Yes
   * - Zone GET on the page's own URL
     - Yes
     - No, a dynamic body is refused
     - Yes
     - Yes
   * - Form re-render after an invalid submission
     - Yes
     - No
     - Yes
     - Yes
   * - Form zone morph against the posted origin
     - Yes
     - No
     - Yes
     - Yes
   * - Wizard step morph into the next step
     - Yes
     - No
     - Yes
     - Yes
   * - Out-of-band foreign morph
     - Yes
     - No, a dynamic body is refused
     - Yes
     - Yes

The two GET rows run the routed view, so a ``render`` function runs with its arguments resolved and a response it returns short-circuits everything after it.
A zone GET resolves the body first and only then reads the partial intent, which is why a zone named on a page whose body comes from ``render()`` is answered with a 400 rather than rendered.

The three middle rows never run the routed view of the page they render, so they call ``render()`` for its authorization alone and compose the markup from ``composed_template_for``, the compiled static body plus its layout chain.
Those are two separate facts about one call.
``render()`` runs, and the body it returns is discarded, while a response it returns is not, so a guard written inside ``render()`` holds on every row of the table.

The denial each row sends differs, deliberately.
The form re-render and every framework-driven ``morph(zone=...)`` answer with the origin page's own short-circuit response verbatim, so a ``render()`` returning ``HttpResponseRedirect("/login/")`` makes the POST answer 302 to ``/login/``.
The wizard row authorizes the next step's page before it renders that step's zone, and a denial falls back to the plain step redirect the runtime-free path already sends, carrying the partial ``Vary`` set.
``Patches.morph_zone`` reached outside the dispatch pipeline raises ``ForeignPageNotAuthorizedError``, because it is public API a handler can call with any posted origin behind it.

The last row names the page in the call rather than in the POST.
``Patches.morph(zone=..., page=...)`` resolves the foreign page's body through ``authorization_outcome`` before it renders anything, so the foreign ``render()`` runs once, a redirect or a denial it returns is raised as ``ForeignPageNotAuthorizedError`` instead of morphed, and a foreign page whose body is dynamic is refused.

Every row asks ``render()`` the same question, so it reads the same kind of request on all of them.
A row that does not run the routed view builds that request with ``next.pages.visits.visit_request``, which copies the live one and restates it as a GET of the URL under authorization, the posted origin on a form re-render and an origin zone morph, the next step's URL on a wizard advance, and the named URL on a foreign morph that was given one.
A ``render()`` keyed on identity, on ``request.GET``, on ``request.method``, or on a canonical-URL comparison therefore answers the same on every row as it does on a visit.
The one gap is a foreign morph whose caller named the page by file path, which carries no URL to present and leaves the live path in place, see :doc:`/content/topics/pages`.

The head is rendered on the two rows that compose the whole document, the full page GET and the form re-render, so a ``@page.metadata`` callable runs on those two and on no zone row.

The ``@context`` column hides one difference worth naming.
The form re-render and the origin zone morph build the context with no zone batch, so every page-level callable runs, ``zone=``-tagged ones included.
The zone GET, the wizard step morph, and the foreign morph pass the requested batch, so a callable bound to another zone is skipped before its dependencies resolve.

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
