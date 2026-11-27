.. _internals-overview:

Internals overview
==================

next.dj is built from the subsystems mapped below, which share one settings layer, one dependency resolver, and one signal bus.
This page maps them and shows how signals flow between them.

.. note::

   If you want to know how to extend the framework rather than how it works inside, read :doc:`/content/topics/extending` first.
   That page covers the six extension mechanisms and the decision tree for choosing between them.
   The pages here explain the implementation.

.. contents::
   :local:
   :depth: 2

Subsystems
----------

.. list-table::
   :header-rows: 1
   :widths: 25 50 25

   * - Subsystem
     - Responsibility
     - Public module
   * - Pages
     - Page modules, layouts, body sources, context, processors.
     - ``next.pages``
   * - Bridge
     - The async runtime that runs coroutines to their result from sync code, and restores the context of deferred work.
     - ``next.bridge``
   * - Components
     - Component discovery, loading, rendering, slots, context.
     - ``next.components``
   * - URLs
     - File router, dispatcher, reverse helpers, hot reload.
     - ``next.urls``
   * - Views
     - The page views the URL router mounts and the per-request route decision.
     - ``next.views``
   * - Forms
     - Form action registry, dispatch, validation, formsets.
     - ``next.forms``
   * - Static
     - Asset discovery, collector, kinds, backends, JS context.
     - ``next.static``
   * - Partial
     - Zones, patches, SSE streams, partial protocol backend.
     - ``next.partial``
   * - Router
     - The server side of the client router, shared layouts kept across navigations, active links, speculation rules.
     - ``next.router``
   * - Defer
     - Deferred boundaries, their tokens and job lanes, and async page code driven from sync views.
     - ``next.defer``
   * - Boundaries
     - The boundary unit every area shares and the inline error boundary that contains a failed subtree behind its error UI.
     - ``next.boundaries``
   * - Transactions
     - The transaction policy of the framework views and the lazy savepoints around rendered subtrees.
     - ``next.transactions``
   * - Compression
     - Response compression with zstd, brotli, and gzip, negotiated per request.
     - ``next.compression``
   * - Realtime
     - Topics, brokers, topic streams, the WebSocket app, tickets and bindings, presence, upstream handlers, the form tunnel.
     - ``next.realtime``
   * - Scripts
     - Third-party scripts, their discovery, and head injection.
     - ``next.scripts``
   * - Consent
     - Visitor consent backend and categories.
     - ``next.consent``
   * - Site
     - Site origin and indexability.
     - ``next.site``
   * - SEO
     - Sitemap and robots discovery, the items registry, the sitemap and robots views.
     - ``next.seo``
   * - Dependencies
     - Parameter resolver, providers, request cache.
     - ``next.deps``
   * - Permissions
     - Object permission predicates, the access rules a page or component declares, the ``has_perm`` template tag.
     - ``next.permissions``
   * - Server
     - Autoreload watcher, watch specs, the development ASGI server, its checks, signals.
     - ``next.server``
   * - Config
     - Settings access, defaults, helpers.
     - ``next.conf``
   * - Testing
     - Test client, signal recorder, isolation.
     - ``next.testing``
   * - App
     - Django ``AppConfig`` that wires autoreload, template-tag builtins, staticfiles integration, app module autodiscovery of ``deps``, ``permissions``, ``forms``, ``realtime``, and ``signals``, component bootstrap, and every port slot during ``ready()``.
     - ``next.apps``

Bootstrap
---------

Django calls ``NextFrameworkConfig.ready()`` once per process after all applications load.
Every area declares what it wires at startup in a ``Manifest`` of its ``manifest`` module, and ``next.apps.areas`` lists the manifests in one explicit order that matters.
A signal reaches the receivers of an earlier area first, and the lifecycle participants of the page view open their blocks in that order.
The framework system checks are registered by then, since ``next.apps.areas`` imports every area's manifest and each manifest names its checks module.

``ready()`` runs two passes over the manifests and then the installers.

1. The receiver pass connects every signal receiver of every manifest, the ordered tables for ``setting_changed``, ``settings_reloaded``, and ``routes_reloaded`` and the explicit connections, each with a ``dispatch_uid`` of its module and qualified name, so no module connects a receiver at import time.
2. The composition pass walks the manifests again and for each one binds its ``next.ports`` slots, runs its startup steps, registers its page declarations, its lifecycle participants, and its page script providers.
   The dependency resolver setting applies in the startup step of ``deps``, ahead of every area that imports user modules.
3. The installers install autoreload, template-tag builtins, and staticfiles integration into the Django runtime, then ``autodiscover.install()`` imports the ``deps``, ``permissions``, ``forms``, ``realtime``, and ``signals`` modules of every installed application, in that order, so providers, object permissions, shared forms, upstream handlers, and receivers register before the first request arrives.
   The last, ``components.install()``, bootstraps the components into the Django runtime, after the providers a ``component.py`` may need are in place.

Adding a feature touches its own manifest and one line of ``next.apps.areas``, and no central table.
See :doc:`/content/ref/apps` for the canonical ordering and the full API, and :doc:`adding-an-area` for the contract of a new area.

The boot pays for registration and wiring.
Component discovery runs there as well, and unless ``LAZY_COMPONENT_MODULES`` defers them it also imports every discovered ``component.py`` so the decorators run before the first request.
Nothing walks a route tree and nothing compiles a template at this point.

The first request pays for what the boot left lazy.
The router builds its patterns from the page tree on the first resolve, the resolver instantiates its providers on the first callable it fills, and a page module enters the mtime-keyed memo the first time something loads it.

A warm request pays neither of those.
It reads the structures those two stages left behind and compares the version counters that guard them, and :doc:`request-lifecycle` is the canonical account of what survives a response and what every request still computes.

.. _internals-process-state:

Process state
-------------

Every memo of the framework is built on a primitive of ``next.caches`` and names the sources its value follows.
``SETTINGS`` moves on any Django setting change and on every framework settings reload, ``FRAMEWORK`` on a framework reload only, ``ROUTES`` when the page routers reload, and ``FORK`` in a forked child.
A ``BoundedCache``, ``PageCache`` or ``LruCache`` takes ``depends=``, ``memo(*sources)`` wraps a function in a ``functools`` cache the sources clear, ``Memo`` holds one value, and ``Source.attach`` runs a reset of a manager or a registry on every move.
A move runs every reset attached to the source in the order each attached, so one move per source replaces a receiver written for each memo, and a memo that compares on read, like a page plan, reads ``Source.version`` instead.
``pure_cache`` marks a function whose result follows only from its arguments and the code, ``IdentityMemo`` holds a value while the object it was built from stays the same, and ``instance_dict`` reads the instance dict of a lazy holder or of ``LazySettings``, which the owner's own resets keep current.
A cache whose entries carry the mtime stamp they were read under declares ``depends=()``, since the stamp revalidates each entry on read.

``tests/test_state.py`` scans ``next/`` with ``ast`` and fails on a bare ``functools`` cache, a cache without ``depends=``, a module-level container or private holder the module writes without a reset attached to a source, a rebound global and a bare ``vars()`` view, naming the file, the line and the primitive to use.
Its list of known exceptions only shrinks.
The leak guard of the test suite names a test that leaves a framework receiver of ``setting_changed``, ``settings_reloaded`` or ``routes_reloaded`` disconnected, since every later memo would then outlive a settings change.

How they compose
----------------

A request passes from ``next.urls`` through ``next.pages`` and ``next.deps`` to ``next.static`` and ``next.components`` before the final HTML returns to the client.
Form submissions take a parallel path through ``next.forms``, which on validation failure reuses the same render pipeline.
Partial requests take a zone-patch path through ``next.partial``, which renders the targeted zones through the same render pipeline and returns patches instead of a full page.
:doc:`request-lifecycle` traces the render and form paths end to end.
:doc:`boundary-pipeline` traces deferred boundaries and their streams, :doc:`router-pipeline` the client router navigation, and :doc:`client-runtime` the browser runtime they run in.
:doc:`partial-pipeline` traces the zone-patch path, and :doc:`/content/topics/partial-rendering/how-it-works` states it the way a user meets it.

Signals fan out
---------------

Most cross subsystem coordination happens through signals.
The diagram below sketches which subsystem emits each signal and the typical receivers.

.. mermaid::

   flowchart LR
       Pages["next.pages"]
       Components["next.components"]
       URLs["next.urls"]
       Forms["next.forms"]
       Static["next.static"]
       Partial["next.partial"]
       Realtime["next.realtime"]
       Seo["next.seo"]
       Scripts["next.scripts"]
       Consent["next.consent"]
       Deps["next.deps"]
       Boundaries["next.boundaries"]
       Defer["next.defer"]
       Router["next.router"]
       Compression["next.compression"]
       Server["next.server"]
       Conf["next.conf"]
       Audit["Audit and metrics"]
       Cache["Cache invalidation"]
       Watch["Long lived listeners"]

       Pages -- "template_loaded, context_registered, metadata_registered, page_rendered" --> Audit
       Seo -- "sitemap_backend_loaded" --> Audit
       Components -- "component_registered, components_registered, component_rendered, component_backend_loaded" --> Audit
       URLs -- "route_registered, intercept_registered, routes_reloaded, page_backend_loaded" --> Watch
       Forms -- "action_registered, action_dispatched, form_validation_failed, form_access_denied, wizard_step_submitted, wizard_completed, form_backend_loaded, wizard_backend_loaded" --> Audit
       Forms -- "action_dispatched" --> Cache
       Static -- "asset_registered, collector_finalized, html_injected, static_backend_loaded" --> Audit
       Partial -- "zone_registered, zone_rendered, patch_op_registered, intercept_access_denied, field_validated, sse_stream_opened, sse_stream_closed, partial_backend_loaded" --> Audit
       Realtime -- "broker_backend_loaded, message_published, stream_subscribed, stream_unsubscribed, upstream_received, upstream_rejected" --> Audit
       Scripts -- "scripts_registered" --> Audit
       Consent -- "consent_backend_loaded" --> Audit
       Deps -- "provider_registered" --> Audit
       Boundaries -- "boundary_failed" --> Audit
       Defer -- "boundary_settled, boundary_state_written, boundary_job_started, boundary_job_finished, boundary_token_rejected, page_stream_opened, page_stream_closed, page_stream_declined" --> Audit
       Router -- "navigation_rendered, prefetch_shed, router_cleared" --> Audit
       Compression -- "compression_backend_loaded" --> Audit
       Server -- "watch_specs_ready" --> Watch
       Conf -- "settings_reloaded" --> Watch
       Conf -- "settings_reloaded" --> Cache

.. note::

   The diagram is a coordination sketch.
   :doc:`/content/topics/signals` explains how receivers use them, and :doc:`/content/ref/signals` is the canonical catalog of signal names, senders, and payloads.
   ``next.transactions`` sends no signal of its own.

.. _internals-subsystem-dependencies:

Subsystem dependencies
----------------------

The areas of ``next/`` stack in layers, and an area imports at runtime only from its own layer or the layers below it.
``tests/test_layers.py`` reads the import graph of the package with ``ast`` and holds the layers and the contracts below.
Its list of known exceptions is empty, so a new import that breaks a contract fails the test, and the fix moves the shared code down a layer or opens a slot of ``next.ports``.

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Layer
     - Areas
   * - foundation
     - The flat cross-area modules (``next.backends``, ``next.caches``, ``next.diagnostics``, ``next.discovery``, ``next.errors``, ``next.identity``, ``next.intercept``, ``next.interpolation``, ``next.introspect``, ``next.lifecycle``, ``next.manifest``, ``next.marks``, ``next.paths``, ``next.ports``, ``next.requests``, ``next.seeding``, ``next.stamps``, ``next.utils``, ``next.wire``), plus ``next.conf``, ``next.site``, and the check tags of ``next.checks``.
   * - kernel
     - ``next.bridge``, ``next.permissions``, ``next.csrf``, ``next.transactions``, and ``next.compression`` at the bottom, then ``next.deps``, ``next.boundaries``, ``next.components``, ``next.pages``, ``next.static``, ``next.forms``, and ``next.urls`` in that order.
   * - protocol
     - ``next.partial``, the transport the router, realtime, and defer features stand on.
   * - features
     - ``next.defer``, ``next.router``, ``next.realtime``, ``next.seo``, ``next.scripts``, and ``next.consent``.
   * - composition
     - ``next.views``, ``next.templatetags``, ``next.middleware``, ``next.server``, ``next.management``, ``next.signals``, ``next.apps``, and the lazy façade of ``next`` itself.
   * - testing
     - ``next.testing``, which may import every other layer.

The test enforces these contracts.

- A lower layer never imports a higher one at runtime.
  An import under ``TYPE_CHECKING`` may point upward, which is how a signature names a type of a higher area without a cycle.
- Inside the kernel each area imports only the areas listed before it, so the kernel has no import cycle.
- A kernel area never imports a protocol or feature area, at runtime or under ``TYPE_CHECKING``.
  A feature reaches down into the kernel through the boundary unit of ``next.boundaries``, a context key, or a slot of ``next.ports`` that ``AppConfig.ready()`` binds, and each slot keeps a fallback for a site that runs without the feature.
- Features stay independent of each other.
  ``next.scripts`` depends on ``next.consent`` by design, and the test names that pair explicitly.
- A ``checks`` module may inspect its neighbours more widely than the rest of its area, so the layer contracts skip it.
  It may import any other ``checks`` module and any code up to the feature layer, and never composition or testing code unless its own area sits there.
- Every import statement runs at module level, so the graph the test reads is the graph the interpreter builds.

``next.permissions`` imports no model and keeps off Django's ``BaseBackend``, whose module imports the auth models, so ``AUTHENTICATION_BACKENDS`` can name its backend before the app registry is ready.
``tests/test_imports.py`` covers the other half of the import story, importing every module first in a fresh interpreter, so the order Django loads apps in never breaks a partially initialised package.

Module map
----------

Each subsystem keeps a shallow module layout, and a submodule becomes a package of its own only when one concern splits across several bodies, as the form dispatch pipeline does.
The set of submodules differs by area, and :doc:`adding-an-area` states the contract a new area follows.

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Subsystem
     - Submodules
   * - ``next.pages``
     - ``manager`` (``bodies``, ``containment``, ``declaring``, ``gating``, ``patterns``, ``registrations``, ``render``, ``templates``), ``registry``, ``declarations``, ``manifest``, ``access``, ``loaders``, ``layouts``, ``context``, ``processors``, ``scan``, ``paths``, ``placeholder``, ``visits``, ``graph`` (``blame``, ``compile``, ``merge``, ``run``, ``variables``), ``gating``, ``guards``, ``defer``, ``boundaries``, ``jobs``, ``skeletons``, ``transactions``, ``compression``, ``metadata`` (``backends``, ``chain``, ``dicts``, ``fold``, ``hreflang``, ``ld``, ``markers``, ``nodes``, ``normalize``, ``registry``, ``resolve``, ``scope``, ``shapes``, ``titles``), ``responses``, ``nodes``, ``ports``, ``errors``, ``checks`` (``access``, ``branches``, ``composed``, ``contexts``, ``gating``, ``groups``, ``intercepts``, ``layouts``, ``loaders``, ``metadata``, ``modules``, ``processors``, ``reads``, ``responses``, ``structure``, ``walk``, ``zones``), ``signals``.
   * - ``next.components``
     - ``manager``, ``registry``, ``manifest``, ``scanner``, ``sources``, ``modules``, ``renderers``, ``context``, ``facade``, ``info``, ``access``, ``boundaries``, ``calls``, ``defer``, ``placement``, ``parsing``, ``paths``, ``trip``, ``reads``, ``backends``, ``errors``, ``nodes``, ``tags``, ``watch``, ``checks``, ``signals``.
   * - ``next.bridge``
     - ``driver``, ``loop``, ``request``, ``context``, ``deadline``, ``tasks``, ``hops``, ``manifest``, ``checks``.
       The async runtime of the kernel, which runs ``async def`` code to its result from a sync caller, serves its thread-sensitive calls on the calling thread, and restores the context snapshots of deferred work, see :doc:`/content/ref/bridge`.
       ``hops`` is the only module that reads or writes the private surface of asgiref, and its contract tests in ``tests/bridge/test_hops.py`` pin the releases the runtime is known to work on.
   * - ``next.urls``
     - ``manager``, ``manifest``, ``ports``, ``backends``, ``dispatcher``, ``parser``, ``resolver``, ``markers``, ``mount``, ``routes``, ``reverse``, ``urlconf``, ``errors``, ``checks``, ``signals``.
   * - ``next.forms``
     - ``manager``, ``manifest``, ``dispatch`` (``build``, ``permissions``, ``responses``, ``wizard``), ``backends``, ``decorators``, ``base``, ``markers``, ``nodes``, ``serializers``, ``formsets``, ``uid``, ``rendering``, ``autodiscover``, ``wizard``, ``widgets``, ``origin``, ``registration``, ``transport``, ``ports``, ``errors``, ``checks`` (``actions``, ``config``, ``csrf``, ``sources``, ``widgets``, ``wizards``), ``signals``.
   * - ``next.static``
     - ``manager``, ``manifest``, ``collector``, ``discovery``, ``backends``, ``assets``, ``runtime``, ``protocol``, ``nonce``, ``inject``, ``serializers``, ``defaults``, ``finders``, ``ports``, ``errors``, ``checks``, ``signals``.
   * - ``next.partial``
     - ``manager``, ``manifest``, ``lifecycle``, ``registry`` (``ops``, ``zones``), ``backends``, ``zone``, ``render``, ``envelope``, ``errors``, ``patches``, ``guards``, ``shaping`` (``csrf``, ``outcomes``, ``responses``, ``scrub``, ``shaper``, ``targets``, ``validate``), ``more``, ``paging``, ``layers``, ``release``, ``sse``, ``stream``, ``intercept``, ``view``, ``headers``, ``keys``, ``origin``, ``checks`` (``backends``, ``codes``, ``forms``, ``intercepts``, ``more``, ``nodes``, ``ops``, ``templates``, ``zones``), ``signals``.
       :doc:`partial-pipeline` walks what each one does on a zone request.
   * - ``next.views``
     - ``pages``, ``plan``, ``kinds``, ``fixed``, ``containers``, ``manifest``.
       The page views the URL router mounts and the per-request decision of the response a page URL answers with.
   * - ``next.router``
     - ``manager``, ``manifest``, ``lifecycle``, ``backends``, ``declarations``, ``markers``, ``navigation``, ``digest``, ``links``, ``render``, ``speculation``, ``checks`` (``layouts``, ``pages``, ``paths``, ``prefixes``, ``scripts``, ``settings``), ``signals``.
       The server side of the client router, from the declarations of a ``page.py`` to the navigation envelope, the active-link pass, and the speculation rules.
   * - ``next.defer``
     - ``manager``, ``manifest``, ``lifecycle``, ``policy``, ``probe``, ``state``, ``ledger``, ``components``, ``render``, ``payload``, ``reload``, ``trip``, ``admission``, ``markers``, ``assembly`` (``delivery``, ``inline``, ``jobs``, ``outcomes``, ``pages``, ``partials``), ``coordinator`` (``machine``, ``runner``, ``drivers``), ``response`` (``chunks``, ``document``, ``lifetime``, ``markup``, ``page``, ``partial``, ``pump``, ``writer``), ``executors`` (``pool``, ``hygiene``), ``connections`` (``backends``, ``detection``, ``facts``, ``guard``, ``settle``, ``snapshot``), ``tokens`` (``backends``, ``binding``, ``carrier``, ``claims``, ``clock``, ``codec``, ``settings``, ``props``, ``seal``, ``storage``), ``errors``, ``checks`` (``backends``, ``calls``, ``connections``, ``declarations``, ``jobs``, ``settings``, ``streams``, ``tokens``), ``signals``.
       Deferred boundaries from the queued job to the settled wrapper, the signed tokens a client-trip boundary carries, and the pool lane the jobs run on.
   * - ``next.compression``
     - ``manager``, ``manifest``, ``backends``, ``negotiation``, ``streams``, ``decorators``, ``errors``, ``checks``, ``signals``.
   * - ``next.boundaries``
     - ``options``, ``failures``, ``calls``, ``ids``, ``errors``, ``markup``, ``scope``, ``protection``, ``reporting``, ``containment``, ``stash``, ``manifest``, ``signals``.
       The boundary unit every area shares, its options, error kinds, deferred calls and wire ids, and the inline error boundary, which contains a failed subtree behind its error UI, see :doc:`/content/ref/boundaries`.
   * - ``next.transactions``
     - ``policy``, ``statements``, ``savepoints``, ``lifecycle``, ``manifest``, ``checks``.
       The transaction policy of the framework views and the lazy savepoints around rendered subtrees.
   * - ``next.realtime``
     - ``manager``, ``manifest``, ``backends``, ``topics``, ``messages``, ``delivery``, ``replay``, ``roster``, ``attendance``, ``lifecycle``, ``limits``, ``pool``, ``transports``, ``negotiation``, ``offer``, ``tickets``, ``binding``, ``access``, ``asgi``, ``socket``, ``frames``, ``handlers``, ``tunnel``, ``views``, ``urls``, ``console``, ``deployment``, ``redis`` (``broker``, ``pubsub``, ``scripts``), ``errors``, ``checks``, ``signals``.
   * - ``next.seo``
     - ``manager``, ``manifest``, ``backends``, ``discovery``, ``decorators``, ``registry``, ``sitemaps``, ``pagination``, ``robots``, ``origin``, ``routes``, ``views``, ``urls``, ``markers``, ``ports``, ``errors``, ``checks`` (``backends``, ``robots``, ``roots``, ``routes``, ``sitemaps``, ``sources``), ``signals``.
       :doc:`seo-pipeline` walks the path from a ``page.py`` to the head and from a ``sitemap.py`` to the served document.
   * - ``next.site``
     - ``config``, ``headers``, ``middleware``, ``manifest``, ``errors``, ``checks``.
       The site origin and indexability every head tag, response, and crawler document reads.
   * - ``next.scripts``
     - ``markers``, ``discovery``, ``registry``, ``manager``, ``manifest``, ``render``, ``nodes``, ``ports``, ``errors``, ``checks``, ``signals``.
   * - ``next.consent``
     - ``markers``, ``backends``, ``manager``, ``manifest``, ``providers``, ``checks``, ``signals``.
   * - ``next.csrf``
     - A single flat module deciding where a page puts its CSRF token and serving ``/_next/csrf/``, read by the static, form, partial, and page areas.
   * - ``next.deps``
     - ``resolver``, ``linear``, ``plan``, ``runner``, ``providers``, ``registry``, ``cache``, ``scope``, ``context``, ``markers``, ``objects``, ``loader``, ``introspect``, ``manifest``, ``errors``, ``checks``, ``signals``.
   * - ``next.permissions``
     - ``access``, ``backends``, ``checks``, ``errors``, ``manager``, ``manifest``, ``markers``, ``nodes``, ``registry``.
   * - ``next.management``
     - ``endpoints``, ``table``, ``commands`` (``permissions``, ``realtime``, ``routes``, ``runserver``, ``showmetadata``).
   * - ``next.middleware``
     - A single flat module holding the middleware that makes a response setting a cookie private for every shared cache and ``CompressionMiddleware``, which compresses every response no stream compressed itself.
   * - ``next.server``
     - ``autoreload``, ``watcher``, ``roots``, ``asgi``, ``manifest``, ``checks``, ``signals``.
   * - ``next.conf``
     - ``settings``, ``defaults``, ``loader``, ``scopes``, ``merge``, ``frozen``, ``sentinels``, ``helpers``, ``imports``, ``shapes``, ``router``, ``defer``, ``boundaries``, ``compression``, ``transactions``, ``manifest``, ``checks``, ``signals``.
   * - ``next.testing``
     - ``client``, ``capture``, ``isolation``, ``actions``, ``rendering``, ``loaders``, ``html``, ``patching``, ``deps``, ``metadata``, ``seo``, ``realtime``, ``responses``, ``streaming``, ``boundaries``, ``tokens``, ``plugin``.
       ``plugin`` is the only module in the area that imports pytest, and a suite loads it with ``-p next.testing.plugin``.
   * - ``next.apps``
     - ``config``, ``areas``, ``autoreload``, ``autodiscover``, ``templates``, ``staticfiles``, ``components``, ``checks``.
   * - ``next.backends``
     - A single flat module that provides ``load_backends``, ``backend_entries``, ``resolve_backend_class``, ``resolve_setting_class``, ``BackendListManager``, ``SingleBackendManager``, and ``BackendRoot`` for every settings-driven backend family.
   * - ``next.ports``
     - A single flat module holding the protocols and slots one subsystem calls another through, each bound in ``AppConfig.ready`` to the implementation its owning area keeps, see :doc:`/content/ref/ports`.
       ``ActionOwners`` lets the body-source check read the directories that declare actions without importing ``next.forms``, ``FormRoutes`` lets the URL checks read the form action routes without importing ``next.forms``, ``RouteAccess`` lets the page watcher and the checks build routers without importing ``next.urls``, ``StaticAssets`` lets the render path reach the static manager without importing ``next.static``, ``PageScan`` lets the checks execute the routed ``page.py`` modules without closing the loop back into ``next.pages.scan``, ``SeoRoutes`` lets the lazy urlpatterns append the SEO routes without importing ``next.seo``, ``page_scripts`` lets the release, the realtime offer, the stream flag, the client router and the third-party scripts add to a page head and init payload without the static injector importing any of them, ``DeferralSink`` lets the template tags and the render paths queue a deferred component call without importing ``next.defer``, and ``RouterStamp`` and ``ZoneMarking`` let a form or action response carry the client router directives and the active-link marks without importing ``next.router``.
   * - ``next.utils``
     - A single flat module holding the path helpers, the ``PageRoot`` value object, the directory-name grammars and the page-tree walk, the ``template_edits_watched`` predicate, the ``TreeSource`` loader of a file at the top of a page tree, ``redirect_or_fallback``, and the lexical HTML scan, ``open_elements`` for the elements a markup prefix leaves open and ``misrooted`` for a body without a single root, with the void and raw-text element sets the router's link pass and the boundary wrappers share, and ``parse_duration``, the one reader of a duration setting or option.
   * - ``next.identity``
     - A single flat module holding the subject a request proves, which realtime tickets and boundary tokens share.
   * - ``next.requests``
     - A single flat module holding ``RequestKind``, the kinds of request a view routes by and the transaction rule receives, spelled once for every layer, and ``PrefetchDepth``, how much of a page a router prefetch asks for.
   * - ``next.intercept``
     - A single flat module holding the table of intercepting routes, which page serves a zone over a routed target.
   * - ``next.interpolation``
     - A single flat module that interpolates ``{{ expr }}`` inside the string a template tag argument holds.
   * - ``next.paths``
     - A single flat module holding the path rule of active links, the normal path, the page and ancestor marks, and the home paths that are exact only, which the router's link pass and the breadcrumb trail share.
   * - ``next.stamps``
     - A single flat module holding the freshness stamps of files, which never trust an mtime written in the current clock tick.
   * - ``next.caches``
     - A single flat module holding the sources of process state and the memo primitives built on them, ``BoundedCache``, ``PageCache``, ``LruCache``, ``Memo``, ``memo``, ``pure_cache``, and ``IdentityMemo``, so every memo carries its own bound and names what drops it, see :ref:`internals-process-state`.
   * - ``next.introspect``
     - A single flat module naming a callable and the file it was declared in, which every area that registers a decorated object reads.
   * - ``next.seeding``
     - A single flat module holding the render-context keys every area shares and the ``RenderFrame`` a component render inherits from the page around it.
   * - ``next.diagnostics``
     - A single flat module holding ``FailureLog``, the one boundary around user and third-party code that raises, see :ref:`topics-failure-containment`.
   * - ``next.errors``
     - A single flat module holding the exceptions more than one subsystem raises, the ``DIRS`` shape refusal and the six a backend the loader cannot resolve produces.
   * - ``next.signals``
     - A single flat module that re-exports every signal its owning subpackage declares, for a receiver that subscribes across subsystems.
   * - ``next.discovery``
     - A single flat module holding the per-run route manager, the walk of the page trees it routes, and the page roots and layout files the autoreloader watches, read by the system checks, the component sources, the static finders, and the page scan alike.
   * - ``next.checks``
     - ``__init__`` holds the shared ``NEXT`` and ``SEO`` tags and imports nothing of the framework, and ``common`` provides shared helpers used by individual ``checks`` modules.
       Each area names its own ``checks`` module in its manifest, and ``next.apps.areas`` collects them.
   * - ``next.lifecycle``
     - A single flat module holding the participants of a page request, their points, and the registry the page view reads, see :ref:`internals-request-lifecycle-participants`.
   * - ``next.manifest``
     - A single flat module holding ``Manifest`` and ``Bind``, what one area wires at startup, which ``next.apps.areas`` lists in the order ``ready`` applies.
   * - ``next.marks``
     - A single flat module holding the active-link pass a rendered fragment runs and the container node that marks its own links, see :doc:`router-pipeline`.
   * - ``next.wire``
     - A single flat module holding the wire names the areas below the partial protocol spell, which ``next.partial.keys`` and ``next.partial.headers`` re-export.
   * - ``next.templatetags``
     - ``boundaries``, ``components``, ``forms``, ``next_static``, ``pages``, ``partial``, ``permissions``, ``scripts``.
       Each module registers its tags and parses their syntax, and the nodes the component and page boundary tags compile to live with their owners in ``next.components.tags`` and ``next.pages.nodes``.

See also
--------

.. seealso::

   :doc:`request-lifecycle` for the end to end request path.
   :doc:`/content/topics/extending` for the user-facing extension mechanisms built on top of this architecture.
   :doc:`/content/topics/signals` for the signal catalog.
   :doc:`/content/ref/index` for the public API.
