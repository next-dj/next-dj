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
``AssetDiscovery`` walks the page and component trees, builds ``StaticAsset`` records, and feeds them to the request ``StaticCollector``.
The walk itself happens once per page and once per component, and later renders reuse what it found on disk.

Asset plans
-----------

An asset plan is what ``AssetDiscovery`` remembers about one page path or one component.
It holds the co-located files the walk found, the assets built from the ``styles`` and ``scripts`` lists of the owning module, and the directories the walk read.
A plan caches what the walk read off the disk plus the URLs those module lists resolved to.
The whole mechanism is gated by ``STATIC_DISCOVERY_CACHE``, which defaults to true, and a process that sets it false rebuilds a plan on every render instead of caching one.

The stem probes, the layout walk, and the module import happen once, and every file the plan holds still goes to ``register_file`` on every render, so a backend free to resolve the same file to a different URL per request is asked every time.
The default backend answers those calls from its own ``(logical_name, suffix)`` memo.
The repeat therefore costs it a dictionary lookup, and its answer changes when ``StaticManager.reload`` builds a new backend or when a ``STATIC_ROOT``, ``STATIC_URL``, or ``STORAGES`` change drops the memo through ``forget_urls``.
A module-list entry is resolved once instead, when the plan is built.
The backend answers a co-located file and an authored reference from one memo, keyed apart so a logical name and a reference cannot collide.

The page plan is keyed by the page file path.
The component plan is keyed by the component's ``template_path``, ``module_path``, and ``name``, which is what identifies the component the plan was built for, so a rescan that produces an equal ``ComponentInfo`` reuses the entry and a renamed or moved component gets its own.
A simple component owns no folder and reaches no plan at all, which keeps the entries the cache holds to the components that read the disk.
Both caches are bounded and evict the oldest entry once full, because the working set of a project sits far below the bound and a warm render answers from them without writing anything.

Five things invalidate a plan.

- A settings reload drops the whole static manager, and the discovery instance with its plans goes with it.
- An ``INSTALLED_APPS`` change moves which page trees the routers report, so ``StaticManager.forget_page_roots`` drops the cached roots and the discovery built from them.
- A registration in the stem, kind, or placeholder registry changes which filenames count as an asset without moving any file, so every plan carries the generation of those three registries and is rebuilt when it no longer matches.
  This check runs whatever ``DEBUG`` is set to, because it costs three integer reads and no syscall.
- A ``STATIC_ROOT``, ``STATIC_URL``, or ``STORAGES`` change calls ``StaticManager.forget_backend_urls``, which drops the discovery instance outright alongside the script builder and the memo of every configured backend, so no plan survives holding a URL read through the old storage.
- Under ``DEBUG`` each render re-stats the directories the plan was read from and rebuilds when one of them has moved, appeared, or gone away.
  The mtimes are read in nanoseconds and compared for inequality, so a directory restored from an archive with an older timestamp counts as changed too, and one that does not stat at all is recorded as absent rather than as a timestamp.
  For a page those directories are the whole walk from the page directory up to the page root, not only the ones that already hold a ``layout.djx``, so a layout added in between is visible on the next request.
  For a component they are the component folder and the folder holding its module.

Two of the five are a comparison the plan itself carries, and the other three discard the discovery instance with every plan in it, so the next render builds a fresh one.
A backend that rejects a file needs no invalidation, because the next render offers the file to it again, the warning repeats, and a fixed backend takes effect at once.
Outside ``DEBUG`` a warm render issues no ``stat`` call at all, because a production process does not mutate co-located files under a running server.
The ``asset_registered`` signal follows the collector rather than the render, so a component mounted several times on one page announces each of its assets once.

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

Under the ``AUTO`` script injection policy the injector wraps the rendered page with the ``next.min.js`` runtime through ``NextScriptBuilder``.
The builder owns the markup of all three fragments, while the bundle URL comes from ``backend.asset_url``, so a request-aware backend moves the runtime the same way it moves a co-located asset.

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
   Also holds ``static_name``, the reading of a reference the default backend resolves through, and the helper that stamps the version parameter.

``next.static.errors``.
   ``StaticAssetNotFoundError``, raised by both asset doors when staticfiles cannot resolve what the project named, and ``StaticAssetTraversalError``, raised for a reference that climbs above the staticfiles root.

``next.static.collector``.
   ``StaticCollector`` plus the dedup strategies ``UrlDedup``, ``HashContentDedup``, ``IdentityDedup`` and the JS context policies.
   Also holds ``PlaceholderSlot``, ``PlaceholderRegistry``, and the ``default_placeholders`` instance.

``next.static.backends``.
   ``StaticBackend`` abstract base class plus the bundled ``StaticFilesBackend``.
   Instances come from ``load_backends``, the shared loader every backend family uses.

``next.static.manager``.
   ``StaticManager`` orchestrates discovery and the per-request collector lifecycle.
   Its ``asset_url`` is the single funnel every rendered URL passes, so the configured ``STATIC_VERSION`` is stamped there, after the backend hook has shaped the URL.

``next.seeding``.
   ``seed_collector`` hydrates one collector from the render context and binds it back under ``COLLECTOR_KEY``, and it sits at the root of the package because the page render reaches this area through a port rather than an import.
   The module holds the shared render-context keys and the ``RenderFrame`` that seeds them as well, so the collector travels with the other ambient values of a render.

``next.static.inject``.
   ``PlaceholderInjector`` renders what a collector holds into the placeholder tokens of a finished page, and the manager delegates its ``inject`` to one.

``next.static.scripts``.
   ``NextScriptBuilder`` and ``ScriptInjectionPolicy`` for the ``Next`` runtime script.

``next.static.serializers``.
   ``JsContextSerializer`` protocol plus ``JsonJsContextSerializer`` and ``PydanticJsContextSerializer``.

``next.static.finders``.
   ``NextStaticFilesFinder`` exposes co-located page and component assets to Django staticfiles, so ``collectstatic`` copies them into ``STATIC_ROOT``.
   :doc:`/content/topics/static-assets/overview` covers the finder from the user side.
   The finder holds the mapping it discovered and rebuilds it when the stem or kind registry moves, when the framework settings reload, when the reported page or component trees change, or, while ``DEBUG`` is true, when the mtime of any directory inside those trees moves.

``next.static.defaults``.
   ``register_defaults`` registers the built in ``css``, ``js``, and ``module`` kinds and the ``styles`` and ``scripts`` slots.

Asset kinds
-----------

Each kind maps an extension to a placeholder slot and a backend renderer method.
The renderer name is a plain string the injector looks up with ``getattr`` on the active static backend per asset, so a backend supplies a renderer by exposing a method of that name.
:doc:`/content/topics/static-assets/asset-kinds` lists the bundled kinds and their renderer methods.

Dedup
-----

The collector holds one dedup strategy for the request.
The strategy is selected by the dotted path under the ``DEDUP_STRATEGY`` key of the first static backend ``OPTIONS``, instantiated once per request, defaulting to ``UrlDedup`` when the key is absent.
One render holds one collector, so it holds one strategy and one JS context policy, and the first entry of ``STATIC_BACKENDS`` settles both for the whole pipeline.
``StaticManager.default_backend`` is the first entry, and it is the only one the render path uses.
A later entry is built and receives ``static_backend_loaded`` and ``forget_urls``, and renders nothing.
:doc:`/content/topics/static-assets/deduplication` covers the bundled strategies and the custom-strategy protocol.

Signals
-------

The pipeline fires four signals.

- ``asset_registered`` fires once per co-located file the collector accepts from a backend registration.
  Module-level ``styles`` and ``scripts`` lists, every ``{% use_style %}`` and ``{% use_script %}`` form, void or block, and the ``{% use_module %}`` tag call ``collector.add`` directly and do not emit it.
  Those paths still resolve their reference through the backend first, so the collector holds a public URL whichever door an asset came through.
- ``collector_finalized`` once per request, as the first statement of ``inject`` and therefore before any slot is rendered.
  Nothing seals the collector, so a receiver that calls ``add`` still reaches the rendered output.
- ``html_injected`` once per request after the manager replaces the placeholder slots.
- ``static_backend_loaded`` once per backend instance when the manager builds it, including the staticfiles backend it seeds when no entry survives.

A standalone zone render runs the same discovery but ships the collected assets in the patch envelope, so ``collector_finalized`` and ``html_injected`` fire only on full-page renders.

Extension points
----------------

- Subclass ``StaticFilesBackend`` to change the rendered output.
- Override ``StaticBackend.resolve_url`` to look an authored reference up somewhere other than Django staticfiles, and keep core's reading of a reference with ``next.static.static_name``.
- Override ``StaticBackend.forget_urls`` when a backend memoises resolved URLs somewhere other than the base memo, and the manager drives it over every configured backend whenever ``STATIC_ROOT``, ``STATIC_URL``, or ``STORAGES`` changes.
- Implement the ``DedupStrategy`` protocol and point ``DEDUP_STRATEGY`` at it.
- Call ``default_kinds.register`` in ``AppConfig.ready`` to recognise a new extension.
- Call ``default_stems.register`` in ``AppConfig.ready`` to recognise a new filename.
- Subscribe to ``collector_finalized`` to inspect the collected set.

See also
--------

.. seealso::

   :doc:`/content/topics/static-assets/index` for the topic subtree.
   :doc:`request-lifecycle` for where the pipeline runs.
