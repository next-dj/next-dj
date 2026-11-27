.. _topics-router:

Client router
=============

The client router turns a click on a same-site link into a fetch of the next page, then swaps in only the part that changed.
The layouts the old and the new page share stay on screen, with their scroll positions, open menus, typed values, and running media, and the server sends the slot of the deepest shared layout instead of a whole document.
A page that cannot keep any layout still morphs its body in place, and a navigation the router cannot serve becomes an ordinary page load.
The router ships on by default, needs no markup, and degrades to plain links when JavaScript is off.
By default it takes over only the links to pages of the file router, and a link to a plain Django view, the admin site, a file, or another application loads as a document with no request before it.

Every navigation renders the target page exactly as a document request would, through the same view, the same access rules, and the same context callables.
The router changes only what travels back and what the browser does with it.

.. contents::
   :local:
   :depth: 1

When a link is taken over
-------------------------

The router listens for clicks and ``GET`` form submits on the document and takes one over when every condition below holds.

- The click is a plain activation, the primary button with no modifier key on an ``<a href>`` whose ``target`` is empty or ``_self`` and which carries no ``download``, and no handler cancelled it first.
- The link carries no ``rel="external"``.
- The URL has the origin of the page, and it is not a fragment jump within the page shown, the same path and query with a ``#`` part.
- No ancestor of the link, the link included, carries ``data-next-router="off"``, ``data-next-layer``, ``data-next-merge``, ``data-next-fetch``, ``data-next-retry``, or a ``data-next-reload`` outside a zone or a boundary wrapper.
  The layer, pagination, boundary, and reload controls of :doc:`/content/topics/partial-rendering/index` and :doc:`/content/topics/boundaries/index` keep their own behaviour.
- The scope admits the URL, see `Which URLs the router owns`_.
- The path starts with none of the excluded prefixes, which are the paths of ``STATIC_URL`` and ``MEDIA_URL``, the admin site when ``django.contrib.admin`` is installed and mounted, and every prefix ``ROUTER["EXCLUDE"]`` adds.
- The tab has not already learned that the URL answers no navigation envelope, see `Answers that are no envelope`_.

A ``GET`` form follows the same rules when its ``target`` stays in the page and the form carries no ``data-next-*`` attribute, and its fields become the query of the action URL.
The ``formmethod``, ``formtarget``, and ``formaction`` of the submit button win over the attributes of the form, and the name and value of the button join the query.
A ``POST`` form is never a navigation, and an action goes through :doc:`/content/topics/forms/index`.
Everything the router leaves alone loads as the browser would load it.

The other ``data-next-*`` attributes a link may carry leave it to the router, and the link ends on the same page with the router on or off.
A link carrying ``data-next-confirm`` is taken over once the prompt is accepted, and a refused prompt cancels the click before the router sees it.
A ``data-next-target`` without ``data-next-merge``, and a link inside a polled or reloading zone, navigate as any other link.

Until the page is idle the router runs without its ``intent`` bundle, so a navigation before then sends its request at the click with no prefetch, see :doc:`browser`.
Scroll, focus, and snapshots never miss a navigation, because they ship in the router bundle itself.

.. _topics-router-scope:

Which URLs the router owns
--------------------------

``ROUTER["SCOPE"]`` decides which same-origin URLs the router takes over.

``"pages"``, the default
   Only a URL a page of the file router answers.
   The init payload names a route table, which the router bundle fetches once as it boots and the browser caches for good under the digest of its content.
   The table lists the routes of the URLconf in resolution order, past the script prefix, each page as exactly the paths Django sends to it, and each route that is no page and may take a path from a page.
   The first row that matches a path decides, as Django resolves it, and a path without its trailing slash is read again with one while ``APPEND_SLASH`` and ``CommonMiddleware`` redirect it.
   Until the table loads no URL is owned, so a click in that moment loads a document.
   A link inside an element carrying ``data-next-router`` is owned whatever the table says.

``"all"``
   Every same-origin URL.
   A URL that answers no envelope costs one request before its document loads, see `Answers that are no envelope`_, and :ref:`ref-system-checks` warns with ``next.W191`` while the URLconf serves routes that are no page.

``"opt_in"``
   Only a link inside an element carrying ``data-next-router``.

A route reload, a new page in development or a deploy, changes the digest, so the next document names the new table.
A tab that still holds the old table loads a new page as a document, and a removed page answers outside ``2xx`` and loads hard as well.

A page backend that is not the file router tells its routes apart through ``PageBackend.serves_page()``.
The default answers yes for a view the file router built and ``None`` for any other, and the router owns a ``None`` route the table carries exactly, so its first click sends a request and a URL that answers no envelope loads hard as under ``"all"``.
A backend that knows a route is no page answers ``False``.

The table carries a route exactly when it can, and otherwise errs toward a document, never toward a plain view.

- The built-in converters ``str``, ``int``, ``slug``, ``uuid`` and ``path`` read as their own patterns, and so do the language prefixes of ``i18n_patterns``.
- A converter of its own and a ``re_path()`` regex are carried when they use only syntax Python and JavaScript read alike, namely literals, ``.``, classes of ASCII characters and ranges, groups, alternation and quantifiers.
  ``\d``, ``\w``, ``\s``, and ``\b`` are not carried, and neither are lookarounds, backreferences, inline flags, and characters outside ASCII.
  Python reads the classes over Unicode and JavaScript over ASCII.
- A ``re_path()`` regex has to open with ``^``, unless it ends an endpoint with ``$``, because Django otherwise finds it anywhere in the path.
- The prefix of an include is carried when its match can end in one place only, a literal, a language prefix, or a ``path()`` route whose ``str``, ``int``, ``slug`` or ``uuid`` converters each stand before a literal character their pattern does not match, as in ``<int:id>/``.
  Django does not go back into a prefix whose include holds no match, while a regex would.
- A route with more than two repeats that cross a slash, such as three ``path`` converters, or with more than two repeats within one segment, is not carried, so no table row backtracks for long on a long link.

A route the table cannot carry stands as a route that is no page, for every path under its literal start.
Its page loads as a document, and so does a page after it under the same start, and :ref:`ref-system-checks` names each such page with ``next.W192`` and the reason.
A built-in converter, a regex with ASCII classes such as ``[0-9]`` in place of ``\d``, or ``data-next-router`` on the links brings it back.

A converter of its own whose ``to_python()`` raises ``ValueError`` for a value its pattern matched passes the path on to the next route, which the table cannot see, so the router still takes that link.
A path longer than 512 characters, script prefix included, and a path holding a control character or a line separator are never owned, and a URLconf that does not include ``next.urls`` has no table, so the router owns no URL there.
The table is fetched by every visitor, so it shows the shape of each page route and of each route that sits in front of a page.

.. _topics-router-foreign:

Answers that are no envelope
----------------------------

A URL the router takes over may answer with something other than a navigation envelope, such as a file download or a view outside the page tree, which happens under ``"all"``, inside a ``data-next-router`` element, or for a route a backend could not tell apart.
``ROUTER["FOREIGN"]`` decides what the browser does with such an answer.

``"hard"``, the default
   The URL loads as a new document on that click.
   The tab remembers the final path and query in ``sessionStorage``, the fifty most recent, so a later link to it is left to the browser at once instead of being fetched twice.
   The list is emptied whenever the router drops its snapshots, see :doc:`browser`.

``"extract"``
   A ``text/html`` answer is read in the browser instead.
   The router reads the layouts block of the document, keeps the deepest layout whose id and digest match the chain on screen from the root and morphs its slot, or morphs the body when none matches, then updates the address and the title and loads the stylesheets and the ``src`` scripts the document lists.
   Later navigations to the same path fetch it as a plain document for the rest of the life of the page.
   Every such stylesheet and script must carry the nonce of the init payload of the document, or none when the document has no nonced payload, and a page shown under a CSP nonce reads only a document whose payload carries one too, so any other answer loads hard.
   An answer of any other type loads hard and is remembered as under ``"hard"``.

An answer outside ``2xx``, or one whose final URL after redirects lies on another origin, loads hard under both modes and is not remembered.

.. _topics-router-intercept:

Links to intercepted pages
--------------------------

An intercepting route opens a page as a layer over another one, see :ref:`topics-file-router-intercepts`.
``ROUTER["INTERCEPT"]`` decides whether a plain link to such a page opens the layer.

``"opt_in"``, the default
   Only a ``data-next-layer`` link opens the layer, and a plain link to the URL navigates to the target page.

``"links"``
   A navigation to a page an intercept targets, started from another page, is answered before any render with a ``layer.open`` operation instead of the page.
   The operation names the URL and the first zone the first intercept of the page serves, in file order, so the browser opens the layer over the page shown and loads that zone of the target into it, as a ``data-next-layer`` link would.
   The zone request that follows runs the access rules of the target and of the intercept.
   A navigation from the target URL itself, or one that names no origin in ``X-Next-Origin``, renders the page, and a prefetch of an intercepted page is answered ``204``.

Opting out
----------

A link or a region opts out with ``data-next-router="off"`` on itself or on any ancestor.

.. code-block:: jinja
   :caption: layout.djx

   <footer data-next-router="off">
     <a href="/legal/">Legal</a>
   </footer>

``ROUTER["SCOPE"] = "opt_in"`` takes over only the links inside an element with ``data-next-router``, a fit for a project that adopts the router one section at a time.
A prefix in ``ROUTER["EXCLUDE"]`` keeps a whole URL space out, such as a section of pages that should load as documents.

A page opts out on the server with ``page.router(...)`` at the top of its ``page.py``.

.. code-block:: python
   :caption: pages/checkout/page.py

   from next import page

   page.router(navigate=False)

``navigate=False``
   Every navigation to the page is answered with a hard load of its URL, before any render, and a prefetch of it is refused as ``prefetch=False`` refuses one.
   A page whose markup or scripts assume a fresh document uses it.

``prefetch=False``
   A navigation request marked as a prefetch, ``X-Next-Prefetch: 1`` or ``X-Next-Prefetch: full``, is answered ``204`` with an empty body before any render, and the browser asks no more for the life of the page, see :doc:`prefetch`.
   A page whose context callables act on a visit, such as counting a view or consuming a one-time link, uses it.

``snapshot=False``
   The page is kept out of the history snapshots described in :doc:`browser`, so Back to it always shows a fresh render.
   A page that shows data which must never reappear from memory uses it.

Each flag defaults to ``True``, and a page with no call keeps all three.
The last call of a module wins, an edited ``page.py`` that drops the call stops applying it once the module loads again, a value that is no bool or a call inside a function raises when the module runs, and a call outside a ``page.py`` declares nothing and is reported as ``next.E238``.

Testing
-------

``NextClient.navigate`` performs a navigation the way the browser does, declaring the layouts the origin page shows, and returns the envelope with the cut the page view chose.

.. code-block:: python
   :caption: tests/test_navigation.py

   from next.router import CutKind
   from next.testing import NextClient

   def test_the_docs_keep_their_layout() -> None:
       navigation = NextClient().navigate("/docs/install/", origin="/docs/")
       assert navigation.kind is CutKind.SLOT
       assert len(navigation.kept) == 2

``kept`` names the layouts that stay on screen, ``diverged`` the first one that did not match, and ``envelope`` the patches the client applies, see :doc:`/content/ref/testing`.

The pages of this guide
-----------------------

:doc:`layouts`
   Layout identity, the slot markers, the verified cut and its three outcomes, what makes a layout diverge, the kept zones of the chrome, streamed navigations, and the two backends behind the cut.

:doc:`browser`
   The router bundles and the history, with the scroll, the snapshots, and when they are dropped.
   Persisted elements, view transitions, page stylesheets, and scripts in navigated HTML.
   Focus and announcements, the progress and pending marks, the events, and the ``Next.router`` API.

:doc:`prefetch`
   The intent prefetch and the press of a link, their modes and limits, what a prefetch renders, the server's refusals, and the Speculation Rules for the links the router does not own.

:doc:`sessions`
   How a login, a logout, and a changed session key clear the router, redirects that ride a full load, ``router_clear``, prefetch refusals, and the caching and security guarantees of a navigation.

:doc:`active-links`
   The marks the framework writes on navigation links to the current page and its ancestors, with the router on or off.

Turning the router off
----------------------

``ROUTER["ENABLED"] = False`` turns the router off for the whole project.

.. code-block:: python
   :caption: settings.py

   NEXT_FRAMEWORK = {
       "ROUTER": {"ENABLED": False},
   }

With the router off, composition writes no slot marker, no document carries the ``$router`` payload entry or the layouts block, the router bundle, or Speculation Rules, every link loads as a plain page, and a request that still carries the navigation headers is answered as a plain page.
The router-only system checks, ``next.E239``, ``next.W176``, ``next.W177``, and ``next.W178``, stay silent.
Active links keep working, since the server writes them into the HTML, see :doc:`active-links`.
A settings reload that flips the switch recomposes the page templates, so the slot markers appear or disappear without a restart.

.. toctree::
   :hidden:
   :maxdepth: 1

   layouts
   browser
   prefetch
   sessions
   active-links

.. seealso::

   :doc:`/content/ref/router` for the cut, the digest, the active-link backend, and ``router_clear``.
   :doc:`/content/ref/settings` for the ``ROUTER`` scope.
   :doc:`/content/ref/client` for the router events, attributes, API, and bundles.
   :doc:`/content/topics/layouts` for how layouts compose.
