.. _topics-static-custom-stems:

Custom Stems
============

A stem is the filename without the extension.
The framework ships ``component``, ``layout``, and ``template`` as default stems.
This page covers how to add new stems so that custom filenames such as ``module.css`` or ``preload.font`` are picked up by the discovery scanner.

.. contents::
   :local:
   :depth: 2

When to Add a Stem
------------------

Use a new stem when the project ships an asset that does not fit the three default names.

- ``module`` for ES module style scripts owned by a component, distinct from ``component.mjs``.
- ``font`` for fonts owned by a layout.
- ``vendor`` for third party scripts that ship inside a component folder.

Stems are independent from kinds.
A single stem can carry multiple kinds.
``component.css`` and ``component.js`` are both stem ``component`` but different kinds.

Adding a Stem Through Settings
------------------------------

Add the stem to ``NEXT_FRAMEWORK["DEFAULT_COMPONENT_STEMS"]`` for components or ``DEFAULT_PAGE_STEMS`` for pages and layouts.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_COMPONENT_STEMS": ["component", "module", "vendor"],
       "DEFAULT_PAGE_STEMS": ["template", "layout", "font"],
   }

The settings replace the framework defaults.
Include the default stems explicitly when you only want to add new names.

Discovery After Adding a Stem
-----------------------------

Each new stem expands the file search inside every owner directory.
A component folder now scans for ``module.css``, ``module.js``, ``module.mjs``, ``vendor.css``, ``vendor.js``, and ``vendor.mjs`` in addition to the standard ``component`` files.

A file that does not match any registered stem is ignored.
The scanner does not report unknown files because the project may keep arbitrary documentation or fixtures inside the folder.

Stem Plus Extension Pairs
-------------------------

Each stem plus extension pair becomes an asset entry.
Two files with the same stem and the same extension in one owner folder are a configuration error.

.. code-block:: text
   :caption: ambiguous layout, will warn

   _components/note_card/
     component.css
     component.css.bak       ignored, unknown extension
     module.css              ok, different stem
     module.js               ok, different extension

System check ``next.W050`` reports the ambiguity at startup.

Owner Resolution
----------------

Stems do not change ownership.
A ``module.css`` inside a component folder is still owned by the component.
A ``font.woff2`` inside a layout folder is still owned by the layout.

The owner determines when the collector adds the asset.
A ``module.js`` adjacent to ``component.djx`` therefore loads on every page that renders the component, even if the standard ``component.js`` is not present.

Stem Plus Kind Mapping
----------------------

Each stem participates in every registered kind that matches its extension.
The default mapping is shown below.

.. list-table::
   :header-rows: 1
   :widths: 25 25 25 25

   * - Stem
     - .css
     - .js
     - .mjs
   * - ``component``
     - kind ``css``
     - kind ``js``
     - kind ``module``
   * - ``layout``
     - kind ``css``
     - kind ``js``
     - kind ``module``
   * - ``template``
     - kind ``css``
     - kind ``js``
     - kind ``module``

Custom stems inherit the same mapping.
A new ``module`` stem participates in all three kinds without extra configuration.

Combining Stems with Custom Kinds
---------------------------------

Pair a custom stem with a custom kind to reach unique file conventions.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_COMPONENT_STEMS": ["component", "preload"],
       "DEFAULT_ASSET_KINDS": {
           "font": {
               "extension": ".woff2",
               "renderer": "notes.static.render_font_preload",
               "bucket": "preload",
           }
       },
   }

A file named ``preload.woff2`` inside a component folder lands in the ``preload`` bucket and is emitted through the custom renderer.

Hot Reload
----------

The autoreload watch spec uses the stem list to build the file globs that the watcher monitors.
Adding a stem at runtime requires a process restart so the watch spec includes the new patterns.

Common Patterns
---------------

Separate Preload Files
~~~~~~~~~~~~~~~~~~~~~~

Use a ``preload`` stem when a component owns a font or image that should preload before render.

Vendor Imports per Component
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use a ``vendor`` stem for third party assets that should ship next to the component that depends on them.

Single Module Per Page
~~~~~~~~~~~~~~~~~~~~~~

Use a ``module`` stem on pages so that ``module.mjs`` lives next to ``template.djx`` without sharing the name with the page body.

See Also
--------

.. seealso::

   :doc:`co-located-files` for the default stem mapping.
   :doc:`asset-kinds` for the kind side of the equation.
   :doc:`/content/howto/add-a-custom-stem` for a recipe.
