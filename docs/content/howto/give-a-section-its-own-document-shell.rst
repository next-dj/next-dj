.. _howto-section-document-shell:

Give a section its own document shell
=====================================

Problem
-------

A section of the site, such as an embeddable widget or a print view, must render a complete document of its own.
The site layout above it writes the navigation, the footer, and the scripts of the site, and none of that belongs in the section.

Solution
--------

Open the ``layout.djx`` of the section with ``{% layout root %}``.
Composition stops at that layout, so the pages below it get the section shell and no layout further out.

Walkthrough
-----------

Lay out the section
~~~~~~~~~~~~~~~~~~~

The embed pages live in a directory of their own, and the site layout sits above it.

.. code-block:: text
   :caption: notes/pages/

   layout.djx
   page.py
   template.djx
   embed/
     layout.djx
     [int:id]/
       page.py
       template.djx

Write the root layout
~~~~~~~~~~~~~~~~~~~~~

The marker comes first in the file.
Only ``{% load %}`` tags, ``{% comment %}`` blocks, ``{# #}`` comments, and blank text may stand before it.

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

The site layout is cut away for these pages, so this file carries the whole shell.
Leaving out ``{% metadata %}`` drops the title and the head tags, and leaving out ``{% collect_styles %}`` or ``{% collect_scripts %}`` drops the assets of the page and its layouts.

Use a route group for the section
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A section whose pages share no URL prefix takes a route group instead of a directory, see :ref:`topics-file-router-route-groups`.
The login and logout pages write a document of their own while the pages of ``(app)`` keep the shared chrome, and the group adds nothing to their URLs.

.. code-block:: text
   :caption: accounts/pages/

   layout.djx
   (app)/
     notes/
       page.py
       template.djx
   (auth)/
     layout.djx
     login/
       page.py
       template.djx
     logout/
       page.py
       template.djx

.. code-block:: jinja
   :caption: accounts/pages/(auth)/layout.djx

   {% layout root %}
   <!doctype html>
   <html>
     <head>
       {% metadata %}
       {% collect_styles %}
     </head>
     <body class="auth">
       {% template %}
       {% collect_scripts %}
     </body>
   </html>

``/login/`` and ``/logout/`` get this shell and no layout further out, and ``/notes/`` keeps the site layout.

Keep what the ancestors declare
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The marker cuts the layout chain and leaves everything else as it was.
The ``page.py`` files above the section still apply, so the access they declare with ``inherit=True`` guards the embed pages, and the values they publish with ``inherit_context=True`` stay in the context.
A ``title_template`` declared above the section still formats the title, and a title the embed page declares replaces it.

.. code-block:: python
   :caption: notes/pages/embed/[int:id]/page.py

   metadata = {"title": "Note"}

Style the section
~~~~~~~~~~~~~~~~~

A ``layout.css`` beside the root layout belongs to the section and loads for every page below it.
The ``layout.css`` of the site layout does not load for these pages, since that layout is no longer in their chain.

Print views follow the same recipe.
A ``print/layout.djx`` with the marker, a print stylesheet, and its own ``{% metadata %}`` gives a page a print version beside the screen one.

Verification
------------

Request a page of the section and read the response.
It holds one ``<html>`` element, the head of the section shell, and none of the site navigation.
A page outside the section keeps the site shell.

Run the checks.

.. code-block:: bash
   :caption: shell

   uv run python manage.py check

``next.E168`` reports a marker that stands after other content or appears twice.
``next.W133`` reports a layout that writes ``<html`` without a working marker while a layout further out exists.
``next.W134`` reports a marker in a ``template.djx`` or a component template, where it has no effect.

Zones declared in the site layout are gone for these pages, so a partial request for one of them is answered as an unknown zone.

Working examples
----------------

The :repo:`live-polls <tree/main/examples/live-polls>` example serves a live embed widget from ``polls/pages/polls/[int:id]/embed/``.
Its root layout cuts the site shell, keeps a co-located ``layout.css``, and leaves the results zone to update over server-sent events.

The :repo:`markdown-blog <tree/main/examples/markdown-blog>` example serves a print view from ``blog/pages/posts/[slug]/print/``.
Its root layout cuts the post layout and the site shell, and the page declares its own metadata with ``robots`` set to ``noindex``.

See also
--------

.. seealso::

   :doc:`/content/topics/layouts` for the rules of the marker and what the cut removes.
   :doc:`/content/ref/template-tags` for the ``{% layout root %}`` tag.
   :doc:`ship-a-site-wide-stylesheet` for the stylesheet the site layout carries.
   :doc:`/content/ref/system-checks` for ``next.E168``, ``next.W133``, ``next.W134``, and ``next.W135``.
