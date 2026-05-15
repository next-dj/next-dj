.. _topics-static-co-located:

Co-located Files
================

A co-located file is an asset that lives in the same directory as the page, component, or layout that owns it.
The framework discovers these files through a stem convention and attaches each one to its owner so the collector adds it to the request when the owner renders.

.. contents::
   :local:
   :depth: 2

Stem Convention
---------------

A stem is the filename without the extension.
Three stems carry special meaning by default.

``component``.
   Owned by a composite component.
   Lives next to ``component.djx``.

``layout``.
   Owned by a layout.
   Lives next to ``layout.djx``.

``template``.
   Owned by a page module.
   Lives next to ``template.djx``.

The framework also recognises ``module`` for ES module style scripts.

.. code-block:: text
   :caption: layout

   notes/_components/note_card/
     component.djx
     component.css
     component.js
     component.mjs
   notes/routes/
     layout.djx
     layout.css
     page.py
     template.djx
     template.css
     template.js

Recognised Extensions
---------------------

Each stem accepts several extensions out of the box.

.. list-table::
   :header-rows: 1
   :widths: 35 35 30

   * - Stem
     - Extension
     - Kind
   * - ``component``, ``layout``, ``template``
     - ``.css``
     - ``css``
   * - ``component``, ``layout``, ``template``
     - ``.js``
     - ``js``
   * - ``component``, ``layout``, ``template``
     - ``.mjs``
     - ``module``

Extensions are configurable through :doc:`asset-kinds`.
Custom stems extend the set through :doc:`custom-stems`.

How Discovery Works
-------------------

Discovery happens once during startup and after each filesystem reload.

1. The discovery scanner walks every page root that the router walks.
2. For each component, page, and layout directory the scanner looks for files that match a registered stem.
3. Each match becomes an ``Asset`` record stored in the registry.
4. The registry is the input for the per request collector.

The scan is incremental.
The autoreload watcher tracks the page roots, so adding a file does not require a restart.

Asset Ownership
---------------

Every asset belongs to one owner.
The owner determines when the collector adds the asset to the request.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Stem
     - Trigger
   * - ``component``
     - The component is rendered through ``{% component %}``.
   * - ``layout``
     - The layout renders as part of the layout chain.
   * - ``template``
     - The page renders, before any inner components.

A component referenced twice on the same page contributes the asset once.
Two pages that share the same component receive separate collectors.

Multiple Files per Stem
-----------------------

Each owner can ship at most one file per stem and extension pair.
A component with both ``component.css`` and ``component.css.map`` is fine because ``.map`` is not a registered extension.

Two CSS files in one component folder are a configuration error.
Reorganise the styles or rename one of the files.
The system check ``next.W050`` reports the conflict at startup.

Loading Order
-------------

Inside one owner the framework follows insertion order.
Across owners the order is layout chain, page, component in template order.
The collector preserves the order so the rendered HTML mirrors the discovery sequence.

See :doc:`overview` for the order rules.

Hot Reload
----------

The discovery scanner participates in the autoreload pipeline.
Adding ``component.css`` to a component folder triggers a router reload signal and the collector picks up the new asset on the next request.

Removing a file works the same way.
The asset disappears from the registry and templates that referenced it through the standard tags simply stop including it.

Recipes
-------

CSS Reset for the Whole Site
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Place ``layout.css`` next to the root ``layout.djx``.
Every page below that layout inherits the reset because the layout chain always emits the root layout assets first.

Per Section Styles
~~~~~~~~~~~~~~~~~~

Add a ``layout.css`` next to an inner layout.
The styles apply only to pages below that layout, the upper layouts and other sections are unaffected.

Component Specific JS Behaviour
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Use ``component.js`` for behaviour that should run on every page that includes the component.
Use ``component.mjs`` when the script depends on ECMAScript module imports.

See Also
--------

.. seealso::

   :doc:`overview` for the pipeline trace.
   :doc:`template-tags` for the injection point in the template.
   :doc:`asset-kinds` for the kind to extension mapping.
   :doc:`custom-stems` for extra stem names.
