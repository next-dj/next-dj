.. _internals-overview:

Internals overview
==================

next.dj is built from the subsystems mapped below, which share one settings layer, one dependency resolver, and one signal bus.
This page maps them and shows how signals flow between them.

.. note::

   If you want to know how to extend the framework rather than how it works inside, read :doc:`/content/topics/extending` first.
   That page covers the five extension mechanisms and the decision tree for choosing between them.
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
     - Django ``AppConfig`` that wires autoreload, template-tag builtins, staticfiles integration, component bootstrap, form autodiscovery, and the partial shaper port during ``ready()``.
     - ``next.apps``

Bootstrap
---------

Django calls ``NextFrameworkConfig.ready()`` once per process after all applications load.
The hook calls ``register_all()`` to register the framework system checks.
It then runs six startup steps in a fixed order.
The first four install autoreload, template-tag builtins, staticfiles integration, and component bootstrap into the Django runtime.
The fifth, ``autodiscover_forms()``, registers shared forms before the first request arrives.
The sixth binds the partial shaper into the ``next.ports`` slot, which is the one composition step the request path depends on.
See :doc:`/content/ref/apps` for the canonical ordering and the full API.

How they compose
----------------

A request passes from ``next.urls`` through ``next.pages`` and ``next.deps`` to ``next.static`` and ``next.components`` before the final HTML returns to the client.
Form submissions take a parallel path through ``next.forms``, which on validation failure reuses the same render pipeline.
Partial requests take a zone-patch path through ``next.partial``, which renders the targeted zones through the same render pipeline and returns patches instead of a full page.
:doc:`request-lifecycle` traces the render and form paths end to end.
:doc:`/content/topics/partial-rendering/how-it-works` traces the zone-patch path.

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
       Deps["next.deps"]
       Server["next.server"]
       Conf["next.conf"]
       Audit["Audit and metrics"]
       Cache["Cache invalidation"]
       Watch["Long lived listeners"]

       Pages -- "template_loaded, context_registered, page_rendered" --> Audit
       Components -- "component_registered, components_registered, component_rendered, component_backend_loaded" --> Audit
       URLs -- "route_registered, router_reloaded" --> Watch
       Forms -- "action_registered, action_dispatched, form_validation_failed, form_access_denied, wizard_step_submitted, wizard_completed" --> Audit
       Forms -- "action_dispatched" --> Cache
       Static -- "asset_registered, collector_finalized, html_injected, backend_loaded" --> Audit
       Partial -- "zone_registered, zone_rendered, patch_op_registered, field_validated, sse_stream_opened, sse_stream_closed" --> Audit
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
  Its ``defaults`` and ``settings`` modules import nothing from the framework, and only its ``checks`` module reaches up to ``next.checks``.
- ``next.deps`` imports nothing from the framework and sits at the bottom next to ``next.conf``.
- ``next.ports`` imports no subsystem at all and declares the protocols one subsystem calls another through.
- ``next.pages`` depends on ``next.conf`` and ``next.deps``, and reaches ``next.static`` and ``next.urls`` only through deferred call-site imports.
- ``next.components`` depends on ``next.conf`` and ``next.deps``, and adds a module-level dependency on ``next.pages.watch`` so the watcher and the checks share one page-tree reading.
- ``next.static`` depends on ``next.conf``, ``next.pages``, and ``next.components``, whose trees its discovery and staticfiles finder walk.
- ``next.forms`` depends on ``next.conf``, ``next.pages``, ``next.deps``, ``next.components``, and ``next.static``, the last two through the component-widget binding.
- ``next.pages`` and ``next.forms`` reach partial shaping through the ``next.ports`` slot rather than through ``next.partial``, so neither imports the partial subsystem on the request path.
- ``next.urls`` depends on ``next.conf``, ``next.deps``, ``next.pages``, ``next.components``, and ``next.forms``.
- ``next.partial`` depends on ``next.conf``, ``next.pages``, ``next.static``, and ``next.forms`` to render zones and shape patches, and reads ``next.templatetags.forms`` in its system checks.
  It touches ``next.urls`` only in those checks, under ``TYPE_CHECKING``.
- ``next.server`` depends on ``next.conf``, ``next.pages``, ``next.urls``, and ``next.components``, the subsystems whose trees it watches.
- ``next.testing`` depends on the page, component, form, dependency, static, and partial subsystems to drive isolation and rendering helpers.
- ``next.apps`` depends on every subsystem.
  It is the Django-facing entry point that calls each subsystem's startup hook.

Module map
----------

Each subsystem keeps a shallow module layout, and a submodule becomes a package of its own only when one concern splits across several bodies, as the form dispatch pipeline does.

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Subsystem
     - Submodules
   * - ``next.pages``
     - ``manager``, ``registry``, ``loaders``, ``context``, ``processors``, ``scan``, ``checks``, ``signals``, ``watch``.
   * - ``next.components``
     - ``manager``, ``registry``, ``scanner``, ``loading``, ``renderers``, ``context``, ``facade``, ``info``, ``backends``, ``watch``, ``checks``, ``signals``.
   * - ``next.urls``
     - ``manager``, ``backends``, ``dispatcher``, ``parser``, ``resolver``, ``markers``, ``reverse``, ``checks``, ``signals``.
   * - ``next.forms``
     - ``manager``, ``dispatch`` (``build``, ``permissions``, ``responses``, ``wizard``), ``backends``, ``decorators``, ``base``, ``markers``, ``serializers``, ``formsets``, ``uid``, ``rendering``, ``autodiscover``, ``wizard``, ``widgets``, ``origin``, ``diagnostics``, ``checks``, ``signals``.
   * - ``next.static``
     - ``manager``, ``collector``, ``discovery``, ``backends``, ``assets``, ``scripts``, ``serializers``, ``defaults``, ``finders``, ``checks``, ``signals``.
   * - ``next.partial``
     - ``manager``, ``registry``, ``backends``, ``zone``, ``render``, ``envelope``, ``errors``, ``patches``, ``shaping``, ``shaper``, ``sse``, ``view``, ``headers``, ``keys``, ``origin``, ``checks``, ``signals``.
   * - ``next.deps``
     - ``resolver``, ``providers``, ``cache``, ``context``, ``markers``, ``signals``.
   * - ``next.server``
     - ``autoreload``, ``watcher``, ``roots``, ``signals``.
   * - ``next.conf``
     - ``settings``, ``defaults``, ``helpers``, ``imports``, ``checks``, ``signals``.
   * - ``next.testing``
     - ``client``, ``signals``, ``isolation``, ``actions``, ``rendering``, ``loaders``, ``html``, ``patching``, ``deps``.
   * - ``next.apps``
     - ``config``, ``autoreload``, ``templates``, ``staticfiles``, ``components``, ``checks``.
   * - ``next.backends``
     - A single flat module that provides ``load_backends``, ``resolve_backend_class``, and ``SingleBackendManager`` for every settings-driven backend family.
   * - ``next.ports``
     - A single flat module holding the protocols and slots one subsystem calls another through.
       ``PartialShaper`` and ``partial_shaper_slot`` let the page and form paths shape partial responses without importing ``next.partial``.
   * - ``next.utils``
     - A single flat module holding the path helpers, the ``PageRoot`` value object, and the declaration-site attribution that several subsystems share.
   * - ``next.signals``
     - A single flat module that re-exports every signal its owning subpackage declares, for a receiver that subscribes across subsystems.
   * - ``next.checks``
     - ``__init__`` aggregates system-check registration across every subpackage.
       ``common`` provides shared helpers used by individual ``checks`` modules.
   * - ``next.templatetags``
     - ``components``, ``forms``, ``next_static``, ``partial``.

See also
--------

.. seealso::

   :doc:`request-lifecycle` for the end to end request path.
   :doc:`/content/topics/extending` for the user-facing extension mechanisms built on top of this architecture.
   :doc:`/content/topics/signals` for the signal catalog.
   :doc:`/content/ref/index` for the public API.
