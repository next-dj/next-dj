.. _topics-pages:

Pages
=====

A page is a directory under a configured page root that produces a body when its URL is requested.
This page covers the three ways a page can produce that body, the priority rules between them, the contract for ``render`` functions, and how custom template loaders extend the discovery beyond ``template.djx``.

.. contents::
   :local:
   :depth: 2

Overview
--------

The smallest page is a folder that contains a ``page.py`` and a sibling ``template.djx``.
The framework calls every ``@context`` function declared in ``page.py``, then renders ``template.djx`` with the resulting variables, then wraps the result in the closest ancestor ``layout.djx``.

Pages share four pluggable parts.

Page module.
   A ``page.py`` file that declares context functions, action handlers, and optional ``render`` or ``template`` attributes.

Body source.
   A function, a string, or a template file that produces the page body.

Context functions.
   Callables decorated with ``@context("key")`` that publish values into the template scope.

Layout chain.
   Zero or more ``layout.djx`` files in ancestor directories that wrap the body.

Body Sources
------------

A page module can supply its body through three mechanisms.

``render`` function on the page module.
   Highest priority.
   Receives DI-resolved arguments and returns either a string body or any ``HttpResponse`` subclass.

``template`` module attribute.
   A plain string assigned at module level.
   Used as the page body when no ``render`` function exists.

Sibling ``template.djx`` file.
   The default source for most pages.
   Loaded through the ``DjxTemplateLoader`` and composed with the layout chain.

The framework runs context processors, the static collector, and the ``page_rendered`` signal once per request regardless of which source produced the body.
When two sources are declared at the same level the lower priority one is silently dropped and ``uv run python manage.py check`` emits a warning.

The render Function
-------------------

The ``render`` function takes any DI-resolved parameters the resolver can fill.
The most common shape is ``request`` plus zero or more values from context functions.

.. code-block:: python
   :caption: notes/routes/reports/page.py

   from django.http import HttpResponse, HttpResponseRedirect

   from next.pages import context


   @context("today")
   def today() -> str:
       return "2026-05-15"


   def render(request, today: str) -> str:
       return f"<section>Report for {today}</section>"

The return value follows a strict contract.

String body.
   The framework composes the string through the ancestor layouts.
   Context processors, the static collector, and the ``page_rendered`` signal run exactly as for a ``template.djx`` page.

Any ``HttpResponse`` subclass.
   Returned verbatim to the client.
   Layouts and the static pipeline are skipped, which makes this the right shape for redirects, JSON APIs, streaming responses, and downloads.

Anything else.
   The framework raises ``TypeError`` naming the page module so the mistake surfaces during development.

Exceptions raised inside ``render`` propagate to the Django request stack unchanged.

The template Attribute
----------------------

Assign a string to the module-level ``template`` attribute when the body is small enough to live next to the Python code.

.. code-block:: python
   :caption: notes/routes/health/page.py

   template = "<p>ok</p>"

This source has the lowest priority of the three.
Use it for trivial pages where a separate template file would be noise.

Template Files
--------------

A ``template.djx`` sibling is the default source.
The file contains the body without any HTML envelope.

.. code-block:: jinja
   :caption: notes/routes/template.djx

   <ul>
     {% for note in notes %}
       <li>{{ note.title }}</li>
     {% endfor %}
   </ul>

The layout chain wraps this body with the ancestor ``layout.djx`` files.
See :doc:`layouts` for the full composition rules.

Context Functions
-----------------

A ``@context`` decorator publishes one or more values into the template scope.

.. code-block:: python
   :caption: keyed single value

   from next.pages import context


   @context("notes")
   def all_notes() -> list:
       return list(Note.objects.all())

The framework calls the function at request time.
The return value lands in the template under the configured key.

The decorator has three shapes.

Keyed single value.
   ``@context("name")`` registers the return value under that name.
   Templates reference it as ``{{ name }}``.

Unkeyed dict.
   ``@context`` on a function that returns a dict merges the dict into the template scope.
   Useful when several values share a dependency you only want to resolve once.

Inherited context.
   ``@context("name", inherit_context=True)`` makes the value visible to every descendant page, not only to the page that declares the function.

.. code-block:: python
   :caption: unkeyed dict

   from next.pages import context


   @context
   def post_context(post: Post) -> dict[str, object]:
       return {
           "post": post,
           "comments": post.comment_set.all(),
       }

The dict form runs the dependency once.
Two ``@context("post")`` and ``@context("comments")`` would each invoke the resolver and possibly hit the database twice.

Custom Template Loaders
-----------------------

The sibling ``template.djx`` discovery is one implementation of the ``next.pages.loaders.TemplateLoader`` contract.
Register additional loaders in ``NEXT_FRAMEWORK["TEMPLATE_LOADERS"]`` to support other file formats.

.. code-block:: python
   :caption: notes/loaders.py

   from pathlib import Path

   import markdown

   from next.pages.loaders import TemplateLoader


   class MarkdownTemplateLoader(TemplateLoader):
       source_name = "template.md"

       def can_load(self, file_path: Path) -> bool:
           return (file_path.parent / "template.md").exists()

       def load_template(self, file_path: Path) -> str | None:
           source = (file_path.parent / "template.md").read_text(encoding="utf-8")
           return markdown.markdown(source, extensions=["fenced_code"])

       def source_path(self, file_path: Path) -> Path | None:
           candidate = file_path.parent / "template.md"
           return candidate if candidate.exists() else None

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "TEMPLATE_LOADERS": [
           "notes.loaders.MarkdownTemplateLoader",
           "next.pages.loaders.DjxTemplateLoader",
       ],
   }

User-provided ``TEMPLATE_LOADERS`` replaces the default list entirely.
Include ``DjxTemplateLoader`` explicitly when you still want sibling ``template.djx`` files to load.

Loader Contract
~~~~~~~~~~~~~~~

A loader implements four methods.

``source_name``.
   String used by the system check to name the loader in conflict warnings.

``can_load(file_path)``.
   Boolean check that decides whether the loader recognises a directory.

``load_template(file_path)``.
   Returns the body string or ``None``.
   The framework treats ``None`` as "did not match" and tries the next loader.

``source_path(file_path)``.
   Returns the backing file path for the cache invalidation hook.
   Edits to that file invalidate the composed template on the next request.

Priority Resolution
-------------------

When more than one body source applies the framework picks the highest priority one and emits a warning for the redundancy.

.. list-table::
   :header-rows: 1
   :widths: 15 35 50

   * - Priority
     - Source
     - Used when
   * - 1
     - ``render`` function
     - The page module defines ``def render(...)``.
   * - 2
     - ``template`` attribute
     - The page module sets ``template = "..."``.
   * - 3
     - Sibling ``template.djx``
     - The directory contains a file named ``template.djx``.
   * - 4
     - Custom loader
     - A loader in ``TEMPLATE_LOADERS`` matches the directory.

Two sources at the same priority level are also flagged.
The check ``next.W043`` points at the file when this happens.

Common Patterns
---------------

Pure Redirect Page
~~~~~~~~~~~~~~~~~~

A page that always redirects elsewhere uses a ``render`` function that returns ``HttpResponseRedirect``.

.. code-block:: python
   :caption: notes/routes/login/page.py

   from django.http import HttpResponseRedirect

   from next.urls.reverse import page_reverse


   def render(request) -> HttpResponseRedirect:
       return HttpResponseRedirect(page_reverse("auth/login"))

JSON Endpoint
~~~~~~~~~~~~~

Return ``JsonResponse`` from ``render`` for a JSON endpoint that still benefits from URL naming.

.. code-block:: python
   :caption: notes/routes/api/health/page.py

   from django.http import JsonResponse


   def render(request) -> JsonResponse:
       return JsonResponse({"status": "ok"})

Markdown Blog Post
~~~~~~~~~~~~~~~~~~

Use a custom loader that reads ``template.md`` and converts it to HTML on demand.
The page body is whatever the loader returns and goes through the standard layout chain.
See ``examples/markdown-blog`` for a working setup.

System Checks
-------------

The pages subsystem contributes Django system checks.

- ``check_page_functions`` warns when a page directory has neither a body source nor a render function.
- ``next.W043`` warns when more than one body source exists in the same directory.

Run them through ``uv run python manage.py check``.

See Also
--------

.. seealso::

   :doc:`layouts` for how the layout chain wraps the page body.
   :doc:`context` for ``@context`` patterns and inheritance.
   :doc:`/content/internals/page-discovery` for the discovery pipeline.
   :doc:`/content/ref/pages` for the public API.
