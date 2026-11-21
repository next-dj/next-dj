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
       CurrentLinks,
       Cut,
       CutKind,
       Declaration,
       Divergence,
       LayoutKey,
       NavigationCut,
       Shell,
       VerifiedCut,
       current_links,
       navigation_rendered,
       prefetch_shed,
       router_clear,
       router_cleared,
   )

Page declaration
----------------

``page.router(*, navigate=True, prefetch=True, snapshot=True)`` at the top of a ``page.py`` declares how the router treats its page.
``navigate=False`` answers every navigation with a hard load of the URL and refuses every prefetch, ``prefetch=False`` answers a prefetch ``204`` with an empty body before any render, and ``snapshot=False`` keeps the page out of the history snapshots.
The declaration binds its own page alone, the last call of a module wins, and an edited ``page.py`` that drops the call stops applying it once the module loads again.
A flag that is no bool, a call inside a function, and a call outside a ``page.py`` declare nothing and are reported as ``next.E238``, and the page keeps the defaults.

Cut and digest
--------------

``ROUTER["CUT"]`` names the cut and ``ROUTER["DIGEST"]`` the digest, each built once per process and again after a settings reload, see :doc:`/content/topics/router/layouts`.

.. autoclass:: next.router.NavigationCut
   :members: cut

.. autoclass:: next.router.VerifiedCut
   :members: cut

.. autoclass:: next.router.ChromeDigest
   :members: normalise, digest

The default normalisation leaves out every top-level zone but one that wraps the slot, the ``{% metadata %}`` head tags, and the placeholders the static injector fills, drops every ``nonce`` attribute, and empties the ``value`` that follows ``name="csrfmiddlewaretoken"`` inside the same tag.
Every other per-render value stays in the digest and makes the layout diverge, see :doc:`/content/topics/router/layouts`.
The digest is a blake2b digest of 8 bytes, written as 16 hex characters, and the root layout carries two in a row, the part up to the end of its ``<body>`` start tag and the rest.

Value objects
-------------

.. autoclass:: next.router.CutKind
   :no-special-members:

.. autoclass:: next.router.Divergence
   :no-special-members:

.. autoclass:: next.router.Cut

.. autoclass:: next.router.LayoutKey

.. autoclass:: next.router.Declaration

.. autoclass:: next.router.Shell

``Shell.layouts`` holds one scanned layout per layout of the page, outer first, each with its ``ident``, its ``digest``, the bounds of its slot, and the digest of its head part for the root layout.

Active links
------------

.. autoclass:: next.router.CurrentLinks
   :members: holds, mark

.. autofunction:: next.router.current_links

``holds`` answers whether a document opens a container, before the path and the origin of the request are read.
A page view also asks it once per compiled template, and skips the pass for a page whose templates can open no container, see :ref:`ref-views-plan`.
``ROUTER["CURRENT"]["BACKEND"]`` names the class, built with ``ROUTER["CURRENT"]["CONTAINERS"]``, and the backend is built again once the merged ``ROUTER`` mapping changes.
The marks are ``aria-current="page" data-next-current="page"`` for the current page and ``data-next-current="ancestor"`` for an ancestor, see :doc:`/content/topics/router/active-links`.

Clearing the router
-------------------

.. autofunction:: next.router.router_clear

The framework calls it with the reason ``login`` from ``user_logged_in``, ``logout`` from ``user_logged_out``, and ``session`` for a request whose session key changed, the signed-cookie engine left out.
The first call of a request wins, and each request that clears sends ``router_cleared`` once.

Signals
-------

``navigation_rendered``, ``prefetch_shed``, and ``router_cleared`` are sent with ``Signal.send_robust`` from the ``Routing`` class of ``next.router.navigation``, and only while a receiver is connected.
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
       A value past ``ROUTER["MAX_LAYOUTS"]`` entries or past 1 KB, or one that does not parse, counts as no declaration.
   * - ``X-Next-Prefetch: 1``
     - Marks the navigation as a prefetch.
   * - ``X-Next-Origin``
     - The path and query the browser navigates from, which decides between pushing and replacing the history entry and, under ``ROUTER["INTERCEPT"] = "links"``, whether a link to an intercepted page opens its layer.
       A value that is no same-site path is ignored.
   * - ``X-Next-Version``
     - The asset version the browser holds, answered ``409`` when it differs from the server's.
   * - ``Accept``
     - ``text/event-stream`` first when the browser can read a streamed navigation.

The answer is a patch envelope, or an event stream that opens with one, whose top-level fields add ``layouts``, ``[id, digest, seal]`` per layout of the new chain with a ``null`` seal for each kept one, ``root``, the id of the root layout, ``nonce``, ``scripts``, ``consent``, and ``shared``, whether a shared cache may store the document of the page.
A prefetch answer that skipped no deferred job and left no client-trip boundary also carries ``complete: true``, and the navigation after it applies that answer without a request of its own.
A navigation after any other prefetch answer sends its request at the click, beside the prefetched shell it applies.
A slot morph targets ``{"slot": id}``, a body morph ``{"body": true}``, and a hard load is a ``visit`` operation.

.. list-table::
   :header-rows: 1
   :widths: 26 74

   * - Answer
     - When
   * - ``200`` envelope
     - A navigation or a prefetch the page rendered.
   * - ``200`` ``visit`` envelope
     - A page that declares ``navigate=False``, a path that opens with ``//``, a ``render()`` that returns a response of its own other than a redirect, and a redirect past a login, a logout, a changed session key, a ``router_clear`` call, or a rotated CSRF token, the cookies of the redirect included.
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
     - A prefetch while the pool queue lags past ``DEFER["EXECUTOR"]["OPTIONS"]["QUEUE_TIMEOUT"]``, with ``Retry-After`` in whole seconds.
   * - ``3xx``
     - Any other redirect the page or its access rules answered with, which the browser fetch follows.

Every answer is sent ``private, no-store`` and carries ``Vary`` over the partial headers, ``X-Next-Navigate``, ``X-Next-Layouts``, and ``X-Next-Prefetch`` included.
Under ``DEBUG`` a rendered navigation names its cut in the ``X-Next-Navigate`` response header.

``X-Next-Router`` carries the directives of a response, comma separated, while the router is on.
``clear`` drops every snapshot and every prefetched answer the browser holds, and ``no-snapshot`` keeps the page out of the snapshots.
A document cannot read its own headers, so a routed document of a page that declares ``snapshot=False`` also carries ``"snapshot": false`` in its ``$router`` payload entry.

Payload entries
~~~~~~~~~~~~~~~

A routed document carries two entries in its init payload.

``$layouts``
   ``[id, digest, seal]`` per layout of the document, outer first.

``$router``
   The settings the browser reads.
   ``scope``, ``exclude``, ``foreign``, ``prefetch``, ``prefetch_delay``, ``prefetch_ttl``, ``prefetch_shell_ttl``, ``prefetch_entries``, ``snapshots``, ``progress_delay``, ``transitions``, ``intercept``, and ``max_layouts`` copy the ``ROUTER`` keys of the same name, and ``exclude`` lists the derived prefixes of ``STATIC_URL``, ``MEDIA_URL``, and the admin site first.
   ``current`` lists the active-link containers and ``homes`` the home paths, both ``null`` while active links are off.
   ``reader``, ``history``, and ``intent`` hold the URLs of those bundles when the page resolves them, ``type`` names the content type of a partial backend whose answers are not the stock one, and ``snapshot`` is ``false`` for a page that refuses snapshots.

Speculation Rules
~~~~~~~~~~~~~~~~~

While ``ROUTER["SPECULATION"]`` names a usable eagerness, every routed document carries one ``<script type="speculationrules">`` in its head, with the CSP nonce of the response.
It holds a single ``prefetch`` rule at that eagerness over the same-origin links the router does not own, those on or inside ``[data-next-router=off]`` or a ``[data-next-reload]`` that is no zone and no boundary wrapper, those under an excluded prefix, and under the ``opt-in`` scope those outside every ``[data-next-router]``.
A link on or inside ``[data-next-layer]``, ``[data-next-merge]``, ``[data-next-fetch]``, ``[data-next-retry]``, ``[data-next-confirm]``, or ``[data-next-prefetch=off]`` is left out, the selector ``next.router.speculation.GUARDED`` holds.
The rules are built once per script prefix and URLconf, and again once a setting they read changes, see :doc:`/content/topics/router/prefetch`.

.. seealso::

   :doc:`/content/topics/router/index` for the guide.
   :doc:`views` for the ``NAVIGATE`` route and the purpose of a request.
   :doc:`ports` for ``RouterStamp``, which sets ``X-Next-Router`` on a form action response.
