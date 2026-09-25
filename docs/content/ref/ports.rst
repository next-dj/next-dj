.. _ref-ports:

Ports reference
===============

Module summary
--------------

``next.ports`` holds the narrow protocols one subsystem calls another through.
Each port is a pair of a ``Protocol`` that states the method contract the caller depends on and a slot object that holds the one implementation composed at startup.
The caller imports the slot instead of the implementing subsystem, so the two areas stay decoupled while the call still lands on real code.
Each slot is bound once, in ``NextFrameworkConfig.ready()``, and nothing rebinds it afterwards, which is what separates it from the settings-driven backend managers in :doc:`backends` that rebuild themselves on a settings reload.

``PortSlot`` is the shared holder every port uses.
It is built with the subject its message names, and it starts unbound and raises :class:`~django.core.exceptions.ImproperlyConfigured` naming that subject when read before the app finished starting, which is the type the system checks already report as a configuration error.

``PartialShaper`` shapes page and form responses for partial requests.
The parsed ``PartialIntent`` of ``next.partial.headers`` travels between its methods, so a shape method never re-reads the request headers.
``next.pages`` and ``next.forms`` read ``partial_shaper_slot`` on the request path, first to ask whether a request is partial at all and then to shape the response when it is.
Neither subsystem imports ``next.partial``.
``intent`` answers what the headers ask for, ``zone_response``, ``shape_response``, and ``shape_validate`` each build one envelope, and ``set_vary`` declares the request headers a response was negotiated on, which a full page and a zone envelope answering one URL both need for a shared cache to tell them apart.

``PageScan`` is the page-tree scan the checks and the discovery helpers run, a single ``load_scanned_page_modules`` that executes every routed ``page.py`` and answers the ones that loaded.
``next.pages.scan`` reads the router manager from ``next.discovery``, so discovery reaches the scan back through ``page_scan_slot`` rather than through an import that would close the loop.

``RouterAccess`` builds router backends and router managers and answers the URL pattern parser, and it answers the concrete classes of ``next.urls`` rather than an abstraction over routing, because it exists to defer an import and nothing else.
``next.urls`` routes to pages and so imports ``next.pages``, which leaves the page watcher and the system checks needing routers from the other direction.
They read ``router_access_slot`` instead, at watch time and at check time.
``url_parser`` answers the parser the file router routes bracket segments through, for a caller that has a route string and no router to ask.

``StaticAssets`` is the static-manager surface one page render calls, a collector, page and component asset discovery, and placeholder injection.
``next.static`` reads page trees and page modules and so imports ``next.pages``, so the render path reads ``static_assets_slot`` rather than importing the static manager back.
``collect_component_assets`` folds the co-located assets of one composite component into a caller-supplied collector, which is how a component render reaches the static pipeline from the same side.
Every method resolves the manager when it is called rather than when the slot is bound, so a settings reload that drops the wrapped manager still reaches every later render.

``SeoRoutes`` answers the sitemap and robots routes the lazy urlpatterns of ``next.urls`` append as their third pattern source, and the version those routes were built under.
``next.seo`` imports ``next.urls`` for the router manager and the reverse helper, so the pattern concat reads ``seo_routes_slot`` rather than importing the seo area back.
``patterns`` answers the two sitemap routes while a ``sitemap.py`` exists and the robots route while a robots source does, and ``version`` is the seo manager version that joins the router and form-action versions in the three-part token the concat is cached against.

Implementations
---------------

Each area binds its own implementation from a ``ports`` module of its own, one class per port, holding nothing but the delegation to the area's real entry points.

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * - Port
     - Implementation
     - Slot
   * - ``PageScan``
     - ``next.pages.ports.PageScanImpl``
     - ``page_scan_slot``
   * - ``PartialShaper``
     - ``next.partial.ports.PartialShaperImpl``
     - ``partial_shaper_slot``
   * - ``RouterAccess``
     - ``next.urls.ports.RouterAccessImpl``
     - ``router_access_slot``
   * - ``SeoRoutes``
     - ``next.seo.ports.SeoRoutesImpl``
     - ``seo_routes_slot``
   * - ``StaticAssets``
     - ``next.static.ports.StaticAssetsImpl``
     - ``static_assets_slot``

``next.apps`` binds all five in ``NextFrameworkConfig.ready()``, ahead of every step that imports user code.
A project that replaces one subclasses the shipped implementation and calls ``set`` on the slot from the ``ready()`` of an application listed after ``next`` in ``INSTALLED_APPS``, since the slot holds one implementation and the last binding wins.

Public API
----------

.. automodule:: next.ports
   :members:

See also
--------

.. seealso::

   :doc:`apps` for the startup step that binds the slots.
   :doc:`partial` for the subsystem that implements ``PartialShaper``, and :doc:`seo` for the one that implements ``SeoRoutes``.
   :doc:`/content/internals/overview` for where ``next.ports`` sits in the subsystem dependency graph.
