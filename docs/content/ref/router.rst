.. _ref-router:

Client router reference
=======================

Module summary
--------------

``next.router`` holds the server side of the client router.
It decides how far a navigation keeps the layouts on screen, digests the chrome of each layout, marks the navigation links of the current page, and tells the browser when to drop what it holds.
:doc:`/content/topics/router/index` is the guide, ``ROUTER`` is in :doc:`settings`, and the browser half is in :doc:`client`.

Every public name is importable from the package root.

.. code-block:: python

   from next.router import (
       ChromeDigest,
       ClientLayouts,
       Cut,
       CutKind,
       Divergence,
       LayoutKey,
       LoadShed,
       NavigationCut,
       PrefetchShed,
       ScannedLayout,
       ScannedPage,
       VerifiedCut,
       navigation_rendered,
       prefetch_shed,
       router_backend_loaded,
       router_clear,
       router_cleared,
   )

Page declaration
----------------

``page.router(*, navigate=True, prefetch=True, snapshot=True)`` at the top of a ``page.py`` declares how the router treats its page.
``navigate=False`` answers every navigation with a hard load of the URL and refuses every prefetch.
``prefetch=False`` answers a prefetch ``204`` with an empty body before any render, and ``snapshot=False`` keeps the page out of the history snapshots.
The declaration binds its own page alone, the last call of a module wins, and an edited ``page.py`` that drops the call stops applying it once the module loads again.
A flag that is no bool raises :exc:`~django.core.exceptions.ImproperlyConfigured` and a call inside a function raises ``DeclarationOutsideModuleError`` when the module runs, while a call outside a ``page.py`` declares nothing and is reported by ``next.E238``.

Cut and digest
--------------

``ROUTER["CUT"]`` names the cut and ``ROUTER["DIGEST"]`` the digest, each built once per process and again after a settings reload, see :doc:`/content/topics/router/layouts`.

.. autoclass:: next.router.NavigationCut
   :members: cut

.. autoclass:: next.router.VerifiedCut
   :members: cut

.. autoclass:: next.router.ChromeDigest
   :members: normalise, digest

The default normalisation leaves out every top-level zone but one that wraps the slot, the ``{% metadata %}`` head tags, and the placeholders the static injector fills.
It also drops every ``nonce`` attribute and empties the ``value`` that follows ``name="csrfmiddlewaretoken"`` inside the same tag.
Every other per-render value stays in the digest and makes the layout diverge, see :doc:`/content/topics/router/layouts`.
The digest is a blake2b digest of 8 bytes, written as 16 hex characters, and the root layout carries two in a row, the part up to the end of its ``<body>`` start tag and the rest.

Prefetch shed
-------------

``ROUTER["SHED"]`` names the class that decides whether a prefetch yields to the work the process already holds, built once per process and again after a settings reload.
A prefetch it sheds answers ``429`` before any render, with ``Retry-After`` in whole seconds and at least one, and sends ``prefetch_shed``.

.. autoclass:: next.router.PrefetchShed
   :members: waited

.. autoclass:: next.router.LoadShed
   :members: waited

``LoadShed`` reads the server load of the ``next.ports`` slot that ``AppConfig.ready()`` binds to the pool of deferred boundaries, which reports a backlog once the oldest queued job has waited past ``DEFER["EXECUTOR"]["QUEUE_TIMEOUT"]``.
Without that slot bound no prefetch is shed.
The pool sees only deferred work, so a project whose request threads saturate first names a subclass that reads its own measure, the request and the page file of the prefetch at hand.

Manager
-------

``next.router.manager.client_router`` is the process-wide ``ClientRouterManager``.
``cut()``, ``digest()``, and ``shed()`` return the three backends, each built on first use.
``entry()`` returns the encoded ``$router`` entry, ``speculation()`` the encoded rules, and ``document()`` both in one lookup.
``forget()`` drops them when the settings or the routes reload, and ``forked()`` does the same in a forked worker with a fresh lock.
Each build sends ``router_backend_loaded`` once per backend, with the one ``ROUTER`` key it was built from as ``config``.

.. autoclass:: next.router.manager.ClientRouterManager
   :members: cut, digest, shed, entry, speculation, document, forget, forked

Value objects
-------------

.. autoclass:: next.router.CutKind
   :no-special-members:

.. autoclass:: next.router.Divergence
   :no-special-members:

.. autoclass:: next.router.Cut

.. autoclass:: next.router.LayoutKey

.. autoclass:: next.router.ClientLayouts

.. autoclass:: next.router.ScannedPage

.. autoclass:: next.router.ScannedLayout

``ScannedPage.layouts`` holds one ``ScannedLayout`` per layout of the page, outer first, each with its ``ident``, its ``digest``, the bounds of its slot, and the digest of its head part for the root layout.
A navigation declares at most 32 layouts, and the server reads a longer declaration as none.

Active links
------------

``ROUTER["ACTIVE_LINKS"]["PASS"]`` picks where the server marks the links of ``ROUTER["ACTIVE_LINKS"]["CONTAINERS"]``.
``"template"`` compiles each container a template spells into a node that marks its own output, a container whose source is text alone read once at compile time, and keeps the pass over the whole document for a page whose containers the source cannot show.
``"document"`` runs the pass over every rendered document.
A page view skips the pass for a page whose templates can open no container, see :ref:`ref-views-plan`, and the pass is built again once the merged ``ROUTER`` mapping changes.
The marks are ``aria-current="page" data-next-current="page"`` for the current page and ``data-next-current="ancestor"`` for an ancestor, see :doc:`/content/topics/router/active-links`.

Clearing the router
-------------------

.. autofunction:: next.router.router_clear

The framework calls it with the reason ``login`` from ``user_logged_in``, ``logout`` from ``user_logged_out``, and ``session`` for a request whose session key changed, the signed-cookie engine left out.
The first call of a request wins, and each request that clears sends ``router_cleared`` once.

Signals
-------

``navigation_rendered``, ``prefetch_shed``, and ``router_cleared`` are sent with ``Signal.send_robust``, and only while a receiver is connected.
The submodules of the ``next.router.navigation`` package send them, with its ``Routing`` class as the sender.
``router_backend_loaded`` is sent by the manager with the backend class as the sender.
:doc:`signals` lists their arguments.

The wire
--------

A navigation is a ``GET`` of the page URL with these request headers.

.. list-table::
   :header-rows: 1
   :widths: 26 74

   * - Header
     - Meaning
   * - ``X-Next-Request: 1``
     - The partial switch every navigation carries.
   * - ``X-Next-Navigate: 1``
     - Marks the request as a navigation, answered as one only while ``ROUTER["ENABLED"]`` holds.
   * - ``X-Next-Layouts``
     - The chain on screen, one ``id.digest`` per layout, outer first, comma separated, an id being 16 hex characters and a digest 16 or 32.
       A value past 32 entries or past 1 KB, or one that does not parse, counts as no declaration.
   * - ``X-Next-Prefetch``
     - Marks the navigation as a prefetch.
       ``1`` asks for the shell of the page, with no deferred job run and no stream.
       ``full`` is sent at the press of a link and asks for the page as a navigation gets it, jobs run and the answer streamed when the server can stream.
       ``request.next.depth`` is ``PrefetchDepth.SHELL`` or ``PrefetchDepth.FULL`` accordingly.
   * - ``X-Next-Origin``
     - The path and query the browser navigates from, which decides between pushing and replacing the history entry and, under ``ROUTER["INTERCEPT"] = "links"``, whether a link to an intercepted page opens its layer.
       A value that is no same-site path is ignored.
   * - ``X-Next-Version``
     - The asset version the browser holds, answered ``409`` when it differs from the server's.
   * - ``Accept``
     - ``text/event-stream`` first when the browser can read a streamed navigation.

The answer is a patch envelope, or an event stream that opens with one, and its top-level fields add six entries.

- ``layouts`` holds ``[id, digest, response_id]`` per layout of the new chain, with a ``null`` response id for each kept one.
- ``root`` holds the id of the root layout.
- ``nonce``, ``scripts``, and ``consent`` carry the nonce, the scripts, and the consent state of the page.
- ``shared`` tells whether a shared cache may store the document of the page.

A prefetch answer that skipped no deferred job and left no client-trip boundary also carries ``complete: true``, whatever its depth, and the navigation after it applies that answer without a request of its own.
A navigation after any other prefetch answer sends its request at the click, beside the prefetched shell it applies.
The click after a press adopts the response of the press as its navigation.
A prefetch answer may be shown with no further request, see :ref:`topics-router-prefetch-visits` for what that means for code that acts on a visit.
A slot morph targets ``{"slot": id}``, a body morph ``{"body": true}``, and a hard load is a ``visit`` operation.

.. list-table::
   :header-rows: 1
   :widths: 26 74

   * - Answer
     - When
   * - ``200`` envelope
     - A navigation or a prefetch the page rendered.
   * - ``200`` ``visit`` envelope
     - A page that declares ``navigate=False``, a path that opens with ``//``, or a ``render()`` that returns a response of its own other than a redirect.
       A redirect past a login, a logout, a changed session key, a ``router_clear`` call, or a rotated CSRF token gets one too, the cookies of the redirect included.
   * - ``200`` ``layer.open`` envelope
     - A navigation from another page to a page an intercept targets, under ``ROUTER["INTERCEPT"] = "links"``.
   * - ``200`` event stream
     - A navigation of a page whose ``stream`` boundaries follow the envelope.
   * - ``204``
     - A prefetch of a page that declares ``prefetch=False`` or ``navigate=False``, or of an intercepted page under ``ROUTER["INTERCEPT"] = "links"``.
   * - ``403``
     - ``Sec-Fetch-Site`` names anything but ``same-origin``.
   * - ``409``
     - ``X-Next-Version`` names another asset version.
   * - ``429``
     - A prefetch ``ROUTER["SHED"]`` sheds, by default while the pool queue of deferred boundaries lags past ``DEFER["EXECUTOR"]["QUEUE_TIMEOUT"]``, with ``Retry-After`` in whole seconds.
   * - ``3xx``
     - Any other redirect the page or its access rules answered with, which the browser fetch follows.

Every answer is sent ``private, no-store`` and carries ``Vary`` over the partial headers, ``X-Next-Navigate``, ``X-Next-Layouts``, and ``X-Next-Prefetch`` included.
Under ``DEBUG`` a rendered navigation names its cut in the ``X-Next-Navigate`` response header.

``X-Next-Router`` carries the directives of a response, comma separated, while the router is on.
``clear`` drops every snapshot and every prefetched answer the browser holds, and ``no_snapshot`` keeps the page out of the snapshots.
A document cannot read its own headers, so a routed document of a page that declares ``snapshot=False`` also carries ``"snapshot": false`` in its ``$router`` payload entry.

Payload entries
~~~~~~~~~~~~~~~

A routed document carries the ``$router`` entry in its init payload, and right after the init script a ``<script type="application/json" data-next-layouts>`` block holding ``[id, digest, response_id]`` per layout of the document, outer first, which the router reads its chain from.
The init script carries ``data-next-init`` whatever ``init_template`` spells, so the router finds it in a foreign document by the mark alone.

``$router``
   The settings the browser reads.
   ``routes``, ``scope``, ``exclude``, ``foreign``, ``prefetch``, ``prefetch_delay``, ``prefetch_ttl``, ``prefetch_shell_ttl``, ``prefetch_entries``, ``press``, ``snapshots``, ``progress``, ``progress_delay``, ``transitions``, and ``intercept`` copy the ``ROUTER`` keys of the same name.
   The string choices are the values of ``RouterScope``, ``PrefetchMode``, ``PressMode``, ``ForeignMode``, and ``InterceptMode`` of ``next.conf.router``.
   ``exclude`` lists the derived prefixes of ``STATIC_URL``, ``MEDIA_URL``, and the admin site first.
   ``routes`` stands in place of ``scope`` under the ``pages`` scope and holds the URL of the route table, ``null`` while the URLconf does not include ``next.urls``.
   The integers ``prefetch_ttl``, ``prefetch_shell_ttl``, and the other second counts are written without a fraction when they are whole.
   The most layouts a navigation declares, ``32``, is ``next.router.markers.MAX_LAYOUTS``, which the runtime reads from its generated protocol module.
   ``links`` lists the active-link containers and ``homes`` the home paths, both ``null`` while active links are off.
   ``boundaries`` and ``intent`` hold the URLs of the boundaries and the intent bundles when the page resolves them.
   ``type`` names the content type of a partial backend whose answers are not the stock one, and ``snapshot`` is ``false`` for a page that refuses snapshots.

Route table
~~~~~~~~~~~

``next.urls`` serves the route table at ``_next/routes/<digest>/``, URL name ``next:routes``, as ``application/json``.
The digest is the first eight hex characters of the SHA-256 of the body, which the answer caches ``public, max-age=31536000, immutable``, and a request naming another digest gets the current table with ``no-cache``.
The body holds ``base``, ``slash``, and ``routes``.
``base`` is the script prefix, ``slash`` tells whether ``APPEND_SLASH`` and ``CommonMiddleware`` redirect a path without its slash, and ``routes`` holds one string per row in resolution order.

A row opens with ``+`` for a route the router owns, ``next.urls.scope.PAGE_ROW``, or ``-`` for a route that is no page, and goes on with a regex over the path past the script prefix that JavaScript and Python read alike, closed by ``$`` for a whole route.
A ``+`` row matches exactly the paths Django sends to its route, and a ``-`` row matches those paths or more.
A route the table cannot carry, a page among them, is a ``-`` row over its literal start, see :ref:`topics-router-scope`.
A route that is no page is left out only when no later page may take a path from it and, while ``slash`` holds, no page may take that path with the slash appended.

The client decides no path longer than ``next.urls.scope.LONGEST``, 512 characters with the script prefix, and no path holding a control character or a line separator.
``next.urls.scope.route_scope()`` returns the table of the URLconf and script prefix of the thread as a ``RouteScope``, whose ``owns(path)`` decides a path past the prefix as the browser does, and ``next.urls.scope.stranded_pages()`` lists the pages the router loads as documents.
``PageBackend.serves_page(pattern)`` answers ``True`` for a page, ``False`` for a route that is no page, and ``None`` when the backend cannot tell, which the table lists as owned when it carries the route exactly.

Speculation Rules
~~~~~~~~~~~~~~~~~

While ``ROUTER["SPECULATION"]`` names a usable eagerness, every routed document carries one ``<script type="speculationrules">`` in its head, with the CSP nonce of the response.
It holds a single ``prefetch`` rule at that eagerness over the same-origin links the router does not own.
Those are the links on or inside ``[data-next-router=off]`` or a ``[data-next-reload]`` that is no zone and no boundary wrapper, the links under an excluded prefix, and under the ``opt_in`` scope the links outside every ``[data-next-router]``.

Under the ``pages`` scope the rule also requires a link to match the URL pattern of a page route and none of a route that is no page, and a table without a page route emits no rules.
A page pattern reads a built-in converter as a whole segment, and a ``path`` converter, a converter of its own, or a regex as ``*``.
It reads a language prefix as the alternation of its codes, ``{(de|fr)/}?``, so it may be wider than the page.
The routes it leaves out therefore come from every route of the URLconf whose literal start meets that of a page, and, while ``APPEND_SLASH`` redirects and a page pattern may match a path without its slash, each of them without its trailing slash as well.

A link on or inside ``[data-next-layer]``, ``[data-next-merge]``, ``[data-next-fetch]``, ``[data-next-retry]``, ``[data-next-confirm]``, or ``[data-next-prefetch=off]`` is left out, the selector ``next.router.speculation.GUARDED`` holds.
The rules are built once per script prefix and URLconf, and again once a setting they read changes, see :doc:`/content/topics/router/prefetch`.

.. seealso::

   :doc:`/content/topics/router/index` for the guide.
   :doc:`views` for the ``NAVIGATE`` route and the purpose of a request.
   :doc:`ports` for ``RouterStamp``, which sets ``X-Next-Router`` on a form action response.
   :repo:`markdown-blog <tree/main/examples/markdown-blog>` for the viewport prefetch and :repo:`observability <tree/main/examples/observability>` for the press of a link, see :doc:`/content/misc/examples`.
