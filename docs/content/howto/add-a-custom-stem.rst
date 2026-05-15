.. _howto-custom-stem:

Add a Custom Stem
=================

Problem
-------

You want the discovery scanner to pick up files named ``module.mjs`` or ``vendor.css`` inside a component folder in addition to the default ``component.<ext>`` files.

Solution
--------

Add the stem name to ``NEXT_FRAMEWORK["DEFAULT_COMPONENT_STEMS"]``.
The scanner expands the file search to include the new name with every registered extension.

Walkthrough
-----------

Pick the stem names.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_COMPONENT_STEMS": ["component", "module", "vendor"],
   }

The list replaces the framework default ``["component"]``.
Include ``component`` so the default behaviour still works.

Ship a file with the new stem.

.. code-block:: text
   :caption: notes/_components/note_card/

   component.djx
   component.css
   module.mjs
   vendor.css

The scanner registers ``module.mjs`` under the ``module`` kind and ``vendor.css`` under the ``css`` kind.
Both files belong to the same owner (the ``note_card`` component) and load together when the component renders.

For Pages and Layouts
~~~~~~~~~~~~~~~~~~~~~

Set ``DEFAULT_PAGE_STEMS`` to extend the page side.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_PAGE_STEMS": ["template", "layout", "vendor"],
   }

A file named ``vendor.css`` next to ``template.djx`` ships with the page.

Verification
------------

Restart the server so the watch spec picks up the new file globs.
Reload the page that uses the component and confirm that the new files appear in the rendered HTML.

See Also
--------

.. seealso::

   :doc:`/content/topics/static-assets/custom-stems` for the mechanics.
   :doc:`/content/topics/static-assets/co-located-files` for the default stems.
