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
   Simple components without a folder have no co-located directory, so discovery skips them.

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

Common Patterns
---------------

A ``layout.css`` at the root applies to every page under that layout because layout assets enter the collector first.
A ``layout.css`` at an inner layout scopes the styles to pages below that layout only.
Use ``component.js`` for plain behaviour and ``component.mjs`` when the script depends on ECMAScript module imports.

External URLs via Module Lists
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Declare ``styles`` and ``scripts`` at module level in ``page.py`` or ``component.py`` to register external URLs alongside co-located files.

.. code-block:: python
   :caption: notes/_components/note_card/component.py

   styles = ["https://cdn.example.com/reset.css"]
   scripts = ["https://cdn.example.com/vendor.js", "/static/local/widget.mjs"]

Each variable is a list of strings.
The slot is picked from the registered placeholder name, ``styles`` or ``scripts``.
The kind is inferred from the URL extension through the kind registry.
URLs with an unknown extension are dropped with a debug log.

See Also
--------

.. seealso::

   :doc:`overview` for the pipeline trace.
   :doc:`template-tags` for the injection point in the layout.
   :doc:`asset-kinds` for the kind to extension mapping.
   :doc:`custom-stems` for extra stem names.
