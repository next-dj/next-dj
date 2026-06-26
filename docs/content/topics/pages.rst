.. _topics-pages:

Pages
=====

A page is a directory under a configured page root that produces a body when its URL is requested.
This page covers the three ways a page produces that body, the priority rules between them, the ``render`` function contract, and how custom template loaders extend discovery beyond ``template.djx``.

.. contents::
   :local:
   :depth: 2

Overview
--------

The smallest page is a folder that contains a ``page.py`` and a sibling ``template.djx``.
A page module declares context functions, action handlers, and optional ``render`` or ``template`` attributes, and any number of ancestor ``layout.djx`` files wrap the resulting body.

Body Sources
------------

A page module can supply its body through these sources.

``render`` function on the page module.
   Highest priority.
   Receives :doc:`DI-resolved <dependency-injection>` arguments.
   Returns either a string body or any :class:`~django.http.HttpResponseBase` subclass, including :class:`~django.http.StreamingHttpResponse` and :class:`~django.http.FileResponse`.

``template`` module attribute.
   A plain string assigned at module level.
   Used as the page body when no ``render`` function exists, and checked before any template loader.

Registered template loaders.
   The ordered list under ``NEXT_FRAMEWORK["TEMPLATE_LOADERS"]``.
   ``DjxTemplateLoader`` is the default first entry and resolves the sibling ``template.djx``.
   Loaders run in declared order and the first one whose ``can_load`` returns ``True`` supplies the body.

A page directory may instead host only a sibling ``layout.djx``.
That directory has no body source, and the layout chain renders with an empty body slot.
See *Layout-only directories* under :doc:`layouts` for the wrapping rules, and note that :ref:`next.E012 <ref-system-checks>` does not fire when a sibling ``layout.djx`` is present.

See *Render Order* below for the per-request sequence and the ``render`` short-circuit.
Processor ordering and ``STRICT_CONTEXT`` behaviour are documented in :doc:`context` and :ref:`ref-settings`.
Multiple body sources trigger :ref:`next.W043 <ref-system-checks>`, see *Priority Resolution*.

Render Order
------------

The body source runs before the template context is built.
This is the authoritative ordering for the page render path.

1. The body source produces the body markup.
   A ``render`` function is called first with its :doc:`DI-resolved <dependency-injection>` arguments.
   When ``render`` returns an :class:`~django.http.HttpResponseBase` the response is sent verbatim and the remaining steps are skipped, so layouts and the static pipeline do not run.
   A ``template`` attribute or a template file supplies the body string directly.
2. The ``@context`` callables are collected and run to build the template variable namespace.
3. The body is composed through the ancestor layouts and rendered with that namespace.

A ``render`` function therefore cannot read a value that a ``@context`` callable would publish, because the callables have not run yet.

A partial-zone request takes a different path.
When the request targets named zones, the view returns a zone response before step 3, so the layout composition does not run.
See :doc:`/content/topics/partial-rendering/zones` for the zone-morph request.

The ``render`` Function
-----------------------

The ``render`` function takes any DI-resolved parameters the resolver can fill.
The most common shape is ``request`` plus captured URL parameters and marker-driven values.

.. code-block:: python
   :caption: notes/pages/reports/[int:report_id]/page.py

   from notes.models import Report
   from next.urls import DUrl

   def render(request, report_id: DUrl[int]) -> str:
       report = Report.objects.get(pk=report_id)
       return f"<section>Report {report.title}</section>"

The return value follows a strict contract.

String body.
   The framework composes the string through the ancestor layouts.
   Context processors, the static collector, and the ``page_rendered`` signal run exactly as for a ``template.djx`` page.

:class:`~django.http.HttpResponseBase` subclass.
   Returned verbatim to the client.
   Layout composition and static placeholder injection are skipped, which makes this the right shape for redirects, JSON APIs, streaming responses, and downloads.

Anything else.
   The framework raises ``TypeError`` naming the page module so the mistake surfaces during development.

Exceptions raised inside ``render`` propagate to the Django request stack unchanged.

The ``template`` Attribute
--------------------------

Assign a string to the module-level ``template`` attribute when the body is small enough to live next to the Python code.

.. code-block:: python
   :caption: notes/pages/health/page.py

   template = "<p>ok</p>"

A ``render`` function outranks this attribute.
When no ``render`` function exists the ``template`` attribute is consulted before any registered template loader, so a module-level ``template`` string wins over a sibling ``template.djx``.
Use it for trivial pages where a separate template file would be noise.

The ``template`` attribute has a matching ``PythonTemplateLoader``, documented in :doc:`/content/ref/pages`.

Template Files
--------------

A ``template.djx`` sibling is the default source.
The file contains the body without any HTML envelope.

.. code-block:: jinja
   :caption: notes/pages/template.djx

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

The decorator has two shapes.

Keyed single value.
   ``@context("name")`` registers the return value under that name.
   Templates reference it as ``{{ name }}``.

Unkeyed dict.
   ``@context`` on a function that returns a dict merges the dict into the template scope.
   Useful when several values share a dependency you only want to resolve once.
   The unkeyed form must return a mapping, and a return annotation that is not a mapping type reports :ref:`next.E029 <ref-system-checks>`.

The ``inherit_context=True`` flag works on both the keyed and the unkeyed-dict shapes.
It publishes the value to every descendant page rather than to the declaring page alone.
See :doc:`context` for that flag and the other ways to vary the decorator.

Including Values in the JS Context
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Pass ``serialize=True`` to include the return value in ``window.Next.context`` on the client side.
The value must be JSON-encodable by the active serializer.
See :ref:`Serialization for the Browser <topics-context-serialization>` for the accepted shapes and common pitfalls.
Pass ``serializer=`` with a ``JsContextSerializer`` instance to use a per-key serializer for that value instead of the global ``JS_CONTEXT_SERIALIZER`` setting.

.. code-block:: python
   :caption: page.py

   from next.pages import context

   @context("featured", serialize=True)
   def featured_payload() -> dict:
       return {"id": 1, "title": "Hello"}

.. code-block:: python
   :caption: unkeyed dict

   from next.pages import context

   @context
   def post_context(post: Post) -> dict[str, object]:
       return {
           "post": post,
           "comments": post.comment_set.all(),
       }

Custom Template Loaders
-----------------------

The sibling ``template.djx`` loader is one implementation of the ``next.pages.loaders.TemplateLoader`` contract.
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
           "next.pages.loaders.DjxTemplateLoader",
           "notes.loaders.MarkdownTemplateLoader",
       ],
   }

A user-provided ``NEXT_FRAMEWORK["TEMPLATE_LOADERS"]`` replaces the default list entirely.
Include ``DjxTemplateLoader`` explicitly when you still want sibling ``template.djx`` files to load.

Call ``next.pages.page.register_template(file_path, template_str)`` from an app's ``ready()`` to attach an in-process template string to a page path without authoring a loader class.
The method stores the supplied body verbatim, with no layout composition, so no ancestor ``layout.djx`` wraps it.
Pre-compose the body through the layout chain before registering it when wrapping is required.
See :doc:`/content/ref/pages` for the full ``Page`` surface.

Loader Contract
~~~~~~~~~~~~~~~~~

A loader sets one class attribute, implements two required methods, and may override one optional method.

``source_name``.
   Class attribute string used by the system check to name the loader in conflict warnings.

``can_load(file_path)``.
   Boolean check that decides whether the loader recognises this page.
   ``file_path`` is the absolute path to the ``page.py`` file.
   The loader inspects the sibling directory through ``file_path.parent`` to look for its source.

``load_template(file_path)``.
   Returns the body string or ``None`` for the same ``page.py`` path.
   The framework treats ``None`` as "did not match" and tries the next loader.

``source_path(file_path)``.
   Optional.
   Returns the backing file path for the cache invalidation hook.
   Edits to that file invalidate the composed template on the next request.
   The default returns ``None`` for non-file-based loaders.

Priority Resolution
-------------------

When more than one body source applies the framework picks the highest priority one and emits a warning for the redundancy.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Source
     - Resolution
   * - ``render`` function
     - Wins outright when the page module defines ``def render(...)``.
   * - ``template`` attribute
     - Used when no ``render`` function exists and the page module sets ``template = "..."``. Consulted before any template loader.
   * - Template loaders
     - Consulted only when neither of the above applies. Loaders run in ``TEMPLATE_LOADERS`` order and the first one whose ``can_load`` returns ``True`` supplies the body.

The template loaders have no fixed numbering between them.
``DjxTemplateLoader`` matches the sibling ``template.djx``, but a custom loader placed before it in ``TEMPLATE_LOADERS`` is consulted first and wins when both could load the same directory.

When a page directory declares more than one body source, :ref:`next.W043 <ref-system-checks>` reports it.
The highest-priority source is used and the others are never consulted.

Common Patterns
---------------

Pure Redirect Page
~~~~~~~~~~~~~~~~~~

A page that always redirects elsewhere uses a ``render`` function that returns ``HttpResponseRedirect``.

.. code-block:: python
   :caption: notes/pages/login/page.py

   from django.http import HttpResponseRedirect
   from next.urls import page_reverse

   def render(request) -> HttpResponseRedirect:
       return HttpResponseRedirect(page_reverse("auth/login"))

JSON Endpoint
~~~~~~~~~~~~~

Return ``JsonResponse`` from ``render`` for a JSON endpoint that still benefits from URL naming.

.. code-block:: python
   :caption: notes/pages/api/health/page.py

   from django.http import JsonResponse

   def render(request) -> JsonResponse:
       return JsonResponse({"status": "ok"})

Streaming Response
~~~~~~~~~~~~~~~~~~

Reach for ``StreamingHttpResponse`` when the body is produced incrementally, such as Server Sent Events or a large export.

.. code-block:: python
   :caption: notes/pages/notes/[id]/stream/page.py

   from collections.abc import Iterator

   from django.http import StreamingHttpResponse
   from notes.models import Note
   from notes.providers import DNote

   def event_stream(note_id: int) -> Iterator[bytes]:
       ...  # see examples/live-polls for a worked generator

   def render(note: DNote[Note]) -> StreamingHttpResponse:
       return StreamingHttpResponse(
           event_stream(note.pk),
           content_type="text/event-stream",
           headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
       )

``DNote`` is a custom DI provider that resolves the note from the captured URL segment, defined in ``notes/providers.py``.
A synchronous generator works under WSGI and the development server.
An ASGI deployment can yield from an async generator instead.
See ``examples/live-polls`` for a worked SSE broker.

Markdown Blog Post
~~~~~~~~~~~~~~~~~~

Register a ``MarkdownTemplateLoader`` in ``NEXT_FRAMEWORK["TEMPLATE_LOADERS"]`` and drop a ``template.md`` next to ``page.py`` instead of a ``template.djx``.
The loader renders the Markdown to a body string, and that body flows through the ancestor layout chain like any other source.
The page module still supplies context functions and action handlers as usual.

The loader output is substituted into the composed layout and rendered in one Django template pass.
Any ``{{ ... }}`` or ``{% ... %}`` token inside the Markdown source is evaluated.
Wrap untrusted prose in ``{% verbatim %}`` blocks inside the loader, or escape the braces before returning the body, when authors should not be able to invoke template tags.

.. seealso::

   The *Custom Template Loaders* section above for the ``template.md`` loader, and ``examples/markdown-blog`` for a working setup.

System Checks
-------------

The pages subsystem contributes Django system checks. The ``check_page_functions`` check inspects every ``page.py`` and reports the following.

``next.E012``.
   The page module has neither a ``render`` function nor a ``template`` attribute, no registered loader can produce a body, and no sibling ``layout.djx`` wraps it.

``next.E013``.
   The page module defines a ``render`` attribute that is not callable.

``next.W043``.
   More than one body source is declared in the same directory, see *Priority Resolution*.

The ``check_context_functions`` check inspects every ``page.py`` for keyless ``@context`` callables.

``next.E029``.
   A keyless ``@context`` callable has a return annotation that is not a mapping type.

Run them through ``uv run python manage.py check``.

See Also
--------

.. seealso::

   :doc:`layouts` for how the layout chain wraps the page body.
   :doc:`context` for ``@context`` patterns and inheritance.
   :doc:`/content/internals/page-discovery` for the discovery pipeline.
   :doc:`/content/ref/pages` for the public API.
