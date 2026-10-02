.. _topics-metadata:

Page metadata
=============

Page metadata is the title, the description, and the rest of the ``<head>`` a page carries.
A ``page.py`` declares it as a module dict or as a dependency-injected callable, every ancestor ``page.py`` and the settings contribute their own layer, and ``{% metadata %}`` in the root layout renders the result.
This page covers the two declaration forms, the title template, the tag, and the ``meta`` patch verb, and :doc:`merge` covers how the layers combine.
Metadata belongs to the pages the file router serves, and a plain Django view renders none, see :doc:`quickstart` for the whole setup.

.. contents::
   :local:
   :depth: 2

Overview
--------

The head of a page is the one region a page fills through data rather than markup.
A layout offers a single ``{% template %}`` placeholder for the body, so a per-page ``<title>`` cannot come from a block override, and a ``@context`` value for it would still leave the description, the canonical link, and the social tags to hand-written template code.
Metadata is that data.
Each ``page.py`` on the path from the page root to the requested page contributes one layer, ``NEXT_FRAMEWORK["METADATA"]["DEFAULTS"]`` contributes the outermost one, and the framework merges them into one immutable ``Metadata`` value the tag renders.

The keys of a layer are ``title``, ``description``, ``site_name``, ``canonical``, ``alternates``, ``robots``, ``og``, ``twitter``, ``verification``, ``keywords``, ``icons``, ``manifest``, ``links``, ``viewport``, ``theme_color``, ``color_scheme``, ``other``, ``properties``, ``jsonld``, and, on a page alone, ``breadcrumb``.
Every key is optional, a text value may be a lazy translation, and an unknown key or a value of the wrong type is a ``PageMetadataShapeError`` naming the file and the key path.
The origin and the name of the site are no metadata keys, they come from ``NEXT_FRAMEWORK["SITE"]``, see :doc:`site`.

Reserved module names
---------------------

``metadata``, ``cache``, and ``headers`` are names the framework reads off a ``page.py``, beside ``render`` and ``template``.
A module attribute of that name is read as the declaration, whatever it was meant to be, so ``from importlib import metadata`` in a ``page.py`` hands the framework a module where a dict belongs, and ``manage.py check`` reports it.
Import such a module under another name.
:doc:`/content/topics/caching` covers ``cache`` and ``headers``.

Static metadata
---------------

A module-level ``metadata`` dict is the static form.
It is readable without a request, so the system checks, the sitemap, and every other offline reader see exactly what the page renders.

.. code-block:: python
   :caption: notes/pages/notes/page.py

   from next.pages import MetadataDict

   metadata: MetadataDict = {
       "title": "Notes",
       "description": "Every note, newest first.",
       "canonical": True,
   }

A static dict reaches every descendant page as well, so the dict at ``notes/pages/notes/`` applies to ``/notes/`` and ``/notes/42/`` alike, and a descendant overrides a key by declaring it again.
The ``MetadataDict`` annotation is optional and lets a type checker read every key.

Dynamic metadata
----------------

``@page.metadata`` registers a callable that builds the metadata per request.
The callable takes dependency-injected parameters exactly like a ``@context`` callable, so a context value the page already published, a captured URL segment, ``Depends``, and ``DQuery`` are all available to it.
Reading the context value by its parameter name is the idiom, because the row a ``@context`` callable fetched for the body is the row the head describes, and it is fetched once.

.. code-block:: python
   :caption: notes/pages/notes/[int:note_id]/page.py

   from django.shortcuts import get_object_or_404
   from notes.models import Note

   from next import context, page
   from next.pages import MetadataDict
   from next.urls import DUrl

   @context("note")
   def note(note_id: DUrl[int]) -> Note:
       return get_object_or_404(Note, pk=note_id)

   @page.metadata
   def note_metadata(note: Note) -> MetadataDict:
       return {"title": note.title, "description": note.summary}

A value of ``None`` means unset, so ``"description": note.summary`` with an empty summary keeps the description an ancestor declared rather than erasing it.
:doc:`merge` covers how the returned dict merges over the ancestors, and ``RESET`` for the case where an inherited value must go.

A callable is local to its own page unless it is registered with ``@page.metadata(inherit=True)``, which runs it for every descendant page as well, ahead of the descendant's own metadata.
One ``page.py`` declares one form, a dict or a single callable, and a file carrying both or two callables raises ``PageMetadataConflictError``.
The callable returns a mapping and never receives the ancestors' metadata as a parameter, since the framework merges the layers itself.

The callable runs once per template render, on the first ``{% metadata %}`` read, against the context the tag renders in, so a value a ``{% with %}`` block or a zone override puts in scope reaches it.
It shares the dependency cache of the render with ``render()`` and the ``@context`` callables, so a ``Depends`` value resolved by one of them is reused.
A callable that raises :exc:`~django.http.Http404` turns the whole page into a 404, which is what a metadata lookup that finds no row should answer.
:exc:`~django.core.exceptions.PermissionDenied` passes through the same way.
Any other exception, or a returned value the schema refuses, leaves that callable out of the merge, and the page renders what the rest of the chain declares.
The failure is logged once per file and exception type, and under ``DEBUG`` or ``STRICT_LOADING`` it raises with a note naming the callable and its file.
A chain the schema refuses as a whole, such as a file declaring both forms, renders the ``DEFAULTS`` alone, logged the same way, and ``manage.py check`` names the cause.
A JSON-LD node a callable builds with a value JSON cannot hold, such as ``nan`` or a set, is left out of the graph the same way.
A zone GET renders no head, and a ``render()`` returning an :class:`~django.http.HttpResponse` short-circuits the layout, so neither path runs the callable.

Title template
--------------

A title is a string or a dict with ``template``, ``default``, and ``absolute`` keys.
The template is a text carrying the placeholders ``{title}`` and ``{site_name}``, and nothing else is substituted.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "SITE": {"URL": "https://notes.example", "NAME": "Notes"},
       "METADATA": {
           "DEFAULTS": {"title": {"template": "{title} · {site_name}", "default": "Notes"}},
       },
   }

``{site_name}`` reads the merged ``site_name`` key, which falls back to ``SITE["NAME"]``.
A ``template`` applies to the descendants of the page that declares it, never to that page's own title, and ``default`` is what the declaring page and any descendant without a title of its own render.
A template therefore requires a default, and ``manage.py check`` reports one without.
The template of ``DEFAULTS`` applies to every page, the root included, because the settings sit outside the tree.
``absolute`` opts one page out of every template in force above it, so a landing page renders ``"Notes"`` rather than ``"Notes · Notes"``.

The placeholders are validated after translation, so a ``gettext_lazy`` template stays lazy until the tag renders and a translation naming a placeholder outside the two is reported per language.
The text is never handed to ``str.format``, so an attribute reach, an index, a conversion, or a format spec is refused rather than evaluated.
Each substituted part is escaped once, so a title built with :func:`~django.utils.safestring.mark_safe` keeps its markup and a plain one never renders a tag.

Rendering in the layout
-----------------------

``{% metadata %}`` renders the head of the page under way.
It takes no arguments, belongs in the ``<head>`` of the root ``layout.djx``, and renders the empty string in a template rendered outside a page, such as an error page or a plain Django view.

.. code-block:: jinja
   :caption: notes/pages/layout.djx

   <!doctype html>
   <html lang="en">
     <head>
       <meta charset="utf-8">
       {% metadata %}
       {% collect_styles %}
       {% collect_head %}
     </head>
     <body>
       {% template %}
       {% collect_scripts %}
     </body>
   </html>

The tag emits one line per head tag in a fixed order, which the key-to-tag table in :doc:`/content/ref/metadata` lists.
The markup comes from the renderer ``NEXT_FRAMEWORK["METADATA"]["RENDERER"]`` names, and a project that needs a tag of its own subclasses the default there, see :doc:`/content/ref/metadata`.
The system checks report a page that declares metadata while nothing its composition renders carries the tag.
The tag is registered as a Django builtin, so no ``{% load %}`` is needed.

Partial updates
---------------

A partial update that changes what the page is about syncs the head through the ``meta`` verb.
``Patches.meta(value)`` takes a title text or a ``MetadataDict``, merges it over the metadata of the origin page as that page's own, and ships the four tags whose value depends on the URL, the title, the description, the canonical link, and the robots meta.
The client upserts each of them, and a ``null`` removes the tag.
Open Graph, Twitter, and JSON-LD stay as first rendered, because the crawlers that read them run no JavaScript.

.. code-block:: python
   :caption: notes/pages/notes/[int:note_id]/page.py

   from django.http import HttpRequest
   from notes.models import Note

   import next.forms
   from next.partial import Patches

   class RenameNoteForm(next.forms.ModelForm):
       class Meta:
           model = Note
           fields = ["title"]

       def on_valid(self, request: HttpRequest):
           note = self.save()
           return Patches(request).morph_zone("note").meta(note.title).response()

An inherited ancestor callable runs as the render would, so ``meta()`` first runs the ``render()`` guard of the origin page, raising ``ForeignPageNotAuthorizedError`` on a denial.
An inherited callable that raises anything else drops the ``meta`` operation alone, logged once, so the rest of the patch still applies, and under ``DEBUG`` it raises.
A value the action passes to ``meta()`` that the schema refuses raises ``PageMetadataShapeError`` every time, since it is a bug in the action itself.
Custom tags a ``MetadataRenderer`` adds are not synced, only the four tags above travel in the envelope.
A builder without an origin page merges the value over ``DEFAULTS`` alone and sends no ``canonical`` key when the canonical names the page itself, so the client keeps the tag it has rather than pointing it at the action endpoint.
A ``push_url()`` or ``replace_url()`` queued before ``meta()`` names the address the canonical is built from, so the tag follows the URL the same envelope moves the browser to.
The tags belong to the page whose envelope carried them, so a ``meta`` for a URL that is neither the page nor an open layer is dropped, see :doc:`/content/topics/partial-rendering/layers`.
The verb sits beside ``push_url()`` in the verbs table of :doc:`/content/topics/partial-rendering/reference`.

See also
--------

.. seealso::

   :doc:`merge` for the merge strategy of every key.
   :doc:`social-and-canonical` and :doc:`head-tags` for the tags each key emits.
   :doc:`auditing` for the checks and the ``showmetadata`` command.
   :doc:`quickstart` for the end-to-end setup.
   :doc:`/content/ref/decorators` for ``@page.metadata`` and :doc:`/content/ref/template-tags` for ``{% metadata %}``.
