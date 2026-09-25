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
   * - Components
     - Component discovery, loading, rendering, slots, context.
     - ``next.components``
   * - URLs
     - File router, dispatcher, reverse helpers, hot reload.
     - ``next.urls``
   * - Forms
     - Form action registry, dispatch, validation, formsets.
     - ``next.forms``
   * - Static
     - Asset discovery, collector, kinds, backends, JS context.
     - ``next.static``
   * - Partial
     - Zones, patches, SSE streams, partial protocol backend.
     - ``next.partial``
   * - SEO
     - Sitemap and robots discovery, the items registry, the sitemap and robots views.
     - ``next.seo``
   * - Dependencies
     - Parameter resolver, providers, request cache.
     - ``next.deps``
   * - Server
     - Autoreload watcher, watch specs, signals.
     - ``next.server``
   * - Config
     - Settings access, defaults, helpers.
     - ``next.conf``
   * - Testing
     - Test client, signal recorder, isolation.
     - ``next.testing``
   * - App
     - Django ``AppConfig`` that wires autoreload, template-tag builtins, staticfiles integration, component bootstrap, form autodiscovery, and the five ports during ``ready()``.
     - ``next.apps``

Bootstrap
---------

Django calls ``NextFrameworkConfig.ready()`` once per process after all applications load.
The hook calls ``register_all()`` to register the framework system checks.
It then runs twelve startup steps in a fixed order.
The first connects five ``router_reloaded`` receivers, ``forget_watch_state``, ``forget_page_roots``, ``forget_manager_page_roots``, ``forget_dep_caches``, and ``seo_manager.reset``, so a router rebuild leaves no watch, page-root, dependency, or SEO-source cache holding a stale generation.
The second, ``apply_resolver_setting()``, points the dependency-injection singleton at the configured resolver class, ahead of every step that imports user modules.
The next five bind the ``next.ports`` slots that the request path, the URL patterns, the watcher, and the checks all read, each one taking the implementation from the ``ports`` module of the area that owns it.
They run early for the same reason the resolver setting does, so no discovery failure leaves a process behind with an unbound port.
The next four install autoreload, template-tag builtins, staticfiles integration, and component bootstrap into the Django runtime.
The last, ``autodiscover_forms()``, registers shared forms before the first request arrives.
See :doc:`/content/ref/apps` for the canonical ordering and the full API.

The boot pays for registration and wiring.
Component discovery runs there as well, and unless ``LAZY_COMPONENT_MODULES`` defers them it also imports every discovered ``component.py`` so the decorators run before the first request.
Nothing walks a route tree and nothing compiles a template at this point.

The first request pays for what the boot left lazy.
The router builds its patterns from the page tree on the first resolve, the resolver instantiates its providers on the first callable it fills, and a page module enters the mtime-keyed memo the first time something loads it.

A warm request pays neither of those.
It reads the structures those two stages left behind and compares the version counters that guard them, and :doc:`request-lifecycle` is the canonical account of what survives a response and what every request still computes.

How they compose
----------------

A request passes from ``next.urls`` through ``next.pages`` and ``next.deps`` to ``next.static`` and ``next.components`` before the final HTML returns to the client.
Form submissions take a parallel path through ``next.forms``, which on validation failure reuses the same render pipeline.
Partial requests take a zone-patch path through ``next.partial``, which renders the targeted zones through the same render pipeline and returns patches instead of a full page.
:doc:`request-lifecycle` traces the render and form paths end to end.
:doc:`partial-pipeline` traces the zone-patch path, and :doc:`/content/topics/partial-rendering/how-it-works` states it the way a user meets it.

Signals fan out
---------------

Most cross subsystem coordination happens through signals.
The diagram below shows which subsystem emits each signal and the typical receivers.

.. mermaid::

   flowchart LR
       Pages["next.pages"]
       Components["next.components"]
       URLs["next.urls"]
       Forms["next.forms"]
       Static["next.static"]
       Partial["next.partial"]
       Seo["next.seo"]
       Deps["next.deps"]
       Server["next.server"]
       Conf["next.conf"]
       Audit["Audit and metrics"]
       Cache["Cache invalidation"]
       Watch["Long lived listeners"]

       Pages -- "template_loaded, context_registered, metadata_registered, page_rendered" --> Audit
       Seo -- "sitemap_items_registered" --> Audit
       Components -- "component_registered, components_registered, component_rendered, component_backend_loaded" --> Audit
       URLs -- "route_registered, router_reloaded, router_backend_loaded" --> Watch
       Forms -- "action_registered, action_dispatched, form_validation_failed, form_access_denied, wizard_step_submitted, wizard_completed, form_backend_loaded, wizard_backend_loaded" --> Audit
       Forms -- "action_dispatched" --> Cache
       Static -- "asset_registered, collector_finalized, html_injected, static_backend_loaded" --> Audit
       Partial -- "zone_registered, zone_rendered, patch_op_registered, field_validated, sse_stream_opened, sse_stream_closed, partial_backend_loaded" --> Audit
       Deps -- "provider_registered" --> Audit
       Server -- "watch_specs_ready" --> Watch
       Conf -- "settings_reloaded" --> Watch
       Conf -- "settings_reloaded" --> Cache

.. note::

   The diagram is a coordination sketch.
   :doc:`/content/topics/signals` is the canonical catalog of signal names, senders, and payloads.

Subsystem dependencies
----------------------

The dependency graph between subsystems is shallow.

- ``next.conf`` sits at the bottom.
  Its ``defaults`` and ``settings`` modules import nothing from the framework, its ``checks`` module reaches up to ``next.checks``, and its ``signals`` module reads a callable name from ``next.introspect``.
- ``next.deps`` sits at the bottom beside ``next.conf``.
  It imports no subsystem, only the flat cross-area modules ``next.backends``, ``next.caches``, ``next.introspect``, and ``next.conf``, which is how the configured resolver class and the reload hook reach it.
- ``next.ports`` imports no subsystem at runtime and declares the protocols one subsystem calls another through.
- ``next.pages`` depends on ``next.conf`` and ``next.deps``, and reaches ``next.static``, ``next.urls``, and ``next.partial`` only through ``next.ports`` slots.
- ``next.components`` depends on ``next.conf`` and ``next.deps``, and adds a module-level dependency on ``next.pages.watch`` so the watcher and the checks share one page-tree reading.
- ``next.static`` depends on ``next.conf``, ``next.pages``, and ``next.components``, whose trees its discovery and staticfiles finder walk.
- ``next.forms`` depends on ``next.conf``, ``next.pages``, ``next.deps``, and ``next.components``, the last through the component-widget binding, and reaches the static collector through the ``StaticAssets`` slot rather than by importing ``next.static``.
- ``next.pages`` and ``next.forms`` reach partial shaping through a ``next.ports`` slot rather than through ``next.partial``, so neither imports the partial subsystem on the request path.
- ``next.urls`` depends on ``next.conf``, ``next.deps``, ``next.pages``, ``next.components``, and ``next.forms``.
- ``next.partial`` depends on ``next.conf``, ``next.pages``, ``next.static``, and ``next.forms`` to render zones and shape patches, and its system checks add ``next.components.sources``.
  It touches ``next.urls`` only in those checks, under ``TYPE_CHECKING``.
  The form nodes those checks walk come from ``next.forms.nodes`` and the attribute names from ``next.partial.keys``, so no area imports a symbol out of the ``next.templatetags`` tag libraries and the shim layer stays a leaf.
- ``next.seo`` depends on ``next.conf``, ``next.deps``, ``next.pages``, and ``next.urls``, the router manager it discovers roots through and the reverse helper its locations go through, and the lazy urlpatterns of ``next.urls`` reach its routes back through the ``SeoRoutes`` slot.
- ``next.server`` depends on ``next.conf``, ``next.pages``, ``next.urls``, and ``next.components``, the subsystems whose trees it watches.
- ``next.testing`` depends on the page, component, form, dependency, static, and partial subsystems to drive isolation and rendering helpers.
- ``next.apps`` depends on every subsystem.
  It is the Django-facing entry point that calls each subsystem's startup hook.

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
     - ``manager`` (``templates``, ``views``), ``registry``, ``loaders``, ``context``, ``processors``, ``scan``, ``paths``, ``placeholder``, ``ports``, ``errors``, ``checks`` (``contexts``, ``layouts``, ``loaders``, ``modules``, ``processors``, ``structure``, ``zones``), ``signals``, ``watch``.
   * - ``next.components``
     - ``manager``, ``registry``, ``scanner``, ``sources``, ``loading``, ``renderers``, ``context``, ``facade``, ``info``, ``backends``, ``watch``, ``checks``, ``signals``.
   * - ``next.urls``
     - ``manager``, ``ports``, ``backends``, ``dispatcher``, ``parser``, ``resolver``, ``markers``, ``reverse``, ``errors``, ``checks``, ``signals``.
   * - ``next.forms``
     - ``manager``, ``dispatch`` (``build``, ``permissions``, ``responses``, ``wizard``), ``backends``, ``decorators``, ``base``, ``markers``, ``nodes``, ``serializers``, ``formsets``, ``uid``, ``rendering``, ``autodiscover``, ``wizard``, ``widgets``, ``origin``, ``registration``, ``errors``, ``checks`` (``actions``, ``config``, ``sources``, ``widgets``, ``wizards``), ``signals``.
   * - ``next.static``
     - ``manager``, ``collector``, ``discovery``, ``backends``, ``assets``, ``scripts``, ``inject``, ``serializers``, ``defaults``, ``finders``, ``ports``, ``errors``, ``checks``, ``signals``.
   * - ``next.partial``
     - ``manager``, ``registry`` (``ops``, ``zones``), ``backends``, ``zone``, ``render``, ``envelope``, ``errors``, ``patches``, ``shaping`` (``outcomes``, ``validate``, ``scrub``, ``targets``, ``csrf``, ``responses``), ``ports``, ``sse``, ``view``, ``headers``, ``keys``, ``origin``, ``checks`` (``backends``, ``codes``, ``forms``, ``nodes``, ``ops``, ``pages``, ``templates``, ``zones``), ``signals``.
       :doc:`partial-pipeline` walks what each one does on a zone request.
   * - ``next.seo``
     - ``manager``, ``discovery``, ``registry``, ``sitemaps``, ``robots``, ``views``, ``urls``, ``markers``, ``ports``, ``errors``, ``checks``, ``signals``.
       :doc:`seo-pipeline` walks the path from a ``sitemap.py`` to the served document.
   * - ``next.deps``
     - ``resolver``, ``linear``, ``plan``, ``providers``, ``registry``, ``cache``, ``context``, ``markers``, ``introspect``, ``errors``, ``signals``.
   * - ``next.server``
     - ``autoreload``, ``watcher``, ``roots``, ``signals``.
   * - ``next.conf``
     - ``settings``, ``defaults``, ``merge``, ``frozen``, ``helpers``, ``imports``, ``checks``, ``signals``.
   * - ``next.testing``
     - ``client``, ``capture``, ``isolation``, ``actions``, ``rendering``, ``loaders``, ``html``, ``patching``, ``deps``, ``plugin``.
       ``plugin`` is the only module in the area that imports pytest, and a suite loads it with ``-p next.testing.plugin``.
   * - ``next.apps``
     - ``config``, ``autoreload``, ``templates``, ``staticfiles``, ``components``, ``checks``.
   * - ``next.backends``
     - A single flat module that provides ``load_backends``, ``backend_entries``, ``resolve_backend_class``, ``resolve_setting_class``, ``BackendListManager``, ``SingleBackendManager``, and ``BackendRoot`` for every settings-driven backend family.
   * - ``next.ports``
     - A single flat module holding the protocols and slots one subsystem calls another through, each bound in ``AppConfig.ready`` to the implementation its owning area keeps in that area's ``ports`` module.
       ``PartialShaper`` lets the page and form paths shape partial responses without importing ``next.partial``, ``RouterAccess`` lets the page watcher and the checks build routers without importing ``next.urls``, ``StaticAssets`` lets the render path reach the static manager without importing ``next.static``, ``PageScan`` lets the checks execute the routed ``page.py`` modules without closing the loop back into ``next.pages.scan``, and ``SeoRoutes`` lets the lazy urlpatterns append the sitemap and robots routes without importing ``next.seo``.
   * - ``next.utils``
     - A single flat module holding the path helpers, the ``PageRoot`` value object, and the ``template_edits_watched`` predicate that several subsystems share.
   * - ``next.caches``
     - A single flat module holding ``BoundedCache`` and ``LruCache``, so every memo in the framework carries its own bound and eviction policy rather than leaving it to the caller.
   * - ``next.introspect``
     - A single flat module naming a callable and the file it was declared in, which every area that registers a decorated object reads.
   * - ``next.seeding``
     - A single flat module holding the render-context keys every area shares and the ``RenderFrame`` a component render inherits from the page around it.
   * - ``next.diagnostics``
     - A single flat module holding the guarded read of what a third-party backend reports, logged once per source.
   * - ``next.errors``
     - A single flat module holding the exceptions more than one subsystem raises, the ``DIRS`` shape refusal and the six a backend the loader cannot resolve produces.
   * - ``next.signals``
     - A single flat module that re-exports every signal its owning subpackage declares, for a receiver that subscribes across subsystems.
   * - ``next.discovery``
     - A single flat module holding the per-run router manager and the walk of the page trees it routes, read by the system checks, the component sources, and the page scan alike.
   * - ``next.checks``
     - ``__init__`` aggregates system-check registration across every subpackage.
       ``common`` provides shared helpers used by individual ``checks`` modules.
   * - ``next.templatetags``
     - ``components``, ``forms``, ``next_static``, ``pages``, ``partial``.

See also
--------

.. seealso::

   :doc:`request-lifecycle` for the end to end request path.
   :doc:`/content/topics/extending` for the user-facing extension mechanisms built on top of this architecture.
   :doc:`/content/topics/signals` for the signal catalog.
   :doc:`/content/ref/index` for the public API.
