.. _ref-seo:

SEO reference
=============

Module summary
--------------

``next.seo`` serves ``/sitemap.xml``, ``/sitemap-<section>.xml``, and ``/robots.txt`` from the ``sitemap.py``, ``robots.py``, and ``robots.txt`` at the top of every page root, on top of :doc:`django:ref/contrib/sitemaps`.
It exports the ``Entry`` and ``Rule`` value objects, the ``sitemap`` declaration object whose ``items`` decorator binds a callable to a route trail, the ``RouteSitemap`` one page tree builds, the ``seo_manager`` façade over the discovered sources, and the two exceptions ``SeoBaseError`` and ``SitemapTrailError``.
The page metadata a ``page.py`` declares belongs to :doc:`pages`, and the sitemap reads its static fold to skip a ``noindex`` route.

Public API
----------

Markers
~~~~~~~

``Entry`` is what an ``@sitemap.items`` callable yields, the kwargs that reverse the trail plus the optional ``lastmod``, ``changefreq``, and ``priority`` of that one URL.
``Rule`` is one ``User-agent`` group of a ``robots.py``, and its sequences are pinned as tuples so a list shared between two groups cannot move under one of them.

.. automodule:: next.seo.markers
   :members:

Declaration and manager
~~~~~~~~~~~~~~~~~~~~~~~

``sitemap`` is the module-level declaration object, and ``sitemap.items("trail")`` registers the decorated callable under the page root the file sits in, which is read off the file that defines the callable.
``seo_manager`` memoises the discovered roots and the one robots source until a reset, answers a fresh ``RouteSitemap`` per root and request, and carries the version the lazy urlpatterns key on.

.. automodule:: next.seo.manager
   :members:

Sitemaps
~~~~~~~~

``RouteSitemap`` subclasses :class:`django.contrib.sitemaps.Sitemap` for one page tree.
Its items are the static routes of the tree, less the ``noindex`` and excluded ones, plus every entry the registered callables yield, and ``location`` reverses each item lazily so an active language prefix lands in the path.
``get_protocol`` and ``get_domain`` read the ``base`` of the site-wide metadata defaults ahead of the site the request derived.

.. automodule:: next.seo.sitemaps
   :members:

Robots
~~~~~~

``RobotsRules`` holds what a ``robots.py`` declares and renders it with the ``Sitemap:`` line the view passes in.
``RobotsFile`` holds a static ``robots.txt`` and re-reads it when its mtime moves.

.. automodule:: next.seo.robots
   :members:

Views
~~~~~

``sitemap`` answers ``/sitemap.xml`` and ``/sitemap-<section>.xml``, and ``robots`` answers ``/robots.txt``.
The sitemap view is wrapped in :func:`~django.views.decorators.cache.cache_page` when a ``sitemap.py`` declares ``cache``, once per manager version, and the robots view is never cached.
The index a multi-root or paginating site answers at ``/sitemap.xml`` is the framework's own view, so ``base`` applies to it where Django's index reads the request host.

.. automodule:: next.seo.views
   :members:

The urls include
~~~~~~~~~~~~~~~~

``next.seo.urls`` is a URLconf module mounting the same three routes under the ``next_seo`` namespace, for a project whose ``include("next.urls")`` sits under a prefix or inside :func:`~django.conf.urls.i18n.i18n_patterns`.

.. code-block:: python
   :caption: config/urls.py

   from django.urls import include, path

   urlpatterns = [
       path("", include("next.seo.urls")),
       path("app/", include("next.urls")),
   ]

The include lists the routes whether or not a source exists, and a route without one answers 404.
The routes ``include("next.urls")`` carries exist only while a source does, under the ``next`` namespace as ``next:sitemap``, ``next:sitemap_section``, and ``next:robots``.

Discovery and registry
~~~~~~~~~~~~~~~~~~~~~~

``discover_seo_roots`` probes every page root the routers report and answers one ``SeoRoot`` per tree with its section label and the sources found at its top.
``sitemap_items_registry`` holds the ``@sitemap.items`` callables keyed by page root and trail, the last registration per key winning.

.. automodule:: next.seo.discovery
   :members:

.. automodule:: next.seo.registry
   :members:

Errors
~~~~~~

``SeoBaseError`` is raised by a build that has neither a request nor a ``base`` to make its URLs absolute with.
``SitemapTrailError`` is raised by a build whose ``@sitemap.items`` names a trail the tree does not route, the condition ``next.E111`` reports ahead of the build.
Both are exported from ``next.seo``.

.. automodule:: next.seo.errors
   :members:

Ports
~~~~~

``next.seo.ports`` holds ``SeoRoutesImpl``, which binds the sitemap and robots routes to the ``SeoRoutes`` port of :doc:`ports`.
``next.seo`` imports ``next.urls`` for the router manager and the reverse helper, so the lazy urlpatterns reach the routes through ``seo_routes_slot`` rather than by importing back across that edge.

.. automodule:: next.seo.ports
   :members:

Signals
-------

The area fires one signal.

``sitemap_items_registered``.
   Sent by ``SitemapItemsRegistry`` on every registration, with the ``root``, ``trail``, and ``func`` keyword arguments.
   The sender is the ``SitemapItemsRegistry`` class.

.. automodule:: next.seo.signals
   :members:
   :no-index:

See :doc:`signals` for the aggregator and :doc:`/content/topics/signals` for the receiver patterns.

Checks
------

``next.seo.checks`` registers the checks below, every one under the ``urls`` tag beside ``next`` and every one in the default tier of ``manage.py check``.

``check_seo_module_imports``.
   Fails with ``next.E110`` for a ``sitemap.py`` or a ``robots.py`` that raises on import or opens with ``from __future__ import annotations``.

``check_sitemap_items_trails``.
   Fails with ``next.E111`` for an ``@sitemap.items`` trail no page of the tree routes.

``check_sitemap_templates``.
   Fails with ``next.E112`` when a ``sitemap.py`` exists and ``sitemap.xml`` or ``sitemap_index.xml`` does not load through the template engines.

``check_seo_module_attributes``.
   Fails with ``next.E113`` for a module attribute outside its shape, a ``changefreq`` outside the protocol, a ``priority`` outside 0 to 1, or a ``rules`` that is not a list of ``Rule``.

``check_robots_single_source``.
   Fails with ``next.E114`` when more than one ``robots.py`` or ``robots.txt`` exists across the page roots, listing every path.

``check_seo_route_collisions``.
   Fails with ``next.E115`` for a page directory on ``sitemap.xml``, ``sitemap-*.xml``, or ``robots.txt``, and for a urlpattern of ``ROOT_URLCONF`` that resolves one of the served addresses ahead of the framework view.

``check_sitemap_section_labels``.
   Fails with ``next.E116`` when two page roots take one sitemap section label.

``check_robots_file``.
   Fails with ``next.E117`` for a static ``robots.txt`` that does not decode as UTF-8, and warns with ``next.W103`` for one naming no ``Sitemap:`` line while a sitemap exists.

``check_sitemap_dynamic_routes``.
   Warns with ``next.W097`` for a dynamic route the ``sitemap.py`` neither lists through ``@sitemap.items`` nor covers with an ``exclude`` glob.

``check_sitemap_noindex_items``.
   Warns with ``next.W098`` for a listed trail whose page is ``noindex`` by its static metadata.

``check_seo_routes_at_host_root``.
   Warns with ``next.W099`` when a source exists and ``/sitemap.xml`` or ``/robots.txt`` does not resolve under ``ROOT_URLCONF``.

``check_robots_disallow``.
   Warns with ``next.W100`` for a ``Disallow`` covering a URL the sitemap lists or the sitemap address, and with ``next.W101`` for one covering a ``noindex`` page.

``check_seo_sources_below_root``.
   Warns with ``next.W102`` for a ``sitemap.py``, ``robots.py``, or ``robots.txt`` below the top of its page tree.

.. automodule:: next.seo.checks
   :members:
   :no-index:

See also
--------

.. seealso::

   :doc:`/content/topics/seo/sitemaps` and :doc:`/content/topics/seo/robots` for the topic guides.
   :doc:`/content/howto/publish-a-sitemap` for the recipe.
   :doc:`/content/internals/seo-pipeline` for the discovery, the registry, the URL slot, and the reset story.
   :doc:`system-checks` for every code in the shared tables.
