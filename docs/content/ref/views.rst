.. _ref-views:

Views reference
===============

Module summary
--------------

``next.views`` holds the views the URL router mounts for pages.
It sits above ``next.pages``, ``next.deps``, and ``next.partial``, reaches every feature through the participants of ``next.lifecycle``, and ``next.urls`` mounts its ``page_view`` through the ``page_views_slot`` port.
An application never imports it to serve a page, and a project reaches it to mount a page view by hand or to read how a request was routed.

The page view
-------------

``page_view(page, file_path)`` returns the view of a real or virtual page, or ``None`` for a page with no body.
A ``page.py`` whose import failed keeps a view, so every request to it surfaces the error.
The view is static when the body comes from files and resolving when the module has a ``render()``, and a ``render`` that is a coroutine function makes the view drive the async part of the render on an event loop.
Each view is marked so Django's ``ATOMIC_REQUESTS`` skips it, carries ``next_page_path``, the page file form dispatch maps an origin to, and carries ``login_required``, which ``LoginRequiredMiddleware`` reads.

.. autofunction:: next.views.pages.page_view

.. autofunction:: next.views.pages.unified_view

One request
-----------

A request to a page URL is routed once, then answered as :ref:`internals-request-lifecycle-participants` describes, inside the transaction the method rule or ``page.transaction()`` gives it, see ``TRANSACTIONS`` in :doc:`settings`.

.. list-table::
   :header-rows: 1
   :widths: 16 84

   * - Kind
     - Answer
   * - ``PAGE``
     - The page access check, then the response policy, the render, and a whole response or a stream, see :doc:`/content/topics/boundaries/streaming`.
   * - ``ZONE``
     - The page access check, then the envelope of the zones the request names, see :doc:`/content/topics/partial-rendering/how-it-works`.
   * - ``BOUNDARY``
     - The reload of one client-trip boundary.
       A reload answers ``GET`` and ``HEAD``, and ``POST`` as well under the ``"body"`` token carrier, and any other method answers 405.
       A request whose ``Sec-Fetch-Site`` is anything but ``same-origin`` answers 403, then the page access check runs, and the token is redeemed last.
   * - ``NAVIGATE``
     - A navigation of the client router, while ``ROUTER["ENABLED"]`` holds.

A partial request carries ``X-Next-Request: 1``, a header no cross-site form can set without a CORS preflight, so no request from another site reaches a boundary reload.
``next.defer.lifecycle.boundary_token`` reads the token from the configured carrier once and drops the token field from the query string and a form body, so nothing the page renders echoes it, and the page gate of ``next.defer`` drops the field from every other page request.
``note_purpose`` sets ``request.next``, whose ``purpose`` names why the view renders, ``document``, ``navigate``, ``prefetch``, or ``speculation`` for a browser prefetch.
Its ``depth`` is the ``PrefetchDepth`` of ``next.requests``, ``PrefetchDepth.SHELL`` for a router prefetch that asks for the shell, ``PrefetchDepth.FULL`` for one sent at the press of a link, and ``None`` for any other request.
``purpose_of(request)`` and ``depth_of(request)`` from ``next.pages.responses`` read them, and :ref:`topics-router-prefetch-visits` says why ``depth`` is no visit counter.

``route_request`` answers a ``RequestKind`` of ``next.requests``, the one vocabulary of request kinds every layer shares, ``page``, ``zone``, ``boundary``, or ``navigate``, and form action dispatch adds ``action``.
A request with ``X-Next-Request: 1`` takes the most specific kind a participant claims, ``boundary`` before ``zone`` before ``navigate``, and any other request is a ``page``.

.. autoclass:: next.requests.RequestKind
   :no-special-members:

.. autoclass:: next.requests.PrefetchDepth
   :no-special-members:

.. autofunction:: next.views.kinds.note_purpose

.. autofunction:: next.views.kinds.route_request

.. autofunction:: next.defer.lifecycle.boundary_token

Participants
------------

``next.lifecycle`` holds the contract every feature takes part in a page request through and the registry the page view reads.
:doc:`/content/topics/extending` shows a project registering its own.

.. autoclass:: next.lifecycle.Participant

.. autoclass:: next.lifecycle.Share
   :no-special-members:

.. autoclass:: next.lifecycle.Gate
   :no-special-members:

.. autoclass:: next.lifecycle.PageFacts
   :no-special-members:

.. autoclass:: next.lifecycle.PageCall

.. autoclass:: next.lifecycle.ResponseSource
   :no-special-members:

.. autofunction:: next.lifecycle.register_participant

.. _ref-views-plan:

The plan of a page
------------------

A view decides once per page which optional request features that page can use, and holds the answer as one ``PagePlan``.
It analyses the template, then asks every participant for its points in each kind of request and method, and holds them as ready tuples.
A safe request for a page whose plan is ``plain`` renders straight, with no deferral block, no error boundary scope, and no async drive, inside only the blocks its points name, so a page no participant takes part in runs the bare render.

.. autoclass:: next.views.plan.PagePlan
   :no-special-members:

.. list-table::
   :header-rows: 1
   :widths: 16 84

   * - Field
     - Decided from
   * - ``links``, ``marks``, ``fragments``
     - The active-link backend, whether the templates of the page can open a container it marks, and whether each container of the page template marks its own output, so the document pass is skipped.
   * - ``plain``
     - No deferral, boundary, or async callable.
   * - ``safe``, ``unsafe``
     - The ``Points`` of each kind by the safety of the method, read with ``points(kind, safe=...)``.
   * - ``defers``, ``guarded``
     - A ``loading.djx`` or an ``error.djx`` in the layout chain, and for a page that is not inert any component of the project that defers or carries an ``error.djx``.
   * - ``streams``
     - Whether any component of the project defers, so the payload of the page says a partial may stream.
   * - ``awaits``
     - An async context or metadata callable of the page.

A template is inert while every node in it is text, a variable without ``safe``, a branching or looping tag of Django, ``{% zone %}`` without a loading skeleton, ``{% has_perm %}``, or one of the layout and asset tags.
A component call, an ``{% include %}``, and any other tag open the template, and so does a ``render()`` body, which is compiled per request.
The active-link pass is skipped only for an inert template that holds no variable and no ``{% now %}``, and whose source spells no container.
Such a template writes nothing but its own source, so the search made once, when the plan is built, answers for every render.
Any variable may carry markup marked safe, so a template that holds one is searched for a container after each render, unless the compiled containers of ``fragments`` mark it, see :doc:`/content/topics/router/active-links`.

The plan is rebuilt when the compiled template or the merged ``ROUTER`` mapping changes.
It is rebuilt when ``lifecycle.version`` moves, which a registration and every settings change cause.
A ``page.py`` load, a page declaration, a context or metadata registration, and a change of the deferring or guarded component counts rebuild it as well.
``blind()`` answers the plan of a page read without its template, open to whatever it may do, which a boundary reload, an async ``render()``, and a module broken at build use.

.. autoclass:: next.views.plan.PagePlans
   :members: of, blind

Transactions of a view
----------------------

A page view, a zone request, and a boundary reload pass the ``RequestKind`` they routed by to the transaction rule.
The kind is ``page``, ``zone``, ``boundary``, or ``navigate``, and form action dispatch passes ``action``.
The access check, the cross-site and stale-version refusals, and the prefetch gate of a navigation run before the transaction opens, so a refused request opens none.
The transaction then wraps the render and closes once the response object exists, so an exception rolls the work back and a returned response commits it whatever its status.
A streamed page writes its chunks after that, so its deferred jobs run outside the transaction of the view, see :doc:`/content/topics/boundaries/async`.

Checks
------

The ``TRANSACTIONS`` scope, ``page.transaction()``, and ``ATOMIC_REQUESTS`` are checked in ``next.transactions.checks``, see :doc:`system-checks`.

See also
--------

.. seealso::

   :doc:`urls` for the router that mounts the views.
   :doc:`defer` for ``AtomicOverride`` and the transaction helpers.
   :doc:`pages` for ``page.transaction()`` and the response policy.
