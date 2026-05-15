.. _topics-static-overview:

Static Assets Overview
======================

The static pipeline finds CSS, JS, and module files that live next to pages and components, deduplicates them across the request, and injects link and script tags into placeholder slots inside the layout.
This page covers the four moving pieces of the pipeline and traces a single asset from disk to the rendered HTML.

.. contents::
   :local:
   :depth: 2

The Pipeline
------------

Four parts make up the pipeline.

Discovery.
   ``AssetDiscovery`` walks the filesystem for files that match a registered stem and a registered kind.
   It produces ``StaticAsset`` records.

Collector.
   ``StaticCollector`` is a request scoped object that accumulates the assets touched by the current render.
   It deduplicates entries through a pluggable strategy.

Backend.
   A ``StaticBackend`` resolves on disk paths into URLs and renders the link, script, and module tags.
   The bundled backend is ``StaticFilesBackend``.

Placeholder slots and template tags.
   ``{% collect_styles %}`` and ``{% collect_scripts %}`` mark placeholder slots in the layout.
   The static manager replaces each slot token with the rendered tags after the page renders.

The pipeline runs once per request.

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
The framework registers three kinds at startup through ``register_defaults``.

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

Trace a file named ``component.css`` inside a component folder.

1. Discovery walks the component roots.
   It finds ``component.css`` next to ``component.djx`` because ``component`` is a registered stem and ``.css`` is the extension of the ``css`` kind.

2. Discovery records a ``StaticAsset`` with kind ``css`` and the resolved ``source_path``.

3. A page that uses the component triggers the collector to add the asset.
   The collector deduplicates the entry through its dedup strategy.

4. The layout contains ``{% collect_styles %}``.
   The tag emits a placeholder token for the ``styles`` slot.

5. After the page renders, the static manager replaces the ``styles`` token with the rendered link tags.
   It calls ``render_link_tag`` on the active backend for each ``css`` asset.

6. The browser receives the final HTML with the link tags in place of the placeholder token.

The same flow applies to ``component.js`` (kind ``js``) and ``component.mjs`` (kind ``module``).
Both land in the ``scripts`` slot emitted by ``{% collect_scripts %}``.

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

Discovery registers watch specs with the autoreloader.
Adding, renaming, or removing an asset triggers a reload.
The collector picks up the new asset set on the next request.

Production Build
----------------

In production, ``collectstatic`` copies every registered asset into ``STATIC_ROOT`` under the ``next/`` namespace.
The framework hooks into the staticfiles finders through ``NextStaticFilesFinder`` so Django sees co-located assets.

See :doc:`/content/deployment/static-files` for production guidance.

Public API Touchpoints
----------------------

The pipeline exposes several public names.

``next.static.StaticCollector``.
   The request scoped collector.

``next.static.StaticBackend`` and ``next.static.StaticFilesBackend``.
   The abstract backend contract and the bundled implementation.

``next.static.StaticAsset``.
   The frozen asset record.

``next.static.AssetDiscovery``.
   The filesystem scanner.

``next.static.default_kinds`` and ``next.static.discovery.default_stems``.
   The kind and stem registries.

See :doc:`/content/ref/static` for the full reference.

See Also
--------

.. seealso::

   :doc:`co-located-files` for the filename conventions.
   :doc:`template-tags` for ``{% collect_styles %}`` and ``{% collect_scripts %}``.
   :doc:`asset-kinds` for registering a new kind.
   :doc:`/content/internals/static-pipeline` for the internal flow.
