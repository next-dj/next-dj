.. _topics-caching:

Caching and response headers
============================

A page response carries headers beside its body, the ``Cache-Control`` a CDN obeys and whatever security header one section of the site needs.
``page.py`` declares the first through ``cache`` and the second through ``headers``, and the framework keeps a response that shows one visitor out of every shared cache.
This page covers the two declarations, what each ``page.py`` name passes down the tree, and the rules that take a shared page private.

.. contents::
   :local:
   :depth: 2

cache
-----

``cache`` in a ``page.py`` sets the ``Cache-Control`` of that one page and takes four forms.

.. list-table::
   :header-rows: 1
   :widths: 28 72

   * - Value
     - Response header
   * - An int
     - ``public, max-age=<n>``, the same reading as ``cache`` in a ``sitemap.py``.
   * - ``False``
     - ``private, no-store``.
   * - A ``CacheDict``
     - The directives it names, ``public``, ``max_age``, ``s_maxage``, ``stale_while_revalidate``, ``stale_if_error``, ``immutable``, ``no_store``, ``no_cache``, and ``must_revalidate``, plus the ``vary`` header names.
   * - A callable
     - Any of the forms above or ``None``, resolved through dependency injection per request like a ``@context`` callable.

.. code-block:: python
   :caption: shop/pages/lp/[campaign]/page.py

   from next.pages import CacheDict

   cache: CacheDict = {
       "public": True,
       "max_age": 60,
       "s_maxage": 300,
       "stale_while_revalidate": 600,
   }

The page answers ``Cache-Control: public, max-age=60, s-maxage=300, stale-while-revalidate=600``.
The directives are applied through :func:`~django.utils.cache.patch_cache_control` and :func:`~django.utils.cache.patch_vary_headers`, and only on a successful response to a ``GET`` or a ``HEAD``, so a ``POST`` the page answers itself never carries the page's cache and a callable ``cache`` is not even resolved for it.
``manage.py check`` reports an unknown key, a negative age, a flag that is no bool, and ``public`` together with ``no_store``.
A callable ``cache`` that raises, or returns anything but the forms above, sends the page out ``private, no-store``, since no cache may keep a response whose policy is unknown.
The failure is logged once per page, and under ``DEBUG`` or ``STRICT_LOADING`` it raises instead, naming the ``page.py`` and the shapes it may return.
``Http404`` and ``PermissionDenied`` raised from it answer 404 and 403 as from any view.

A page is shared when its cache lets a CDN keep a copy, through ``public`` or ``s_maxage``.
The checks cannot call a callable ``cache``, so they count its page as one that may be shared and mark it ``(callable cache)`` in the messages.
A shared page renders so that its HTML is the same for every visitor.
The CSRF token stays out of the HTML under ``CSRF_DELIVERY="auto"``, see :doc:`/content/security/csrf-and-forms`, a ``Consent`` parameter reads an undecided visitor, and gated scripts ride the runtime manifest, see :doc:`/content/topics/scripts/consent`.
A render that still puts one visitor into the HTML takes the page private, as `Going private`_ describes.

headers
-------

``headers`` is a mapping of header names to values, and unlike ``cache`` it flows down the tree.
Each ``page.py`` from the page root to the page contributes its mapping, names merge without regard to case, the nearest value wins, and ``None`` removes a header an ancestor set.

.. code-block:: python
   :caption: shop/pages/checkout/page.py

   from next.pages import HeadersDict

   headers: HeadersDict = {
       "Cross-Origin-Opener-Policy": "same-origin",
       "Permissions-Policy": "payment=(self)",
   }

Every page under ``/checkout/`` carries both headers, and a value is ASCII text on one line.
``headers`` never names a caching header, ``Cache-Control``, ``CDN-Cache-Control``, ``Surrogate-Control``, ``Cloudflare-CDN-Cache-Control``, ``Expires``, ``Age``, or ``Vary``, because ``cache`` owns them and a header set here would outlive the private form a personal response takes.
The framework owns ``X-Robots-Tag``, ``Set-Cookie``, ``Content-Type``, ``Content-Length``, ``Transfer-Encoding``, and ``Connection`` as well.
``Content-Security-Policy`` and its ``-Report-Only`` form stay with the CSP middleware, since Django's middleware and django-csp skip a response that already carries one, so a page's own would replace the whole site policy and its nonce.
``manage.py check`` reports a forbidden name, pointing a caching one at ``cache``, an invalid header name, and a value with a line break, another control character, or a character outside ASCII, and the response leaves each of them out.

What passes down the tree
-------------------------

Every name a ``page.py`` declares follows its own rule for the pages below it.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Name
     - What a descendant page gets
   * - ``cache``
     - Nothing, since a public cache is a claim about one page's content, and a descendant that should be cached declares its own.
   * - ``headers``
     - Every header, merged by name with the nearest value winning and ``None`` removing one.
   * - ``metadata`` dict
     - Every key, merged deep with the nearest layer winning, see :doc:`seo/merge`.
   * - ``@page.metadata``
     - Nothing by default, and with ``inherit=True`` the callable runs for every descendant ahead of its own metadata.
   * - ``@context``
     - Nothing by default, and with ``inherit_context=True`` the value, the outermost ancestor winning and the page's own key shadowing it, see :doc:`context`.
   * - ``breadcrumb`` key
     - Nothing, each page labels its own crumb, see :doc:`seo/breadcrumbs`.
   * - ``render`` and the template
     - Nothing, they belong to the page, while ``layout.djx`` wraps every page below it.

A response render() returns
---------------------------

A ``render()`` that returns an :class:`~django.http.HttpResponse` keeps every header it set.
``headers`` fill only the names it left out, and ``cache`` applies only when the response carries no ``Cache-Control`` of its own and succeeded.
A redirect, a 404, or any other non-2xx response never receives the declared cache.

Going private
-------------

A response a CDN keeps must not carry anything of one visitor, and the framework enforces it at runtime.
A shared page goes out ``private``, with the other directives kept and one warning per page naming the file, when its response does any of the following.

- It sets a cookie, from the view, from ``render()``, or from a middleware after the view, the session and CSRF middleware among them.
- It reads the session, or needs the CSRF cookie refreshed during its render.
- It answers a request that carries an ``Authorization`` header, since a shared copy would reach everyone.
- Its render minted a CSP nonce, through the framework tags or a template reading ``request.csp_nonce``, since a nonce belongs to one response.
- Its HTML follows the consent cookie, which ``CONSENT["SERVER_RENDER"] = True`` forces on every page, the shared ones included.

The response carries ``SharedCookies`` in place of Django's cookie jar, so the first cookie set on it takes the cache private whenever it lands, and a ``TemplateResponse`` rendered after the view is settled once its content exists.
``public`` and ``Set-Cookie`` never leave the server together, provided ``ConditionalGetMiddleware`` sits above every middleware that sets a cookie, because the 304 it answers copies the cache before an outer cookie lands, and ``next.W124`` reports the order that breaks it.
``next.middleware.SharedCacheGuardMiddleware`` closes that gap and any other one a third-party middleware opens, see `The shared cache guard`_.
A cookie written through ``update()`` or ``load()`` on the jar takes the response private as well.
A layout that reads ``request.user`` touches the session and takes every shared page under it private, which the warning makes visible.

The shared cache guard
~~~~~~~~~~~~~~~~~~~~~~

``SharedCacheGuardMiddleware`` is opt-in and checks the finished response rather than the page.
A response that sets a cookie while its ``Cache-Control`` carries ``public`` or ``s-maxage`` loses both directives and gains ``private``, its ``CDN-Cache-Control``, ``Cloudflare-CDN-Cache-Control``, and ``Surrogate-Control`` headers go, and ``Vary`` gains ``Cookie``.
Every other directive stays, and each path is logged once.
List it first in ``MIDDLEWARE``, or right below ``UpdateCacheMiddleware``, so it sees every cookie the stack sets and the copy Django's cache stores is the private one.
In either place ``next.W124`` stays silent.

.. code-block:: python
   :caption: config/settings.py

   MIDDLEWARE = [
       "next.middleware.SharedCacheGuardMiddleware",
       "django.middleware.security.SecurityMiddleware",
       "django.contrib.sessions.middleware.SessionMiddleware",
       "django.middleware.http.ConditionalGetMiddleware",
       # ...
   ]

The middleware runs on a sync and an async stack alike.

Two settings take every shared page private at once, and ``manage.py check`` warns about both.
``CSRF_USE_SESSIONS = True`` keeps the CSRF token in the session, so every render reads it.
An active CSP nonce, ``CSP_NONCE`` on beside the CSP middleware of Django or django-csp, stamps every render with a fresh one, and shared pages allow their scripts by hash or by source instead, see :doc:`/content/security/csp-and-nonce`.

A shared landing page
---------------------

A campaign page is the typical shared page, and three habits keep it shared.
Its layout reads no ``request.user``, and an account menu moves into a lazy zone the browser fetches with its own cookies, see :doc:`/content/howto/cache-pages-on-a-cdn`.
A form on it posts through the runtime, which fetches the deferred token, and ``Meta.requires_runtime = True`` on the form records that choice, so the check that warns about forms needing JavaScript stays quiet for it.
A paid-traffic variant of the page stays out of the index and points at the organic page, so the two never compete.

.. code-block:: python
   :caption: shop/pages/lp/[slug]/page.py

   from django.shortcuts import get_object_or_404
   from shop.models import Campaign

   from next import context, page
   from next.pages import CacheDict, MetadataDict
   from next.urls import DUrl

   cache: CacheDict = {"public": True, "max_age": 60, "s_maxage": 600, "stale_while_revalidate": 600}

   @context("campaign")
   def campaign(slug: DUrl[str]) -> Campaign:
       return get_object_or_404(Campaign.objects.live(), slug=slug)

   @page.metadata
   def campaign_metadata(campaign: Campaign) -> MetadataDict:
       return {
           "title": {"absolute": campaign.headline},
           "canonical": f"/lp/{campaign.organic_slug}/" if campaign.paid else True,
           "robots": {"index": not campaign.paid, "follow": True},
       }

.. code-block:: bash
   :caption: shell

   curl -sI https://acme.example/lp/spring/ | grep -iE "cache-control|set-cookie|x-robots-tag"
   curl -sI https://acme.example/lp/spring-fb/ | grep -i x-robots-tag

The organic page answers ``Cache-Control: public, max-age=60, s-maxage=600, stale-while-revalidate=600`` with no ``Set-Cookie`` and no robots header, and the paid variant answers ``X-Robots-Tag: noindex, follow``.

Partial responses
-----------------

Every zone response carries ``Cache-Control: private, no-store`` together with the ``headers`` of its page, the answer to a zone GET, a lazy zone, and a refused zone request alike.
Every patch envelope an action answers carries ``private, no-store`` as well, and a stream carries ``no-cache, no-transform``.
Many CDNs ignore ``Vary``, so a cached zone response would reach a visitor who asked for the full page at the same URL.
A CDN in front of a shared page therefore bypasses its cache for every request that carries ``X-Next-Request``, see :doc:`/content/howto/cache-pages-on-a-cdn`.

See also
--------

.. seealso::

   :doc:`/content/howto/cache-pages-on-a-cdn` for the CDN rules end to end.
   :doc:`seo/social-and-canonical` for the ``X-Robots-Tag`` a page's robots directives set.
   :doc:`/content/ref/pages` for ``CacheDict`` and ``HeadersDict``, and :doc:`/content/ref/csrf` for the deferred token.
