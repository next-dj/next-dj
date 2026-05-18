.. _topics-static-co-located:

Co-located Files
================

A co-located file is an asset that lives in the same directory as the page, layout, or component that owns it.
Discovery pairs each file with its owner through a stem convention so the collector adds it when the owner renders.

.. contents::
   :local:
   :depth: 2

Stem Convention
---------------

A stem is the filename without the extension.
Discovery groups stems into three roles.

``template`` role.
   Default stem ``template``.
   Matches files next to a ``template.djx``.

``layout`` role.
   Default stem ``layout``.
   Matches files next to a ``layout.djx``.

``component`` role.
   Default stem ``component``.
   Matches files alongside the component's ``component.djx`` or ``component.py``.

.. code-block:: text
   :caption: directory layout

   notes/_components/note_card/
     component.djx
     component.css
     component.js
   notes/pages/
     layout.djx
     layout.css
     page.py
     template.djx
     template.css
     template.js

Recognised Extensions
---------------------

Each stem accepts the extension of every registered asset kind.
The framework ships three kinds.

.. list-table::
   :header-rows: 1
   :widths: 35 30 35

   * - Extension
     - Kind
     - Slot
   * - ``.css``
     - ``css``
     - ``styles``
   * - ``.js``
     - ``js``
     - ``scripts``
   * - ``.mjs``
     - ``module``
     - ``scripts``

A file named ``component.mjs`` is therefore picked up as a ``module`` asset.
Custom kinds extend the set through :doc:`asset-kinds`.
Custom stems extend the recognised filenames through :doc:`custom-stems`.

How Discovery Pairs a File
--------------------------

The full disk-to-HTML trace lives in :doc:`overview`.
For co-located files the pairing rule is the part that matters.
A file is recognised only when its stem matches a registered stem for the owner's role and its extension matches a registered kind.

A file that does not match a registered stem and kind pair is ignored.
Discovery does not warn on unknown files because the folder may hold documentation or fixtures.

Asset Ownership
---------------

Every asset belongs to one owner.
The owner determines when the collector adds the asset to the request.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Role
     - Trigger
   * - ``component``
     - The component is rendered through ``{% component %}``.
   * - ``layout``
     - The layout renders as part of the layout chain.
   * - ``template``
     - The page renders.

A component referenced twice on the same page contributes the asset once.
The collector deduplicates entries through its dedup strategy, see :doc:`deduplication`.

Loading Order
-------------

The collector preserves insertion order.
Layout assets enter first as the layout chain unfolds from the root.
Page assets follow.
Component assets enter in the order the components appear in the template.

Hot Reload
----------

See :doc:`overview` for how the per-request collector picks up new co-located files without a process restart.

Recipes
-------

Site-Wide Reset
~~~~~~~~~~~~~~~

Place ``layout.css`` next to the root ``layout.djx``.
Every page below the layout inherits the reset because layout assets enter the collector first.

Per-Section Styles
~~~~~~~~~~~~~~~~~~

Add a ``layout.css`` next to an inner layout.
The styles apply only to pages below that layout.

Component Behaviour
~~~~~~~~~~~~~~~~~~~

Use ``component.js`` for behaviour that runs on every page that includes the component.
Use ``component.mjs`` when the script depends on ECMAScript module imports.

See Also
--------

.. seealso::

   :doc:`overview` for the pipeline trace.
   :doc:`template-tags` for the injection point in the layout.
   :doc:`asset-kinds` for the kind to extension mapping.
   :doc:`custom-stems` for extra stem names.
