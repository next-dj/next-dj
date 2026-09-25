.. _howto-set-page-titles:

Set page titles and SEO tags
============================

Problem
-------

Every page of the site should carry its own ``<title>``, a description, a canonical link, and Open Graph tags, without a ``<title>`` line hand-written into each layout.

Solution
--------

Put the site defaults and the title template in ``NEXT_FRAMEWORK["METADATA"]["DEFAULTS"]``, declare a ``metadata`` dict or a ``@page.metadata`` callable in each ``page.py`` that has something to say, and render the fold with one ``{% metadata %}`` tag in the root layout.

Walkthrough
-----------

Declare the site defaults
~~~~~~~~~~~~~~~~~~~~~~~~~

The settings tier is the outermost segment of every page, so the site name, the title template, and the fallback description are written once.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "METADATA": {
           "DEFAULTS": {
               "base": "https://notes.example",
               "site_name": "Notes",
               "title": {"template": "{title} · {site_name}", "default": "Notes"},
               "description": "A notebook that lives in the browser, with every note one search away.",
               "og": {"type": "website"},
           },
       },
   }

The template applies to every page, the default is what a page without a title renders, and the ``og`` block turns Open Graph derivation on for the whole site.

Render the head
~~~~~~~~~~~~~~~

One ``{% metadata %}`` tag in the root layout writes the whole head.

.. code-block:: jinja
   :caption: notes/pages/layout.djx

   <!doctype html>
   <html>
     <head>
       <meta charset="utf-8">
       {% metadata %}
       {% collect_styles %}
     </head>
     <body>
       {% template %}
       {% collect_scripts %}
     </body>
   </html>

Title a static page
~~~~~~~~~~~~~~~~~~~

A module-level ``metadata`` dict is readable without a request, so the checks see what the page renders.

.. code-block:: python
   :caption: notes/pages/about/page.py

   metadata = {
       "title": "About",
       "description": "Who writes these notes, why they exist, and how to reach the author.",
       "canonical": True,
   }

Build a dynamic page from its row
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A ``@page.metadata`` callable builds the segment per request from the row the URL names.

.. code-block:: python
   :caption: notes/pages/notes/[int:note_id]/page.py

   from django.shortcuts import get_object_or_404
   from notes.models import Note

   from next import page
   from next.pages import MetadataDict
   from next.urls import DUrl

   @page.metadata
   def note_metadata(note_id: DUrl[int]) -> MetadataDict:
       note = get_object_or_404(Note, pk=note_id)
       return {
           "title": note.title,
           "description": note.summary,
           "canonical": True,
           "og": {"type": "article"},
       }

A missing row raises :exc:`~django.http.Http404`, which answers the request with a 404 page, the same as a ``@context`` callable raising it would.

Verification
------------

Request the two pages and read their heads.

.. code-block:: bash
   :caption: shell

   uv run python manage.py runserver

``/about/`` renders ``<title>About · Notes</title>``, the description meta, ``<link rel="canonical" href="https://notes.example/about/">``, and the ``og:title``, ``og:description``, ``og:url``, ``og:type``, ``og:site_name``, and ``og:locale`` properties derived from the fold.
``/notes/42/`` renders the same set with the note's title and summary, and ``/notes/999999/`` answers 404.

Run the checks.

.. code-block:: bash
   :caption: shell

   uv run python manage.py check
   uv run python manage.py check --deploy --tag seo

The first run reports no error.
The second one runs the four audits, ``next.W089`` to ``next.W096``, and stays quiet for these pages, because both descriptions fall between 50 and 160 characters and the title and description audits skip the note page, whose head a callable builds.
It warns with ``next.W090`` once two static pages fold to the same title, such as two pages that both fall back on the default ``Notes``.

See also
--------

.. seealso::

   :doc:`/content/topics/seo/metadata` for the merge order and the title template.
   :doc:`/content/topics/seo/social-and-canonical` for the remaining keys.
   :doc:`audit-seo-before-deploy` for the audit in CI.
