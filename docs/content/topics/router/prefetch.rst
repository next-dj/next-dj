.. _topics-router-prefetch:

Prefetch and speculation
========================

The router fetches a page the visitor is about to open before the click, so the navigation applies an answer that is already in memory.
The ``intent`` bundle carries this prefetch for the links the router owns, and Speculation Rules hand the links it does not own to the browser.

.. contents::
   :local:
   :depth: 1

Viewport and intent prefetch
----------------------------

``ROUTER["PREFETCH"]`` decides when a link is prefetched.

``"intent"``, the default
   A link the pointer rests on, or one that gains keyboard focus, is prefetched once ``ROUTER["PREFETCH_DELAY"]`` seconds pass, ``0.065`` by default.
   Leaving the link earlier cancels it, and a press of the pointer or a touch skips the wait, see `The press of a link`_.

``"viewport"``
   Every link the router owns is also prefetched once it scrolls into view.
   A touch screen has no hover, so the links in view are what makes its first tap land on a prefetched answer.
   Each prefetch is a render of the target page on the server, so the mode costs up to ``ROUTER["PREFETCH_ENTRIES"]`` renders for a page of links, and those requests run beside whatever the visitor does next.
   A browser test against a SQLite database shared in memory can meet a locked table when such a read overlaps a write, which is a reason to keep the test settings on ``"intent"``.

``"off"``
   No link is prefetched on its own, and ``Next.router.prefetch()`` still works.

``data-next-prefetch`` on a link or an element around it overrides the mode for the links inside.
``"viewport"`` turns the viewport prefetch on for a region under ``"intent"``, ``"intent"`` turns it off for a region under ``"viewport"``, and ``"off"`` turns every prefetch off for the region.
Under ``"off"`` the attribute turns nothing on.

.. code-block:: jinja
   :caption: layout.djx

   <aside data-next-prefetch="intent">
     {% for item in archive %}<a href="{{ item.url }}">{{ item.title }}</a>{% endfor %}
   </aside>
   <a href="/invites/accept/" data-next-prefetch="off">Accept the invite</a>

A link is prefetched only when the router would take it over, see :doc:`index`, when its ``target`` stays in the page, and when it carries no ``download`` and no ``rel="external"``.
A link on or inside an element with ``data-next-prefetch="off"``, ``data-next-confirm``, or ``data-next-action`` is never prefetched.
Nothing is prefetched while the tab is hidden, while the browser reports Save-Data, or on a connection the browser rates ``2g`` or ``slow-2g``.

Prefetches wait in one queue, and at most three run at once.
An intent prefetch goes ahead of every link in view, the latest intent first, and a link in view takes at most two of the three, so a hover never waits behind a page of links.
A URL already queued or in flight is not asked again.
A link that leaves the view, and an intent the pointer leaves, drop out of the queue unless their request has started.
A link in view is prefetched once for the life of the page, whatever its answer, and after that only an intent asks for its URL again, so an expired answer is never refetched in a loop.
A request a flush aborts leaves its link watched, and the link is prefetched again the next time it scrolls into view.
A link in view waits while the kept answers and the prefetches in flight reach ``ROUTER["PREFETCH_ENTRIES"]``, so a long page of links never evicts fresh answers.
When it needs room, the oldest kept shell older than ``ROUTER["PREFETCH_TTL"]`` gives way, except the answer for the link the pointer rests on, and a fresher answer never does.
The link of an answer that gave way is prefetched again only on intent.

The prefetch cache
------------------

Each answer is kept under its URL without the fragment and the layout chain on screen when it was asked for, so a prefetch made under another chain is never applied.
An answer marked ``complete`` stays usable for ``ROUTER["PREFETCH_TTL"]`` seconds, ``30`` by default, and a shell, any other answer, for ``ROUTER["PREFETCH_SHELL_TTL"]`` seconds, ``300`` by default, since the navigation that applies a shell fetches the page beside it and morphs the fresh render over it within one round trip.
A URL with a usable answer is not asked again.
``ROUTER["PREFETCH_ENTRIES"]`` bounds the answers kept, ``16`` by default, and the oldest goes first.
Only a ``2xx`` navigation envelope that names a layout chain and was not redirected is kept.

The first navigation to the URL takes the answer out of the cache and applies it at once.
An answer the server marked ``complete`` is the whole page, so the navigation stops there and sends no request.
For any other answer the navigation asks for the page at the click, beside the prefetched shell it applies, and the fresh render morphs over the shell as it arrives, see :doc:`browser`.
A navigation to a URL whose prefetch is still in flight waits for that prefetch and sends nothing beside it, so a quick click or a tap costs the server one render.
A complete answer then lands with no request, a shell lands and the request for the page leaves at that moment, and an answer that never came, such as a refusal or a failure, leaves the navigation to its own request.
A URL still waiting in the queue leaves it, and the navigation asks for the page alone.
A URL the router is already navigating to is not prefetched.

The press of a link
-------------------

A press of a link starts the page request before the click, so the click adopts an answer that is already on its way.
``ROUTER["PRESS"]`` decides which pointers do it.

``"mouse"``, the default
   A press of a mouse sends the full request.
   A touch or a pen keeps the shell prefetch it had.

``"all"``
   Every pointer sends the full request at its press.

``"off"``
   Every press stays at the shell depth.

``data-next-press`` on a link or an element around it overrides the setting for the links inside, with the same three values.
The press follows the other prefetch rules of this page, so ``ROUTER["PREFETCH"] = "off"``, ``data-next-prefetch="off"``, a hidden tab, Save-Data, and a ``2g`` connection turn it off with every other prefetch.
A path the router fetches as a plain document sends no press request either.

.. code-block:: jinja
   :caption: layout.djx

   <nav data-next-press="all">
     <a href="/reports/">Reports</a>
   </nav>
   <a href="/search/" data-next-press="off">Search</a>

A press with the main button and no modifier key sends the page request at once, ahead of the queue, with ``X-Next-Prefetch: full``.
The request keeps its response unread.
A press with another button, or with Shift, Ctrl, Alt, or Meta held, keeps the shell prefetch, since the click would not navigate in place.

The click that follows takes that response as its navigation and sends no request of its own.
When a hover left a shell for the URL in the cache, the click lands that shell first and the response of the press morphs over it as it arrives.
When the response streams, the navigation commits on its envelope and the boundaries fill as their jobs settle.
A press sends nothing when the cache holds a fresh ``complete`` answer for the URL, when the URL was refused with ``204`` earlier, and while prefetches are paused.
A shell prefetch of the URL that waits in the queue is dropped by the press, and one already in flight runs on.

A press that does not become a click ends in one of these ways.

- The browser fires ``pointercancel``, ``dragstart``, or ``contextmenu``.
- The pointer is released outside the link.
- Another navigation starts.
- One second passes without a click.

An answer that arrived whole as a navigation envelope is kept in the prefetch cache, under the rules above.
A response that is still streaming, or was redirected, is closed.
A navigation that a ``router:before`` listener cancels closes the request of its press as well.

A full request runs the deferred jobs of the page, and a job that started is not interrupted when the visitor leaves.
That cost is why a touch keeps the shell depth by default, since a scroll that starts on a link begins with the same event as a tap.

When an answer lands, each stylesheet it lists that the document holds no ``<link>`` for is fetched ahead with a ``<link rel="preload" as="style">`` carrying the nonce of the router script, so the navigation does not wait on the stylesheet gate.
An answer asked for by the viewport alone uses ``rel="prefetch"`` instead, since a link in view is a weaker hint than an intent.
An answer for an asset version other than the one the page holds is dropped unused.
The cache is emptied with the snapshots, after a non-``GET`` partial request, a clearing answer, a fresh CSRF token, a restore from the back-forward cache, and ``Next.router.flush()``, as :doc:`browser` lists, and all but the restore also abort a prefetch in flight.

What a prefetch renders
-----------------------

A prefetch is a navigation request that also carries ``X-Next-Prefetch``.
The server renders the page through the same view, with its access rules and context callables, and ``request.next.purpose`` is ``prefetch`` at both depths.
``request.next.depth`` tells them apart, ``Depth.SHELL`` for ``X-Next-Prefetch: 1`` and ``Depth.FULL`` for ``X-Next-Prefetch: full``.
It is ``None`` for a request that is no router prefetch.

At the shell depth a deferred boundary renders its loading content and no deferred job runs, so the answer is the shell of the page, and it never streams.
The navigation that applies it shows that loading content until the fresh render fills the boundaries.
A shell that skipped no deferred job and left no client-trip boundary is marked ``complete`` in its envelope, since a navigation would render the same page.

At the full depth the server answers as it answers a navigation.
The deferred jobs run, and the answer streams when the delivery policy lets a navigation stream, see :doc:`/content/topics/boundaries/streaming`.
Otherwise it is the assembled page in a JSON envelope, marked ``complete`` unless it left a client-trip boundary.

The context callables of a prefetched page do run, and a prefetch answer may be shown to the visitor with no further request, a complete one after a hover and a full one after a press.
A page that must act on a visit declares ``page.router(prefetch=False)``, and the click then navigates with its own request.
A counter on the server that skips prefetches still counts a prefetch at the full depth, ``request.next.depth == "full"``, or the counting happens in the browser on ``router:commit``.

The server's answers
--------------------

``204`` with an empty body
   The page declares ``page.router(prefetch=False)`` or ``page.router(navigate=False)``, or it is an intercepted page under ``ROUTER["INTERCEPT"] = "links"``.
   The browser asks for the URL no more for the life of the page.

``429`` with ``Retry-After``
   The oldest job in the pool queue of deferred boundaries waited longer than ``DEFER["EXECUTOR"]["OPTIONS"]["QUEUE_TIMEOUT"]``, see :doc:`sessions`.
   The browser pauses every prefetch for the seconds ``Retry-After`` names, and drops the links waiting in the queue, which come back the next time they scroll into view.

A ``429`` without a usable ``Retry-After``, or a ``5xx``, pauses every prefetch for thirty seconds, a wait that doubles with each further failure up to five minutes, and a successful answer resets it.
A full request meets the same answers.
A ``204`` marks the URL as refused, and a ``429`` or a ``5xx`` pauses every prefetch.
In each case the press leaves nothing to adopt, and the click navigates with its own request.
A prefetch answer that carries ``X-Next-Router: clear`` empties the prefetch cache and drops every snapshot.

Prefetching from code
---------------------

``Next.router.prefetch(url)`` loads the router bundles when they are not loaded yet and prefetches ``url``.
It ignores ``ROUTER["PREFETCH"]``, the hidden tab, and the connection, and follows every other rule of this page, the cache, the refusals, the pause, and the queue, where it counts as an intent.
A URL on another origin, under an excluded prefix, or remembered as an answer that is no envelope is not fetched.

.. code-block:: javascript

   document.addEventListener("router:commit", () => {
     Next.router?.prefetch("/checkout/");
   });

Speculation Rules
-----------------

``ROUTER["SPECULATION"]`` hands the links the router does not own to the browser's own prefetch.
It is ``None`` by default, which emits nothing.
An eagerness, ``"conservative"``, ``"moderate"``, or ``"eager"``, written as a string or as ``{"eagerness": ...}``, makes every routed document carry a ``<script type="speculationrules">`` in its head, with the CSP nonce of the response.

.. code-block:: python
   :caption: settings.py

   NEXT_FRAMEWORK = {
       "ROUTER": {"SPECULATION": "moderate"},
   }

The rules hold one ``prefetch`` rule at that eagerness and never a ``prerender`` one.
It covers every same-origin link the router does not own.

- A link on or inside an element with ``data-next-router="off"``.
- A link on or inside an element with ``data-next-reload`` that is no zone and no boundary wrapper.
- A link whose path starts with an excluded prefix, the paths of ``STATIC_URL`` and ``MEDIA_URL``, the admin site, and every prefix of ``ROUTER["EXCLUDE"]``.
- Under ``ROUTER["SCOPE"] = "opt-in"``, a link outside every element with ``data-next-router``.

A link on or inside an element with ``data-next-layer``, ``data-next-merge``, ``data-next-fetch``, ``data-next-retry``, ``data-next-confirm``, or ``data-next-prefetch="off"`` is left out.
The links the router owns stay with the intent prefetch.

.. warning::

   The rule prefetches the links the router does not own, and those often include a sign-out link or a download link marked ``data-next-router="off"``.
   An endpoint behind such a link must be safe to fetch with a speculative ``GET``, or the link, or an element around it, carries ``data-next-prefetch="off"``.
   Files under ``STATIC_URL`` and ``MEDIA_URL`` are covered too.

.. code-block:: jinja

   <a href="/reports/export.csv" data-next-router="off" data-next-prefetch="off">Export</a>

A browser prefetch is a document request with ``Sec-Purpose: prefetch``.
``request.next.purpose`` is ``speculation``, and the page is assembled whole, every deferred job included, without a stream.

A string or a mapping that names another eagerness, asks for ``prerender``, or holds another key emits no rules and is reported as :ref:`next.E242 <ref-system-checks>`.
A value that is neither ``None``, a string, nor a mapping is reported as ``next.E236`` and read as ``None``.

.. seealso::

   :doc:`browser` for the navigation that applies a prefetched answer.
   :doc:`sessions` for the prefetch refusals on the server.
   :doc:`/content/ref/settings` for the ``ROUTER`` keys.
