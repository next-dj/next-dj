.. _internals-router-pipeline:

The client router inside
========================

The client router turns a link click into a request for the part of the page that changes.
This page follows a navigation through both halves, the Python area ``next.router`` on the server and the TypeScript runtime in ``next/client/`` in the browser.
Read :doc:`/content/topics/router/index` first for what the router does and how a project switches it on.

.. contents::
   :local:
   :depth: 2

The shape of a navigation
-------------------------

.. mermaid::

   sequenceDiagram
       participant C as router.ts
       participant S as router/lifecycle.py
       C->>S: GET target with X-Next-Navigate and X-Next-Layouts
       S->>S: gate, then claim the kind navigate
       S->>S: foresee the cut from fixed chrome, or render whole
       S->>S: digest the layouts, cut, build the envelope
       S-->>C: envelope with morph of the slot, layouts and head ops
       C->>C: swap the slot, commit the chain, save the snapshot

The client declares the layouts on screen, each with the digest of its chrome, in ``X-Next-Layouts``.
The server renders the target, compares its layouts with the declaration, and answers only with what differs.
Everything else in this page serves that comparison, making it cheap, making it safe, or making the client able to apply the answer.

The server side
---------------

The participant
~~~~~~~~~~~~~~~

``RouterParticipant`` in ``next/router/lifecycle.py`` is the router's part in the page view, see :ref:`internals-request-lifecycle-participants`.
It claims a navigation request while the router is on, and its gate answers without rendering when it can.
The gate refuses a request another site sent, answers a stale client version, lets an intercept answer, and refuses a prefetch the page opts out of or the process sheds under load.
A page that opts out of navigation and a path that opens with ``//``, which browsers read as a host, get a ``visit`` envelope, which tells the client to load the page whole.
The participant then gives the render a block that binds the router state, a ``respond`` that builds the envelope, and a ``finish`` that stamps the directives of the router on the response, ``X-Next-Router`` among them.

The navigation package
~~~~~~~~~~~~~~~~~~~~~~

The server half of a navigation lives in the ``next/router/navigation/`` package, one submodule per concern, and ``next.router.navigation`` re-exports its names.

- ``intent`` reads what a request declares, its layouts on screen, its origin, and its URL.
- ``shedding`` answers before any render with a refusal, a stale version, or a shed prefetch.
- ``interception`` opens the layer of a link to an intercepted page.
- ``shell`` reads the rendered page, the bounds and the digests of each layout.
- ``cuts`` picks the cut a navigation lands on, and ``envelopes`` builds the answer.
- ``marking`` runs the active-link passes, and ``clearing`` sets the router directives on login, logout, and a cycled session.
- ``reporting`` sends ``navigation_rendered`` and the ``DEBUG`` report of the cut, and ``state`` holds the router state of one page view.

The envelope
~~~~~~~~~~~~

``navigation_envelope`` in ``next/router/navigation/envelopes.py`` builds the answer from a ``ScannedPage``, the rendered page with the bounds of each layout and its slot, and a ``Cut``.
The cut is one of three.

``SLOT``
   The declared chain matches the page down to some layout, and the answer replaces that layout's slot.
``BODY``
   No layout matches but the root and its head do, and the answer replaces the body.
``VISIT``
   The root or its head differs, or the page cannot be applied softly, and the client loads the page whole.

The envelope also carries the new chain of layout ids, digests and response ids, which the client keeps for the next navigation, the head tags, the assets the page adds, and the context.
When the response holds deferred boundaries, the envelope is the first frame of an event stream and the boundaries follow, see :doc:`boundary-pipeline`.

The digest of a layout
~~~~~~~~~~~~~~~~~~~~~~

A layout's digest says that its chrome, the markup around the slot, is the same bytes as the client has.
``next/router/digest.py`` finds the regions of a rendered layout by the comment markers the router encloses each slot with, removes what changes per response, such as nonces and CSRF tokens, and hashes the rest.
``ChromeDigest`` in ``next/router/backends.py`` is the shipped backend, and a project subclass strips a token of its own.
``VerifiedCut`` walks the declared chain from the root and keeps the deepest layout whose id and digest both match.

The digest is computed from a render, so the straight way renders the whole page before it knows what to keep.
The cheap way needs no render of the chrome.

Foreseeing the cut
~~~~~~~~~~~~~~~~~~

A layout whose chrome holds only text, zones, the metadata tag, comments, loads, the CSRF tag, and the asset slots writes the same bytes on every render once normalised.
``next/views/fixed.py`` reads such a layout from its compiled template and derives its digest from its source, held per template.
When every layout of the target page is fixed, ``foresee`` in ``next/router/navigation/cuts.py`` runs ``VerifiedCut`` on those digests before any render, and the answer is a ``Foreseen`` cut.

The page then renders only the slot the cut keeps.
``SlotView`` renders that one layout's slot and runs the chrome around it for its effects, the metadata fold and the CSRF token, and the HTML it returns is the whole shell of the answer.
The prediction is trusted only for the shipped cut and the shipped digest, and a request that clears the router, or a page with errors or deferred work in its chain, renders whole.
A test compares the envelope of a foreseen navigation with the one of a full render, so the shortcut cannot drift.

Active links
~~~~~~~~~~~~

A navigation container, ``<nav>`` or whatever ``ROUTER["ACTIVE_LINKS"]["CONTAINERS"]`` lists, marks the links that point at the current URL.
Two passes exist, and they must agree.

The template pass runs at compile time.
``compile_containers`` in ``next/views/containers.py`` wraps each container a template writes in a ``ContainerNode``, so the container marks its own links when it renders and no pass reads the document.
A container of plain text is read once at compile time into ``StaticLinks``, and a request only compares the path with the notes of its anchors.
The nodes live in ``next/marks.py`` and ``FragmentPass`` binds the request to them.

The document pass is the fallback.
``DocumentLinks`` in ``next/router/links.py`` scans the rendered HTML once for containers and marks them.
A page uses it when a template cannot be compiled that way, when a container comes from a variable or an include the compiler cannot see, or when ``ROUTER["ACTIVE_LINKS"]["PASS"]`` is ``"document"``.
``PagePlan.fragments`` in ``next/views/plan.py`` is the decision, made once per page and held with the plan.
A project backend that marks links its own way has to override ``fixed`` as well as ``mark``.

Prefetch and shed
~~~~~~~~~~~~~~~~~

A prefetch is a navigation request with a depth, ``shell`` for the page without its deferred jobs and ``full`` for the page a press may show.
The gate checks that the page allows it, then ``shed`` asks the ``PrefetchShed`` backend whether the process is behind.
``LoadShed`` answers with the age of the oldest waiting job, and the response is a 429 with ``Retry-After``, which makes the client back off before it prefetches again.
``prefetch_shed`` reports each one.
A project without deferred boundaries binds no load, so nothing sheds.

The client side
---------------

Chunks
~~~~~~

The runtime in ``next/client/next.ts`` is small, and every other feature is a chunk that registers through ``Next._extend``, see :doc:`client-runtime`.
A routed document declares ``next.router.min.js`` in its payload.
``next/client/entries/router.ts`` installs ``createRouter`` of ``router.ts``, and the router loads the ``intent`` chunk when the page is idle, and the ``boundaries`` chunk that reads streamed answers.
The router never waits for the intent chunk, so a navigation works before prefetch exists.

A visit
~~~~~~~

``router.ts`` owns plain same-origin links and GET forms, under the default scope only those whose URL the route table of ``routes.ts`` names as a page.
The table loads once at boot from the URL in ``$router.routes``, and until then the router owns no URL.
``isPage`` decodes the path, refuses one past 512 characters or holding a control character, cuts the script prefix, and lets the first row that matches decide, reading the path again with a slash while the table says the server appends one.

It sends the request with the layouts on screen and reads the envelope.
``plan`` sorts the operations in it, a ``morph`` of a slot or the body, a ``layer.open``, or a ``visit``.
The first envelope of a visit swaps the slot with ``swap.ts``, which carries persisted nodes across, runs the scripts of the new slot, and retires the stylesheets of the left page.
``commit`` then records the new chain, so the next navigation declares what is now on screen.
A visit in flight is aborted by the next one, and the progress bar shows only when a visit outlives its delay.

Prefetch and press
~~~~~~~~~~~~~~~~~~

``next/client/prefetch.ts`` lives in the intent chunk.
Hover and focus wishes wait out a delay, a viewport wish waits for a spare slot, and a press or a touch sends the full request at once, so the click that usually follows finds it under way.
All of them share one request limit.
A visit takes its answer from the cache or from the press in flight, never both for one URL.
A 429 or a 5xx pauses every prefetch for the ``Retry-After`` time or a doubling pause, and a 204 refuses the one page for good.

Snapshots and history
~~~~~~~~~~~~~~~~~~~~~

``history.ts`` keeps, for each history entry, the snapshot of the slot the page showed and the scroll position, and the paths the server refused to answer as an envelope.
Back restores the snapshot of an entry it holds, a bounded number of them, and loads the page otherwise.
``navigation.ts`` writes the history and fires ``next:navigated`` once per commit, and a layer pushes its own URL as it opens.
The router clear, which login and logout send, drops the snapshots that may show what the session held before it changed.

When a navigation becomes a full load
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The client calls ``visitHard`` in each of these cases.

- The envelope holds a ``visit`` operation, which the server sends for a root or head that differs, a page that opts out, a cleared router, and a swap that would lose a script.
- The answer is not ok, and a plain document the router does not extract loads whole.
- The envelope is malformed, or its operations fail to apply.
- The request fails.

The script case is decided on the server by ``scripted`` in ``next/router/navigation/cuts.py`` through ``dropped_script`` of ``next/static/runtime.py``.
A swap runs an inline script only under the nonce of the response, so a ``src`` script or an inline script without it turns the cut into a ``VISIT`` with the reason ``script``.
Under ``DEBUG`` the envelope names the script and the template that holds it.

Where to look
-------------

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - The symptom
     - The first files
   * - A navigation loads the page whole
     - ``X-Next-Navigate`` under ``DEBUG``, then ``next/router/backends.py`` and ``next/router/navigation/cuts.py``
   * - A layout is not kept
     - ``next/router/digest.py`` and the chrome of the layout
   * - A link is not marked current
     - ``next/router/links.py``, ``next/views/containers.py`` and ``PagePlan.fragments``
   * - A prefetch does nothing or backs off
     - ``next/router/lifecycle.py`` for the gate and ``next/client/prefetch.ts``
   * - Back shows the wrong page
     - ``next/client/history.ts`` and ``next/client/router.ts``

See also
--------

.. seealso::

   :doc:`request-lifecycle` for the participants and the kinds of request.
   :doc:`boundary-pipeline` for the boundaries a navigation streams.
   :doc:`client-runtime` for the build and the chunks of the runtime.
   :doc:`/content/topics/router/index` for the router as a user meets it.
