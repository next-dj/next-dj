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

``PageScan`` is the page-tree scan the checks and the discovery helpers run, a single ``load_scanned_page_modules`` that executes every routed ``page.py`` and answers the ones that loaded.
``next.pages.scan`` reads the router manager from ``next.discovery``, so discovery reaches the scan back through ``page_scan_slot`` rather than through an import that would close the loop.

``RouterAccess`` builds router backends and router managers and answers the URL pattern parser, and it answers the concrete classes of ``next.urls`` rather than an abstraction over routing, because it exists to defer an import and nothing else.
``next.urls`` routes to pages and so imports ``next.pages``, which leaves the page watcher and the system checks needing routers from the other direction.
They read ``router_access_slot`` instead, at watch time and at check time.
``url_parser`` answers the parser the file router routes bracket segments through, for a caller that has a route string and no router to ask.
``page_kwargs`` answers the URL kwargs a page at a trail takes, or ``None`` when the router refuses the trail, which the intercept checks use to compare a target with an intercept.

``StaticAssets`` is the static-manager surface one page render calls, a collector, page and component asset discovery, and placeholder injection.
``next.static`` reads page trees and page modules and so imports ``next.pages``, so the render path reads ``static_assets_slot`` rather than importing the static manager back.
``collect_component_assets`` folds the co-located assets of one composite component into a caller-supplied collector, which is how a component render reaches the static pipeline from the same side.
Every method resolves the manager when it is called rather than when the slot is bound, so a settings reload that drops the wrapped manager still reaches every later render.

``SeoRoutes`` answers the SEO routes the lazy urlpatterns of ``next.urls`` append as their third pattern source.
``next.seo`` imports ``next.urls`` for the router manager and the reverse helper, so the pattern concat reads ``seo_routes_slot`` rather than importing the seo area back.
``patterns`` answers the routes whose source exists.
The port carries no version.
The concat's cache token reads ``seo_routes_version`` in ``next.urls.manager``, the counter of the spliced SEO routes that ``NextFrameworkConfig.ready()`` moves right after it binds the slot and the seo manager moves on every reset of its sources, so a resolve reads the token without a call across the port.

``PageScripts`` answers the head tags and the reserved payload entries, ``$scripts`` and ``$consent``, the third-party scripts add to one render.
``next.scripts`` reads the static manager to resolve a script's ``src``, so the injector of ``next.static`` reads ``page_scripts_slot`` through ``peek`` rather than importing it back.

``ActionOwners`` answers the ``page.py`` of every directory whose files declare an ``@action``, the file whose rule such an action answers to.
The forms area imports ``next.pages``, so the body-source check ``next.E012`` reads ``action_owners_slot`` rather than importing ``next.forms`` back, and a ``page.py`` with no body that serves an action passes.
The intercept check ``next.E174`` reads the same slot, so a replaced binding moves both checks together, while ``next.E166`` and ``next.W128`` live in the forms area and read its registry directly.

``FormRoutes`` answers the URL patterns of every form backend.
The forms area imports ``next.pages``, which loads the URL checks, so those checks read ``form_routes_slot`` rather than importing ``next.forms.manager`` back.

``RealtimeNegotiation`` answers the transports and the partial content types the ``$realtime`` entry of every page is built from.
``next.realtime`` and ``next.partial`` import the static runtime, so the runtime reads ``realtime_negotiation_slot`` rather than importing either back.
``transports`` answers the enabled transports in preference order, and ``content_types`` answers the content type of the active partial backend next to the stock one.

``AsyncBridge`` runs an awaitable, or a factory of one, to completion from sync code and answers its result.
``next.suspense`` sits above the areas that render components, resolve dependencies, and dispatch forms, so those areas reach its driver through ``async_bridge_slot`` rather than importing it.
The driver keeps one long-lived event loop and serves every thread-sensitive call of the awaited work on the calling thread, so ORM access inside the work shares the connection and the transaction of the sync code around it.
Callers go through ``run_bridged``, which reads the slot through ``peek`` and falls back to :func:`~asgiref.sync.async_to_sync` when no driver is bound, correct but slower, since outside a server it starts an event loop per call.
An inline component with an ``async def`` part and a sync caller of an ``async def`` named dependency or ``Depends`` factory both run through it.

``ThreadWait`` blocks the calling thread until a :class:`~concurrent.futures.Future` is done, and on a thread the driver serves it runs the thread-sensitive calls queued for that thread while it waits.
``next.suspense`` sits above ``next.deps``, so a ``Loader`` waiting for a batch on the request thread reaches the driver through ``thread_wait_slot``, an ORM batch is a sync function the waiting thread may run itself, and an ``async def`` batch function refuses every thread-sensitive call.
Callers go through ``wait_served``, which falls back to a plain blocking wait when no implementation is bound, the same wait a pool thread gets from the bound one.

``ContextPropagator`` restores a ``ContextSnapshot`` of the request's context on the thread or the event loop that runs work deferred from it, ``restore`` on the calling sync thread and ``arestore`` on the running loop, and each answers the snapshot context itself when nothing had to move.
asgiref 3.12 and later show a ``Local`` value only on a thread one of its own hops carried it to, so the language, the URLconf, and a ``Local`` of the project reach a pool job, a loop job, a ``drive`` coroutine, and a loader batch through this port.
``next.suspense`` sits above ``next.deps``, so the loader reaches the propagator through ``context_propagator_slot``, and every other site reads the same slot.
The default, ``next.suspense.bridge.BridgePropagator``, keeps one restored copy per thread of each snapshot, restores on a loop through a hop on a thread of its own, so a busy default executor of the loop never holds it up, and onto a sync thread through a hop from the server loop of that thread or from the background loop, waiting no longer than the deadline of the current job.
``NextFrameworkConfig.ready()`` builds it with ``plain`` set when a ``Local`` written in a context already reads through that context on another thread, as before asgiref 3.12, and then it moves nothing and makes no hop.
A project binds its own propagator, built on another mechanism, by setting the slot.

``ContextSnapshot`` of ``next.tasks`` is what a propagator receives.
``context`` is the request's context as captured, and ``thread`` the identity of the thread it was captured on.
``get()`` answers the context restored on the calling thread, ``context`` itself on the capturing thread, and ``None`` until a copy was kept there.
``keep(context)`` holds a copy a hop restored on the calling thread and answers it, so later jobs on that thread reuse it.
``landing(carry)`` answers the task that restores a copy on the running loop, started by the first caller from the coroutine ``carry`` makes, so one hop serves every caller on that loop.
A deferred job asks ``get()`` first, so a job on the thread its snapshot was taken on does not call the propagator.

.. autoclass:: next.tasks.ContextSnapshot
   :members: get, keep, landing

``DeferralSink`` takes a deferred component call whose template context holds no sink of its own, and answers the text the render holds in its place.
``next.suspense`` sits above the template tags and the render paths, so a component call and a render reach the assembled-inline sink through ``deferrals_slot`` rather than importing it.
``queue`` queues a job into the session of the response a ``deferring`` block opened and answers the loading wrapper, and outside such a block it renders the call in place.
``inline`` renders the call in place inside its settled wrapper.
``assembled`` runs one render inside a ``deferring`` block, runs the jobs the render queued in parallel, and answers the render with every outcome spliced into its wrapper, the assets of each job merged into the collector before injection.
Callers go through ``render_deferred``, which renders plainly when no sink is bound.
A page render, a zone GET, a form re-render, a ``Patches`` context, and a ``next.testing`` render all reach it, while a page view opens its own block with the sink that asks the delivery policy.
When no sink is bound, a deferred component renders in place behind its error boundary, without a wrapper.

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
Every implementation lives in the ``ports`` module of its area, save ``FormRoutes``, which the form action manager answers itself, ``ContextPropagator``, which ``next.suspense.bridge.BridgePropagator`` answers, ``AsyncBridge`` and ``ThreadWait``, which ``next.suspense.drive`` and ``next.suspense.serve_until`` answer themselves, ``DeferralSink``, which ``next.suspense.assembly.inline_sink`` answers, ``RouterStamp``, which ``next.router.navigation.stamp`` answers, and ``ZoneMarking``, which ``next.router.navigation.action_marking`` answers.

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * - Port
     - Implementation
     - Slot
   * - ``ActionOwners``
     - ``next.forms.ports.ActionOwnersImpl``
     - ``action_owners_slot``
   * - ``AsyncBridge``
     - ``next.suspense.drive``
     - ``async_bridge_slot``
   * - ``ContextPropagator``
     - ``next.suspense.bridge.BridgePropagator``
     - ``context_propagator_slot``
   * - ``DeferralSink``
     - ``next.suspense.assembly.inline_sink``
     - ``deferrals_slot``
   * - ``FormRoutes``
     - ``next.forms.manager.form_action_manager``
     - ``form_routes_slot``
   * - ``PageScan``
     - ``next.pages.ports.PageScanImpl``
     - ``page_scan_slot``
   * - ``RealtimeNegotiation``
     - ``next.realtime.ports.RealtimeNegotiationImpl``
     - ``realtime_negotiation_slot``
   * - ``RouterAccess``
     - ``next.urls.ports.RouterAccessImpl``
     - ``router_access_slot``
   * - ``RouterStamp``
     - ``next.router.navigation.stamp``
     - ``router_stamp_slot``
   * - ``SeoRoutes``
     - ``next.seo.ports.SeoRoutesImpl``
     - ``seo_routes_slot``
   * - ``StaticAssets``
     - ``next.static.ports.StaticAssetsImpl``
     - ``static_assets_slot``
   * - ``PageScripts``
     - ``next.scripts.ports.PageScriptsImpl``
     - ``page_scripts_slot``
   * - ``ThreadWait``
     - ``next.suspense.serve_until``
     - ``thread_wait_slot``
   * - ``ZoneMarking``
     - ``next.router.navigation.action_marking``
     - ``zone_marking_slot``

``next.apps`` binds every slot in the table in ``NextFrameworkConfig.ready()``, ahead of every step that imports user code.
A project that replaces one subclasses the shipped implementation, or wraps it where the implementation is a function, and calls ``set`` on the slot from the ``ready()`` of an application listed after ``next`` in ``INSTALLED_APPS``, since the slot holds one implementation and the last binding wins.

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
