.. _ref-views:

Views reference
===============

Module summary
--------------

``next.views`` holds the views the URL router mounts for pages.
It sits above ``next.pages``, ``next.partial``, ``next.suspense``, and ``next.deps`` and calls them directly, and ``next.urls`` mounts its ``page_view``.
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

A request to a page URL is routed once, then answered inside the transaction the method rule or ``page.atomic()`` gives it, see ``TRANSACTIONS`` in :doc:`settings`.

.. list-table::
   :header-rows: 1
   :widths: 16 84

   * - Route
     - Answer
   * - ``PAGE``
     - The page access check, then the response policy, the render, and a whole response or a stream, see :doc:`/content/topics/boundaries/streaming`.
   * - ``ZONE``
     - The page access check, then the envelope of the zones the request names, see :doc:`/content/topics/partial-rendering/how-it-works`.
   * - ``BOUNDARY``
     - The reload of one client-trip boundary.
       A reload answers ``GET`` and ``HEAD``, and ``POST`` as well under ``BodyTransport``, and any other method answers 405.
       A request whose ``Sec-Fetch-Site`` is anything but ``same-origin`` answers 403, then the page access check runs, and the token is redeemed last.
   * - ``NAVIGATE``
     - A navigation of the client router, while ``ROUTER["ENABLED"]`` holds.

A partial request carries ``X-Next-Request: 1``, a header no cross-site form can set without a CORS preflight, so no request from another site reaches a boundary reload.
``boundary_token`` reads the token through the configured transport once and drops the token field from the query string and a form body, so nothing the page renders echoes it.
``note_purpose`` sets ``request.next``, whose ``purpose`` names why the view renders, ``document``, ``navigation``, ``prefetch``, or ``speculation`` for a browser prefetch.
Its ``depth`` is ``Depth.SHELL`` for a router prefetch that asks for the shell, ``Depth.FULL`` for one sent at the press of a link, and ``None`` for any other request.
``purpose_of(request)`` and ``depth_of(request)`` from ``next.pages.responses`` read them, and a prefetch answer may be shown with no further request, so code that must act on a visit declares ``page.router(prefetch=False)``.

.. autoclass:: next.views.Route
   :no-special-members:

.. autofunction:: next.views.routes.decide_route

.. autofunction:: next.views.routes.boundary_token

.. autofunction:: next.views.routes.note_purpose

``route_request`` does both for a view, and for a request without ``X-Next-Request: 1`` it reads no token and parses the query string only when the request has one.

.. autofunction:: next.views.routes.route_request

.. _ref-views-plan:

The plan of a page
------------------

A view decides once per page which optional request features that page can use, and holds the answer as one ``PagePlan``.
A safe request for a page whose plan is ``plain`` then renders straight, with no transaction, no deferral block, no error boundary scope, and no router state.

.. autoclass:: next.views.plan.PagePlan
   :no-special-members:

.. list-table::
   :header-rows: 1
   :widths: 16 84

   * - Field
     - Decided from
   * - ``router``
     - ``ROUTER["ENABLED"]``.
   * - ``links``, ``marks``
     - The active-link backend, and whether the templates of the page can open a container it marks.
   * - ``reads``
     - The aliases a safe request opens, from ``TRANSACTIONS`` and ``page.atomic()``, ``None`` under a ``RULE``, which answers per request.
   * - ``defers``, ``guarded``
     - A ``loading.djx`` or an ``error.djx`` in the layout chain, and for a page that is not sealed any component of the project that defers or carries an ``error.djx``.
   * - ``streams``
     - Whether any component of the project defers, so the payload of the page says a partial may stream.
   * - ``awaits``
     - An async context or metadata callable of the page.

A template is sealed while every node in it is text, a variable without ``safe``, a branching or looping tag of Django, ``{% zone %}`` without a loading skeleton, ``{% has_perm %}``, or one of the layout and asset tags.
A component call, an ``{% include %}``, and any other tag open the template, and so does a ``render()`` body, which is compiled per request.
The active-link pass is skipped only for a sealed template that holds no variable and no ``{% now %}``, and whose source spells no container.
Such a template writes nothing but its own source, so the search made once, when the plan is built, answers for every render.
Any variable may carry markup marked safe, so a template that holds one is searched for a container after each render.

The plan is rebuilt when the compiled template, the merged ``ROUTER`` or ``TRANSACTIONS`` mapping, the database settings, a ``page.py`` load, a ``page.atomic()`` declaration, a context or metadata registration, or the deferring and guarded component counts change.

.. autoclass:: next.views.plan.PagePlans
   :members: of

Transactions of a view
----------------------

A page view, a zone request, and a boundary reload pass their ``ViewKind`` to the transaction rule, ``page``, ``zone``, ``boundary``, or ``navigation``, and form action dispatch passes ``action``.
The access check, the cross-site and stale-version refusals, and the prefetch gate of a navigation run before the transaction opens, so a refused request opens none.
The transaction then wraps the render and closes once the response object exists, so an exception rolls the work back and a returned response commits it whatever its status.
A streamed page writes its chunks after that, so its deferred jobs run outside the transaction of the view, see :doc:`/content/topics/boundaries/async`.

Checks
------

``next.views.checks`` validates the ``TRANSACTIONS`` scope, reports a ``page.atomic()``, an ``@action(atomic=...)``, or a ``Meta.atomic`` that declares nothing as ``next.E240``, and warns about ``ATOMIC_REQUESTS`` as ``next.W154``, see :doc:`system-checks`.

See also
--------

.. seealso::

   :doc:`urls` for the router that mounts the views.
   :doc:`defer` for ``ViewKind``, ``AtomicOverride``, and the transaction helpers.
   :doc:`pages` for ``page.atomic()`` and the response policy.
