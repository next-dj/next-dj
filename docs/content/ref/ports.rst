.. _ref-ports:

Ports reference
===============

Module summary
--------------

``next.ports`` holds the narrow protocols one subsystem calls another through.
Each port is a pair of a ``Protocol`` that states the method contract the caller depends on and a slot object that holds the one implementation composed at startup.
The caller imports the slot instead of the implementing subsystem, so the two areas stay decoupled while the call still lands on real code.
Each slot is bound in ``NextFrameworkConfig.ready()`` and never rebuilt on a settings reload, which is what separates it from the settings-driven backend managers in :doc:`backends`.

``PortSlot`` is the shared holder every port uses.
It is built with the subject its message names, and it starts unbound and raises :class:`~django.core.exceptions.ImproperlyConfigured` naming that subject when read before the app finished starting, which is the type the system checks already report as a configuration error.

The page view in ``next.views`` sits above ``next.partial`` and calls it directly, and a form backend answers through the ``ResponseShaper`` its ``OPTIONS["SHAPER"]`` entry names, a dotted path ``next.forms`` imports only when a backend first shapes a response, see :doc:`forms`.
The async runtime of ``next.bridge`` sits in the kernel below every area that runs ``async def`` code from sync code, so those areas import it and no port leads to it, see :doc:`bridge`.

``PageScan`` is the page-tree scan the checks and the discovery helpers run, a single ``load_scanned_page_modules`` that executes every routed ``page.py`` and answers the ones that loaded.
``next.pages.scan`` reads the route manager from ``next.discovery``, so discovery reaches the scan back through ``page_scan_slot`` rather than through an import that would close the loop.

``RouteAccess`` builds page backends and route managers and answers the URL pattern parser, and it answers the concrete classes of ``next.urls`` rather than an abstraction over routing, because it exists to defer an import and nothing else.
``next.urls`` routes to pages and so imports ``next.pages``, which leaves the page watcher and the system checks needing routers from the other direction.
They read ``route_access_slot`` instead, at watch time and at check time.
``url_parser`` answers the parser the file router routes bracket segments through, for a caller that has a route string and no router to ask.
``page_kwargs`` answers the URL kwargs a page at a trail takes, or ``None`` when the router refuses the trail, which the intercept checks use to compare a target with an intercept.

``StaticAssets`` is the static-manager surface one page render calls, a collector, page and component asset discovery, and placeholder injection.
``next.static`` reads page trees and page modules and so imports ``next.pages``, so the render path reads ``static_assets_slot`` rather than importing the static manager back.
``collect_component_assets`` folds the co-located assets of one composite component into a caller-supplied collector, which is how a component render reaches the static pipeline from the same side.
``runtime_loads`` answers whether a page loads the runtime script, which the error UI of every boundary reads to tell whether it can offer a retry.
Every method resolves the manager when it is called rather than when the slot is bound, so a settings reload that drops the wrapped manager still reaches every later render.

``PageViews`` builds the view a routed page answers with, or ``None`` for a page with no body.
``next.views`` composes the request on top of ``next.urls``, so the file router reads ``page_views_slot`` rather than importing the page view factory.

``SeoRoutes`` answers the SEO routes the lazy urlpatterns of ``next.urls`` append as their third pattern source.
``next.seo`` imports ``next.urls`` for the route manager and the reverse helper, so the pattern concat reads ``seo_routes_slot`` rather than importing the seo area back.
``patterns`` answers the routes whose source exists.
The port carries no version.
The concat's cache token reads ``seo_routes_version`` in ``next.urls.manager``, the counter of the spliced SEO routes that ``NextFrameworkConfig.ready()`` moves right after it binds the slot and the seo manager moves on every reset of its sources, so a resolve reads the token without a call across the port.

``PageScripts`` is one area's part in the head, the init payload and the declared bundles of every page render.
Unlike a slot it is a list, ``page_scripts``, a ``PortList`` that holds any number of providers in rank order, so the injector of ``next.static`` names no feature and a new one joins without a change to the static area.
Each provider has a ``name``, a ``priority`` that ranks it, the lowest first and a tie kept in registration order, and the ``keys`` of the init payload it writes, which page context may not use.
``applies`` answers whether it takes part in any render under the loaded settings, and the injector asks it once per settings load, not per request.
``share`` receives the render as a ``ScriptRender`` and answers a ``ScriptShare``, or ``None`` for nothing.
A ``ScriptRender`` names the request, the ``page.py``, the collector, whether the page loads the runtime and whether it streams, mints the nonce on demand, and resolves the URL of a declared bundle.
Its ``entries`` hold the request entries of every provider once all of them ran, which is how the router hands the scripts entries to a navigation.
The parts of a ``ScriptShare`` merge in rank order.

* ``head`` holds tags written before ``</head>``.
* ``entries`` holds payload values of this request alone, encoded with the page context.
* ``shared`` holds encoded entries every request of one configuration repeats, written after ``$chunks`` and escaped once per value.
* ``after`` writes markup after the init script, given the request nonce.
* ``eager`` names declared bundles the page needs at once, their tags first and loaded ``async``.
* ``bundles`` names declared bundles the page needs, loaded as its urgency says.
* ``listed`` names the optional ``$chunks`` entries the page turns on, such as ``ws``.
* ``enclose`` wraps the output of every filled placeholder, ``stylesheet`` adds attributes to every stylesheet tag, and ``finish`` runs over the finished HTML in place of the pass the view binds.

The shipped providers rank ``release`` 100 for ``$version`` and ``$protocol`` (``next.partial.release.ReleaseScripts``), ``realtime`` 200 for ``$realtime`` and the ``ws`` chunk (``next.realtime.offer.RealtimeScripts``), ``stream`` 300 for ``$stream`` and the boundaries reader (``next.defer.payload.StreamScripts``), ``router`` 400 for ``$router``, the layouts block and the enclosure (``next.router.render.RouterScripts``), and ``scripts`` 500 for the third-party scripts (``next.scripts.ports.PageScriptsImpl``).
With none registered a page still renders, its payload holding only the context, ``$csrf`` and ``$chunks``.

``ActionOwners`` answers the ``page.py`` of every directory whose files declare an ``@action``, the file whose rule such an action answers to.
The forms area imports ``next.pages``, so the body-source check ``next.E012`` reads ``action_owners_slot`` rather than importing ``next.forms`` back, and a ``page.py`` with no body that serves an action passes.
The intercept check ``next.E174`` reads the same slot, so a replaced binding moves both checks together, while ``next.E166`` and ``next.W128`` live in the forms area and read its registry directly.

``FormRoutes`` answers the URL patterns of every form backend.
The forms area imports ``next.pages``, which loads the URL checks, so those checks read ``form_routes_slot`` rather than importing ``next.forms.manager`` back.

``DeferralSink`` takes a deferred component call whose template context holds no sink of its own, and answers the text the render holds in its place.
``next.defer`` sits above the template tags and the render paths, so a component call and a render reach the assembled-inline sink through ``deferrals_slot`` rather than importing it.
``queue`` queues a job into the session of the response a ``deferring`` block opened and answers the loading wrapper, and outside such a block it renders the call in place.
``inline`` renders the call in place inside its settled wrapper.
``assembled`` runs one render inside a ``deferring`` block, runs the jobs the render queued in parallel, and answers the render with every outcome spliced into its wrapper, the assets of each job merged into the collector before injection.
Callers go through ``render_deferred``, which renders plainly when no sink is bound.
A page render, a zone GET, a form re-render, a ``Patches`` context, and a ``next.testing`` render all reach it, while a page view opens its own block with the sink that asks the delivery policy.
When no sink is bound, a deferred component renders in place behind its error boundary, without a wrapper.

``ServerLoad`` answers the backlog of the deferred work of the process, the wait of the oldest queued job once it passes ``QUEUE_TIMEOUT``.
``next.router`` and ``next.defer`` are features that never import each other, so the shipped prefetch shed reads ``server_load_slot`` instead.
Callers go through ``server_backlog``, which answers ``None`` when no load is bound, and the router then sheds no prefetch.

``RouterStamp`` sets the ``X-Next-Router`` directives of the client router on a response, the ``clear`` a login, a logout, or a cycled session key sends among them.
``next.router`` sits above ``next.forms``, so a form or ``@action`` response reaches it through ``router_stamp_slot`` rather than importing it.
Callers go through ``stamp_router``, which leaves the response untouched when no stamp is bound.

``ZoneMarking`` binds the active-link pass over the zone HTML an action response renders, so the zones it re-renders are marked for the page the form lives on.
``next.router`` sits above ``next.forms`` here as well, so the action dispatch opens the block through ``zone_marking_slot`` rather than importing the router.
Callers go through ``marked_zones``, which opens a block that marks nothing when no marking is bound.

``PortSlot.peek`` answers the bound implementation, or ``None`` before the app is ready.
The lazy urlpatterns read the seo slot through it, so a concat built before ``NextFrameworkConfig.ready()`` leaves the seo routes out and caches nothing, and a URL resolved or reversed from an earlier ``ready()`` still works.

Implementations
---------------

Each area binds its own implementation, one class per port, holding nothing but the delegation to the area's real entry points.
Every implementation lives in the ``ports`` module of its area, save ``FormRoutes``, which the form action manager answers itself, ``DeferralSink``, which ``next.defer.assembly.inline_sink`` answers, ``PageViews``, which ``next.views.page_view`` answers, ``ServerLoad``, which ``next.defer.manager.defer_manager`` answers, ``RouterStamp``, which ``next.router.navigation.stamp`` answers, and ``ZoneMarking``, which ``next.router.navigation.action_marking`` answers.

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * - Port
     - Implementation
     - Slot
   * - ``ActionOwners``
     - ``next.forms.ports.ActionOwnersImpl``
     - ``action_owners_slot``
   * - ``DeferralSink``
     - ``next.defer.assembly.inline_sink``
     - ``deferrals_slot``
   * - ``FormRoutes``
     - ``next.forms.manager.form_action_manager``
     - ``form_routes_slot``
   * - ``PageScan``
     - ``next.pages.ports.PageScanImpl``
     - ``page_scan_slot``
   * - ``PageViews``
     - ``next.views.page_view``
     - ``page_views_slot``
   * - ``RouteAccess``
     - ``next.urls.ports.RouteAccessImpl``
     - ``route_access_slot``
   * - ``RouterStamp``
     - ``next.router.navigation.stamp``
     - ``router_stamp_slot``
   * - ``SeoRoutes``
     - ``next.seo.ports.SeoRoutesImpl``
     - ``seo_routes_slot``
   * - ``ServerLoad``
     - ``next.defer.manager.defer_manager``
     - ``server_load_slot``
   * - ``StaticAssets``
     - ``next.static.ports.StaticAssetsImpl``
     - ``static_assets_slot``
   * - ``ZoneMarking``
     - ``next.router.navigation.action_marking``
     - ``zone_marking_slot``

``next.apps`` binds every slot in the table in ``NextFrameworkConfig.ready()``, ahead of every step that imports user code.
A project that replaces one subclasses the shipped implementation, or wraps it where the implementation is a function, and calls ``set`` on the slot from the ``ready()`` of an application listed after ``next`` in ``INSTALLED_APPS``, since the slot holds one implementation and the last binding wins.
A page script provider is added with ``page_scripts.register`` from the same place, and one registered under a shipped name replaces it, see :doc:`/content/topics/extending`.

Public API
----------

.. automodule:: next.ports
   :members:

See also
--------

.. seealso::

   :doc:`apps` for the startup step that binds the slots.
   :doc:`seo` for the subsystem that implements ``SeoRoutes``.
   :doc:`/content/internals/overview` for where ``next.ports`` sits in the subsystem dependency graph.
