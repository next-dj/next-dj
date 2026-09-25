.. _internals-seo-pipeline:

SEO pipeline
============

This page covers how the seo subsystem finds the ``sitemap.py``, ``robots.py``, and ``robots.txt`` of every page root, how the ``@sitemap.items`` registrations reach a ``RouteSitemap``, how the routes join the lazy urlpatterns without a circular import, and what resets the memos.

.. contents::
   :local:
   :depth: 2

Components
----------

.. mermaid::

   flowchart LR
       Router["next.urls router_manager"] --> Discovery["discovery<br/>discover_seo_roots"]
       Discovery --> Loader["next.pages.loaders<br/>load_page_module"]
       Loader -- "executes sitemap.py" --> Registry["registry<br/>sitemap_items_registry"]
       Discovery --> Manager["manager<br/>seo_manager"]
       Manager --> Ports["ports<br/>SeoRoutesImpl"]
       Ports --> Slot["next.ports<br/>seo_routes_slot"]
       Slot --> Lazy["next.urls.manager<br/>_LazyUrlPatterns"]
       Lazy --> Views["views<br/>sitemap, robots"]
       Views --> Sitemaps["sitemaps<br/>RouteSitemap"]
       Registry --> Sitemaps
       Views --> Robots["robots<br/>RobotsRules, RobotsFile"]
       Manager --> Robots

Discovery
---------

``discover_seo_roots`` walks the backends of the router manager and, for each root a backend reports, probes the three file names at the top of the tree.
A root two backends report is visited once, and the roots keep router order, which is the order the robots selection and the sitemap index follow.
Each root becomes a ``SeoRoot`` carrying the page root, its section label, the paths of the sources found, and the directory names the router skips.

The section label is the label of the installed application whose directory contains the root, and for a ``DIRS`` root the slugified directory name, ``root`` when the slug is empty.
A label already handed out gains a ``-2`` suffix, and ``next.E116`` names the pair.

A module source is loaded through ``load_page_module``, the same mtime-keyed memo that executes a ``page.py``, so a ``sitemap.py`` runs once per mtime and a re-executed one re-registers its callables.
A module that fails to import is logged, reported by ``next.E110``, and treated as absent, so the routes still build and the site keeps serving.

Registry
--------

``sitemap.items("trail")`` reads the file that defines the decorated callable and registers the callable under that file's directory and the trail.
The directory is the key the ``RouteSitemap`` of a root reads back, which is why the file has to sit at the top of the root and why ``next.W102`` reports one below it.
The registry keeps an ordered list and a dict index keyed by ``(root, trail)``, replaces an earlier binding of the same key, moves its version on every write, and sends ``sitemap_items_registered`` with the root, the trail, and the callable.

Manager
-------

``seo_manager`` memoises the discovered roots and the selected robots source, and hands out a version from a process-wide counter that ``reset`` advances.
``sitemaps(request)`` builds a fresh ``RouteSitemap`` per declaring root on every call, because one instance serves one request and memoises its item list on itself.
``cache_seconds`` answers the smallest positive ``cache`` any ``sitemap.py`` declares.
``robots_source`` picks the first source in root order, ``robots.py`` ahead of ``robots.txt`` within a root, and logs one warning listing the sources it passed over.

Building a section
------------------

``RouteSitemap`` reads its Django attributes off the module once, and its ``items`` walks the page tree on first read.
Every static trail is kept unless a glob in ``exclude`` matches it or the static metadata fold of its ``page.py`` says ``noindex``.
Every registered callable of the root is then resolved through the dependency resolver with the request, when there is one, and each yielded ``Entry`` or mapping becomes a ``SitemapItem`` of the trail.
A trail the walk did not find raises ``SitemapTrailError``, and two items sharing a trail and kwargs collapse to the first with a logged warning.
``location`` reverses through ``page_reverse`` per call, so Django's ``i18n`` loop, which asks for the location under each language, gets the prefixed path.
``get_domain`` and ``get_protocol`` read the ``base`` of the site-wide metadata defaults ahead of the site the request derived, and a build with neither raises ``SeoBaseError``.

The URL slot
------------

``next.seo`` imports ``next.urls`` for the router manager and the reverse helper, so ``next.urls.manager`` cannot import the routes back.
``SeoRoutes`` in ``next.ports`` declares ``patterns`` and ``version_source``, ``SeoRoutesImpl`` in ``next.seo.ports`` implements them over the manager, and ``NextFrameworkConfig.ready()`` binds the implementation into ``seo_routes_slot``.
``_LazyUrlPatterns`` concatenates the router patterns, the form-action patterns, and the slot's patterns as its third source, and its ``version_token`` is the three-part tuple of the router, form-action, and seo versions, so a reset of the manager rebuilds the concat and the resolver's route index.
``patterns`` answers the two sitemap routes while a ``sitemap.py`` exists and the robots route while a robots source does, and an empty answer is what leaves a project's own pattern after the include in charge.

Views
-----

``sitemap`` is a thin entry point that hands the request to the real view through a single-slot wrapper, which wraps the sitemap view in :func:`~django.views.decorators.cache.cache_page` when the manager reports a ``cache`` and memoises the wrapped callable against the manager version, while ``robots`` answers directly and is never cached.
The sitemap view asks the manager for the sections, answers 404 without any, and serves the index when there is more than one section or a section paginates.
The index is the framework's own, built from ``get_protocol`` and ``get_domain`` of each section so ``base`` applies, and it reverses the section route in the namespace the request arrived through, ``next`` or ``next_seo``.
A single unpaginated section is handed to Django's ``sitemap`` view as is.
The robots view answers the bytes of a static file, or renders the declared rules with the absolute sitemap URL when a sitemap exists.

Resets
------

Three events move the manager version.

- ``router_reloaded``, connected in ``ready()``, drops the discovered roots and the robots source, since a reload can change which roots exist.
- ``settings_reloaded`` drops the same and leaves the items registry alone, because a memoised ``sitemap.py`` is not re-executed by a reload and its registrations would otherwise be lost until the file moved on disk.
- ``reset_check_caches`` drops both, the items registry, and the page-module memo as well, so a check run after an in-place edit of the tree re-executes ``sitemap.py`` and repopulates the registry from its current source.

The version reaching ``version_token`` rebuilds the lazy pattern concat, and the same version drops the cached ``cache_page`` wrapper of the sitemap view.

Autoreload
----------

The development watcher adds ``sitemap.py``, ``robots.py``, and ``robots.txt`` at the top of every page root to its watch specs, beside the ``**/page.py`` glob of the tree.
A change to one of them restarts the process like a ``page.py`` change does.
See :doc:`autoreload` for the spec list and the reload decisions.

Checks
------

``loaded_seo_roots`` in ``next.seo.checks`` walks the same roots the discovery does and builds a ``CheckedRoot`` per tree with the routed trails, the loaded sources, and any import error, so every check reads one pass.
The route collisions and the host-root check resolve ``/sitemap.xml`` and ``/robots.txt`` against ``ROOT_URLCONF`` and compare the matched view with the framework's, which is how an include under a prefix and a pattern ahead of the include are told apart.
See :doc:`/content/ref/seo` for the check list and :doc:`/content/ref/system-checks` for the codes.

See also
--------

.. seealso::

   :doc:`/content/topics/seo/sitemaps` and :doc:`/content/topics/seo/robots` for the project-facing behaviour.
   :doc:`url-router` for the lazy pattern sequence the routes join.
   :doc:`page-discovery` for the module memo the sources share with ``page.py``.
   :doc:`/content/ref/ports` for the slot contract.
