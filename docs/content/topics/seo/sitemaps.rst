.. _topics-sitemaps:

Sitemaps
========

A ``sitemap.py`` at the top of a page root switches ``/sitemap.xml`` on for that tree.
Every static route is listed on its own, a dynamic route lists the URLs an ``@sitemap.items`` callable yields, and the XML comes from :doc:`django:ref/contrib/sitemaps`.
This page covers the file convention, the two kinds of routes, the module attributes, the index, the origin every URL is made absolute on, caching, and the checks that read the file.

.. contents::
   :local:
   :depth: 2

The file convention
-------------------

A page root is every tree the routers report, an application's pages directory or an entry of ``DIRS``.
The framework probes the top of each root for ``sitemap.py``, and a file there is what switches the feature on.
Without one no sitemap route exists, so a ``path("sitemap.xml", ...)`` a project places after ``include("next.urls")`` keeps answering, and with one the routes join the lazy pattern sequence the include mounts, named ``next:sitemap`` and ``next:sitemap_section``.

.. code-block:: python
   :caption: notes/pages/sitemap.py

   changefreq = "weekly"
   exclude = ["drafts/**"]

It is executed like a ``page.py``, memoised on its mtime, and watched by the development server, so an edit lands on the next request.
``next.W102`` reports a ``sitemap.py`` below the top of the tree, where nothing reads it.

``django.contrib.sitemaps`` has to sit in ``INSTALLED_APPS`` with ``APP_DIRS`` on the Django template backend, because the XML is rendered from the ``sitemap.xml`` and ``sitemap_index.xml`` templates that application ships.
``next.E112`` reports a project whose ``sitemap.py`` exists while the templates do not load.

Static routes
-------------

Every route of the tree without a bracket segment is listed automatically, reversed through ``page_reverse`` at render time.
Two things keep a static route out.
A route whose static metadata carries ``robots.index`` set to ``False``, or a robots string naming ``noindex``, is skipped, because a sitemap invites the crawler to a page the tag then turns away.
A route matching a glob in ``exclude`` is skipped as well, and the globs are :func:`~fnmatch.fnmatch` patterns over the route trail, ``drafts/**`` for a whole subtree and ``admin`` for one page.

Only the static fold is read, the settings tier plus every ``metadata`` dict along the chain.
A ``noindex`` a ``@page.metadata`` callable decides at request time is invisible here, and ``exclude`` is the way to keep such a route out.

Dynamic routes
--------------

A route with a parameter has no URLs until the file names them.
``@sitemap.items`` binds a callable to a trail, and the callable yields one ``Entry`` per URL, each carrying the kwargs that reverse the route.

.. code-block:: python
   :caption: notes/pages/sitemap.py

   from collections.abc import Iterator

   from notes.models import Note

   from next.seo import Entry, sitemap

   @sitemap.items("notes/[int:note_id]")
   def notes() -> Iterator[Entry]:
       for note in Note.objects.filter(published=True).only("pk", "updated_at"):
           yield Entry(kwargs={"note_id": note.pk}, lastmod=note.updated_at)

The trail is the route as the directory names spell it, and it has to be routed by the same tree, otherwise the build raises ``SitemapTrailError`` and ``next.E111`` reports it ahead of time.
The callable is resolved through dependency injection like a ``@context`` callable, so ``Depends`` and a provider of the project are available to it, and a parameter annotated :class:`~django.http.HttpRequest` receives the request of the crawler.
A bare mapping yielded instead of an ``Entry`` is read as the kwargs alone, and any other value is a ``TypeError`` naming the callable.
``lastmod`` is whatever the entry carries, a :class:`~datetime.datetime` or a :class:`~datetime.date`, and it is never read off a file mtime, because the mtime of a deployed checkout says nothing about the content.
Two entries that reverse to one location are collapsed to the first, with a logged warning naming the trail and the kwargs.
``next.W097`` reports a dynamic route the file neither lists nor excludes, and ``next.W098`` a listed trail whose page is ``noindex`` by its static metadata.

Module attributes
-----------------

The attributes below map onto :class:`django.contrib.sitemaps.Sitemap`, with ``exclude`` and ``cache`` as the two the framework adds.
An attribute the file does not declare keeps the Django default, and ``next.E113`` reports a value outside its shape.

.. list-table::
   :header-rows: 1
   :widths: 18 22 60

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
     - a list of trail globs
     - Static routes matching a glob are left out, and a dynamic route matching one draws no ``next.W097``.
   * - ``i18n``
     - a bool
     - Every URL is listed once per entry of ``LANGUAGES``, reversed under that language so the :func:`~django.conf.urls.i18n.i18n_patterns` prefix lands in the path.
   * - ``languages``
     - a list of language codes
     - Narrows the languages ``i18n`` walks.
   * - ``alternates``
     - a bool
     - Adds the ``<xhtml:link rel="alternate" hreflang="...">`` block to every URL under ``i18n``.
   * - ``x_default``
     - a bool
     - Adds the ``x-default`` alternate, pointing at the prefix-free URL.
   * - ``protocol``
     - ``"http"`` or ``"https"``
     - The scheme of every URL, ahead of the ``base`` scheme and the request.
   * - ``limit``
     - a positive int
     - URLs per page, 50000 by default, and a section past it paginates.
   * - ``cache``
     - seconds as an int
     - Wraps the sitemap views in :func:`~django.views.decorators.cache.cache_page`.

hreflang alternates
-------------------

``i18n = True`` lists each URL once per language, and every entry is reversed under :func:`~django.utils.translation.override` for that language.
The router include therefore has to sit inside :func:`~django.conf.urls.i18n.i18n_patterns`, the way :doc:`/content/howto/internationalize-routes` mounts it, otherwise every language reverses to the same path.
``alternates = True`` adds the hreflang block to each entry, the same set the ``<head>`` renders through ``alternates.languages``, and ``x_default = True`` adds the fallback that Django derives by stripping the language prefix.
That derivation is right when the default language carries no prefix, so a project that sets ``prefix_default_language=False`` gets a correct ``x-default`` and a project that prefixes every language gets one pointing at a redirect.

The index and the sections
--------------------------

Each page root that declares a ``sitemap.py`` is one section, served at ``/sitemap-<section>.xml``.
The section label is the label of the installed application whose directory holds the root, and for a ``DIRS`` root it is the slugified directory name.
Two roots taking one label are served as ``<label>`` and ``<label>-2``, which ``next.E116`` reports so the trees can be routed from differently named directories.

``/sitemap.xml`` is the one document while there is one section and it fits in a page.
With several sections, or a section whose URLs exceed ``limit``, the same address answers an index listing every section and every ``?p=N`` page of it.
The index is a thin view of the framework's own rather than Django's, because Django's index reads the domain off the request and would ignore ``base``.
Both documents carry the ``X-Robots-Tag: noindex`` header Django's sitemap views set, so the XML itself never ranks, and a ``Last-Modified`` header when every listed entry carries a ``lastmod``.

.. mermaid::

   flowchart LR
       Request["GET /sitemap.xml"] --> Include["include('next.urls')"]
       Include --> Slot["seo_routes_slot"]
       Slot --> Manager["seo_manager.sitemaps()"]
       Manager --> Root1["RouteSitemap per root"]
       Root1 -- "one section, one page" --> Django["django.contrib.sitemaps.views.sitemap"]
       Root1 -- "several sections or pages" --> Index["next.seo index view"]
       Django --> XML["sitemap.xml"]
       Index --> IndexXML["sitemap_index.xml"]

The origin of every URL
-----------------------

A sitemap lists absolute URLs, so every location needs a scheme and a host.
``NEXT_FRAMEWORK["METADATA"]["DEFAULTS"]["base"]`` supplies both once for the whole site, the same origin the canonical links and the social images are made absolute on, and the sitemap reads it ahead of everything else.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "METADATA": {
           "DEFAULTS": {"base": "https://notes.example"},
       },
   }

Without a base the host comes from :func:`~django.contrib.sites.shortcuts.get_current_site`.
With ``django.contrib.sites`` installed that is the ``Site`` row of the database, which ships as ``example.com`` until the row is edited, so a project that installs the application and sets no base publishes a sitemap of ``example.com`` URLs.
Without the sites application it is the host of the request.
The scheme is ``protocol`` when the file declares one, then the scheme of ``base``, then the scheme of the request.
A build with neither a request nor a base raises ``SeoBaseError`` naming the tree.

.. warning::

   Set ``base`` on every deployment that installs ``django.contrib.sites``.
   No check reads the ``Site`` table, so the ``example.com`` default is caught by nothing before a crawler reads it.

Caching
-------

Every request rebuilds the document from the routes and the callables, so a catalog of many rows pays its query on every crawler visit.
``cache`` in ``sitemap.py`` names a number of seconds, and the framework wraps the sitemap and index views in :func:`~django.views.decorators.cache.cache_page` for that long on the default cache, while ``/robots.txt`` is never cached.
The index and every section share the wrapper, with several roots the shortest declared ``cache`` wins for all of them, and a ``cache`` of ``0`` leaves the views unwrapped.

What the checks catch
---------------------

The sitemap checks run on every ``manage.py check``, under the ``urls`` tag beside ``next``.
The errors are ``next.E110`` for a file that fails to import or defers its annotations, ``next.E111`` for a trail the tree does not route, ``next.E112`` for templates that do not load, ``next.E113`` for an attribute outside its shape, ``next.E115`` for a page or a urlpattern on ``/sitemap.xml``, and ``next.E116`` for two roots sharing a section label.
The warnings are ``next.W097`` for a dynamic route the file neither lists nor excludes, ``next.W098`` for a listed trail whose page is ``noindex``, ``next.W099`` for a sitemap the host root does not resolve, and ``next.W102`` for a file below the top of its tree.
:doc:`auditing` places these beside the metadata checks, and :doc:`/content/ref/system-checks` tabulates every code.

See also
--------

.. seealso::

   :doc:`robots` for the ``Sitemap:`` line and the ``/robots.txt`` that goes with the document.
   :doc:`/content/howto/publish-a-sitemap` for the recipe from an empty project to a verified document.
   :doc:`/content/ref/seo` for ``Entry``, ``sitemap``, ``RouteSitemap``, and the two exceptions.
   :doc:`/content/internals/seo-pipeline` for the discovery, the registry, and the URL slot.
   :repo:`markdown-blog <tree/main/examples/markdown-blog#13-sitemap-and-robots-from-the-page-root>` for a static tree under ``i18n``, :repo:`wiki <tree/main/examples/wiki#12-a-sitemap-fed-by-the-database>` for entries read off a table with ``lastmod``, and :repo:`search-catalog <tree/main/examples/search-catalog#12-cached-sitemap-over-categories-and-products>` for two dynamic trails behind ``cache``.
