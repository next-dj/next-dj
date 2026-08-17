.. _internals-static-pipeline:

Static pipeline
===============

This page covers how the static subsystem discovers assets, collects them per request, deduplicates them, and emits the final HTML through the configured backend.

.. contents::
   :local:
   :depth: 2

Overview
--------

The static pipeline runs entirely per request.
``AssetDiscovery`` walks the page and component trees on each render, builds ``StaticAsset`` records, and feeds them to the request ``StaticCollector``.

Discovery and injection
-----------------------

.. mermaid::

   flowchart LR
       subgraph Request["Request"]
           Walk["Filesystem walk"] --> StemMatch["Match stem and extension"]
           StemMatch --> Discovery["AssetDiscovery"]
           Discovery --> Collector["StaticCollector"]
       end
       Collector --> Dedup["Dedup strategy"]
       Dedup --> Backend["StaticFilesBackend"]
       Backend --> Tags["Render link or script tags"]
       Tags --> HTML["Final HTML"]

Collector slots
---------------

The collector keeps assets in named slots, one per registered slot, each backed by a placeholder token in templates.
Each slot matches the ``collector slot`` term in :doc:`/content/misc/glossary`.

.. mermaid::

   flowchart TB
       Trigger["Layout, page, or component renders"] --> Route["Route to slot named by KindRegistry.slot(kind)"]
       Route --> Slot["Slot, for example styles or scripts"]
       Slot --> Finalize["collector_finalized"]
       Finalize --> Emit["collect tag for each slot"]
       Emit --> Injected["html_injected"]

Runtime script injection
------------------------

Under the ``AUTO`` script injection policy the static manager wraps the rendered page with the ``next.min.js`` runtime through ``NextScriptBuilder``.

.. mermaid::

   flowchart LR
       JsContext["JS context values"] --> Builder["NextScriptBuilder"]
       Builder --> Preload["Preload hint before </head>"]
       Builder --> Runtime["next.min.js script tag"]
       Builder --> Init["Inline Next._init payload"]
       Preload --> Wrapped["Wrapped HTML"]
       Runtime --> Wrapped
       Init --> Wrapped

See :doc:`/content/topics/static-assets/js-context` for the ``ScriptInjectionPolicy`` values, the three injected fragments, and the ``NEXT_JS_OPTIONS`` keys.

Modules
-------

``next.static.discovery``.
   ``AssetDiscovery`` walks the filesystem and produces ``StaticAsset`` records.
   Hosts ``StemRegistry`` plus the ``default_stems`` instance and reads the ``default_kinds`` registry from ``next.static.assets``.
   ``default_stems`` is not re-exported from the ``next.static`` package surface, so code that registers a stem imports it from ``next.static.discovery`` directly.

``next.static.assets``.
   The ``StaticAsset`` frozen dataclass and ``KindRegistry`` plus the ``default_kinds`` instance.

``next.static.collector``.
   ``StaticCollector`` plus the dedup strategies ``UrlDedup``, ``HashContentDedup``, ``IdentityDedup`` and the JS context policies.
   Also holds ``PlaceholderSlot``, ``PlaceholderRegistry``, and the ``default_placeholders`` instance.

``next.static.backends``.
   ``StaticBackend`` abstract base class plus the bundled ``StaticFilesBackend``.
   Instances come from ``load_backends``, the shared loader every backend family uses.

``next.static.manager``.
   ``StaticManager`` orchestrates discovery and the per request collector lifecycle.

``next.static.scripts``.
   ``NextScriptBuilder`` and ``ScriptInjectionPolicy`` for the ``Next`` runtime script.

``next.static.serializers``.
   ``JsContextSerializer`` protocol plus ``JsonJsContextSerializer`` and ``PydanticJsContextSerializer``.

``next.static.finders``.
   ``NextStaticFilesFinder`` exposes co-located page and component assets to Django staticfiles, so ``collectstatic`` copies them into ``STATIC_ROOT``.
   :doc:`/content/topics/static-assets/overview` covers the finder from the user side.

``next.static.defaults``.
   ``register_defaults`` registers the built in ``css``, ``js``, and ``module`` kinds and the ``styles`` and ``scripts`` slots.

Asset kinds
-----------

Each kind maps an extension to a placeholder slot and a backend renderer method.
The renderer name is a plain string the manager looks up with ``getattr`` on the active static backend per asset, so a backend supplies a renderer by exposing a method of that name.
:doc:`/content/topics/static-assets/asset-kinds` lists the bundled kinds and their renderer methods.

Dedup
-----

The collector holds one dedup strategy for the request.
The strategy is selected by the dotted path under the ``DEDUP_STRATEGY`` key of the first static backend ``OPTIONS``, instantiated once per request, defaulting to ``UrlDedup`` when the key is absent.
:doc:`/content/topics/static-assets/deduplication` covers the bundled strategies and the custom-strategy protocol.

Signals
-------

The pipeline fires four signals.

- ``asset_registered`` fires once per co-located file registered through a backend.
  Module-level ``styles`` and ``scripts`` lists and every ``{% use_style %}`` and ``{% use_script %}`` form, void or block, call ``collector.add`` directly and do not emit it.
- ``collector_finalized`` once per request after the collector closes its set.
- ``html_injected`` once per request after the manager replaces the placeholder slots.
- ``backend_loaded`` once per backend instance when the manager builds it, including
  the staticfiles backend it seeds when no entry survives.

A standalone zone render runs the same discovery but ships the collected assets in the patch envelope, so ``collector_finalized`` and ``html_injected`` fire only on full-page renders.

Extension points
----------------

- Subclass ``StaticFilesBackend`` to change the rendered output.
- Implement the ``DedupStrategy`` protocol and point ``DEDUP_STRATEGY`` at it.
- Call ``default_kinds.register`` in ``AppConfig.ready`` to recognise a new extension.
- Call ``default_stems.register`` in ``AppConfig.ready`` to recognise a new filename.
- Subscribe to ``collector_finalized`` to inspect the collected set.

See also
--------

.. seealso::

   :doc:`/content/topics/static-assets/index` for the topic subtree.
   :doc:`request-lifecycle` for where the pipeline runs.
