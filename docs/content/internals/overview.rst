.. _internals-overview:

Internals Overview
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
The hook registers the framework system checks, runs five installers that wire the subsystems into the Django runtime, and calls ``autodiscover_forms()`` so shared forms register before the first request arrives.
See :doc:`/content/ref/apps` for the canonical ordering and the full API.

How They Compose
----------------

A request hands off ``next.urls`` to ``next.pages`` to ``next.deps`` to ``next.static`` and ``next.components`` before the final HTML returns to the client.
Form submissions take a parallel path through ``next.forms``, which on validation failure reuses the same render pipeline.
:doc:`request-lifecycle` traces both paths end to end.

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
- ``next.pages``, ``next.components``, ``next.static`` depend on ``next.conf`` and ``next.deps``.
- ``next.forms`` depends on ``next.pages`` and ``next.deps``.
- ``next.urls`` depends on ``next.conf``, ``next.deps``, ``next.pages``, ``next.components``, and ``next.forms``.
- ``next.server`` depends on ``next.conf``, ``next.pages``, ``next.urls``, and ``next.components``, the subsystems whose trees it watches.
- ``next.testing`` depends on the page, component, form, dependency, and static subsystems to drive isolation and rendering helpers.
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
     - ``manager``, ``dispatch``, ``backends``, ``decorators``, ``base``, ``markers``, ``serializers``, ``formsets``, ``uid``, ``rendering``, ``autodiscover``, ``checks``, ``signals``.
   * - ``next.static``
     - ``manager``, ``collector``, ``discovery``, ``backends``, ``assets``, ``scripts``, ``serializers``, ``defaults``, ``finders``, ``checks``, ``signals``.
   * - ``next.deps``
     - ``resolver``, ``providers``, ``cache``, ``context``, ``markers``, ``signals``. The ``checks`` module is a reserved stub with no checks registered yet.
   * - ``next.server``
     - ``autoreload``, ``watcher``, ``roots``, ``checks``, ``signals``.
   * - ``next.conf``
     - ``settings``, ``defaults``, ``helpers``, ``imports``, ``checks``, ``signals``.
   * - ``next.testing``
     - ``client``, ``signals``, ``isolation``, ``actions``, ``rendering``, ``loaders``, ``html``, ``patching``, ``deps``.
   * - ``next.apps``
     - ``config``, ``autoreload``, ``templates``, ``staticfiles``, ``components``.
   * - ``next.checks``
     - ``__init__`` aggregates system-check registration across every subpackage. ``common`` provides shared helpers used by individual ``checks`` modules.
   * - ``next.templatetags``
     - ``components``, ``forms``, ``next_static``.

See Also
--------

.. seealso::

   :doc:`request-lifecycle` for the end to end request path.
   :doc:`/content/topics/extending` for the user-facing extension mechanisms built on top of this architecture.
   :doc:`/content/topics/signals` for the signal catalog.
   :doc:`/content/ref/index` for the public API.
