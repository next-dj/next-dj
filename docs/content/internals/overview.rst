.. _internals-overview:

Internals Overview
==================

next.dj is built from the ten subsystems mapped below, which share one settings layer, one dependency resolver, and one signal bus.
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
     - Django ``AppConfig`` that wires autoreload, template-tag builtins, staticfiles integration, and component bootstrap during ``ready()``.
     - ``next.apps``

Bootstrap
---------

Django calls ``NextFrameworkConfig.ready()`` once per process after all applications load.
``ready()`` first registers every framework :doc:`system check </content/ref/system-checks>` through ``next.checks.register_all``.
It then runs installers in order: ``next.apps.autoreload.install``, ``next.apps.templates.install``, ``next.apps.staticfiles.install``, and ``next.apps.components.install``.
These hooks wire the subsystems above into the Django runtime before the first request arrives.
See :doc:`/content/ref/apps` for the full API.

How They Compose
----------------

A request enters through the Django URL resolver and is routed to the file router under ``next.urls``.
The router resolves the matching page through ``next.pages``, the page invokes context functions through ``next.deps``, the rendered body collects assets through ``next.static`` and components through ``next.components``, and the final HTML returns to the client.

Form submissions follow a parallel path through ``/_next/form/<uid>/`` into ``next.forms``, which validates and dispatches to a registered handler.
On validation failure the dispatcher reuses the page render pipeline above to produce the same page with the bound form in scope.

Signals Fan Out
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
       Deps["next.deps"]
       Server["next.server"]
       Conf["next.conf"]
       Audit["Audit and metrics"]
       Cache["Cache invalidation"]
       Watch["Long lived listeners"]

       Pages -- "template_loaded, context_registered, page_rendered" --> Audit
       Components -- "component_registered, components_registered, component_rendered, component_backend_loaded" --> Audit
       URLs -- "route_registered, router_reloaded" --> Watch
       Forms -- "action_registered, action_dispatched, form_validation_failed" --> Audit
       Forms -- "action_dispatched" --> Cache
       Static -- "asset_registered, collector_finalized, html_injected, backend_loaded" --> Audit
       Deps -- "provider_registered" --> Audit
       Server -- "watch_specs_ready" --> Watch
       Conf -- "settings_reloaded" --> Watch
       Conf -- "settings_reloaded" --> Cache

.. note::

   The diagram is a coordination sketch.
   :doc:`/content/topics/signals` is the canonical catalog of signal names, senders, and payloads.

Subsystem Dependencies
----------------------

The dependency graph between subsystems is shallow.

- ``next.conf`` has no internal dependencies and sits at the bottom.
- ``next.deps`` depends only on ``next.conf``.
- ``next.pages``, ``next.components``, ``next.urls``, ``next.static`` depend on ``next.conf`` and ``next.deps``.
- ``next.forms`` depends on ``next.pages``, ``next.deps``, and ``next.urls``.
- ``next.server`` depends on every subsystem that contributes a watch spec.
- ``next.testing`` depends on every subsystem to enable isolation.
- ``next.apps`` depends on every subsystem.
  It is the Django-facing entry point that calls each subsystem's startup hook.

Module Map
----------

Each subsystem keeps a flat module layout.

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Subsystem
     - Submodules
   * - ``next.pages``
     - ``manager``, ``registry``, ``loaders``, ``context``, ``processors``, ``checks``, ``signals``, ``watch``.
   * - ``next.components``
     - ``manager``, ``registry``, ``scanner``, ``loading``, ``renderers``, ``context``, ``facade``, ``info``, ``backends``, ``watch``, ``checks``, ``signals``.
   * - ``next.urls``
     - ``manager``, ``backends``, ``dispatcher``, ``parser``, ``markers``, ``reverse``, ``checks``, ``signals``.
   * - ``next.forms``
     - ``manager``, ``dispatch``, ``backends``, ``decorators``, ``base``, ``markers``, ``serializers``, ``formsets``, ``uid``, ``rendering``, ``checks``, ``signals``.
   * - ``next.static``
     - ``manager``, ``collector``, ``discovery``, ``backends``, ``assets``, ``scripts``, ``serializers``, ``defaults``, ``finders``, ``checks``, ``signals``.
   * - ``next.deps``
     - ``resolver``, ``providers``, ``cache``, ``context``, ``markers``, ``checks``, ``signals``.
   * - ``next.server``
     - ``autoreload``, ``watcher``, ``roots``, ``checks``, ``signals``.
   * - ``next.conf``
     - ``settings``, ``defaults``, ``helpers``, ``imports``, ``checks``, ``signals``.
   * - ``next.testing``
     - ``client``, ``signals``, ``isolation``, ``actions``, ``rendering``, ``loaders``, ``html``, ``patching``, ``deps``.
   * - ``next.apps``
     - ``config``, ``autoreload``, ``templates``, ``staticfiles``, ``components``.

See Also
--------

.. seealso::

   :doc:`request-lifecycle` for the end to end request path.
   :doc:`/content/topics/extending` for the user-facing extension mechanisms built on top of this architecture.
   :doc:`/content/topics/signals` for the signal catalog.
   :doc:`/content/ref/index` for the public API.
