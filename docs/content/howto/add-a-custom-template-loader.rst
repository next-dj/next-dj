.. _howto-custom-template-loader:

Add a custom template loader
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
That string names the loader in the ``next.W043`` body-source warning, which fires whenever one page declares more than one body source.
Two loaders matching the same directory is one way to reach it, and a single loader alongside a ``template`` attribute on ``page.py`` or a ``render`` function is another.
A loader whose ``source_name`` is empty is left out of that report.

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

``source_path`` names the file whose modification time the composed-template cache tracks, so a loader backed by a sibling file overrides it and one backed by anything else keeps the ``None`` default.

The loaded body is rendered as a Django template after composition with the layout chain.
A ``{{ ... }}`` or ``{% ... %}`` token inside ``template.md`` is evaluated by the template engine before the user sees the page.
Wrap untrusted Markdown in ``{% verbatim %}{% endverbatim %}`` inside ``load_template``, or escape the braces, when the source comes from an author who should not run template tags.

Append the loader after the built-in DJX loader unless you intend to replace it entirely.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "TEMPLATE_LOADERS": [
           "next.pages.loaders.DjxTemplateLoader",
           "notes.loaders.MarkdownTemplateLoader",
       ],
   }

Loader order and priority
~~~~~~~~~~~~~~~~~~~~~~~~~

The chain is consulted in the order the dotted paths appear in ``TEMPLATE_LOADERS``.
The framework calls ``can_load`` on each loader in turn and stops at the first that answers ``True``.
That loader owns the body, and a ``load_template`` that then returns ``None`` yields an empty body instead of passing the page down the chain.
With the settings above, a page directory holding both ``template.djx`` and ``template.md`` renders the DJX file and never reads the Markdown.

The chain is only the third body source.
``Page`` resolves a page body in this order.

1. A ``render`` function in ``page.py``, which returns a body string or an ``HttpResponseBase`` that short-circuits layout composition and the static pipeline.
2. A module-level ``template`` attribute on ``page.py``, used when its value is a string.
3. The first registered loader whose ``can_load`` answers ``True``.
4. An empty body, so an ancestor ``layout.djx`` still renders with an empty slot.

Registering a loader never overrides a ``render`` function or a ``template`` attribute on the same page.

Constructor and entry rules
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Each loader class is instantiated with no arguments, so a loader that needs configuration reads it from class attributes or from settings rather than from constructor parameters.
Entries the framework cannot use are skipped at build time with a debug-level log rather than an exception.

- A non-string entry never reaches the import step.
- An entry whose dotted path cannot be imported is dropped.
- An entry that does not name a ``TemplateLoader`` subclass is dropped.
- A class already present in the chain is dropped, because the chain deduplicates by class and the same dotted path listed twice yields one instance.

A skipped entry leaves the chain shorter than the list suggests and pages fall through to the next loader or to an empty body.
``manage.py check`` is the loud report for the same misconfigurations, ``next.E042`` for a non-string entry and ``next.E043`` for a path that cannot be imported or a class that is not a ``TemplateLoader`` subclass.
A duplicate entry is not reported by a check, so read the list itself when a loader you registered never runs.

Verification
------------

Run ``uv run python manage.py check`` to validate import paths and loader subclasses.

Request the page in the browser or through ``NextClient`` and confirm the Markdown body renders through your layout chain.

See also
--------

.. seealso::

   :doc:`/content/topics/pages` for body-source priority rules.
   :doc:`/content/topics/extending` for where template loaders fit among extension mechanisms.
   :doc:`/content/ref/pages` for ``TemplateLoader`` reference material.
