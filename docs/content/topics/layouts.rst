.. _topics-layouts:

Layouts
=======

A layout is a ``layout.djx`` file in any directory under a page root.
The framework wraps every page below it through string substitution into a ``{% template %}`` placeholder.
A layout carries markup alone, and the logic and the access of a segment live in its ``page.py``.

.. contents::
   :local:
   :depth: 2

Overview
--------

A page is wrapped by every ``layout.djx`` between the page directory and the page root.
The closest layout is the innermost wrapper.
The root layout is the outermost wrapper.
The page body is substituted into a ``{% template %}`` placeholder that each layout must contain.

Layouts compose through string substitution, not through Django template inheritance.
There is no ``{% extends %}`` directive and no parent template identifier.
The composition is purely structural, driven by directory placement.

Layout discovery
----------------

The framework walks every ancestor directory upward from the page directory, bounded at 64 levels.
Every directory along the way is inspected for a ``layout.djx`` file.
The collected layouts are composed in order, with the closest layout wrapping the page body and the farthest layout wrapping everything else.

The walk does not stop at the page root.
A ``layout.djx`` in a directory above the page root, such as the application package directory, also joins the chain and wraps every page below it.
Keep layout files inside the page tree unless that wrapping is intended.
A layout above the page tree root wraps the page but serves no co-located assets, and :ref:`next.W135 <ref-system-checks>` reports a ``layout.css`` or ``layout.js`` there.
A ``{% layout root %}`` marker in a layout ends the walk, see :ref:`topics-layouts-root`.

.. code-block:: text
   :caption: layout discovery

   notes/pages/
     layout.djx                outer wrapper for every page
     notes/
       layout.djx              inner wrapper for /notes/* pages
       [id]/
         layout.djx            innermost wrapper for /notes/<id>/* pages
         template.djx
         page.py

A request to ``/notes/42/`` composes three layouts.
The innermost wraps the page body.
The middle layout wraps the result.
The outermost layout wraps the result again.

The cost of that composition follows the depth of the page directory, which the bounded walk caps, not the number of pages in the project.
The composed template is kept per page and reused by later requests, under a snapshot of every file the composition read, each ancestor ``layout.djx`` among them, and of the directories the walk visited.
In a process that watches template edits a layout that is edited, created, or deleted therefore reaches the next render rather than the next restart.
See :doc:`/content/internals/page-discovery` for the snapshot and the staleness check that reads it.

A layout is no body
~~~~~~~~~~~~~~~~~~~

A ``layout.djx`` wraps the body of a page and is no body itself.
A ``page.py`` with no ``render``, no ``template``, and no template a loader finds routes no URL, whatever ``layout.djx`` sits beside it or above it.
:ref:`next.E012 <ref-system-checks>` reports such a file when it passes nothing to the pages below it and its directory declares no action, and :ref:`next.E166 <ref-system-checks>` when it declares access without ``inherit=True`` and no action of its directory answers to that rule.
A ``layout.djx`` in a directory with no page and no action at any depth wraps nothing, and :ref:`next.W128 <ref-system-checks>` names the outermost such directory.
A landing route whose whole markup belongs in the layout shell gets a ``template.djx``, and an empty one is enough.

.. _topics-layouts-root:

Root layout
-----------

A page under a section such as an embeddable widget or a print view needs its own document shell, and the outer site shell must not wrap it.
A layout that opens with ``{% layout root %}`` becomes the root of its chain.
Composition stops at that layout, and no layout further out wraps the page.

.. code-block:: text
   :caption: a section with its own shell

   notes/pages/
     layout.djx                site shell
     page.py
     template.djx
     embed/
       layout.djx              root layout, starts with {% layout root %}
       [id]/
         page.py
         template.djx

.. code-block:: jinja
   :caption: notes/pages/embed/layout.djx

   {% layout root %}
   <!doctype html>
   <html>
     <head>
       {% metadata %}
       {% collect_styles %}
     </head>
     <body class="embed">
       {% template %}
       {% collect_scripts %}
     </body>
   </html>

A request to ``/embed/42/`` composes the page body and ``embed/layout.djx``, and ``notes/pages/layout.djx`` stays out of the result.
Pages elsewhere in the tree compose as before.

The marker rule
~~~~~~~~~~~~~~~

Only ``{% load %}`` tags, ``{% comment %}`` blocks, ``{# #}`` comments, ASCII whitespace, and a leading byte order mark may stand before the marker.
Any other tag or text before it, or a second marker in the same file, is ``next.E168``, and the framework ignores that marker.
A marker written under ``{% verbatim %}`` is text to the template lexer and counts as no marker.
The tag renders nothing and accepts the single argument ``root``, see :doc:`/content/ref/template-tags`.

What the cut removes
~~~~~~~~~~~~~~~~~~~~

The cut removes every layout further out than the root layout from the chain of the page.
That covers the ancestor ``layout.djx`` files inside the page tree and the layouts above the page tree root.
It covers the root layouts collected from ``DIRS`` too, and when the layout of one ``DIRS`` root carries a marker, it cuts the layouts of the ``DIRS`` roots that come after it.

A root layout writes the whole document shell itself.
The outer shell no longer runs for the page, so the root layout carries its own ``{% metadata %}``, ``{% collect_styles %}``, and ``{% collect_scripts %}`` tags.
A page under the root renders no title, no stylesheet, and no script without them.

The cut touches the layout chain and nothing else.

Metadata.
   The metadata of the ancestor ``page.py`` files still applies, so a ``title_template`` declared above the root still formats the title.
   A title that the page itself declares overrides it, as it does without the marker, see :doc:`seo/metadata`.

Access.
   The access declared with ``inherit=True`` in an ancestor ``page.py`` still guards the pages below the root layout, see :doc:`access`.

Inherited context.
   The ``inherit_context=True`` callables of ancestor ``page.py`` files still run and publish their values.
   The framework computes them even where the layout that reads them was cut away, and the root layout and the page read them as usual.

Zones.
   A zone written in a cut layout is not part of the composed template of a page under the root.
   A partial request from such a page that names that zone gets the unknown zone answer.

Static assets.
   The co-located ``layout.css`` and ``layout.js`` of a cut layout are not collected for the page, because discovery reads the same cut chain composition does.
   Each layout is named after the deepest page tree root that holds it, so a layout of a ``DIRS`` root keeps its own key on a page of an application tree.
   The root layout keeps its own co-located assets, and so do the layouts between the page and the root.

The root layout in a ``DIRS`` directory
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A page composes its own ancestor layouts first and the ``layout.djx`` files of the ``DIRS`` roots after them, in the order of the settings.
A marker in one of those ``DIRS`` layouts ends the chain there, so the ``DIRS`` layouts that come after it wrap no page.
Ancestor layouts that stand nearer to the page than the marker still wrap it.

Checks
~~~~~~

:ref:`next.E168 <ref-system-checks>` reports a marker that stands in the wrong place or repeats.
:ref:`next.W133 <ref-system-checks>` reports a layout that writes ``<html`` without a working marker while a layout further out exists, since the page would carry two documents.
:ref:`next.W134 <ref-system-checks>` reports a marker in a ``template.djx`` or a component template, where the tag has no effect.
:ref:`next.W135 <ref-system-checks>` reports co-located assets on a layout above the page tree root.
See :doc:`/content/howto/give-a-section-its-own-document-shell` for the full recipe.

Layout template contract
------------------------

Every layout must contain a ``{% template %}`` placeholder where the body of the wrapped content is substituted.
The tag takes no arguments and rejects any with a ``TemplateSyntaxError``.
Whitespace inside the braces is free, so ``{%  template  %}`` and a tag broken across lines are read as the same placeholder.
Composition fills the first placeholder a layout carries, so keep exactly one per file.

.. code-block:: jinja
   :caption: notes/pages/layout.djx

   <!doctype html>
   <html>
     <head>
       {% metadata %}
       {% collect_styles %}
     </head>
     <body>
       <header>{{ site_name }}</header>
       <main>
         {% template %}
       </main>
       {% collect_scripts %}
     </body>
   </html>

Without the placeholder that layout is skipped during composition, so its own markup vanishes from the output while the page body and every inner layout still render, with no error raised.
The ``check_layout_templates`` system check emits ``next.W001`` for a ``layout.djx`` sitting next to a discovered page when it carries no placeholder, and ``next.W078`` when it carries more than one.
The check walks every layout of the chain of every page, so both codes surface for a layout in a folder with no page of its own, in a ``DIRS`` root, and above the page tree root, under any application.
A layout that a root layout cuts away from every chain is judged too, because the check reads the whole ancestry and ignores the cut.

Layouts can declare layout-level CSS and JS through the static collector tags shown above.
The tags also live in inner layouts when you want a section-scoped style sheet.
``{% metadata %}`` renders the title and the rest of the head from the metadata the pages declare, so no layout writes a ``<title>`` of its own, see :doc:`seo/metadata`.

Fallback for an unfilled placeholder
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The paired form ``{% #template %}...{% /template %}`` marks the same hole and carries a body that renders only where composition never reached.
A layout rendered on its own, outside the chain that would fill its hole, shows that body instead of an empty gap.
Write the fallback where a layout doubles as a standalone template, such as a shell a plain Django view renders, and keep the single form everywhere else.

.. _topics-layouts-segment-files:

layout.djx beside page.py
-------------------------

A ``layout.djx`` carries markup alone.
The logic of a segment lives in the ``page.py`` of its directory, and so does the access of the directory and every page below it, declared with ``inherit=True``, see :doc:`access`.
A layout markup reads every value of the page context, because the whole chain renders from one context.

.. _topics-layouts-inherited-context:

Inherited context from a segment page.py
----------------------------------------

A segment directory that holds a ``page.py`` publishes context to every page below it through ``inherit_context=True``.

.. code-block:: python
   :caption: notes/pages/page.py

   from notes.models import Note

   from next import context

   @context("site_name", inherit_context=True)
   def site_name() -> str:
       return "Notes"

   @context("note_count", inherit_context=True)
   def note_count() -> int:
       return Note.objects.count()

The ``inherit_context=True`` flag publishes the value to every descendant page, whereas without the flag the value is available only when ``notes/pages/page.py`` itself handles the request.
The chain follows the ancestor directories on disk, not the URL, so a tree that ``pages()`` places under a prefix of another tree inherits no context from the ``page.py`` files of the host.

Context functions in a segment ``page.py`` take dependencies the same way any page does.
The resolver shares its cache across the entire chain and the leaf page, so a value resolved at an ancestor segment is not recomputed further down.

A render runs the inherited callables root first, directory by directory, and the callables of the page itself last.
A nested ``DObject`` relies on that order.
A ``DObject[Comment, "post"]`` in a page reads the ``post`` value an ancestor ``page.py`` loaded, so a comment URL under another post answers 404, see :ref:`topics-di-dobject`.

.. code-block:: python
   :caption: blog/pages/posts/[int:post]/page.py

   from blog.models import Post

   from next import context
   from next.deps import DObject

   @context("post", inherit_context=True)
   def post(post: DObject[Post]) -> Post:
       return post

.. code-block:: python
   :caption: blog/pages/posts/[int:post]/comments/[int:comment]/page.py

   from blog.models import Comment

   from next import context
   from next.deps import DObject

   @context("comment")
   def comment(comment: DObject[Comment, "post"]) -> Comment:
       return comment

The same segment ``page.py`` is where a section declares its metadata.
A module-level ``metadata`` dict reaches every page under the directory without a flag, and a ``@page.metadata(inherit=True)`` callable does the same with the request in hand.
The section title template, the robots directives of a private area, and the Open Graph type of a blog all live here, see :doc:`seo/metadata` for the merge order.

Nested layout patterns
----------------------

Section layout
~~~~~~~~~~~~~~

Add a layout inside a section to share a sub navigation across every page under that section.

.. code-block:: jinja
   :caption: notes/pages/admin/layout.djx

   <section class="admin">
     <nav class="admin-nav">
       <a href="{% url 'next:page_admin' %}">Overview</a>
       <a href="{% url 'next:page_admin_stats' %}">Stats</a>
     </nav>
     {% template %}
   </section>

Empty pass through
~~~~~~~~~~~~~~~~~~

Sometimes a directory needs a visual-only layout boundary for context but no extra HTML.
Use an empty layout that contains only the placeholder.

.. code-block:: jinja
   :caption: notes/pages/api/layout.djx

   {% template %}

A sibling ``page.py`` in the same directory can publish inherited context for every page under ``/api/`` without that layout injecting any visible markup.

Layout scoped static assets
~~~~~~~~~~~~~~~~~~~~~~~~~~~

Co-locate ``layout.css`` next to ``layout.djx`` to ship styles that apply only to pages under that directory.
The static collector emits the file only when a request reaches a page below that layout.

The root layout is the site-wide case of the same rule, because every page sits below it.
A ``layout.css`` beside the root ``layout.djx`` of the page tree reaches every page of that tree, except the pages under a ``{% layout root %}`` layout that cuts it away.
A ``layout.css`` beside the ``layout.djx`` of a ``DIRS`` root that has a key reaches the pages of every tree too, under a name that starts with the key of that root, see :ref:`topics-static-name-resolution-colocated`.
A layout under no page tree root, or under a root with no key, wraps pages but serves no assets to a page inside a tree.
A ``{% use_style %}`` tag in the root layout adds a file staticfiles serves or a URL a third party hosts, see :doc:`/content/howto/ship-a-site-wide-stylesheet`.

Multiple backends and layout roots
----------------------------------

Two backends that scan different directories produce two separate layout chains.
The separation comes from the directories, not from the backends, because layout discovery is purely filesystem-based.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "PAGE_BACKENDS": [
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "APP_DIRS": True,
               "PAGES_DIR": "pages",
           },
           {
               "BACKEND": "next.urls.FileRouterBackend",
               "APP_DIRS": True,
               "PAGES_DIR": "admin_pages",
           }
       ]
   }

The first backend reads ``notes/pages/`` and uses ``notes/pages/layout.djx`` as its root.
The second backend reads ``notes/admin_pages/`` and uses ``notes/admin_pages/layout.djx`` as its root.
The two chains stay separate only because the two directories share no ancestor that carries a ``layout.djx``.
A layout in a shared ancestor directory, such as the ``notes`` package directory itself, wraps pages from both backends.
Root layouts collected from ``DIRS`` are also global.
The framework gathers them from every ``PAGE_BACKENDS`` entry and appends them to the chain of every page, regardless of which backend discovered the page.

Project level root layout
-------------------------

Place a root layout outside of any application by adding a project directory to ``DIRS`` so a single ``layout.djx`` wraps every page across every installed app.
See :doc:`multi-project` for the full settings example and the layered-projects pattern.

Cross cutting behaviour
-----------------------

Context processors
~~~~~~~~~~~~~~~~~~

Context processors are applied after every ``@context`` callable finishes but before any template (including the layout chain) renders.
They contribute variables that every layout and every page template can use.
Because they run last, a processor that returns the same key as a ``@context`` function overwrites that value.
Processor-to-processor duplicates are resolved by dotted path, keeping the first occurrence.
See :doc:`context` under *Resolution order* for the full merge order and the **first** ``TEMPLATES`` entry rule.
A processor that raises ``TypeError``, ``ValueError``, ``AttributeError``, or ``KeyError`` is logged and swallowed by default.
Set ``STRICT_CONTEXT = True`` to re-raise those exceptions instead.
Any other exception type propagates regardless of the setting.
Semantics live in :ref:`ref-settings`.

Static collector
~~~~~~~~~~~~~~~~

The static collector runs once per request.
Both layouts and the page contribute to the same collection slot.
A ``{% collect_styles %}`` tag inside an inner layout still emits every collected stylesheet, not only the ones declared in that layout.

Page rendered signal
~~~~~~~~~~~~~~~~~~~~

The ``page_rendered`` signal fires once per request after the layout chain produces the final HTML.
Subscribe to observe rendering duration, the number of collected styles and scripts, and the keys present in the template scope.
See :doc:`signals` for the full payload.

Common pitfalls
---------------

Layout markup is missing from the output.
   A ``{% template %}`` placeholder is required in every layout.
   Without one the framework drops that layout from the chain and renders the page body without its wrapper, which ``next.W001`` reports at check time.
   A layout carrying two placeholders draws ``next.W078`` instead, because composition fills the first one and the second renders its own fallback in place of the page.

Inherited context not visible to a sub-page.
   The ``inherit_context=True`` flag is required on the ancestor ``page.py`` that declares the value.
   Without it the value is not injected into descendant routes.

A ``pages()`` tree misses a requirement.
   A tree that ``pages()`` places under a prefix inherits no declaration of the tree that hosts it.
   Declare the requirement with ``inherit=True`` in the ``page.py`` at the root of that tree, see :doc:`access`.

Two roots produce one composed page.
   A page composes its layout chain from its own ancestor directories.
   The chain follows the directory the file sits in, so a file routed by two backends composes the same layouts under both routes.
   The duplicate route itself is reported as ``next.E015``, see :doc:`file-router`.

See also
--------

.. seealso::

   :doc:`pages` for the page body sources and the layout placeholder.
   :doc:`context` for context inheritance rules.
   :doc:`access` for the access a ``page.py`` declares over a subtree.
   :doc:`seo/metadata` for the ``{% metadata %}`` tag and the metadata a segment ``page.py`` declares.
   :doc:`multi-project` for project-level layout roots.
   :doc:`/content/internals/page-discovery` for the composition pipeline.
