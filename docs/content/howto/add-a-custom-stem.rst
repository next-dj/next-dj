.. _howto-custom-stem:

Add a Custom Stem
=================

Problem
-------

You want discovery to pick up a file named ``page.css`` next to ``template.djx``, or ``vendor.js`` inside a component folder, in addition to the default stems.

Solution
--------

Register the stem through ``next.static.discovery.default_stems`` in ``AppConfig.ready``.

Walkthrough
-----------

Register the stem under the appropriate role.

.. code-block:: python
   :caption: notes/apps.py

   from django.apps import AppConfig
   from next.static.discovery import default_stems

   class NotesConfig(AppConfig):
       name = "notes"

       def ready(self) -> None:
           default_stems.register("template", "page")
           default_stems.register("component", "vendor")

The first argument is the role, one of ``template``, ``layout``, or ``component``.
The second argument is the new stem.

Ship a file with the new stem.

.. code-block:: text
   :caption: notes/pages/

   page.py
   template.djx
   page.css

Discovery now records ``page.css`` as a ``css`` asset owned by the page, because ``page`` is a registered stem under the ``template`` role and ``.css`` is the extension of the ``css`` kind.

Stem and Kind Pairing
~~~~~~~~~~~~~~~~~~~~~

A new stem participates in every registered kind.
After registering ``vendor`` under the ``component`` role, discovery looks for ``vendor.css``, ``vendor.js``, and ``vendor.mjs`` inside component folders.

Verification
------------

Editing ``apps.py`` restarts the dev server, so the new stem registration is live on the next boot.
Reload a page that uses the file and confirm the asset appears in the rendered HTML.

Confirm the staticfiles finder picks the new file up.

.. code-block:: bash
   :caption: shell

   uv run python manage.py findstatic next/notes/pages/page.css

See Also
--------

.. seealso::

   :doc:`/content/topics/static-assets/custom-stems` for the mechanics.
   :doc:`/content/topics/static-assets/co-located-files` for the default stems.
