.. _topics-router-layouts:

Shared layouts
==============

A navigation keeps on screen every layout the old and the new page share, from the root layout down, and replaces only what lies inside the slot of the deepest one.
The server decides how deep the shared chain reaches by comparing what the browser declares it shows with what the new page rendered, so a layout is kept only when its rendered chrome is the same.

.. contents::
   :local:
   :depth: 1

Layout identity
---------------

Each ``layout.djx`` that carries a ``{% template %}`` placeholder has an id, 16 hex characters digested from its path below its page root and its source.
Two pages that compose through the same file share its id, and an edit or a deploy that changes the source changes the id, so a tab opened before the change never keeps a layout that no longer exists.

With the router on, composition wraps the placeholder of each layout in two HTML comments that name the layout id and a seal.
The seal is minted per response, so a look-alike comment in page content never matches a marker.
A placeholder inside a ``{% verbatim %}`` block, inside an HTML attribute, or inside a ``<title>`` cannot carry the comments, and ``next.E239`` reports it.

Every routed document carries the chain it rendered in its ``data-next-layouts`` JSON block, one ``[id, digest, seal]`` per layout, outer first.
A navigation sends the chain the browser shows in the ``X-Next-Layouts`` header, one ``id.digest`` per layout, outer first, comma separated.

The chrome digest
-----------------

The chrome of a layout is the HTML it renders around its slot, the slot itself left out.
The digest of the chrome is taken after the parts that change on every response or travel on their own are left out of it.

- Every ``{% zone %}`` in the chrome, which a kept layout receives fresh as its own morph.
  A zone that wraps the slot itself is no such region, and the chrome it wraps counts as chrome.
- The head tags ``{% metadata %}`` renders, which the navigation sends as a ``meta`` operation.
- The placeholders the static injector fills, whose assets the navigation sends as asset operations.
- Every ``nonce`` attribute.
- The ``value`` of a tag with ``name="csrfmiddlewaretoken"``, when it follows the ``name`` inside the same tag, as ``{% csrf_token %}`` writes it.

Nothing else is masked, so any other value that changes on every render makes the layout diverge, see `What makes a layout diverge`_.

The digest of the root layout is two digests in a row, one for the document up to the end of the ``<body>`` start tag and one for the rest of its chrome.
The marks of :doc:`active-links` are written after the digests are taken, so a link that is current on one page and not on the next never breaks a layout.

The verified cut
----------------

``VerifiedCut``, the default ``ROUTER["CUT"]``, walks the declared chain and the rendered one together from the root and stops at the first layout whose id or digest differs.
The walk ends in one of three outcomes.

Slot morph
   At least one layout matched.
   The response carries the inner HTML of the slot of the deepest matched layout, a fresh copy of every zone in the kept chrome, and a ``null`` seal for each kept layout in its new chain.
   Everything outside that slot stays as it is in the browser.

Body morph
   No layout matched, but the root layout keeps its id and the part of the document up to the ``<body>`` start tag is the same.
   The response carries the children of ``<body>``, which morph in place, so the document head, its scripts, and its stylesheets stay.
   A page without layouts reached from another such page, and a declaration the server cannot read, morph the body too.

Hard load
   The root layout differs in its id, its head part differs, one side has layouts and the other has none, or the page renders no ``<body>``.
   The response carries a ``visit`` operation, and the browser loads the URL as a new document.

A declaration past 32 entries or past 1 KB, or one that does not parse, counts as no declaration and lands on a body morph.

Under ``DEBUG`` the response names its outcome in ``X-Next-Navigate``, such as ``kept 3f…,9a… slot 9a…``, ``body_morph, diverged 3f… digest``, or ``visit head``.
The ``navigation_rendered`` signal reports the same facts to a receiver in every environment, see :doc:`/content/topics/signals`.

What makes a layout diverge
---------------------------

A layout that renders differently from one page to the next is never kept, and the navigation cuts at its parent.
The common causes follow.

A value that changes per page.
   A layout that prints ``request.path``, the name of the page, or a highlighted section computed in the template differs on every page.
   Move the value into a ``{% zone %}``, which a kept layout receives fresh, or into the page.
   ``next.W176`` reports a root layout that reads the request path outside a zone.
   Navigation links need no such care, since :doc:`active-links` marks them after the digest.

A key that page context sets.
   A layout that reads, in its chrome, a key the ``@context`` callables of its pages provide, such as an active tab, a per-page id on a header button, or a page title, renders it differently on every page that sets it differently.
   ``next.W183`` reports such a layout at startup while the router is on.
   The scan leaves out reads inside a ``{% zone %}`` and inside the ``{% template %}`` slot, keys a context processor provides, and keys an ``inherit_context`` callable of the layout's own directory or above provides, since that callable feeds every page below the layout alike.
   Wrap the part that reads the key in a ``{% zone %}``, which a kept layout morphs on its own, or move it into the page slot.

Under ``DEBUG`` the first navigation that diverges at a layout by its digest logs one ``INFO`` record on the ``next.router`` logger, once per layout and process.
The record names the layout file and repeats the ``X-Next-Navigate`` text, so the runserver console shows which layout the router could not keep.

A deferred boundary in the chrome.
   The ids and tokens of a deferred boundary carry the seal of their response, so a layout whose chrome holds one differs on every response and is never kept.
   Keep deferred components inside the page, or inside a layout deeper than the part that should persist.

A session that changed.
   A response that clears the router, after a login, a logout, or a changed session key, turns a slot morph into a body morph, because the kept chrome may still show what the old session held, see :doc:`sessions`.

A different ``<body>`` tag or document head.
   The ``<body>`` start tag belongs to the head part of the root layout, so a page that writes ``<body class="{{ theme }}">`` with another value loads hard.
   So does a document head that differs outside the normalised parts, such as a ``<title>`` written by hand instead of through ``{% metadata %}``.
   Set such classes on an element inside the body, and leave the head tags to ``{% metadata %}``.

A value that changes on every render.
   The digest leaves out only the parts `The chrome digest`_ lists, so any other per-render value in the chrome differs on every response, even between two renders of the same page.
   A CSRF token in a ``<meta>`` tag or a data attribute, such as ``<meta name="csrf-token" content="{{ csrf_token }}">``, is the common case, since Django masks the token afresh on each render.
   A request id, a timestamp, a random id, and any attribute written anew per response behave the same way.
   In the head part of the root layout such a value turns every navigation into a hard load, and in other chrome it keeps the layout from ever being kept.
   The pages still load correctly, but nothing persists across navigations.
   Move such a value out of the chrome, into a ``{% zone %}`` or the page, write the token through ``{% csrf_token %}`` inside a form, or strip the value in a ``ChromeDigest`` subclass, see `Replacing the cut and the digest`_.

A new deploy.
   An edited layout has a new id and diverges.
   A navigation that asserts an asset version other than the one the server holds is answered ``409`` before any render, and the browser follows it with a hard load.

Kept chrome zones
-----------------

A zone in the chrome of a kept layout is re-rendered with the page and sent as its own morph, so a cart count, a user menu, or a notification badge stays current while the rest of the chrome is untouched.
A zone that did not change morphs to the same DOM and costs nothing visible.
:doc:`/content/topics/partial-rendering/zones` covers the zone itself.

A zone may not sit inside an ``{% if %}`` (``next.E063``), because a zone render does not evaluate the enclosing condition and would show the zone to users the condition hides it from.
Chrome shown only to signed-in users keeps the zone outside and moves the condition inside the zone body, as in ``{% zone "account" tag="header" %}{% if user.is_authenticated %}...{% endif %}{% endzone %}``.
The wrapper then renders empty for anonymous users, and its ``class`` and other HTML attributes let it be the header element itself.

What a navigation response carries
----------------------------------

A slot or body morph is one patch envelope.
Beside the morph it carries the URL to push or replace, the head tags of the page as a ``meta`` operation, the page-owned ``Next.context`` keys as a replacing ``context`` operation, the assets of the whole page, which the browser deduplicates against what it already loaded, and a fresh CSRF token when the request rotated it.
Its top-level fields name the new chain, the root layout id, the CSP nonce, the ``$scripts`` and ``$consent`` state, and whether a shared cache may store the document of the page.

The URL operation replaces the history entry when the navigation targets the URL it started from, and pushes one otherwise.
A path that opens with ``//``, which a browser would read as another host, is answered with a hard load of the single-slash path.

Streamed navigations
--------------------

A page with ``stream`` boundaries streams a navigation the way it streams a document, when the router asks for ``text/event-stream`` and the delivery policy of :doc:`/content/topics/boundaries/streaming` lets the response stream.
The router asks for it when the ``boundaries`` bundle is on the page or named in the ``$router`` payload entry, and loads the reader before it reads a streamed answer.
The envelope is the first frame, the browser commits the navigation on it, and each boundary arrives after it as its job settles.
A shell prefetch and a hard load never stream, a full prefetch streams as a navigation does, and a navigation that does not stream renders its boundaries before the envelope leaves.

A layout that should persist across pages keeps its deferred components out of its chrome, as `What makes a layout diverge`_ explains.

Replacing the cut and the digest
--------------------------------

``ROUTER["CUT"]`` names the ``NavigationCut`` subclass that decides the outcome, and ``ROUTER["DIGEST"]`` the ``ChromeDigest`` that normalises and digests the chrome.
A digest subclass strips a per-response token of its own on top of the default normalisation.

.. code-block:: python
   :caption: myproject/router.py

   import re

   from next.router import ChromeDigest

   _BUILD = re.compile(r' data-build="[^"]*"')


   class BuildStampDigest(ChromeDigest):
       def normalise(self, text: str, seal: str) -> str:
           return _BUILD.sub("", super().normalise(text, seal))

.. code-block:: python
   :caption: settings.py

   NEXT_FRAMEWORK = {
       "ROUTER": {"DIGEST": "myproject.router.BuildStampDigest"},
   }

A path that does not import, or names no concrete subclass, is reported as ``next.E219``, and every navigation raises :exc:`~django.core.exceptions.ImproperlyConfigured` until it is fixed.
Each backend is built once per process and again after a settings reload.

.. seealso::

   :doc:`/content/topics/layouts` for the composition of the layout chain.
   :doc:`/content/ref/router` for ``Cut``, ``CutKind``, ``Divergence``, and the backends.
