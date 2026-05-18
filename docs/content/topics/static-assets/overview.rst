.. _topics-static-overview:

Static Assets Overview
======================

The static pipeline finds CSS, JS, and module files that live next to pages and components.
It deduplicates them across the request and injects link and script tags into placeholder slots inside the layout.
This page covers the four moving pieces of the pipeline and traces a single asset from disk to the rendered HTML.

.. contents::
   :local:
   :depth: 2

The Pipeline
------------

Four parts make up the pipeline.

Discovery.
   ``AssetDiscovery`` produces ``StaticAsset`` records from files matching a registered stem and kind.

Collector.
   ``StaticCollector`` accumulates and deduplicates the assets touched by the current render.

Backend.
   A ``StaticBackend`` resolves on disk paths into URLs and renders the tags, ``StaticFilesBackend`` is the bundled one.

Placeholder slots and template tags.
   ``{% collect_styles %}`` and ``{% collect_scripts %}`` mark the slots the static manager fills after the page renders.

"A Single Asset From Disk to HTML" below traces these four parts on a concrete file.

StaticAsset
-----------

``StaticAsset`` is a frozen dataclass with four fields.

``url``.
   The public URL of the asset.
   Empty for inline assets.

``kind``.
   The asset kind, such as ``css``, ``js``, or ``module``.

``source_path``.
   The path of the co-located file on disk, or ``None`` for inline and external assets.

``inline``.
   The pre rendered inline body, or ``None`` for URL assets.

Asset Kinds
-----------

A kind binds a file extension to a placeholder slot and a backend renderer method.
The framework registers the two placeholder slots and three asset kinds at startup through ``register_defaults``.

.. list-table::
   :header-rows: 1
   :widths: 20 25 25 30

   * - Kind
     - Extension
     - Slot
     - Renderer method
   * - ``css``
     - ``.css``
     - ``styles``
     - ``render_link_tag``
   * - ``js``
     - ``.js``
     - ``scripts``
     - ``render_script_tag``
   * - ``module``
     - ``.mjs``
     - ``scripts``
     - ``render_module_tag``

The kind registry is ``next.static.default_kinds``.
Projects register additional kinds through ``default_kinds.register``.
See :doc:`asset-kinds` for the registration recipe.

A Single Asset From Disk to HTML
--------------------------------

A file named ``component.css`` next to ``component.djx`` reaches the browser in one pass.
Discovery records it as a ``css`` ``StaticAsset`` because ``component`` is a registered stem and ``.css`` is the ``css`` kind extension.
A render that uses the component adds the asset to the collector, which deduplicates it.
After the page renders, the static manager replaces the ``styles`` slot token emitted by ``{% collect_styles %}`` with the link tags produced by ``render_link_tag`` on the active backend.

The same flow applies to ``component.js`` (kind ``js``) and ``component.mjs`` (kind ``module``), which land in the ``scripts`` slot emitted by ``{% collect_scripts %}``.
:doc:`/content/internals/static-pipeline` traces the pipeline step by step.

Stems and Owners
----------------

Discovery recognises files by stem.
A stem is the filename without the extension.

.. list-table::
   :header-rows: 1
   :widths: 25 35 40

   * - Role
     - Default stem
     - Matches
   * - ``template``
     - ``template``
     - ``template.css``, ``template.js`` next to ``template.djx``
   * - ``layout``
     - ``layout``
     - ``layout.css``, ``layout.js`` next to ``layout.djx``
   * - ``component``
     - ``component``
     - ``component.css``, ``component.js`` inside a component folder

The stem registry is ``next.static.discovery.default_stems``.
Projects register extra stems through ``default_stems.register``.
See :doc:`custom-stems`.

Where Assets Live
-----------------

.. code-block:: text
   :caption: typical layout

   notes/
     _components/
       note_card/
         component.djx
         component.css
         component.js
     routes/
       layout.djx
       layout.css
       page.py
       template.djx
       template.css

Hot Reload
----------

Co-located assets are not watched by the autoreloader.
The collector re-runs discovery on every request, so a saved or added asset is picked up on the next page load without a process restart.
A change to ``page.py`` or ``component.py`` does restart the process through the normal Python reloader.

Production Build
----------------

In production, ``collectstatic`` copies every registered asset into ``STATIC_ROOT`` under the ``next/`` namespace.
The framework hooks into the staticfiles finders through ``NextStaticFilesFinder`` so Django sees co-located assets.

See :doc:`/content/deployment/static-files` for production guidance.

Public API Touchpoints
----------------------

The pipeline exposes several public names.
The full set is in :doc:`/content/ref/static`.

``next.static.StaticCollector``.
   The request scoped collector.

``next.static.StaticBackend`` and ``next.static.StaticFilesBackend``.
   The abstract backend contract and the bundled implementation.

``next.static.StaticAsset``.
   The frozen asset record.

``next.static.AssetDiscovery``.
   The filesystem scanner.

``next.static.default_kinds``.
   The kind registry, exported from the package root.

``next.static.discovery.default_stems``.
   The stem registry, which lives at ``next.static.discovery`` rather than the package root.

See :doc:`/content/ref/static` for the full reference.

See Also
--------

.. seealso::

   :doc:`co-located-files` for the filename conventions.
   :doc:`template-tags` for ``{% collect_styles %}`` and ``{% collect_scripts %}``.
   :doc:`asset-kinds` for registering a new kind.
   :doc:`/content/internals/static-pipeline` for the internal flow.
