.. _ref-csrf:

CSRF delivery reference
=======================

Module summary
--------------

``next.csrf`` decides where a rendered page puts its CSRF token and serves the ``/_next/csrf/`` endpoint the runtime fetches a deferred token from.
It is a flat, cross-area module, so ``next.pages``, ``next.forms``, ``next.static``, and ``next.partial`` read it without importing each other.

Delivery modes
--------------

``CsrfDelivery`` names the three modes of the ``CSRF_DELIVERY`` setting, which :doc:`settings` describes, and ``csrf_delivery()`` answers the configured one, ``AUTO`` for a value that names none.

A deferred render leaves the hidden field out of ``{% form %}``, and ``$csrf`` carries ``{"header": ..., "url": ...}`` in place of ``{"header": ..., "token": ...}``.
The runtime fetches the token from ``url`` before the first unsafe request, and Django reads it from the header, so the form posts as before.
``csrf_url()`` reverses ``next:csrf``, and where ``next.urls`` is the root URLconf, without a namespace, it reverses ``csrf_view`` itself, so a project pattern named ``csrf`` is never taken for the endpoint.
The address is reversed once per URLconf, script prefix, and language, and again after the routes change.
When ``ROOT_URLCONF`` does not route the endpoint, ``csrf_payload`` embeds the token after all and logs at most once every ten minutes, so forms still post, and under ``DEBUG`` or ``STRICT_LOADING`` it raises the ``NoReverseMatch`` instead.
``next.E148`` reports the missing route at startup.
``CSRF_USE_SESSIONS = True`` reads the session on every render, so every shared page is sent with ``Cache-Control: private`` whatever the mode, and ``manage.py check`` warns about it.

.. automodule:: next.csrf
   :members: CsrfDelivery, csrf_delivery, csrf_header_name, csrf_payload, csrf_token_payload, csrf_url, defer_token, token_deferred, CSRF_URL_NAME

The token endpoint
------------------

``csrf_view`` answers ``GET`` and ``HEAD`` at ``/_next/csrf/``, named ``next:csrf``, with ``{"header": ..., "token": ...}`` and a freshly masked token.
A method other than ``GET`` or ``HEAD`` answers 405.
A request without ``X-Next-Request: 1`` answers 400, since the custom header forces a CORS preflight no other site passes, and a ``Sec-Fetch-Site`` header other than ``same-origin`` answers 403.
The response carries the headers :func:`~django.utils.cache.add_never_cache_headers` sets, ``Cache-Control: max-age=0, no-cache, no-store, must-revalidate, private`` and an ``Expires`` in the past, then ``Vary: Cookie``, ``X-Content-Type-Options: nosniff``, ``Cross-Origin-Resource-Policy: same-origin``, and ``X-Robots-Tag: noindex``.
The endpoint reads the token through :func:`~django.middleware.csrf.get_token`, so it works with ``CSRF_USE_SESSIONS`` and ``CSRF_COOKIE_HTTPONLY`` alike.

See also
--------

.. seealso::

   :doc:`/content/security/csrf-and-forms` for the delivery modes from the security side.
   :doc:`/content/howto/cache-pages-on-a-cdn` for lazy CSRF behind a CDN.
   :doc:`/content/topics/caching` for the shared pages that defer the token.
   :doc:`settings` for ``CSRF_DELIVERY``.
