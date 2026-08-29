.. _topics-static-overview:

Static assets overview
======================

The static pipeline finds CSS, JS, and module files that live next to pages and components.
It deduplicates them across the request and injects link and script tags into placeholder slots inside the layout.
This page covers the four moving pieces of the pipeline and traces a single asset from disk to the rendered HTML.

.. contents::
   :local:
   :depth: 2

The pipeline
------------

Four parts make up the pipeline.

Discovery.
   ``AssetDiscovery`` produces ``StaticAsset`` records from files matching a registered stem and kind.

Collector.
   ``StaticCollector`` accumulates and deduplicates the assets touched by the current render.

Backend.
   A ``StaticBackend`` resolves on-disk paths into URLs and renders the tags.
   ``StaticFilesBackend`` is the bundled one.

Placeholder slots and template tags.
   ``{% collect_styles %}`` and ``{% collect_scripts %}`` mark the slots the static manager fills after the page renders.

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
   The pre-rendered inline body, or ``None`` for URL assets.
   The manager wraps a ``css`` inline body in a ``<style>`` element and a ``js`` inline body in a ``<script>`` element on injection.

``url`` and ``inline`` are mutually exclusive.
A URL asset carries a non-empty ``url`` and a ``None`` ``inline``.
An inline asset carries an empty ``url`` and a non-empty ``inline`` body.

Asset kinds
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

A single asset from disk to HTML
--------------------------------

A file named ``component.css`` next to ``component.djx`` reaches the browser in one pass.
Discovery records it as a ``css`` ``StaticAsset`` because ``component`` is a registered stem and ``.css`` is the ``css`` kind extension.
A render that uses the component adds the asset to the collector, which deduplicates it.
After the page renders, the static manager replaces the ``styles`` slot token emitted by ``{% collect_styles %}`` with the link tags produced by ``render_link_tag`` on the active backend.
``register_file`` resolves the on-disk path to a public URL through Django staticfiles during discovery, so manifest hashing and CDN settings apply to the URL that ``render_link_tag`` formats into the tag.

The same flow applies to ``component.js`` (kind ``js``, classic script) and ``component.mjs`` (kind ``module``, ECMAScript module), which both land in the ``scripts`` slot emitted by ``{% collect_scripts %}``.
The extension picks the kind, so a file named ``component.js`` never renders through ``render_module_tag``.
:doc:`/content/internals/static-pipeline` traces the pipeline step by step.

Stems and owners
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

The stem registry is ``default_stems``, which lives at ``next.static.discovery`` rather than the package root.
Projects register extra stems through ``default_stems.register``.
See :doc:`custom-stems`.

Where assets live
-----------------

:doc:`co-located-files` shows the directory layout where co-located assets sit next to pages, layouts, and components.

Hot reload
----------

Discovery re-probes co-located files while ``DEBUG`` is true, so a saved or added ``component.css`` or ``component.js`` is picked up on the next page load without a process restart.
A page is re-probed when the directory holding its assets moves, which is what creating or deleting a file there does.
With ``DEBUG`` off, what a page contributes is read once per process and the probing cost disappears from the render path.
Module-level ``styles`` and ``scripts`` lists are read when the module imports, so an edit to one takes effect after a ``page.py`` change restarts the dev server.
See the Hot reload section of :doc:`/content/topics/components` for the full reload contract across ``page.py``, ``component.py``, and ``.djx`` changes.

Production build
----------------

In production, ``collectstatic`` copies every registered asset into ``STATIC_ROOT`` under the ``next/`` namespace.
The framework hooks into the staticfiles finders through ``NextStaticFilesFinder`` so Django sees co-located assets.

See :doc:`/content/deployment/static-files` for production guidance.

Public API touchpoints
----------------------

:doc:`/content/ref/static` is the full reference for the pipeline's public names.

See also
--------

.. seealso::

   :doc:`co-located-files` for the filename conventions.
   :doc:`template-tags` for ``{% collect_styles %}`` and ``{% collect_scripts %}``.
   :doc:`asset-kinds` for registering a new kind.
   :doc:`/content/internals/static-pipeline` for the internal flow.
