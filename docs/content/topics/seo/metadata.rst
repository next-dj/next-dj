.. _topics-metadata:

Page metadata
=============

Page metadata is the title, the description, and the rest of the ``<head>`` a page carries.
A ``page.py`` declares it as a module dict or as a dependency-injected callable, every ancestor ``page.py`` and the settings contribute their own layer, and ``{% metadata %}`` in the root layout renders the fold.
This page covers the two declaration forms, the title template, the merge order along the tree, the tag, and the ``meta`` patch verb that retitles the document after a partial update.

.. contents::
   :local:
   :depth: 2

Overview
--------

The head of a page is the one region a page fills through data rather than markup.
A layout offers a single ``{% template %}`` placeholder for the body, so a per-page ``<title>`` cannot come from a block override, and a ``@context`` value for it would still leave the description, the canonical link, and the social tags to hand-written template code.
Metadata is that data.
Each ``page.py`` on the path from the page root to the requested page contributes one segment, the settings contribute the outermost one, and the framework folds them into one immutable ``Metadata`` value the tag renders.

The keys of a segment are ``title``, ``description``, ``base``, ``site_name``, ``canonical``, ``alternates``, ``robots``, ``og``, ``twitter``, ``verification``, ``other``, and ``jsonld``.
Every key is optional, a text value may be a lazy translation, and an unknown key or a value of the wrong type is a ``PageMetadataShapeError`` naming the file and the key.
:doc:`social-and-canonical` covers the keys past the title and the description.

Static metadata
---------------

A module-level ``metadata`` dict is the static form.
It is readable without a request, so the system checks and every other offline reader see exactly what the page renders.

.. code-block:: python
   :caption: notes/pages/notes/page.py

   metadata = {
       "title": "Notes",
       "description": "Every note, newest first.",
   }

A static dict is inherited by every descendant page.
The segment at ``notes/pages/notes/`` therefore reaches ``/notes/`` and ``/notes/42/`` alike, and a descendant overrides a key by declaring it again.

Dynamic metadata
----------------

``@page.metadata`` registers a callable that builds the segment per request.
The callable takes dependency-injected parameters exactly like a ``@context`` callable, so captured URL segments, ``Depends``, ``DQuery``, and the published context values of the page are all available to it.

.. code-block:: python
   :caption: notes/pages/notes/[int:note_id]/page.py

   from notes.models import Note

   from next import page
   from next.pages import Metadata, MetadataDict
   from next.urls import DUrl

   @page.metadata
   def note_metadata(note_id: DUrl[int], parent: Metadata) -> MetadataDict:
       note = Note.objects.get(pk=note_id)
       return {
           "title": note.title,
           "description": note.summary or parent.description,
       }

A parameter annotated ``Metadata`` receives the fold of every segment before the callable, which is how a page reads the description an ancestor settled on before deciding whether to replace it.
A callable is local to its own page unless it is registered with ``@page.metadata(inherit=True)``, which runs it for every descendant page as well, ahead of the descendant's own segment.
One ``page.py`` declares one form, a dict or a callable, and a file carrying both raises ``PageMetadataConflictError``.

The callable runs once per render, on the first ``{% metadata %}`` read.
It shares the dependency cache of the request with ``render()`` and the ``@context`` callables, so a ``Depends`` value resolved by one of them is reused rather than resolved again.
A callable that raises :exc:`~django.http.Http404` turns the whole page into a 404, which is the right answer for a metadata lookup that finds no row.
A zone GET renders no head, and a ``render()`` returning an :class:`~django.http.HttpResponse` short-circuits the layout, so neither path runs the callable.

Title template
--------------

A title is a string or a dict with ``template``, ``default``, and ``absolute`` keys.
The template is a text carrying the placeholders ``{title}`` and ``{site_name}``, and nothing else is substituted.

.. code-block:: python
   :caption: notes/pages/page.py

   metadata = {
       "site_name": "Notes",
       "title": {"template": "{title} · {site_name}", "default": "Notes"},
   }

A ``template`` applies to the descendants of the page that declares it, never to that page's own title, and ``default`` is what the declaring page and any descendant without a title of its own render.
A template therefore requires a default, which ``next.E100`` enforces.
The template of the settings tier applies to every page, the root included, because the settings sit outside the tree.
``absolute`` opts one page out of every template in force above it, so a landing page renders ``"Notes"`` rather than ``"Notes · Notes"``.

The placeholders are validated after translation, so a ``gettext_lazy`` template stays lazy until the tag renders and a translation naming a placeholder outside the two is reported per language by ``next.E099``.
The text is never handed to ``str.format``, so a ``{title.__class__}`` attribute reach, an index, a conversion, or a format spec is refused rather than evaluated.
The ``%s`` placeholder of Next.js is plain text here, and ``next.W084`` warns about a template that never names ``{title}``.

Merge along the tree
--------------------

The chain of a page runs from the settings tier through every ancestor ``page.py`` to the page itself, root first.
The fold is shallow.
Each top-level key takes the value of the nearest segment that sets it, and a nested block such as ``og``, ``robots``, ``twitter``, ``alternates``, or ``verification`` is replaced whole rather than merged key by key.
A page that wants one extra Open Graph field therefore restates the block.

This is the opposite of inherited ``@context``.
When two ancestors publish the same inherited context key, the outermost ancestor wins, so a root value cannot be shadowed by a section below it.
Metadata folds the other way round, the segment nearest to the page wins, because a page knows its own title better than the root does.
The one key with more structure is the title, which walks the chain with the template in force as `Title template`_ describes.

The fold of a page is memoised on the page-metadata registry and rebuilt when a ``page.py`` is re-executed, a callable is registered, or the settings reload.
:doc:`/content/internals/page-discovery` traces the chain and the memo tokens.

Rendering in the layout
-----------------------

``{% metadata %}`` renders the fold of the page under way.
It takes no arguments, belongs in the ``<head>`` of the root ``layout.djx``, and renders the empty string in a template rendered outside a page, such as an error page or a plain Django view.

.. code-block:: jinja
   :caption: notes/pages/layout.djx

   <!doctype html>
   <html>
     <head>
       {% metadata %}
       {% collect_styles %}
     </head>
     <body>
       {% template %}
       {% collect_scripts %}
     </body>
   </html>

The tag emits one line per tag in a fixed order, the title, the description, the robots directives, the canonical link, the hreflang alternates, the verification tokens, the ``other`` entries, the Open Graph properties, the Twitter card, and the JSON-LD script.
A page whose fold sets none of them renders nothing, and ``next.W085`` reports a page that declares metadata while no layout in its chain carries the tag.
The tag is registered as a Django builtin, so no ``{% load %}`` is needed.

Partial updates
---------------

A partial update that changes what the page is about retitles the document through the ``meta`` verb.
``Patches.meta(title)`` ships the title the origin page would render, the chain template already applied, and the client assigns it to ``document.title`` without touching the markup.
A layer captures the title at open time and restores it when it closes, so a ``meta`` sent while a layer is open lasts as long as the layer, and a ``meta`` that follows ``layer_close()`` in the same envelope still wins.

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

``absolute=True`` skips the template, and a builder without an origin page sends the bare text.
The verb sits beside ``push_url()`` in the verbs table of :doc:`/content/topics/partial-rendering/reference`.

Common patterns
---------------

Site defaults in settings.
   Put the site name, the title template, the base origin, and the description every page falls back to in ``NEXT_FRAMEWORK["METADATA"]["DEFAULTS"]``, see :doc:`/content/ref/settings`.
   The examples keep their root layout in a ``DIRS`` root with no ``page.py`` beside it, and the settings tier is the place that reaches every page from there.

A section title in a segment page.
   A ``page.py`` next to a section ``layout.djx`` declares the section's ``title`` default and, through ``inherit=True`` on a callable, values that need the request.

Translated titles.
   Wrap the texts in :func:`~django.utils.translation.gettext_lazy`, and see :doc:`/content/howto/internationalize-routes` for the hreflang alternates that go with them.

See also
--------

.. seealso::

   :doc:`social-and-canonical` for the remaining keys and the tags they emit.
   :doc:`auditing` for the checks that read the static fold.
   :doc:`/content/howto/set-page-titles-and-seo-tags` for the end-to-end recipe.
   :doc:`/content/topics/context` for the inherited context rule this page contrasts with.
   :doc:`/content/ref/decorators` for ``@page.metadata`` and :doc:`/content/ref/template-tags` for ``{% metadata %}``.
