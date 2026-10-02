.. _topics-sitemaps:

Sitemaps
========

A ``sitemap.py`` at the top of a page root switches ``/sitemap.xml`` on for that tree.
Every static route is listed on its own, a dynamic route lists the rows an ``@sitemap.items`` callable answers, and the XML comes from the templates of :doc:`django:ref/contrib/sitemaps`.
This page covers the file, the two kinds of routes, lazy items, the module attributes, and languages, and :doc:`sitemap-sections` covers sections, backends, the origin, and caching.

.. contents::
   :local:
   :depth: 2

The file convention
-------------------

A page root is every tree the routers report, an application's pages directory or an entry of ``DIRS``.
The framework probes the top of each root for ``sitemap.py``, and a file there is what switches the feature on.
Without one no sitemap route exists, so a ``path("sitemap.xml", ...)`` placed after ``include("next.urls")`` keeps answering.

.. code-block:: python
   :caption: notes/pages/sitemap.py

   changefreq = "weekly"
   exclude = ["drafts/*"]

The file is executed once when the framework discovers the tree and kept until the SEO routes reset on a router or settings reload.
While ``DEBUG`` is on an edit shows on the next request without a restart, since every SEO route first checks the files at the top of each page root.
A file that fails to import is logged once, keeps its route, and answers 404, so no partial sitemap ships, and ``manage.py check`` reports the cause.
A 404 is safe here, since a crawler reads a missing sitemap as nothing listed and keeps the URLs it already knows.
``sitemap.py`` is read by the dependency resolver like a ``page.py``, so it keeps ``from __future__ import annotations`` out.
A ``sitemap.py`` below the top of the tree is never read, and the checks say so.

``django.contrib.sitemaps`` sits in ``INSTALLED_APPS`` with ``APP_DIRS`` on the Django template backend, because the XML is rendered from the ``sitemap.xml`` and ``sitemap_index.xml`` templates it ships.
A site closed to search answers 404 for every sitemap address, and a site only ``DEBUG`` closes serves them under ``noindex``, see :doc:`site`.

Static routes
-------------

Every route of the tree without a bracket segment is listed automatically, reversed through ``page_reverse`` at render time.
A route whose static metadata is ``noindex`` is left out, because a sitemap invites the crawler to a page the tag then turns away.
A route matching a glob in ``exclude`` is left out as well, and a glob matches the whole route, ``drafts/*`` for a subtree and ``admin`` for one page.
Only ``*`` and ``?`` are wildcards, and brackets are literal because routes spell parameters with them, so ``posts/[slug]`` names that one dynamic route.

Only the static metadata is read, ``DEFAULTS`` plus every ``metadata`` dict from the page root down.
Metadata the schema refuses does not break the document, the route is listed with a logged warning, and the metadata checks report the fault.
The list is memoised until a ``page.py`` of the tree loads again.

Dynamic routes and lazy items
-----------------------------

A route with a parameter has no URLs until the file names them.
``@sitemap.items`` binds a callable to a route, and the callable answers the rows, a ``QuerySet``, a list, a range, or any other sliceable sequence.

.. code-block:: python
   :caption: notes/pages/sitemap.py

   from django.db.models import QuerySet
   from notes.models import Note

   from next.seo import sitemap

   changefreq = "weekly"
   cache = 900

   @sitemap.items("notes/[int:note_id]", kwargs=lambda note: {"note_id": note.pk}, lastmod="updated_at")
   def notes() -> QuerySet[Note]:
       return Note.objects.filter(published=True).only("pk", "updated_at").order_by("pk")

A ``QuerySet`` stays lazy.
The paginator counts it with one ``COUNT`` and reads a page with one ``LIMIT`` and ``OFFSET``, so a table of a million rows costs the rows of one page per request, and the newest ``lastmod`` of the section is one aggregate query.
An unordered ``QuerySet`` is ordered by primary key, since a page slice needs a stable order.
A generator is read into a list first, so a large table belongs in a ``QuerySet``.

Each row becomes one URL in one of three ways.
A ``SitemapEntry`` carries its own ``kwargs``, ``lastmod``, ``changefreq``, and ``priority``, a mapping is read as the kwargs alone, and any other row, a model instance among them, needs ``kwargs=`` on the decorator, a callable answering the kwargs of the row.
``lastmod="updated_at"`` names the column or key the date of each row is read from.
A row that none of these reverses is a ``TypeError`` naming the callable.

.. code-block:: python
   :caption: notes/pages/sitemap.py

   from next.seo import SitemapEntry, sitemap

   @sitemap.items("tags/[slug]")
   def tags() -> list[SitemapEntry]:
       return [SitemapEntry(kwargs={"slug": slug}, changefreq="daily") for slug in ("python", "django")]

The first argument is the route as the directory names spell it, and the same tree has to serve it, otherwise the build raises ``SitemapTrailError`` and ``manage.py check`` reports it ahead of time.
An items callable, its ``kwargs=`` callable, a row of the wrong shape, or a row that does not reverse makes the sitemap answer 503 with ``Retry-After`` and logs the failure once, so a crawler comes back later rather than reading a 500.
Under ``DEBUG`` the exception reaches the technical page with a note naming the callable and the trail.
The callable is resolved through dependency injection like a ``@context`` callable, so ``Depends`` and a parameter annotated :class:`~django.http.HttpRequest` are available to it.
A registration binds to the ``sitemap.py`` that runs the decorator, so the callable may live in a helper module and be registered as ``sitemap.items("notes/[int:note_id]")(notes)`` after its import.
The decorator run anywhere else registers nothing, and two callables on one route keep only the later one, and the checks report both.

A route with an ``@sitemap.items`` callable is listed by that callable alone, so a static route named by one loses its automatic entry and takes the callable's ``lastmod``, ``changefreq``, and ``priority``.
An ``exclude`` glob drops a callable's route as well, which the checks flag as a contradiction.
``lastmod`` is never read off a file mtime, because the mtime of a deployed checkout says nothing about the content, and a date or a naive datetime reads in the current ``TIME_ZONE``.
``manage.py check`` reports a dynamic route the file neither lists nor excludes, and a route whose static metadata is ``noindex`` needs neither.

Keeping noindex rows out
------------------------

A ``noindex`` a ``@page.metadata`` callable decides per row is invisible to the sitemap, since no request renders the page.
Filter such rows out in the items callable, ideally through the same manager method the page uses to decide its robots, so the two cannot drift.

.. code-block:: python
   :caption: notes/models.py

   from django.db import models

   class NoteQuerySet(models.QuerySet):
       def indexable(self):
           return self.filter(published=True, private=False)

A test that fetches a listed URL and asserts its robots with ``assert_metadata`` catches the rows the filter missed, see :doc:`auditing`.
``manage.py check`` reports a listed route whose page is ``noindex`` by its static metadata.

Module attributes
-----------------

The attributes below map onto :class:`django.contrib.sitemaps.Sitemap`, with ``exclude``, ``cache``, and ``section`` as the ones the framework adds.
An attribute the file does not declare keeps the Django default, and ``manage.py check`` reports a value outside its shape.

.. list-table::
   :header-rows: 1
   :widths: 16 28 56

   * - Attribute
     - Shape
     - Effect
   * - ``changefreq``
     - one of ``always``, ``hourly``, ``daily``, ``weekly``, ``monthly``, ``yearly``, ``never``
     - The ``<changefreq>`` of every URL without one of its own.
   * - ``priority``
     - a number from 0 to 1
     - The ``<priority>`` of every URL without one of its own.
   * - ``exclude``
     - a list of route globs
     - Routes, those of an items callable included, matching a glob are left out.
   * - ``i18n``
     - a bool
     - Every URL is listed once per language, reversed under that language.
   * - ``languages``
     - a list of language codes
     - Narrows the languages ``i18n`` walks.
   * - ``alternates``
     - a bool
     - Adds the ``<xhtml:link rel="alternate" hreflang="...">`` block to every URL under ``i18n``.
   * - ``x_default``
     - a bool
     - Adds the ``x-default`` alternate under ``alternates``, naming the URL of ``LANGUAGE_CODE``.
   * - ``protocol``
     - ``"http"`` or ``"https"``
     - The scheme of every URL, ahead of the site URL and the request.
   * - ``limit``
     - an int from 1 to 50000
     - URLs per page, 50000 by default, and a section past it paginates.
   * - ``cache``
     - an int, ``False``, or a ``CacheDict``
     - The ``Cache-Control`` of the sitemap responses, and the server-side cache, see :doc:`sitemap-sections`.
   * - ``section``
     - a slug
     - The section name of the tree, ahead of the application label.

Languages
---------

Without ``i18n`` every URL is reversed under ``LANGUAGE_CODE``, so the document is the same whatever the ``Accept-Language`` of the crawler.
``i18n = True`` lists each URL once per language, and the router include has to sit inside :func:`~django.conf.urls.i18n.i18n_patterns` for the languages to differ.
``alternates = True`` adds the hreflang block the ``<head>`` renders through ``alternates.languages``, and ``x_default = True`` adds the ``x-default`` alternate on the URL of the default language, the same one the head names.
``manage.py check`` reports ``alternates`` or ``x_default`` without ``i18n``, ``x_default`` without ``alternates``, a ``languages`` code outside ``LANGUAGES``, and ``i18n`` on a router outside ``i18n_patterns``.

A sitemap document weighs at most 50 MB, and under ``alternates`` every URL carries a link per language, so a page of 50000 URLs would outgrow it.
A page then holds ``50000 // (languages + 1)`` URLs, or ``50000 // (languages + 2)`` with ``x_default``, and ``limit`` lowers it further but never raises it.
Two languages and ``x_default`` make pages of 12500 URLs, and a ``limit`` above that number draws ``next.W086``.

See also
--------

.. seealso::

   :doc:`sitemap-sections` for sections, backends, the origin, caching, and the host-root mount.
   :doc:`robots` for the ``Sitemap:`` line.
   :doc:`quickstart` for the first sitemap of a site.
   :doc:`/content/ref/seo` for ``sitemap``, ``SitemapEntry``, and the errors.
   :repo:`wiki <tree/main/examples/wiki>` for items read off a table and :repo:`search-catalog <tree/main/examples/search-catalog>` for two dynamic routes behind ``cache``.
