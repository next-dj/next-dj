.. _internals-static-pipeline:

Static Pipeline
===============

This page covers how the static subsystem discovers assets, collects them per request, deduplicates them, and emits the final HTML through the configured backend.

.. contents::
   :local:
   :depth: 2

Overview
--------

The static pipeline has two stages.
Discovery runs once at startup and produces a registry of ``Asset`` records.
Collection runs once per request and produces an ordered list of assets that the layout emits.

Discovery and Injection
-----------------------

.. mermaid::

   flowchart LR
       Walk[Filesystem walk] --> StemMatch[Match stem and extension]
       StemMatch --> Discovery[AssetDiscovery]
       Discovery --> Registry[Asset registry]
       Registry --> Request[Request]
       Request --> Collector[StaticCollector]
       Collector --> Dedup[Dedup strategy]
       Dedup --> Backend[StaticBackend]
       Backend --> Tags[Render link or script tags]
       Tags --> HTML[Final HTML]

Collector Slots
---------------

The collector keeps assets in named slots that map to template tags.

.. mermaid::

   flowchart TB
       Trigger[Layout, page, or component renders] --> Pick{Pick slot}
       Pick -- styles --> StylesSlot[styles slot]
       Pick -- scripts --> ScriptsSlot[scripts slot]
       Pick -- custom --> CustomSlot[custom bucket]
       StylesSlot --> Finalize[collector_finalized]
       ScriptsSlot --> Finalize
       CustomSlot --> Finalize
       Finalize --> EmitStyles[{% collect_styles %}]
       Finalize --> EmitScripts[{% collect_scripts %}]
       Finalize --> EmitBucket[{% collect_bucket %}]
       EmitStyles --> Injected[html_injected]
       EmitScripts --> Injected
       EmitBucket --> Injected

Modules
-------

``next.static.discovery``.
   Walks the filesystem and produces ``Asset`` records.
   Honours the stem and kind registries.

``next.static.assets``.
   ``Asset`` dataclass and ``Asset.inline`` factory.

``next.static.collector``.
   ``StaticCollector`` plus ``DedupStrategy`` implementations including ``HashContentDedup``.

``next.static.backends``.
   ``StaticBackend`` base class with ``url``, ``render_link_tag``, ``render_script_tag``, ``render_module_tag``.
   The default ``StaticFilesBackend`` is bundled.

``next.static.manager``.
   Orchestrates discovery and the per request collector lifecycle.

``next.static.scripts``.
   Inline script and inline style management.

``next.static.serializers``.
   ``JsContextSerializer`` base class for the ``Next`` browser object.

``next.static.defaults``.
   Default asset kinds (css, js, module) and default stems.

Asset Kinds
-----------

Each kind maps an extension to a renderer and a bucket.
The default registry holds three kinds.

- ``css`` to ``render_link_tag`` in the ``styles`` bucket.
- ``js`` to ``render_script_tag`` in the ``scripts`` bucket.
- ``module`` to ``render_module_tag`` in the ``scripts`` bucket.

The ``module`` kind is interesting because it lives on ``StaticBackend`` (specifically in ``next.static.backends``) as ``render_module_tag``.
A custom backend can override the method to add ``crossorigin``, ``integrity``, or any other attribute.

Dedup
-----

The default strategy fingerprints by content hash.
Two files with the same bytes deduplicate even when they sit at different paths.

The strategy is swappable through ``NEXT_FRAMEWORK["STATIC_DEDUP"]``.

Signals
-------

The pipeline fires four signals.

- ``asset_registered`` once per asset on discovery.
- ``collector_finalized`` once per request after the collector closes its set.
- ``html_injected`` once per bucket after the template tag emits the HTML.
- ``backend_loaded`` once per backend instance.

Extension Points
----------------

- Subclass ``StaticBackend`` to change the rendered output.
- Subclass ``DedupStrategy`` to replace the dedup decision.
- Add an entry to ``DEFAULT_ASSET_KINDS`` to recognise a new extension.
- Add an entry to ``DEFAULT_COMPONENT_STEMS`` or ``DEFAULT_PAGE_STEMS`` to recognise a new filename.
- Subscribe to ``collector_finalized`` to inject runtime assets.

See Also
--------

.. seealso::

   :doc:`/content/topics/static-assets/index` for the topic subtree.
   :doc:`request-lifecycle` for where the pipeline runs.
