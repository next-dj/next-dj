.. _ref-seo:

SEO reference
=============

Module summary
--------------

``next.seo`` serves ``/sitemap.xml``, ``/sitemap-<section>.xml``, and ``/robots.txt`` from the sources at the top of every page root and from the configured sitemap backends, on top of :doc:`django:ref/contrib/sitemaps`.
It exports the ``SitemapEntry`` and ``RobotsRule`` value objects, the ``sitemap`` declaration object, the ``SitemapBackend`` contract with the shipped ``PageTreeSitemapBackend``, the ``seo_manager`` façade, and the four exceptions.
The routes join ``include("next.urls")`` on their own, and ``next.seo.urls`` mounts them at the host root when the router sits elsewhere, while the views behind them are internal.
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
``serves()`` routes ``/sitemap.xml`` while any backend answers true, and ``cache_control()`` names the cache the sitemap views carry.
``shortest_cache`` settles the cache of several backends, one that forbids storing winning, then the shortest age.
``PageTreeSitemapBackend`` answers one section per page tree with a ``sitemap.py``, plus one per ``section=`` its items name.

.. automodule:: next.seo.backends
   :members:

Manager
~~~~~~~

``seo_manager`` loads the backends, merges their sections, and memoises the robots source until a reset.
Its ``version`` reads ``seo_routes_version`` of ``next.urls.manager``, the token the cached views and the lazy URL patterns key on, and every ``reset`` moves it.
``refresh()`` resets the manager once a source file of any tree moved on disk while ``DEBUG`` watches template edits, and every SEO view asks it first, so an edited ``sitemap.py``, ``robots.py``, or ``robots.txt`` shows without a restart, as a ``scripts.py`` does.
``fingerprint()`` digests the sources, the backend entries, and the site config through ``stable_repr``, which spells a value alike in every process, so the cache keys of every worker agree.
``forget_seo_sources`` is the receiver ``settings_reloaded`` and ``router_reloaded`` reset it through.
``reset_seo_sources`` drops the manager state and every ``@sitemap.items`` registration, which ``next.testing.reset_seo`` and ``reset_check_caches`` call.

.. automodule:: next.seo.manager
   :members:

Sitemaps
~~~~~~~~

``PageTreeSitemap`` subclasses :class:`django.contrib.sitemaps.Sitemap` for one section of a page tree.
Its items are one lazy ``ChainedEntries`` of the static routes and every items callable, and its ``paginator`` slices each part rather than listing it, so a ``QuerySet`` part reads one page of rows.
``SitemapOptions`` is the one reader of the module attributes the build and the checks share, and ``listed_trails``, ``is_excluded``, and ``static_noindex`` are the route filters both apply.

.. automodule:: next.seo.sitemaps
   :members:

.. automodule:: next.seo.pagination
   :members:

Robots
~~~~~~

``DeclaredRobots`` holds a ``robots.py`` and renders its groups, calling ``rules`` through the resolver when it is a callable.
``TextFile`` holds a static ``robots.txt``, re-read when its mtime moves, the last good bytes answering a failed read.

.. automodule:: next.seo.robots
   :members:

Routes
~~~~~~

Every route answers ``GET`` and ``HEAD`` alone, a miss as a plain-text 404 that skips the project's 404 page, and on a site closed to search every response carries ``X-Robots-Tag: noindex, nofollow``.
The 404 body reads ``Not found`` unless ``DEBUG`` is on, which names the reason, since that reason may name a file on disk.
The sitemap and robots routes are wrapped in :func:`~django.views.decorators.cache.cache_page` when their source declares a storable ``cache``, keyed on the fingerprint of the sources.

``next.seo.routes`` names every route and answers the patterns whose source exists.
``include("next.urls")`` carries the routes under ``next`` as ``next:sitemap``, ``next:sitemap_section``, and ``next:robots``.
``next.seo.urls`` is the URLconf that mounts the same routes under the ``next_seo`` namespace, for a project whose ``include("next.urls")`` sits under a prefix or inside :func:`~django.conf.urls.i18n.i18n_patterns`.
:ref:`topics-seo-host-root` shows the two includes side by side.

.. automodule:: next.seo.routes
   :members:

Origin
~~~~~~

``request_origin`` answers the scheme and the domain a response writes its absolute URLs on, read through ``site_origin`` of :doc:`site`, ``SITE["URL"]`` first, then :func:`~django.contrib.sites.shortcuts.get_current_site`, then the request host.
The page metadata reads the same origin, so a canonical link and the sitemap location of one page never disagree.

.. automodule:: next.seo.origin
   :members:

Discovery and registry
~~~~~~~~~~~~~~~~~~~~~~

``page_tree_roots`` answers one ``SeoRoot`` per routed page tree, with its section name, its routes, and the sources at its top, loaded once per reset through ``load_source``.
A source that fails to import keeps its ``SeoSourceImportError`` on the ``SeoSource`` for the checks, and its route answers 404 through a ``BrokenSource``.
``sitemap_items_registry`` holds the ``@sitemap.items`` callables keyed by the running file and route, the last registration per key winning and a repeat recorded as a conflict.

.. automodule:: next.seo.discovery
   :members:

.. automodule:: next.seo.registry
   :members:

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
The port carries no version, since the lazy urlpatterns read ``seo_routes_version`` directly.

.. automodule:: next.seo.ports
   :members:

Signals
-------

``sitemap_items_registered``.
   Sent by ``SitemapItemsRegistry`` on every registration, with the ``file``, ``trail``, and ``func`` keyword arguments.

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
