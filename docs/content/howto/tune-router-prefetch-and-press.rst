.. _howto-tune-router-prefetch-and-press:

Tune or turn off router prefetch and press
==========================================

Problem
-------

The client router fetches a page before the click, on hover, on scroll, and at the press of a link.
Each of those is a render on the server, and some pages must not be rendered speculatively at all.
A page that counts a visit, or a link that signs the visitor out, needs the router to leave it alone, and a deployment that is short of capacity wants fewer speculative renders.

Solution
--------

Set the mode project-wide with ``ROUTER["PREFETCH"]`` and ``ROUTER["PRESS"]``.
Override it for a region or a link with ``data-next-prefetch`` and ``data-next-press``.
Refuse it for one page on the server with ``page.router(prefetch=False)``.

Walkthrough
-----------

Know what each step costs
~~~~~~~~~~~~~~~~~~~~~~~~~

A prefetch is a navigation request that carries ``X-Next-Prefetch``.
The server renders the page through the same view, its access rules and its context callables included.
The depth tells what runs.

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Trigger
     - What the server does
   * - Hover or focus
     - Renders the shell, every deferred boundary as its loading UI and no deferred job.
   * - Scroll, under ``"viewport"``
     - The same shell render for every link in view, up to ``ROUTER["PREFETCH_ENTRIES"]`` pages.
   * - Press of a mouse
     - Renders the full page, runs the deferred jobs, and streams when a navigation would.

A press is the expensive one, since a job that started is not interrupted when the visitor leaves.
That is why a touch or a pen keeps the shell depth by default.

Each trigger reaches only the URLs the router owns.
Under the default ``ROUTER["SCOPE"]`` of ``"pages"`` those are the URLs a page answers, so a plain Django view, the admin site, or a file is never prefetched, see :ref:`topics-router-scope`.
Under ``"all"`` every same-origin link is prefetched, and a view with an effect on ``GET`` needs ``data-next-router="off"`` on its links or its prefix in ``ROUTER["EXCLUDE"]``.

Choose the mode for the project
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "ROUTER": {"PREFETCH": "viewport", "PRESS": "off"},
   }

``PREFETCH`` is ``"intent"`` by default, which prefetches on hover and keyboard focus.
``"viewport"`` adds every link that scrolls into view, which suits a site of mostly reading pages and read-only renders, and costs a render per link in view.
``"off"`` stops every automatic prefetch, and ``Next.router.prefetch(url)`` still works.

``PRESS`` is ``"mouse"`` by default, ``"all"`` sends the full request for every pointer, and ``"off"`` keeps every press at the shell depth.
``PREFETCH = "off"`` turns the press off as well, since the press follows every other prefetch rule.
``PREFETCH_DELAY``, ``PREFETCH_TTL``, ``PREFETCH_SHELL_TTL``, and ``PREFETCH_ENTRIES`` tune the wait and the cache, see :doc:`/content/ref/settings`.

Override a region or a link
~~~~~~~~~~~~~~~~~~~~~~~~~~~

``data-next-prefetch`` and ``data-next-press`` on a link, or on an element around it, replace the setting for the links inside.

.. code-block:: jinja
   :caption: shop/pages/layout.djx, inside the body

   <nav data-next-press="all">
     <a href="/reports/">Reports</a>
   </nav>
   <a href="/invites/accept/" data-next-prefetch="off">Accept the invite</a>
   <a href="/export/" data-next-press="off">Export</a>

``data-next-prefetch="off"`` stops every prefetch of the link, press included.
``"viewport"`` turns the scroll prefetch on for a region under ``"intent"``, and ``"intent"`` turns it off for a region under ``"viewport"``.
Under ``ROUTER["PREFETCH"] = "off"`` the attribute turns nothing on.
A link on or inside an element with ``data-next-confirm`` or ``data-next-action`` is never prefetched.

Refuse a prefetch on the server
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A page whose context callables act on a visit, such as one that records a view or consumes a one-time token, declares that no prefetch may render it.
The page keeps its template and its callables, and its ``page.py`` gains one call.

.. code-block:: python
   :caption: shop/pages/invites/accept/page.py

   from next import page

   page.router(prefetch=False)

The server answers every prefetch of that page with ``204`` before any render, and the browser asks for the URL no more for the life of the page.
The click then navigates with its own request, which is the only render.
``page.router(navigate=False)`` goes further and answers every navigation with a hard load.

Count visits in the browser
~~~~~~~~~~~~~~~~~~~~~~~~~~~

A prefetch answer can be shown to the visitor with no further request, a complete one after a hover and a full one after a press.
On the server a press is a prefetch too, one whose ``request.next.depth`` is ``PrefetchDepth.FULL``.
A counter that skips prefetches therefore misses every visit a prefetch answer served, and a counter that counts them also counts the hovers and the presses that never became clicks.
Count in the browser on ``router:commit``, which fires for each navigation the router commits, and leave the first load of a document to the server.
A page that must act on a visit on the server declares ``page.router(prefetch=False)`` instead, see :ref:`topics-router-prefetch-visits`.

.. code-block:: javascript
   :caption: static/shop/visits.js

   document.addEventListener("router:commit", () => {
     navigator.sendBeacon("/visits/", new URLSearchParams({ path: location.pathname }));
   });

Verification
------------

The settings reach the browser in the ``$router`` payload of the document.

.. code-block:: python
   :caption: tests/test_router.py

   from next.testing import NextClient, init_payload

   def test_the_press_is_off(db) -> None:
       router = init_payload(NextClient().get("/").content.decode())["$router"]
       assert router["prefetch"] == "viewport"
       assert router["press"] == "off"

A page that refuses prefetch answers ``204``, which the client sends as the router does through ``navigate``.

.. code-block:: python
   :caption: tests/test_router.py

   def test_the_invite_page_refuses_a_prefetch(db) -> None:
       client = NextClient()
       client.get("/")
       for headers in ({"X-Next-Prefetch": "1"}, {"X-Next-Prefetch": "full"}):
           step = client.navigate("/invites/accept/", origin="/", headers=headers)
           assert step.response.status_code == 204

A ``prefetch=True`` argument of ``navigate`` sends the shell depth, and ``headers={"X-Next-Prefetch": "full"}`` sends the press.
In the browser, the network panel shows the requests with ``X-Next-Prefetch`` on hover and on press, and none for a link the page opted out.

See also
--------

.. seealso::

   :doc:`/content/topics/router/prefetch` for the queue, the cache, the refusals, and the press in full.
   :doc:`/content/topics/router/sessions` for the answers a server gives a prefetch and what clears the cache.
   :doc:`/content/security/overview` for the cost a speculative render adds.
   :repo:`markdown-blog <tree/main/examples/markdown-blog>` for ``"viewport"`` with one opted-out link, and :repo:`observability <tree/main/examples/observability>` for the press and its tests.
