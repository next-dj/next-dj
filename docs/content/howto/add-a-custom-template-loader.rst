.. _howto-custom-template-loader:

Add a Custom Template Loader
============================

Problem
-------

You need page bodies to load from a filename other than the default sibling ``template.djx``, or from a computed path that still maps to a ``page.py`` directory.

Solution
--------

Subclass ``next.pages.loaders.TemplateLoader`` and implement ``can_load`` and ``load_template``.
Register the dotted path in ``NEXT_FRAMEWORK["TEMPLATE_LOADERS"]``.
Keep ``next.pages.loaders.DjxTemplateLoader`` in the chain when you still want ``template.djx`` support.

Walkthrough
-----------

Implement ``TemplateLoader`` with a distinct ``source_name`` class attribute.
That string appears in diagnostics such as the ``next.W043`` body-source warning when multiple loaders match the same directory.

.. code-block:: python
   :caption: notes/loaders.py

   from pathlib import Path
   from next.pages.loaders import TemplateLoader

   class MarkdownTemplateLoader(TemplateLoader):
       """Load sibling ``template.md`` files as plain text bodies."""

       source_name = "template.md"

       def can_load(self, file_path: Path) -> bool:
           return (file_path.parent / "template.md").is_file()

       def load_template(self, file_path: Path) -> str | None:
           path = file_path.parent / "template.md"
           if not path.is_file():
               return None
           return path.read_text(encoding="utf-8")

       def source_path(self, file_path: Path) -> Path | None:
           candidate = file_path.parent / "template.md"
           return candidate if candidate.is_file() else None

Append the loader after the built-in DJX loader unless you intend to replace it entirely.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "TEMPLATE_LOADERS": [
           "next.pages.loaders.DjxTemplateLoader",
           "notes.loaders.MarkdownTemplateLoader",
       ],
   }

Verification
------------

Run ``uv run python manage.py check`` to validate import paths and loader subclasses.

Request the page in the browser or through ``NextClient`` and confirm the Markdown body renders through your layout chain.

See Also
--------

.. seealso::

   :doc:`/content/topics/pages` for body-source priority rules.
   :doc:`/content/topics/extending` for where template loaders fit among extension mechanisms.
   :doc:`/content/ref/pages` for ``TemplateLoader`` reference material.
