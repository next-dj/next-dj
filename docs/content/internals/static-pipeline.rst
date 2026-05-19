.. _internals-static-pipeline:

Static Pipeline
===============

This page covers how the static subsystem discovers assets, collects them per request, deduplicates them, and emits the final HTML through the configured backend.

.. contents::
   :local:
   :depth: 2

Overview
--------

The static pipeline runs entirely per request.
``AssetDiscovery`` walks the page and component trees on each render, builds ``StaticAsset`` records, and feeds them to the request ``StaticCollector``.

Discovery and Injection
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

Collector Slots
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

Runtime Script Injection
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
   Honours the ``default_stems`` and ``default_kinds`` registries.

``next.static.assets``.
   The ``StaticAsset`` frozen dataclass and the ``KindRegistry`` plus the ``default_kinds`` instance.

``next.static.collector``.
   ``StaticCollector`` plus the dedup strategies ``UrlDedup``, ``HashContentDedup``, ``IdentityDedup`` and the JS context policies.

``next.static.backends``.
   ``StaticBackend`` abstract base class plus the bundled ``StaticFilesBackend`` and the ``StaticsFactory``.

``next.static.manager``.
   ``StaticManager`` orchestrates discovery and the per request collector lifecycle.

``next.static.scripts``.
   ``NextScriptBuilder`` and ``ScriptInjectionPolicy`` for the ``Next`` runtime script.

``next.static.serializers``.
   ``JsContextSerializer`` protocol plus ``JsonJsContextSerializer`` and ``PydanticJsContextSerializer``.

``next.static.defaults``.
   ``register_defaults`` registers the built in ``css``, ``js``, and ``module`` kinds and the ``styles`` and ``scripts`` slots.

Asset Kinds
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

- ``asset_registered`` once per asset when the collector records it.
- ``collector_finalized`` once per request after the collector closes its set.
- ``html_injected`` once per request after the manager replaces the placeholder slots.
- ``backend_loaded`` once per backend instance when the factory builds it.

Extension Points
----------------

- Subclass ``StaticFilesBackend`` to change the rendered output.
- Implement the ``DedupStrategy`` protocol and point ``DEDUP_STRATEGY`` at it.
- Call ``default_kinds.register`` in ``AppConfig.ready`` to recognise a new extension.
- Call ``default_stems.register`` in ``AppConfig.ready`` to recognise a new filename.
- Subscribe to ``collector_finalized`` to inspect the collected set.

See Also
--------

.. seealso::

   :doc:`/content/topics/static-assets/index` for the topic subtree.
   :doc:`request-lifecycle` for where the pipeline runs.
