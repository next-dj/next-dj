.. _topics-router-browser:

In the browser
==============

This page follows one navigation through the browser, from the click to the moment it settles, and lists what a page can hook into on the way.

.. contents::
   :local:
   :depth: 1

The three bundles
-----------------

The router ships as three lazy bundles, so a page pays for the first click alone until it is idle.

``router``
   The link and form takeover, the navigation request, the commit, the progress marks, the events, and ``Next.router``.
   It loads as an ``async`` script right after the runtime on every routed document, and ``Next.router`` exists once it has evaluated.

``history``
   The snapshots, the scroll restore, the client pass of :doc:`active-links`, the default focus move and announcement, persisted elements, view transitions, the retirement of page stylesheets, the inline scripts a navigation runs, and the reading of a ``text/html`` answer under ``ROUTER["FOREIGN"] = "extract"``.

``intent``
   The intent and viewport prefetch and the cache of prefetched answers, see :doc:`prefetch`.

The ``$router`` payload entry names the ``history`` and ``intent`` bundles and the stream ``reader`` by URL, and the router loads the three as scripts carrying its own nonce once the page is idle, or after two seconds at the latest where the browser has ``requestIdleCallback``.
A browser without ``requestIdleCallback`` loads them right after the router starts.
It loads them at once when the document opens on a history entry the router wrote before, such as after a reload of the tab, and every navigation and every ``Next.router.prefetch()`` waits for them, fifteen seconds at most, before it applies anything.

One navigation
--------------

The router runs one navigation at a time.
A second click aborts the first, which ends with the status ``superseded``.

#. ``router:before`` fires.
   It is cancelable, and cancelling it leaves the page as it is.
   A Back or Forward step skips it, since the browser has already moved.
#. Every open layer closes as a dismissal with the reason ``navigate``, and a navigation that starts with a layer open replaces the history entry instead of pushing one.
#. ``router:start`` fires, and the link or form that started the navigation gains ``data-next-pending``.
#. Once ``ROUTER["PROGRESS_DELAY"]`` seconds pass without an answer, ``<html>`` gains ``data-next-navigating`` and the element around the slot that will change gains ``aria-busy="true"``, so a quick navigation shows no progress at all.
#. The answer arrives, and ``router:before-swap`` fires before anything in the page changes.
#. The envelope applies, the address and the title update, the marks of steps 3 and 4 are removed, and ``router:commit`` fires.
#. ``router:end`` fires with the status of the navigation.

A navigation that finds a prefetched answer marked ``complete`` applies it at once, commits on it, and sends no request.
A prefetched answer that is not complete, the shell of a page with deferred boundaries, is applied and committed at once while the request for the page leaves at the click, and the first frame of the fresh render morphs over the shell and refills its boundaries, so a prefetched page never stays on screen stale.
The morph of that frame swaps nothing again, it runs no second transition, snapshot, scroll, or focus move.
A navigation to a URL whose prefetch is still in flight waits for that prefetch first and then goes on the same way, with no request beside it until it lands, see :doc:`prefetch`.

An answer the router cannot apply, a status outside ``2xx``, a final URL on another origin after redirects, or a response that is no navigation envelope ends in a hard load of the URL with the status ``hard``, and a ``visit`` operation in a hard load of the URL it names, unless ``ROUTER["FOREIGN"] = "extract"`` reads a ``text/html`` answer in the browser, see :ref:`topics-router-foreign`.
A request that throws, or an envelope that fails to apply, fires ``router:error`` first.
The page never stays half-swapped, because a failure falls back to the browser.

A redirect the page answers with, such as the login redirect of a protected page, is followed by the fetch itself, and the router commits the final URL.
A redirect past a login, a logout, a changed session key, or a rotated CSRF token comes back as a ``visit`` operation instead, so the change reaches a fresh document, see :doc:`sessions`.

Progress styling
----------------

Every routed document carries a default progress bar, a thin line across the top of the window drawn from ``html[data-next-navigating]::before``.
It sits in a ``<style>`` in the head with the CSP nonce of the response, so a policy whose ``style-src`` allows that nonce shows it, and a policy without a style nonce turns it off with ``ROUTER["PROGRESS"] = False``.
Under ``prefers-reduced-motion: reduce`` the bar stands still across the full width.
Three custom properties restyle it.

.. code-block:: css

   :root {
     --next-progress-color: #e11d48;
     --next-progress-height: 2px;
     --next-progress-duration: 12s;
   }

The color defaults to the ``LinkText`` system color, the height to ``3px``, and the duration of the growth from a tenth of the width to most of it to ``8s``.

The marks are plain attributes, so a progress UI of the project's own is CSS too, with ``ROUTER["PROGRESS"] = False``.

.. code-block:: css

   html[data-next-navigating] .topbar::after {
     content: "";
     position: fixed;
     inset: 0 auto auto 0;
     height: 2px;
     width: 100%;
     animation: progress 2s ease-out;
   }

   a[data-next-pending] {
     opacity: 0.6;
   }

``aria-busy="true"`` tells assistive technology that the region is about to change.

History and scroll
------------------

Every history entry the router writes carries a state of its own, and the router takes over Back and Forward between such entries.
A step that changes only the fragment scrolls to it and fetches nothing.

The router sets ``history.scrollRestoration`` to ``manual`` and places the scroll itself.

- A forward navigation scrolls to the element the ``#fragment`` of the URL names, or to the top.
- A link inside an element carrying ``data-next-scroll="preserve"`` keeps the current scroll position, a fit for tabs and filters.
- Back and Forward restore the scroll of the window and of every element marked ``data-next-scroll-region``, in document order.
  The positions are saved per history entry in ``sessionStorage``, for as many recent entries as ``ROUTER["SNAPSHOTS"]`` allows, when the page is left and when the tab is hidden, so they survive a reload of the tab.
  A page that is still too short to reach the saved position is scrolled again once its fresh render lands.
- A Back or Forward step taken before the ``history`` bundle has loaded waits for it like any navigation, and with no snapshot to show it waits for the fetch.

.. code-block:: jinja
   :caption: layout.djx

   <aside data-next-scroll-region class="sidebar">{% include "menu.html" %}</aside>
   <nav data-next-scroll="preserve">
     <a href="?tab=overview">Overview</a>
     <a href="?tab=activity">Activity</a>
   </nav>

Snapshots
---------

Before a navigation replaces a slot or the body, the router keeps a detached copy of the nodes it replaces, the title, the layout chain, the page stylesheets in use, and the page keys of ``Next.context`` as the snapshot of the history entry being left.
Back or Forward to an entry with a snapshot shows it at once and restores the title, the stylesheets, the context, and the scroll.
A snapshot younger than ``ROUTER["PREFETCH_TTL"]`` seconds is the page, and nothing is fetched.
An older one is fetched again, and the fresh render morphs over the snapshot.
An entry without a snapshot waits for the fetch.

Snapshots live in the memory of the tab and are never written to storage.
``ROUTER["SNAPSHOTS"]`` bounds how many are kept, ``10`` by default, and the oldest goes first.

Every snapshot and every prefetched answer is dropped at once in five cases.

- A navigation answer, a prefetch answer, or a partial answer carries ``X-Next-Router: clear``, after a login, a logout, a changed session key, or a ``router_clear`` call, see :doc:`sessions`.
  The page a clearing navigation leaves is not kept either.
- A partial request other than a ``GET`` leaves, such as a form action, since a write can make every snapshot stale.
- A navigation answer carries a fresh CSRF token.
- The browser restores the page from its back-forward cache.
- A script calls ``Next.router.flush()``.

Every case but the back-forward restore also empties the list of URLs the tab remembers as answering no envelope, see :ref:`topics-router-foreign`.

A page that declares ``page.router(snapshot=False)`` carries the refusal in its document and in its navigation answers, and the router keeps no snapshot of it once it is left.

Persisted elements
------------------

An element marked ``data-next-persist`` survives a navigation that replaces the part of the page it sits in.
Before the swap the router lifts every such element out of the slot or the body it replaces, and after the swap it puts each one in place of the element of the new content that carries the same ``data-next-persist`` value.
A playing video, an embedded map, or a widget with state of its own stays as it was.

.. code-block:: jinja
   :caption: pages/watch/[int:id]/template.djx

   <video data-next-persist="player" src="{{ video.url }}" controls></video>

The first element of each value counts, and a marked element inside another marked one moves with its ancestor.
An element whose value the new content does not hold is removed, and its unmount hooks run.
A browser that supports ``moveBefore`` moves the element without resetting it, and another one inserts it again, which reloads an ``<iframe>``.
A snapshot shown on Back or Forward keeps persisted elements the same way.
An element in a layout the navigation keeps needs no mark, since nothing replaces it.

View transitions
----------------

``ROUTER["TRANSITIONS"] = True`` runs each swap of a slot or the body inside ``document.startViewTransition``, when the browser supports it and the visitor has not asked for reduced motion with ``prefers-reduced-motion: reduce``.
Before the transition starts, ``<html>`` gains ``data-next-transition`` with the direction, ``forward``, ``back`` for a Back or Forward step, or ``replace``, and a browser that supports transition types receives the same word as the type.
The swap waits until the browser has captured the old state.
A snapshot shown on Back or Forward appears without a transition, and the fresh render that follows an older one runs in one.

.. code-block:: css

   ::view-transition-old(root),
   ::view-transition-new(root) {
     animation-duration: 0.2s;
   }

   html[data-next-transition="back"]::view-transition-new(root) {
     animation-direction: reverse;
   }

Page stylesheets
----------------

A routed document writes ``data-next-asset="page"`` on every stylesheet the static injector renders, the co-located CSS of the page, its layouts, and its components.
After each swap the router disables every such stylesheet the asset list of the new page does not name, and enables again one it names, so the CSS of a page left behind stops applying while the CSS the next page shares stays loaded.
A ``<link>`` written by hand in a template carries no mark and is never disabled.

Focus and announcements
-----------------------

After a forward navigation the router moves focus into the new content, so a keyboard or screen reader user starts where the page changed.
It picks the first element marked ``data-next-focus``, else the first ``<h1>``, else the first element the navigation inserted.
An element that cannot take focus receives ``tabindex="-1"`` until it loses focus again, and focusing never scrolls.

When focus does not move, on Back and Forward, under ``data-next-scroll="preserve"``, or when no target is found, the router announces the new document title through a polite live region it appends to the body.

``Next.router.configure()`` replaces either behaviour, see `The Next.router API`_.

Scripts after a navigation
--------------------------

A navigation applies its markup as every patch does, with each ``<script>`` element removed before insertion.
The one exception is an inline script in the swapped slot or body whose ``nonce`` equals the CSP nonce of the response, with no ``src`` and a JavaScript or module type.
The router lifts such a script out before the swap and runs it after the swap as a fresh script carrying the nonce of the page.
Every other inline script, every inline script in a zone of the kept chrome, and every inline script of a response without a CSP nonce never runs.
:ref:`next.W177 <ref-system-checks>` reports an inline script without a nonce in a page or layout template while the router is on.

The scripts of the page arrive through the asset operations of the envelope instead, and code that should run on every page lives in a co-located script, whose mount hooks run for the inserted content, see :doc:`/content/topics/partial-rendering/co-located-js`.
A script a ``{% use_script %}`` or ``{% use_module %}`` declares runs after the swap, once the new markup is in place, and the runtime inserts it with ``async`` off, so the scripts one navigation brings run in the order the server lists them.
Each source runs once per page, so a script the document already ran, on its first load or on an earlier navigation, does not run again, and work it must repeat for each page belongs in a mount hook.
The third-party scripts a ``scripts.py`` declares travel in the ``scripts`` field of a navigation envelope rather than as head tags, and after the swap the scripts chunk loads each one the page has not loaded yet, under the consent the envelope carries, see :doc:`/content/topics/scripts/index`.
A page that depends on an inline script without a nonce opts out with ``page.router(navigate=False)``, or its links with ``data-next-router="off"``.

Scripts that render once on load
--------------------------------

A widget loader such as a captcha or an embed scans the document once when it loads, so the widgets a navigation, a zone morph, or a layer brings later stay empty.
The co-located script of the component renders each widget explicitly through ``Next.partial.onMount``, which runs over the document on ``ready`` and over everything each swap inserts, a slot or body swap, a zone of the kept chrome, and the content of a layer alike.
The callback runs once for each element a swap brings and again for an element the morph reconciled in place, which has lost the markup the vendor rendered into it unless it carries ``data-next-keep``.
``next:removed`` fires on the root of every subtree that leaves the document, so a widget inside it is released there.

.. code-block:: javascript
   :caption: components/captcha/component.js

   const widgets = new WeakMap();

   Next.partial.onMount("[data-captcha]", (el) => {
     const id = widgets.get(el);
     if (id !== undefined && el.hasChildNodes()) return vendor.reset(id);
     if (id !== undefined) vendor.remove(id);
     widgets.set(el, vendor.render(el, { sitekey: el.dataset.sitekey }));
   });

   document.addEventListener("next:removed", (event) => {
     for (const el of [event.target, ...event.target.querySelectorAll("[data-captcha]")]) {
       if (!widgets.has(el)) continue;
       vendor.remove(widgets.get(el));
       widgets.delete(el);
     }
   });

The vendor script loads with the ``explicit`` render mode its documentation describes, so it renders nothing on its own, and a callback that may run before it has loaded waits for it with ``Next.scripts.load(name)``, see :doc:`/content/topics/scripts/declaring`.
``Next.scripts.load`` takes the name of a script a ``scripts.py`` declares and rejects any other name, so the vendor script belongs in ``scripts.py``, since a ``{% use_script %}`` asset has no name to wait on.
``next:mounted`` fires only on the roots a swap touches, such as the top elements of a slot, so a widget deep inside a slot is reached through ``onMount``, see :doc:`/content/topics/partial-rendering/co-located-js`.

Events
------

Every router event fires on the document as a ``CustomEvent`` and on the ``Next.on`` bus with the same payload.
Only the document event of ``router:before`` can cancel a navigation.

.. list-table::
   :header-rows: 1
   :widths: 24 34 42

   * - Event
     - Detail
     - Fires
   * - ``router:before``
     - ``{url, trigger, cached}``
     - Before a navigation starts, cancelable.
       ``trigger`` is the link or form, ``undefined`` for ``Next.router.visit()``, and ``cached`` tells whether a prefetched answer is ready or in flight.
   * - ``router:start``
     - ``{url}``
     - Once the navigation request is about to leave.
   * - ``router:before-swap``
     - ``{url, layouts, hold, nodes}``
     - Before the envelope applies.
       ``layouts`` is the new chain, ``nodes`` the nodes on screen the swap replaces, and ``hold()`` returns a release function the swap waits for, so an exit animation of ``nodes`` finishes first.
   * - ``router:commit``
     - ``{url}``
     - After the new content, the address, and the title are in place.
   * - ``router:end``
     - ``{url, status}``
     - Once the navigation settled, with ``status`` ``complete``, ``hard``, or ``superseded``.
       It also fires once when the router boots, with ``initial: true``.
   * - ``router:error``
     - ``{url, error}``
     - When the request throws or the envelope fails to apply, before the hard load.

The commit also fires ``next:navigated`` with ``action: "push"`` or ``"replace"``, and a Back or Forward step fires it with ``action: "pop"``, so code written against ``next:navigated`` sees router navigations as well, see :doc:`/content/ref/client-extras`.
A Back or Forward step that a fresh snapshot answers sends no request, so it fires ``next:navigated`` alone, without ``router:start``, ``router:commit``, or ``router:end``.

.. code-block:: javascript

   document.addEventListener("router:before", (event) => {
     if (document.querySelector("form[data-dirty]") && !confirm("Leave this page?")) {
       event.preventDefault();
     }
   });

   document.addEventListener("router:commit", () => {
     window.analytics?.page(location.pathname);
   });

The Next.router API
-------------------

``Next.router.visit(url, options)``
   Navigates to ``url`` as a click would and returns a promise that settles with the navigation.
   ``options.replace`` replaces the current history entry instead of pushing one, and ``options.scroll`` set to ``false`` keeps the scroll position.
   A URL the router does not take over loads hard.

``Next.router.prefetch(url)``
   Prefetches ``url`` and returns a promise that settles once the request ends, see :doc:`prefetch`.

``Next.router.flush()``
   Drops every snapshot and every prefetched answer.

``Next.router.configure(options)``
   ``options.focus(nodes)`` replaces the focus move and returns whether it moved focus, ``options.announce(text)`` replaces the announcement, and ``options.host`` replaces the browser adapter, the seam a test harness drives.

.. code-block:: javascript

   Next.router?.configure({
     announce: (text) => myLiveRegion.say(text),
   });

.. seealso::

   :doc:`/content/ref/client` for the router events, attributes, and bundles in the runtime reference.
   :doc:`prefetch` for the prefetch the ``intent`` bundle carries.
   :doc:`/content/topics/partial-rendering/layers` for the layers a navigation closes.
