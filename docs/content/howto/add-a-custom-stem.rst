.. _howto-custom-stem:

Add a custom stem
=================

Problem
-------

You want discovery to pick up a ``page.css`` next to ``template.djx`` in place of ``template.css``, or a ``vendor.js`` inside a component folder that holds no ``component.js``.

Solution
--------

Register the stem through ``next.static.default_stems`` in ``AppConfig.ready``.

Walkthrough
-----------

Register the stem under the appropriate role.

.. code-block:: python
   :caption: notes/apps.py

   from django.apps import AppConfig

   from next.static import default_stems

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

Verification
------------

Editing ``apps.py`` restarts the dev server, so the new stem registration is live on the next boot.
Reload a page that uses the file and confirm the asset appears in the rendered HTML.

Confirm the staticfiles finder picks the new file up.

.. code-block:: bash
   :caption: shell

   uv run python manage.py findstatic next/notes/pages/index.css

The finder maps a template-directory asset to ``next/<logical_name><suffix>``.
The logical name is the key of the page tree followed by the template directory relative to its page root, and a root template has the logical name ``index``, see :ref:`topics-static-name-resolution-colocated`.
The stem does not appear in the static path, so ``page.css`` next to the root ``template.djx`` of the ``notes`` application resolves under ``next/notes/pages/index.css``.

A new stem pairs with every registered kind, and two stems of one role share one logical path per extension.
Register an extra stem as a rename, see :doc:`/content/topics/static-assets/custom-stems` for the pairing and collision rules.

See also
--------

.. seealso::

   :doc:`/content/topics/static-assets/custom-stems` for the mechanics.
   :doc:`/content/topics/static-assets/co-located-files` for the default stems.
