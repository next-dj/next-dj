.. _topics-seo-quickstart:

SEO quickstart
==============

Page metadata works on the pages the file router serves, a directory with a ``page.py`` or a template.
A plain Django view renders none of it, and ``{% metadata %}`` in its template renders the empty string.
This page wires a site up from scratch, the settings, the head tag, a title per page, a sitemap, and a robots file, and every step links the topic that covers it in depth.

.. contents::
   :local:
   :depth: 1

Name the site
-------------

Three settings give every page an origin, a site name, and a title template.

.. code-block:: python
   :caption: config/settings.py

   INSTALLED_APPS = ["django.contrib.staticfiles", "django.contrib.sitemaps", "next", "notes"]

   NEXT_FRAMEWORK = {
       "SITE": {"URL": "https://notes.example", "NAME": "Notes"},
       "METADATA": {
           "DEFAULTS": {
               "title": {"template": "{title} · {site_name}", "default": "Notes"},
               "description": "A notebook that lives in the browser.",
               "canonical": True,
               "og": {"type": "website"},
           },
       },
   }

``SITE["URL"]`` is the origin every absolute URL is built on, the canonical link, the social tags, and the sitemap alike.
``DEFAULTS`` is the outermost layer of every page's metadata, so a page names only what differs.
``django.contrib.sitemaps`` ships the XML templates the sitemap renders through.
See :doc:`site` for the ``SITE`` scope.

.. note::

   Open Graph tags render only when some layer declares an ``og`` block, even an empty one.
   The ``og`` block in ``DEFAULTS`` above is what turns them on for the whole site, deriving ``og:title``, ``og:description``, and ``og:url`` from the rest of the head.
   Without it a page gets a title and a description and no social card.

Render the head
---------------

One ``{% metadata %}`` tag in the root layout writes the whole head.
The tag is a builtin, so no ``{% load %}`` is needed.

.. code-block:: jinja
   :caption: notes/pages/layout.djx

   <!doctype html>
   <html lang="en">
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

Title a page
------------

A module-level ``metadata`` dict in ``page.py`` is the static form.
It reaches every page below its directory too, and a nearer ``page.py`` overrides a key by naming it again.

.. code-block:: python
   :caption: notes/pages/about/page.py

   from next.pages import MetadataDict

   metadata: MetadataDict = {
       "title": "About",
       "description": "Who writes these notes and how to reach the author.",
   }

``/about/`` renders ``<title>About · Notes</title>``, its description, a canonical link to ``https://notes.example/about/``, and the Open Graph tags derived from both.

Build metadata from data
------------------------

``@page.metadata`` registers a callable that builds the metadata per request.
It takes dependency-injected parameters like a ``@context`` callable, so it reads the row the page already fetched by the name of its context key.

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
       return {"title": note.title, "description": note.summary, "og": {"type": "article"}}

The row is fetched once, a missing row answers 404, and a summary of ``None`` keeps the site description, because ``None`` means unset.
See :doc:`metadata` for both forms and :doc:`merge` for how the layers combine.

Publish a sitemap
-----------------

A ``sitemap.py`` at the top of the page root switches ``/sitemap.xml`` on, and an empty file is enough.

.. code-block:: text
   :caption: notes/pages

   notes/pages/
       layout.djx
       sitemap.py
       robots.py
       about/
       notes/
           [int:note_id]/

Every route without a bracket segment is listed on its own, and a page whose metadata is ``noindex`` stays out.
A route with a parameter lists the rows an ``@sitemap.items`` callable answers.

.. code-block:: python
   :caption: notes/pages/sitemap.py

   from django.db.models import QuerySet
   from notes.models import Note

   from next.seo import sitemap

   @sitemap.items("notes/[int:note_id]", kwargs=lambda note: {"note_id": note.pk}, lastmod="updated_at")
   def notes() -> QuerySet[Note]:
       return Note.objects.filter(published=True).order_by("pk")

The first argument is the route as the directories spell it, and a ``QuerySet`` stays lazy, so each sitemap page reads only its own rows.
See :doc:`sitemaps` for the module attributes and :doc:`sitemap-sections` for sections, caching, and backends.

Add a robots file
-----------------

A ``robots.py`` beside it serves ``/robots.txt``.

.. code-block:: python
   :caption: notes/pages/robots.py

   from next.seo import RobotsRule

   rules = [RobotsRule(disallow="/search/")]

The file answers the group and a ``Sitemap:`` line naming ``https://notes.example/sitemap.xml``.
An empty ``robots.py`` answers ``User-agent: *`` with an empty ``Disallow:``, which allows everything.
Keep a page out of the index with ``"robots": {"index": False}`` in its metadata rather than a ``Disallow``, since a crawler that never fetches the page never reads its ``noindex``, see :doc:`robots`.

Both routes join ``include("next.urls")`` on their own.
A router mounted under a prefix or inside :func:`~django.conf.urls.i18n.i18n_patterns` needs ``include("next.seo.urls")`` at the root of the URLconf, see :ref:`topics-seo-host-root`.

Check the result
----------------

.. code-block:: bash
   :caption: shell

   uv run python manage.py runserver
   uv run python manage.py showmetadata /notes/42/
   uv run python manage.py check

While ``DEBUG`` is on the site is closed to search.
Every page renders ``noindex, nofollow``, and ``/sitemap.xml`` and ``/robots.txt`` still answer with ``X-Robots-Tag: noindex, nofollow`` for a preview.
With ``DEBUG`` off the pages render the robots directives they declare, see :doc:`site`.
``showmetadata`` prints which layer settles each key of a page, and :doc:`auditing` covers the checks and the test helpers.

See also
--------

.. seealso::

   :doc:`social-and-canonical` and :doc:`head-tags` for every other key.
   :doc:`/content/topics/caching` for ``cache`` and ``headers`` in ``page.py``.
   :doc:`/content/ref/metadata` for the value objects and :doc:`/content/ref/seo` for the sitemap and robots API.
