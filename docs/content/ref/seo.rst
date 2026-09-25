.. _ref-seo:

SEO reference
=============

Module summary
--------------

``next.seo`` serves ``/sitemap.xml``, ``/sitemap-<section>.xml``, and ``/robots.txt`` from the ``sitemap.py``, ``robots.py``, and ``robots.txt`` at the top of every page root, on top of :doc:`django:ref/contrib/sitemaps`.
It exports the ``Entry`` and ``Rule`` value objects, the ``sitemap`` declaration object whose ``items`` decorator binds a callable to a route trail, the ``RouteSitemap`` one page tree builds, the ``seo_manager`` façade over the discovered sources, and the two exceptions ``SitemapOriginError`` and ``SitemapTrailError``.
The page metadata a ``page.py`` declares belongs to :doc:`pages`, and the sitemap reads its static fold to skip a ``noindex`` route.

Public API
----------

Markers
~~~~~~~

``Entry`` is what an ``@sitemap.items`` callable yields, the kwargs that reverse the trail plus the optional ``lastmod``, ``changefreq``, and ``priority`` of that one URL.
``Rule`` is one ``User-agent`` group of a ``robots.py``, and its sequences are pinned as tuples so a list shared between two groups cannot move under one of them.
A bare string for ``user_agent``, ``allow``, or ``disallow`` counts as one value, so ``Rule(disallow="/private/")`` disallows one path.

.. automodule:: next.seo.markers
   :members:

Declaration and manager
~~~~~~~~~~~~~~~~~~~~~~~

``sitemap`` is the module-level declaration object, and ``sitemap.items("trail")`` registers the decorated callable under the ``sitemap.py`` that runs the decorator, wherever the callable itself is defined.
``seo_manager`` memoises the discovered roots, their loaded sources, and the one robots source until a reset, answers a fresh ``RouteSitemap`` per root and request, and carries the version the cached sitemap view keys on.
``reset_seo_sources`` drops the manager state and every ``@sitemap.items`` registration together, which is how ``reset_check_caches`` rebuilds the sources from disk.
``has_sitemap`` is false while no ``sitemap.py`` imported or ``NOINDEX`` is on, and every sitemap route reads it.

.. automodule:: next.seo.manager
   :members:

Sitemaps
~~~~~~~~

``RouteSitemap`` subclasses :class:`django.contrib.sitemaps.Sitemap` for one page tree.
Its items are the static routes of the tree, less the ``noindex`` and excluded ones, plus every entry the registered callables yield, and ``location`` reverses each item lazily so an active language prefix lands in the path.
``get_protocol`` and ``get_domain`` read the ``base`` of the site-wide metadata defaults ahead of the site the request derived.

An entry declared for a static trail replaces the automatic item of that URL, and ``lastmod`` answers every value as an aware datetime through ``lastmod_datetime``, a date or a naive value placed in the current time zone.
``SitemapOptions``, ``listed_trails``, ``serves_sitemap``, ``is_excluded``, and ``static_noindex`` are the helpers the build and the system checks share, so a check reads a trail the way the served document does.
``is_excluded`` treats ``*`` and ``?`` as the only wildcards and brackets as literal, and ``static_noindex`` reads a chain the schema refuses as indexed with a logged warning.

.. automodule:: next.seo.sitemaps
   :members:

Robots
~~~~~~

``RobotsRules`` holds what a ``robots.py`` declares and renders it with the ``Sitemap:`` line the view passes in.
``RobotsFile`` holds a static ``robots.txt`` and re-reads it when its mtime moves.
``robots_candidates`` orders every source the way the route prefers them and ``declared_rules`` reads the ``Rule`` groups of a module, and the route and the checks both call them.

.. automodule:: next.seo.robots
   :members:

Views
~~~~~

``sitemap_view`` answers ``/sitemap.xml`` and ``/sitemap-<section>.xml``, and ``robots_view`` answers ``/robots.txt``.
The sitemap view answers 404 while no sitemap is served, and the robots view leaves the ``Sitemap:`` line out of a generated robots in the same case.
The sitemap view is wrapped in :func:`~django.views.decorators.cache.cache_page` when a ``sitemap.py`` declares ``cache``, once per manager version, and the robots view is never cached.
The index a multi-root or paginating site answers at ``/sitemap.xml`` is the framework's own view, so ``base`` applies to it where Django's index reads the request host.

.. automodule:: next.seo.views
   :members:

The urls include
~~~~~~~~~~~~~~~~

``next.seo.urls`` is a URLconf module mounting the same three routes under the ``next_seo`` namespace, for a project whose ``include("next.urls")`` sits under a prefix or inside :func:`~django.conf.urls.i18n.i18n_patterns`.
Once it serves the host root, the copies that include mounts at another address answer 404, so each document is served once.

.. code-block:: python
   :caption: config/urls.py

   from django.urls import include, path

   urlpatterns = [
       path("", include("next.seo.urls")),
       path("app/", include("next.urls")),
   ]

The include lists the routes whether or not a source exists, and a route without one answers 404, as the sitemap routes do under ``NOINDEX``.
The routes ``include("next.urls")`` carries exist only while a source does, under the ``next`` namespace as ``next:sitemap``, ``next:sitemap_section``, and ``next:robots``.

Discovery and registry
~~~~~~~~~~~~~~~~~~~~~~

``discover_seo_roots`` probes every page root the routers report and answers one ``SeoRoot`` per tree with its natural ``label``, the ``section`` it is served as, and the sources found at its top.
``load_seo_source`` loads a ``sitemap.py`` or a ``robots.py`` once, into a ``SeoSource`` that holds the path, the module, and the import error when there is one, and the root carries the two as ``sitemap`` and ``robots``, with ``sitemap_module`` and ``robots_module`` answering the module alone.
``discover_seo_roots`` takes each label from the innermost application holding the tree and hands the labels out through ``unique_sections``, where a repeat takes the lowest ``-N`` suffix no other tree owns as its label.
``SeoRoot.trails`` is a cached property that walks the page tree once and holds the trails until the manager rediscovers the roots.
``sitemap_path`` answers where the ``sitemap.py`` of the tree sits, present or not, and ``items_entries`` answers the ``(trail, func)`` pairs that file registered.

``sitemap_items_registry`` holds the ``@sitemap.items`` callables keyed by the running file and trail, the last registration per key winning.
Each ``SitemapItemsEntry`` records the ``file`` that ran the decorator, the ``trail``, and the ``func``.

.. automodule:: next.seo.discovery
   :members:

.. automodule:: next.seo.registry
   :members:

Errors
~~~~~~

``SitemapOriginError`` is raised by a build that has neither a request nor a ``base`` to make its URLs absolute with.
``SitemapTrailError`` is raised by a build whose ``@sitemap.items`` names a trail the tree does not route, the condition ``next.E111`` reports ahead of the build.
Its ``file`` names the ``sitemap.py`` that registered the callable and its ``trail`` the unrouted trail, and the message reads ``@sitemap.items('<trail>') in <file> names a trail no page under <file.parent> routes``.
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
   Sent by ``SitemapItemsRegistry`` on every registration, with the ``file``, ``trail``, and ``func`` keyword arguments.
   ``file`` is the module that ran the decorator.
   The sender is the ``SitemapItemsRegistry`` class.

.. automodule:: next.seo.signals
   :members:
   :no-index:

See :doc:`signals` for the aggregator and :doc:`/content/topics/signals` for the receiver patterns.

Checks
------

``next.seo.checks`` is a package, and importing it registers every check below.
Each one runs in the default tier of ``manage.py check`` under the ``seo`` and ``urls`` tags beside ``next``.
The conditions live in :doc:`system-checks`, and the table maps each callable to its codes and its submodule.

.. list-table::
   :header-rows: 1
   :widths: 40 30 30

   * - Check
     - Codes
     - Submodule
   * - ``check_seo_module_imports``
     - ``next.E110``
     - ``sources``
   * - ``check_seo_module_attributes``
     - ``next.E113``
     - ``sources``
   * - ``check_sitemap_items_files``
     - ``next.E118``
     - ``sources``
   * - ``check_seo_sources_below_root``
     - ``next.W102``
     - ``sources``
   * - ``check_sitemap_items_trails``
     - ``next.E111``
     - ``sitemaps``
   * - ``check_sitemap_templates``
     - ``next.E112``
     - ``sitemaps``
   * - ``check_sitemap_section_labels``
     - ``next.W104``
     - ``sitemaps``
   * - ``check_sitemap_dynamic_routes``
     - ``next.W097``
     - ``sitemaps``
   * - ``check_sitemap_noindex_items``
     - ``next.W098``
     - ``sitemaps``
   * - ``check_robots_single_source``
     - ``next.E114``
     - ``robots``
   * - ``check_robots_file``
     - ``next.E117``, ``next.W103``
     - ``robots``
   * - ``check_robots_disallow``
     - ``next.W100``, ``next.W101``
     - ``robots``
   * - ``check_seo_route_collisions``
     - ``next.E115``
     - ``routes``
   * - ``check_seo_routes_at_host_root``
     - ``next.W099``
     - ``routes``

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
