.. _howto-cache-pages-on-a-cdn:

Cache pages on a CDN
====================

Problem
-------

Marketing pages should be served from a CDN edge, while forms on them keep working, partial requests never receive a cached full page, and no visitor ever receives another visitor's cookie.

Solution
--------

Give each public page a ``cache`` with ``public`` or ``s_maxage``, keep ``CSRF_DELIVERY`` at ``"auto"`` so a shared page carries no token in its HTML, tell the CDN to bypass every request that carries ``X-Next-Request``, and let the checks and the runtime catch a page that turns personal.

Walkthrough
-----------

Mark the page as shared
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python
   :caption: site/pages/pricing/page.py

   from next.pages import CacheDict

   cache: CacheDict = {"public": True, "max_age": 60, "s_maxage": 3600, "stale_while_revalidate": 86400}

Browsers keep the page for a minute, the CDN for an hour, and the CDN serves a stale copy for a day while it revalidates.
``cache`` applies to that one page, so every public page declares its own.
A CDN-targeted header such as ``CDN-Cache-Control`` or ``Surrogate-Control`` cannot be set through ``headers``, since it would remain on a response the framework makes private, see :doc:`/content/topics/caching`.

Keep the token out of the HTML
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A CSRF token in the HTML sets the CSRF cookie and ties the page to one visitor.
Under the default ``CSRF_DELIVERY = "auto"`` a shared page renders its ``{% form %}`` without the hidden token and hands the runtime the address of ``/_next/csrf/`` instead of a token.
The runtime fetches a fresh token the first time the visitor focuses or presses inside a form, and before the first unsafe request at the latest, so a page cached for a day never posts a stale token.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {"CSRF_DELIVERY": "auto"}

A form on a shared page then needs JavaScript, and ``manage.py check`` says so unless the form's action declares ``Meta.requires_runtime = True``, or ``requires_runtime=True`` on ``@action``, see :ref:`topics-forms-actions-requires-runtime`.
It also reports a shared page under ``CSRF_DELIVERY = "eager"`` that renders a form or the runtime, and one that renders ``{% csrf_token %}``, since either sets the cookie on every response.
Keep ``CSRF_USE_SESSIONS`` off, because a token in the session reads the session on every render and makes every shared page private.

Keep the render anonymous
~~~~~~~~~~~~~~~~~~~~~~~~~

A shared page renders nothing about the visitor.
A layout that reads ``request.user``, a ``@context`` that reads the session, a template that sets a cookie, a CSP nonce, or consent rendered on the server makes the response personal, and the framework then sends it ``private`` and logs one warning per page.
A request that carries an ``Authorization`` header is answered ``private`` as well.
Only a ``GET`` or a ``HEAD`` carries the declared cache at all, so a ``POST`` the page answers is never shared.
Move the account menu into a lazy zone, which the browser fetches with its own cookies after the cached page loads.

.. code-block:: jinja
   :caption: site/pages/layout.djx

   <header>
     <a href="/">Acme</a>
     {% zone "account" lazy="load" %}
       {% if request.user.is_authenticated %}<a href="/account/">{{ request.user }}</a>{% else %}<a href="/login/">Sign in</a>{% endif %}
     {% placeholder %}
       <span class="account-slot"></span>
     {% endzone %}
   </header>

A ``Consent`` parameter reads an undecided visitor on a shared page, and gated scripts wait for the runtime, so consent never varies the cached HTML.
``CONSENT["SERVER_RENDER"] = True`` gives that up, and every shared page whose HTML then follows the consent cookie is sent with ``Cache-Control: private``.

Configure the CDN
~~~~~~~~~~~~~~~~~

The same URL answers a full page and a partial envelope, told apart by request headers, and many CDNs ignore ``Vary``.
Every zone response and every envelope carries ``Cache-Control: private, no-store``, which keeps it out of the cache, and the CDN also bypasses the cache for every request that asks for one, so a cached page never answers a partial request.

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Rule
     - Why
   * - Bypass the cache when the request carries ``X-Next-Request``.
     - Zone GETs, lazy zones, polls, and the token fetch send it.
   * - Bypass the cache for ``/_next/``.
     - The form dispatch and the token endpoint are never shared.
   * - Leave cookies out of the cache key and honour the origin ``Cache-Control``.
     - The page declares what may be shared, and a ``private`` downgrade must win.
   * - Forward ``Accept-Language`` only when the pages vary on it.
     - A page outside :func:`~django.conf.urls.i18n.i18n_patterns` under ``LocaleMiddleware`` answers several languages at one URL, which ``manage.py check`` reports.

A CSP nonce belongs to one response, so a response whose render mints one is sent with ``Cache-Control: private``, and ``manage.py check`` warns while a nonce is active beside shared pages.
Set ``CSP_NONCE`` to ``False`` and allow the scripts by hash or by source on a site a CDN serves, see :doc:`/content/security/csp-and-nonce`.

Verification
------------

.. code-block:: bash
   :caption: shell

   curl -sI https://acme.example/pricing/ | grep -iE "cache-control|set-cookie|vary"
   curl -sI -H "X-Next-Request: 1" -H "X-Next-Zone: account" https://acme.example/pricing/ | grep -i cache-control
   uv run python manage.py check

The full page answers the declared ``Cache-Control``, no ``Set-Cookie``, and ``Vary: X-Next-Request, X-Next-Zone, X-Next-Merge, X-Next-Version``, and the zone request answers ``private, no-store``.
Submit the form with the browser's network panel open, and one ``GET /_next/csrf/`` precedes the ``POST``.
A shared page that is made private logs ``declares a shared cache, but its response follows the visitor through a cookie, the session, the CSRF token, the consent, a CSP nonce or the Authorization header, so it goes out private.``, naming the file.

See also
--------

.. seealso::

   :doc:`/content/topics/caching` for ``cache``, ``headers``, and the rules that make a page private.
   :doc:`/content/security/csrf-and-forms` for the token delivery modes and :doc:`/content/ref/csrf` for the endpoint.
   :doc:`/content/topics/partial-rendering/reference` for the partial request headers.
