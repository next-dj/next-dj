.. _topics-static-overview:

Static Assets Overview
======================

The static pipeline finds CSS, JS, and module files that live next to pages and components, deduplicates them across the request, and emits link and script tags from inside the template.
This page covers the four moving pieces of the pipeline and traces a single asset from the disk to the rendered HTML.

.. contents::
   :local:
   :depth: 2

The Pipeline
------------

Four parts make up the pipeline.

Discovery.
   Scans the filesystem for assets co-located with pages and components.
   Builds a registry of files keyed by stem, kind, and owner.

Collector.
   A request scoped object that accumulates the assets touched by the current render.

Backend.
   Resolves the on disk paths into URLs, computes content hashes, and decides the order of emission.

Template tags.
   ``{% collect_styles %}`` and ``{% collect_scripts %}`` ask the collector for the current bucket and emit the HTML tags.

The pipeline runs once per request.
Two pages that include the same component result in two requests, each with their own collector instance.

A Single Asset From Disk to HTML
--------------------------------

Trace a file named ``component.css`` from inside a component folder.

1. Discovery scans the components root and registers ``note_card`` as a component.
   The scan finds ``component.css`` next to ``component.djx`` and records it under the ``css`` kind.

2. A page that uses ``{% component "note_card" %}`` triggers the collector to add every asset linked to the component.
   The collector keeps an ordered set of assets.

3. The layout contains ``{% collect_styles %}``.
   The tag asks the collector for every ``css`` asset gathered so far.

4. The backend converts each asset entry into a URL with a content hash and renders a ``<link rel="stylesheet">`` tag.

5. The browser receives the final HTML with the link tag at the top of ``<head>``.

The same flow applies to ``component.js`` (kind ``js``), ``component.mjs`` (kind ``module``), and any custom kind registered through ``DEFAULT_ASSET_KINDS``.

Owners and Triggers
-------------------

An asset has an owner.
The owner is the page module, the component, or the layout that declares it.
The collector adds an asset to the request when the owner is rendered.

Three trigger patterns exist.

Component asset.
   Added when the component is rendered through ``{% component "name" %}``.

Page asset.
   Added when the page renders, before any component runs.

Layout asset.
   Added when the layout renders, in the order layouts unfold from root to leaf.

Order in HTML
-------------

The collector stores assets in insertion order.
The template tag emits them in that order.

A typical ordering is root layout assets, then inner layout assets, then page assets, then component assets in the order they appear in the template.
The order is deterministic and stable across requests.

Customise the ordering through a custom backend.
See :doc:`backends` for the contract.

Where Assets Live
-----------------

The static pipeline expects files to live next to the entity that owns them.

.. code-block:: text
   :caption: typical layout

   notes/
     _components/
       note_card/
         component.djx
         component.css
         component.js
         component.mjs
     routes/
       layout.djx
       layout.css
       page.py
       template.djx
       template.css

The filenames follow a stem convention.

- ``component.<ext>`` for component owned assets.
- ``layout.<ext>`` for layout owned assets.
- ``template.<ext>`` for page owned assets.

A project can register additional stems through ``DEFAULT_COMPONENT_STEMS``.
See :doc:`custom-stems` for the recipe.

Hot Reload
----------

The discovery step also registers a watch spec with the autoreloader.
Adding, renaming, or removing an asset triggers a router reload signal.
The collector picks up the new asset set on the next request.

Production Build
----------------

In production, ``collectstatic`` copies every registered asset into ``STATIC_ROOT``.
The framework hooks into the staticfiles finders so Django sees co-located assets as if they sat under ``static/``.

See :doc:`/content/deployment/static-files` for production guidance.

Public API Touchpoints
----------------------

The pipeline exposes several public names that other parts of the framework and your code can import.

``next.static.collector.StaticCollector``.
   The request scoped collector.

``next.static.backends.StaticBackend``.
   Abstract base class for backends.
   Has methods ``render_link_tag``, ``render_script_tag``, ``render_module_tag``.

``next.static.assets.Asset``.
   Frozen dataclass that describes one registered asset.

``next.static.discovery.AssetDiscovery``.
   The filesystem scanner.

See :doc:`/content/ref/static` for the full reference.

See Also
--------

.. seealso::

   :doc:`co-located-files` for the filename conventions.
   :doc:`template-tags` for ``{% collect_styles %}`` and ``{% collect_scripts %}``.
   :doc:`deduplication` for the dedup contract.
   :doc:`/content/internals/static-pipeline` for the internal flow.
