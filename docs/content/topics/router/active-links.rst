.. _topics-router-active-links:

Active links
============

The framework marks the navigation links that point at the current page and at its ancestors, so a menu highlights the right entry with CSS alone and no template computes an active state.
The server writes the marks into every page it renders, with the client router on or off, and the router keeps them current after each navigation.

.. contents::
   :local:
   :depth: 1

The marks
---------

A link to the current page gains ``aria-current="page" data-next-current="page"``.
A link to an ancestor of the current page gains ``data-next-current="ancestor"``.
Every other link inside a navigation container loses both attributes, so the pass owns them there and a stale hand-written value never survives.

.. code-block:: html

   <!-- on /docs/topics/router/ -->
   <nav>
     <a href="/">Home</a>
     <a href="/docs/" data-next-current="ancestor">Docs</a>
     <a href="/docs/topics/router/" aria-current="page" data-next-current="page">Router</a>
     <a href="/blog/">Blog</a>
   </nav>

``aria-current="page"`` is what a screen reader announces, and ``data-next-current`` is the styling hook for both states.

Which links are marked
----------------------

Only an ``<a href>`` inside a navigation container is marked.
``ROUTER["CURRENT"]["CONTAINERS"]`` lists the containers, ``("nav", "[role=navigation]", "[data-next-nav]")`` by default, and a link outside every container is left exactly as written.
The link is compared within its innermost container.

Each entry of ``CONTAINERS`` is one simple selector in one of three shapes.

``tag``
   A tag name, such as ``nav`` or ``header``, matched without regard to case.

``[attr]``
   An element that carries the attribute, such as ``[data-next-nav]``.

``[attr=value]``
   An element whose attribute holds exactly the value, written bare as an identifier or in single or double quotes, such as ``[role=navigation]`` or ``[aria-label="Primary"]``.

A descendant, a class, an id, or a list in one entry is no simple selector, and ``next.E076`` reports it while the whole list falls back to its default.
Markup inside ``<script>``, ``<style>``, ``<template>``, ``<textarea>``, ``<title>``, and the other raw text elements is never scanned.

The rule
--------

The link target is resolved against the current URL and normalised before it is compared.
A relative link resolves against the directory of the current path, an absolute or protocol-relative link counts only when its scheme and host are those of the request, and a link that is only a ``#fragment`` is never marked.
Normalisation resolves ``.`` and ``..`` segments, normalises percent-encoding, and cuts one trailing slash, so ``/docs``, ``/docs/``, and ``/docs/./`` are the same path.
The query and the fragment take no part by default.

The current URL depends on the response.

- A document or a navigation is marked for the path and query of its request.
- A zone response is marked for the page the browser shows beneath it, which the ``X-Next-Origin`` header names, and for the URL of the zone request when the header is absent.
- The zones an action response re-renders are marked for the page the form lives on, which ``X-Next-Origin`` names, or else the origin the form posted.
  An action whose request names neither leaves its zones unmarked on the server.
- A server push, which no request path stands behind, is not marked on the server.
  While the router is on, the client pass of `After a navigation`_ marks it once it applies.

An ``X-Next-Origin`` that is no same-site path is ignored, so a forged header cannot pick the page the marks follow.

Exact
   A link whose path equals the current path is the page.

Ancestor
   A link whose path is a segment prefix of the current path is an ancestor, so ``/docs`` is an ancestor of ``/docs/topics`` while ``/doc`` is not.

Home paths
   A home path is never an ancestor, only the page when it is the current path, so a Home link does not light up on every page.
   The home paths are ``/``, the script prefix from :func:`django.urls.get_script_prefix` when the site is served under a ``FORCE_SCRIPT_NAME`` or a ``SCRIPT_NAME``, and each language root :func:`django.conf.urls.i18n.i18n_patterns` serves below that prefix, such as ``/en/`` and ``/de/``, the default language left out when ``prefix_default_language`` is ``False``.
   They follow the script prefix and the URLconf of the request, and the ``$router`` payload entry hands them to the browser.

The marks follow the URL hierarchy only, and no page or link declares that it belongs to a tab whose URL is not a segment prefix of its own.
A page that should light up a section link therefore lives below the URL of that link, such as ``/operations/`` and ``/operations/42/`` under an Operations link to ``/operations/``.
A Home link to ``/`` marks only the home page, so a list that belongs under Home in the menu gets a section of its own in the URL and a link to it.

Changing the rule per container or link
---------------------------------------

``data-next-match`` changes the comparison for a link or for every link in a container, a nested container inheriting the value of its parent.
Its value is a list of tokens separated by spaces, and a value on a link replaces the one it inherits.

``exact``
   No ancestor marks, so only the link to the current page is marked, a fit for tabs where a parent tab should stay dim.

``query``
   Every parameter the link names in its query must hold the same values in the current URL, in any order, so ``?tab=activity`` is the page only on that tab.
   A link whose target is a query string alone, such as the ``?page=2`` of a pager, is compared this way in every mode, since its path is always the current one.

.. code-block:: jinja

   <nav data-next-match="exact query">
     <a href="?tab=overview">Overview</a>
     <a href="?tab=activity">Activity</a>
   </nav>

``data-next-current="off"`` on a link or a container leaves its links exactly as written, marks included, for a menu that sets its own state.

.. code-block:: jinja

   <nav data-next-current="off">
     <a href="{{ step_url }}" aria-current="step">Step 2</a>
   </nav>

Breadcrumbs
-----------

``{% breadcrumbs %}`` writes the same marks by the same rule, ``aria-current="page" data-next-current="page"`` on the current crumb and ``data-next-current="ancestor"`` on each ancestor link but a home path.
The trail renders inside ``<nav aria-label="Breadcrumb">``, so the pass finds its marks already settled and leaves them alone, see :doc:`/content/topics/seo/breadcrumbs`.

Styling
-------

The marks are plain attributes, so a stylesheet styles them directly.

.. code-block:: css

   nav a[aria-current="page"] {
     font-weight: 600;
   }

   nav a[data-next-current="ancestor"] {
     text-decoration: underline;
   }

Tailwind reaches them through its attribute variants, ``aria-[current=page]:`` and ``data-[next-current=ancestor]:``, which is how the shared UI kit of the examples styles its ``nav_link`` component, see :doc:`/content/misc/examples`.

After a navigation
------------------

The ``$router`` payload hands the containers and the home paths to the browser.
The ``intent`` bundle, which the router loads once the page is idle, marks the links of every container again by the same rule after each envelope applies, a navigation, a zone update, an action response, a server push, or a restored snapshot alike, so a menu in a kept layout follows the page.
Inside an open layer it marks the links for the page beneath the layer.
The client pass runs only on a routed document, so with the router off the marks of a server push hold as the push wrote them.
The chrome digest of :doc:`layouts` is taken before the server writes the marks, so a link that changes its state never stops a layout from being kept.

With the router off
-------------------

The marks do not depend on the router.
``ROUTER["ENABLED"] = False`` keeps the server pass on every document, zone, and action response.

``ROUTER["CURRENT"]`` takes ``True``, ``False``, or a dict.
``True`` keeps the defaults, ``False`` turns the server pass and the client pass off, and a dict sets the keys it names, ``{"ENABLED": False}`` being the same as ``False``.

.. code-block:: python
   :caption: settings.py

   NEXT_FRAMEWORK = {
       "ROUTER": {
           "ENABLED": False,
           "CURRENT": {"CONTAINERS": ("nav", "[data-menu]")},
       },
   }

A replacement backend
---------------------

``ROUTER["CURRENT"]["BACKEND"]`` names the ``CurrentLinks`` subclass that runs the pass, ``next.router.CurrentLinks`` by default.
It is built with the configured containers, and its ``mark(html, path, origin=None)`` returns ``html`` with the links marked for the request.
``path`` is a callable that answers the request path with its query, and ``origin`` one that answers the scheme and host absolute links are compared with, and each runs only once ``html`` opens a container.
A subclass that marks links by a rule of its own overrides ``mark``, and ``current_links()`` returns the backend the settings name, or ``None`` while the pass is off.
The ``intent`` bundle marks the links again with the default rule after each update in the browser, so the marks of a different rule hold until the first navigation or partial update of the page.
A path that does not import or names no concrete subclass is reported as ``next.E219``.

.. seealso::

   :doc:`/content/ref/router` for ``CurrentLinks`` and ``current_links``.
   :doc:`/content/ref/settings` for the ``ROUTER["CURRENT"]`` keys.
