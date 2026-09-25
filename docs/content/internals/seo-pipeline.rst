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
       Lazy --> Views["views<br/>sitemap_view, robots_view"]
       Views --> Sitemaps["sitemaps<br/>RouteSitemap"]
       Registry --> Sitemaps
       Views --> Robots["robots<br/>RobotsRules, RobotsFile"]
       Manager --> Robots

Discovery
---------

``discover_seo_roots`` walks the backends of the router manager and, for each root a backend reports, probes the three file names at the top of the tree.
A root two backends report is visited once, and the roots keep router order, which is the order the robots selection and the sitemap index follow.
Each root becomes a ``SeoRoot`` carrying the page root, its natural label and the section it is served as, the loaded ``sitemap`` and ``robots`` sources, the path of a static ``robots.txt``, and the directory names the router skips.

The section label is the label of the innermost installed application whose directory contains the root, and for a ``DIRS`` root the slugified directory name, ``root`` when the slug is empty.
``unique_sections`` hands the labels out in router order, and a label already handed out gains the lowest ``-N`` suffix that no other tree takes as its natural label, so a suffix never lands on a label a later tree owns.
``next.W104`` warns about the clashing trees with the sections they are served as, since those numbered addresses follow router order.

``load_seo_source`` loads a module source once, at discovery, through ``load_page_module``, the same mtime-keyed memo that executes a ``page.py``, so a re-executed ``sitemap.py`` re-registers its callables.
The resulting ``SeoSource`` holds the path, the module, and the import error, and it stays on the root until a reset, so no request reads the file again.
An edited source therefore takes effect when the manager resets and the next read discovers the tree again, and the development server gets there by restarting on the edit.
A module that fails to import is logged and keeps its error on the source for ``next.E110``, and it serves nothing, so the routes still build and the site keeps serving.

Registry
--------

``sitemap.items("trail")`` reads the file running the decorator off the calling frame and registers a ``SitemapItemsEntry`` of that file, the trail, and the callable.
Where the callable itself is defined does not matter, so a ``sitemap.py`` may import it from a helper module and decorate it there.
The ``RouteSitemap`` of a root reads back the entries of the ``sitemap.py`` at its top through ``SeoRoot.items_entries``, which is why ``next.W102`` reports a file below the top and ``next.E118`` reports the decorator run in any file other than that one.
The registry keeps an ordered list and a dict index keyed by ``(file, trail)``, replaces an earlier binding of the same key, moves its version on every write, and sends ``sitemap_items_registered`` with the running file, the trail, and the callable.

Manager
-------

``seo_manager`` memoises the discovered roots with their loaded sources and the selected robots source, and hands out a version from a process-wide counter that ``reset`` advances and the cached sitemap view is keyed on.
``has_sitemap`` answers ``serves_sitemap`` of ``next.seo.sitemaps``, which is false while no ``sitemap.py`` imported or ``NOINDEX`` is on, and every sitemap path reads it.
``sitemaps(request)`` answers nothing while ``has_sitemap`` is false and otherwise builds a fresh ``RouteSitemap`` per declaring root on every call, because one instance serves one request and memoises its item list on itself.
``cache_seconds`` answers the smallest positive ``cache`` any ``sitemap.py`` declares, a bool not counting as seconds.
``robots_source`` picks the first serving candidate ``robots_candidates`` answers, in root order with ``robots.py`` ahead of ``robots.txt`` within a root, and logs one warning listing the sources it passed over.

Building a section
------------------

``RouteSitemap`` reads its Django attributes off the module once through ``SitemapOptions.read``, the single reader the checks share.
The reader takes a wrong shape as unset, so a ``limit`` of ``True`` reads as the default 50000 and a ``priority`` of ``True`` as no priority.
The ``items`` of the section reads the trails of the tree through ``SeoRoot.trails``, a walk held on the root until the manager resets.
Every static trail is kept unless a glob in ``exclude`` matches it or the static metadata fold of its ``page.py`` says ``noindex``, the filter ``listed_trails`` applies for the build and the checks alike.
``is_excluded`` compiles each glob with ``*`` and ``?`` as the only wildcards and everything else literal, brackets included, and matches it against the whole trail.
``static_noindex`` reads a chain the schema refuses as indexed and logs a warning, so a broken ``metadata`` dict never takes the sitemap down.

Every registered callable of the root is then resolved through the dependency resolver with the request, when there is one, and each yielded ``Entry`` or mapping becomes a ``SitemapItem`` of the trail.
A declared item replaces the automatic one of the same trail and kwargs, and two declared items sharing a key collapse to the first with a logged warning.
A trail the walk did not find raises ``SitemapTrailError``.
``lastmod`` answers the value through ``lastmod_datetime``, an aware datetime with a date or a naive value placed in the current time zone, so dates and datetimes compare and the index view reads them the same way.

``location`` reverses through ``page_reverse`` per call, so Django's ``i18n`` loop, which asks for the location under each language, gets the prefixed path.
``get_domain`` and ``get_protocol`` read the ``base`` of the site-wide metadata defaults ahead of the site the request derived, and a build with neither raises ``SitemapOriginError``.

The URL slot
------------

``next.seo`` imports ``next.urls`` for the router manager and the reverse helper, so ``next.urls.manager`` cannot import the routes back.
``SeoRoutes`` in ``next.ports`` declares ``patterns``, ``SeoRoutesImpl`` in ``next.seo.ports`` implements them over the manager, and ``NextFrameworkConfig.ready()`` binds the implementation into ``seo_routes_slot``.
``_LazyUrlPatterns`` concatenates the router patterns, the form-action patterns, and the slot's patterns as its third source, and its ``version_token`` stays the pair of the router and form-action versions.
The seo sources change only through a router reload, which moves the router version, so no seo version joins the token.
The concat reads the slot through ``PortSlot.peek``, and one built before ``NextFrameworkConfig.ready()`` leaves the seo routes out and caches nothing, so an application ``ready()`` that resolves or reverses a URL ahead of the framework still works.
``patterns`` answers the two sitemap routes while ``has_sitemap`` holds and the robots route while a robots source exists, so each route is spliced only with its source, and an empty answer is what leaves a project's own pattern below ``include("next.urls")`` in charge of that address.

Views
-----

``sitemap_view`` is a thin entry point that hands the request to the real view through a single-slot wrapper, which wraps the sitemap view in :func:`~django.views.decorators.cache.cache_page` when the manager reports a ``cache`` and memoises the wrapped callable against the manager version, while ``robots_view`` answers directly and is never cached.
The sitemap view asks the manager for the sections, answers 404 without any, which covers ``NOINDEX`` on a ``next.seo.urls`` mount whose routes always exist, and serves the index when there is more than one section or a section paginates.
The index is the framework's own, built from ``get_protocol`` and ``get_domain`` of each section so ``base`` applies, and it reverses the section route in the namespace the request arrived through, ``next`` or ``next_seo``.
A single unpaginated section is handed to Django's ``sitemap`` view as is.

Both views first reverse the route they matched in the ``next_seo`` namespace, and a request that arrived through another mount at a different address answers 404, so an ``include("next.urls")`` under a prefix or inside ``i18n_patterns`` never serves a second copy beside the host root.
The robots view answers the bytes of a static file, or renders the declared rules with the absolute sitemap URL while ``has_sitemap`` holds.

Resets
------

Three events move the manager version.

- ``router_reloaded``, connected in ``ready()``, drops the discovered roots and the robots source, since a reload can change which roots exist.
- ``settings_reloaded`` drops the same and leaves the items registry alone, because a memoised ``sitemap.py`` is not re-executed by a reload and its registrations would otherwise be lost until the file moved on disk.
- ``reset_check_caches`` calls ``next.seo.manager.reset_seo_sources``, which drops both and the items registry as well, and the same call drops the page-module memo and the run memo of the SEO roots, so a check run after an in-place edit of the tree re-executes ``sitemap.py`` and repopulates the registry from its current source.

The version drops the cached ``cache_page`` wrapper of the sitemap view.
The lazy pattern concat follows the router version instead, which the reload behind the first event moves.

Autoreload
----------

The development watcher adds ``sitemap.py``, ``robots.py``, and ``robots.txt`` at the top of every page root to its watch specs, beside the ``**/page.py`` glob of the tree.
A change to one of them restarts the process like a ``page.py`` change does.
See :doc:`autoreload` for the spec list and the reload decisions.

Checks
------

``loaded_seo_roots`` in ``next.seo.checks.roots`` runs ``discover_seo_roots`` itself and answers the ``SeoRoot`` objects the runtime builds, routed trails included, so every check reads the sources, the modules, and the import errors the runtime loads.
It keeps the list in a ``RunMemo`` keyed on the router manager, so the checks of one run share a single discovery.
The checks then call the helpers the runtime calls, ``listed_trails``, ``serves_sitemap``, and ``is_excluded`` for the sitemap and ``declared_rules`` and ``robots_candidates`` for robots, rather than restating them.
The route collisions and the host-root check resolve ``/sitemap.xml`` and ``/robots.txt`` against ``ROOT_URLCONF`` and compare the matched view with the framework's, which is how an include under a prefix and a pattern ahead of the include are told apart.
``route_paths`` in ``next.seo.checks.robots`` gives the robots checks a URL for every trail, reversing a dynamic one with a placeholder its converter accepts and cutting the result at the first parameter, and it skips a trail whose converter takes no placeholder.
See :doc:`/content/ref/seo` for the check list and :doc:`/content/ref/system-checks` for the codes.

See also
--------

.. seealso::

   :doc:`/content/topics/seo/sitemaps` and :doc:`/content/topics/seo/robots` for the project-facing behaviour.
   :doc:`url-router` for the lazy pattern sequence the routes join.
   :doc:`page-discovery` for the module memo the sources share with ``page.py``.
   :doc:`/content/ref/ports` for the slot contract.
