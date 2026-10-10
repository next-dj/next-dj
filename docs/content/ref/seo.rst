.. _ref-seo:

SEO reference
=============

Module summary
--------------

``next.seo`` serves ``/sitemap.xml``, ``/sitemap-<section>.xml``, and ``/robots.txt`` from the sources at the top of every page root and from the configured sitemap backends, on top of :doc:`django:ref/contrib/sitemaps`.
Its public surface is what the package exports, the ``SitemapEntry`` and ``RobotsRule`` value objects, the ``sitemap`` declaration object, the ``SitemapBackend`` contract with the shipped ``PageTreeSitemapBackend``, the four exceptions, the signal, and the checks, plus the ``next.seo.urls`` URLconf.
Every other module of the package is internal, and :doc:`/content/internals/seo-pipeline` describes it.
The page metadata the sitemap reads belongs to :doc:`metadata`, and the site origin and indexability to :doc:`site`.

Public API
----------

Markers
~~~~~~~

``SitemapEntry`` is one URL an ``@sitemap.items`` callable lists, the kwargs that reverse the route plus its optional ``lastmod``, ``changefreq``, and ``priority``, validated on construction.
``RobotsRule`` is one ``User-agent`` group of a ``robots.py``, its sequences pinned as tuples and every value validated on construction.
A bare string for ``user_agent``, ``allow``, or ``disallow`` counts as one value.

.. automodule:: next.seo.markers
   :members: SitemapEntry, RobotsRule, CHANGEFREQS

Declaration
~~~~~~~~~~~

``sitemap`` is the module-level declaration object, and ``sitemap.items(trail, *, kwargs=None, lastmod=None, section=None)`` registers the decorated callable under the ``sitemap.py`` that runs the decorator, wherever the callable is defined.
``trail`` is the route as the directories spell it, ``kwargs`` turns a row into the URL kwargs, ``lastmod`` names the column or key of the row's date, and ``section`` moves the route into a section of its own.

.. automodule:: next.seo.decorators
   :members:

Backends
~~~~~~~~

A backend takes its ``SITEMAP_BACKENDS`` entry, keeps ``OPTIONS`` as ``options``, and answers fresh Django sitemaps keyed by section name from ``sections(request)``, the request ``None`` for a check.
``serves()`` routes ``/sitemap.xml`` while any backend answers true, and ``cache_control()`` names the cache the sitemap views carry as a ``next.pages.CacheControl``, or ``None``.
``serves()`` runs while URLs resolve, outside any view, so one that raises, ``Http404`` included, counts as serving, and ``sections()`` raising answers 503 through the route, see :ref:`topics-failure-containment`.
``backend_path`` answers the dotted path of a backend class.
``shortest_cache`` combines the cache of several backends, one that forbids storing winning, then the shortest age.
``PageTreeSitemapBackend`` answers one section per page tree with a ``sitemap.py``, plus one per ``section=`` its items name.

.. automodule:: next.seo.backends
   :members:

Routes
~~~~~~

``include("next.urls")`` carries the routes under ``next`` as ``next:sitemap``, ``next:sitemap_section``, and ``next:robots``, each mounted only while its source exists.
``next.seo.urls`` is the URLconf that mounts the same routes under the ``next_seo`` namespace, for a project whose ``include("next.urls")`` sits under a prefix or inside :func:`~django.conf.urls.i18n.i18n_patterns`, see :ref:`topics-seo-host-root`.
Every route answers ``GET`` and ``HEAD`` alone and a miss as a plain-text 404 that skips the project's 404 page.
Project code that raises inside a route answers 503 with ``Retry-After``, a broken ``robots.py`` included, while a broken ``sitemap.py`` answers 404.

Errors
~~~~~~

``SitemapTrailError`` is an ``@sitemap.items`` route the tree does not serve, ``SitemapEntryError`` a ``SitemapEntry`` value outside the protocol, ``RobotsRuleError`` a ``RobotsRule`` value that would break the file, and ``SeoSourceImportError`` a source that failed to import.
All four are exported from ``next.seo``.
A build with neither a request nor a site URL raises ``SiteOriginError`` of :doc:`site`.

.. automodule:: next.seo.errors
   :members:

Ports
~~~~~

``SeoRoutesImpl`` binds the served routes to the ``SeoRoutes`` port of :doc:`ports`, so the lazy urlpatterns reach them without importing ``next.seo``.

.. automodule:: next.seo.ports
   :members:

Signals
-------

``sitemap_backend_loaded``.
   Sent once per ``SITEMAP_BACKENDS`` entry the manager builds, with the backend class as the sender and the ``config`` and ``instance`` keyword arguments.

.. automodule:: next.seo.signals
   :members:
   :no-index:

Checks
------

``next.seo.checks`` is a package, and importing it registers every check below under the ``seo`` tag beside ``next``.
The codes each one reports and their conditions live in :doc:`system-checks`.

.. csv-table::
   :header: "Check", "Submodule"
   :widths: 60 40

   "check_seo_module_imports", "sources"
   "check_seo_module_attributes", "sources"
   "check_sitemap_items_files", "sources"
   "check_seo_module_annotations", "sources"
   "check_seo_sources_below_root", "sources"
   "check_seo_sources_on_closed_site", "sources"
   "check_sitemap_items_trails", "sitemaps"
   "check_sitemap_templates", "sitemaps"
   "check_sitemap_section_collisions", "sitemaps"
   "check_sitemap_dynamic_routes", "sitemaps"
   "check_sitemap_noindex_items", "sitemaps"
   "check_sitemap_section_labels", "sitemaps"
   "check_sitemap_i18n_options", "sitemaps"
   "check_sitemap_excluded_items", "sitemaps"
   "check_seo_single_sources", "robots"
   "check_seo_text_files", "robots"
   "check_robots_disallow", "robots"
   "check_seo_route_collisions", "routes"
   "check_seo_routes_at_host_root", "routes"
   "check_seo_settings", "backends"

.. automodule:: next.seo.checks
   :members:
   :no-index:

See also
--------

.. seealso::

   :doc:`/content/topics/seo/sitemaps`, :doc:`/content/topics/seo/sitemap-sections`, and :doc:`/content/topics/seo/robots` for the topic guides.
   :doc:`/content/topics/seo/quickstart` for the first sitemap and robots file of a site.
   :doc:`/content/internals/seo-pipeline` for the discovery, the manager, the URL slot, and the reset story.
