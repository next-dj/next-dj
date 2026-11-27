.. _topics-router-sessions:

Sessions, caching, and security
===============================

A navigation keeps state in the browser between requests, namely the kept layouts, the history snapshots, and the prefetched answers.
The server tells the router when that state belongs to a session that has ended or changed.
A navigation answer is also a response a cache could store and another site could ask for, and this page lists what the framework guarantees for both.

.. contents::
   :local:
   :depth: 1

Clearing the router
-------------------

A response tells the router to drop what it holds with the ``X-Next-Router: clear`` directive.
On a navigation answer the router drops every snapshot and every prefetched answer and keeps none of the page it leaves.
The server turns a slot morph into a body morph, because the kept chrome may still show a user name, a cart, or a menu of the session that ended.

Three events set the directive with no code.

Login.
   Django sends ``user_logged_in`` from :func:`django.contrib.auth.login`, and the framework clears the router on the request that logged in, with the reason ``login``.

Logout.
   ``user_logged_out`` from :func:`django.contrib.auth.logout` clears it with the reason ``logout``.

A changed session key.
   A request whose session cookie names another key by the time the response leaves, as :meth:`~django.contrib.sessions.backends.base.SessionBase.cycle_key` does after a privilege change, clears it with the reason ``session``.
   The signed-cookie session engine is left out, because its key changes on every write.

The directive is set on every response a request produces while the router is on, so a login or a logout inside an action carries it as well.
That covers a document, a navigation, a prefetch, a zone, a denial of the access rules, and the response of a form or an ``@action``.
The router drops its snapshots and its prefetched answers when it reads the directive on a navigation answer, a prefetch answer, or any partial answer, the response of a form action included.
Apart from the directive, every partial request other than a ``GET`` empties both before it leaves, since a write can make them stale, see :doc:`browser`.

``router_clear(request, reason="policy")`` sets it from code, for any change the session itself does not show, such as switching the active team.

.. code-block:: python
   :caption: pages/teams/page.py

   from django.http import HttpRequest

   from next import action
   from next.forms import redirect_to_origin
   from next.router import router_clear

   @action("switch_team")
   def switch_team(request: HttpRequest):
       request.session["team"] = request.POST.get("team")
       router_clear(request, "team")
       return redirect_to_origin(request)

The first call of a request wins and later calls change nothing, so the reason names what cleared it first.
Each request that clears the router sends ``router_cleared`` once, with its ``reason``, see :doc:`/content/ref/signals`.

Redirects after a session change
--------------------------------

The fetch of a navigation follows a redirect unseen, so the router would never read the headers or the token the redirect carries.
A navigation whose page answers with a redirect while the request logged in, logged out, changed its session key, called ``router_clear``, or rotated the CSRF token is therefore answered with a ``visit`` envelope instead.
The envelope carries the cookies of the redirect, ``X-Next-Router``, and the target of the redirect, and the router loads that target as a new document, so the new session and the new token reach a fresh page.
A redirect to another origin rides the same envelope as an external visit.
Every other redirect is followed by the fetch.

Prefetch requests
-----------------

A navigation request that also carries ``X-Next-Prefetch`` is a prefetch, at one of two depths.
``X-Next-Prefetch: 1`` asks for the shell of the page, its deferred boundaries at their loading content and no deferred job run, and the answer never streams.
``X-Next-Prefetch: full`` is sent at the press of a link and asks for the page as a navigation gets it, deferred jobs run and the answer streamed when the server can stream, see :doc:`prefetch`.
``request.next.purpose`` is ``prefetch`` for both, and ``request.next.depth`` is ``PrefetchDepth.SHELL`` or ``PrefetchDepth.FULL``.

A prefetch answer may be shown to the visitor with no further request, so code that must act on a visit does not rely on the prefetch request, see :ref:`topics-router-prefetch-visits`.

A zone request of a layer link that carries ``X-Next-Prefetch`` is a layer prefetch, at the full depth whatever the value.
It renders the zone as the layer open does and keeps the purpose of a zone request, see :doc:`prefetch`.

Two answers refuse a prefetch before any render.

``204`` with an empty body
   The page declares ``page.router(prefetch=False)`` or ``page.router(navigate=False)``, or it is an intercepted page under ``ROUTER["INTERCEPT"] = "links"``.
   A layer prefetch is refused when the page or the intercept serving its zone declares ``page.router(prefetch=False)``.

``429`` with ``Retry-After``
   ``ROUTER["SHED"]`` shed the prefetch, so a speculative render would only add load.
   The default ``LoadShed`` sheds while the oldest job in the pool queue of deferred boundaries waits longer than ``DEFER["EXECUTOR"]["QUEUE_TIMEOUT"]``, and a project names its own ``PrefetchShed`` subclass to shed by another measure, see :ref:`ref-router`.
   ``Retry-After`` holds the wait in whole seconds, at least one, and ``prefetch_shed`` reports each refusal.

Caching
-------

No navigation answer is ever stored.
An envelope is sent ``Cache-Control: private, no-store``, and a streamed navigation ``private, no-cache, no-store, no-transform``.
The empty ``204``, ``403``, ``409``, and ``429`` refusals are sent ``private, no-store`` as well, and so are a redirect the page answers with and a denial its access rules return.
The router keeps its snapshots in the memory of the tab, never in the HTTP cache.

A document and a navigation share their URL, so every page the view renders and every partial response carries ``Vary`` over ``X-Next-Request``, ``X-Next-Zone``, ``X-Next-Merge``, ``X-Next-Version``, ``X-Next-Boundary``, ``X-Next-Navigate``, ``X-Next-Layouts``, and ``X-Next-Prefetch``.
A shared cache that honours ``Vary`` therefore never hands an envelope to a document request or a document to the router.
A ``render()`` that returns its own cacheable response gains the same ``Vary`` when it answers ``2xx`` with a ``Cache-Control`` header of its own.
A navigation to such a page is answered with a ``visit`` envelope and loads hard, unless the response is a redirect.
A zone response also varies on ``X-Next-Origin`` while active links are on, because its links are marked for the page beneath it.

A page cached publicly stays cacheable for document requests, see :doc:`/content/topics/caching`.
Its navigation envelope says so in its ``shared`` field.
The router then fetches that path as a plain document on later navigations within the life of the page, so a shared cache can answer, and reads the swap out of the document as ``ROUTER["FOREIGN"] = "extract"`` does.
A page whose ``cache`` forbids every store keeps no back-forward copy in the browser, and ``next.W178`` suggests ``no_cache`` instead.

Security
--------

A navigation runs the same view as a document request, so the access rules of the page, its guards, and its ``render()`` decide it exactly as they decide a page load, and a refusal reaches the browser as it would for a document.

A navigation request whose ``Sec-Fetch-Site`` names anything but ``same-origin`` is refused ``403`` with an empty body before any render.
The navigation headers are custom headers, so another site cannot send them through a plain link or form, and a script on another origin would need a CORS preflight that a project without a CORS policy of its own never grants.

``X-Next-Layouts`` is untrusted input.
The server reads it against a strict grammar of hex ids and digests, ignores a value past 32 entries or past 1 KB, and only compares it with the page it rendered for the same request.
A forged declaration changes how much of that render the response carries and nothing else, since every byte of the answer comes from the render of the request in flight.

The slot and zone markers carry a seal minted per response, so markup in page content cannot pose as a marker, and a navigation answer carries the CSP nonce of its own response for the scripts it brings.
An inline script in navigated HTML runs only when its ``nonce`` is that nonce, so markup injected into page content cannot run through a navigation.

``X-Next-Origin`` is untrusted input as well.
A value that is no same-site path is ignored, so it can neither steer the layer a link to an intercepted page opens nor the page active links are marked for.

A prefetch runs the context callables of the page, and Speculation Rules let the browser fetch the links the router does not own.
A page or an endpoint that changes state on a ``GET`` refuses both, see :doc:`prefetch`.
The router follows no answer off the site, since a final URL on another origin after redirects becomes a hard load, and a path that opens with ``//`` is answered with a hard load of the single-slash path rather than of another host.

:doc:`/content/security/overview` restates these guarantees beside the rest of the framework.

.. seealso::

   :doc:`/content/ref/router` for ``router_clear``.
   :doc:`/content/topics/access` for the access rules a navigation runs.
