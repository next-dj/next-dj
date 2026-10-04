.. _ref-site:

Site reference
==============

Module summary
--------------

``next.site`` reads ``NEXT_FRAMEWORK["SITE"]`` and answers the site origin and its indexability, the two facts every head tag, response header, sitemap, and robots file of :doc:`/content/topics/seo/site` reads.
``next.pages`` and ``next.seo`` both read it, so it imports neither.

Site
----

``site_origin(request)`` answers the ``(scheme, host)`` pair every absolute URL of a request is built on, the declared ``URL`` first, then the ``domain`` of the current ``Site`` row, then the ``Host`` of the request.
It keeps the answer on the request, and without a declared ``URL`` and without a request it raises ``SiteOriginError``, a ``ValueError`` whose ``url`` names the URL that stayed relative when one did.
``site_url(request=None)`` answers the declared origin alone, the literal ``URL`` or what a callable ``URL`` answers for the request, and ``None`` without one or when the callable answers ``None``.
A callable ``URL`` is called once per request.
One that raises or answers no origin raises ``ImproperlyConfigured`` under ``DEBUG`` or ``STRICT_LOADING`` and is logged once otherwise, and ``site_url_failed(request)`` then answers true so the SEO routes answer 503.
``url_origin(value)`` is the one validation both read, an ``http`` or ``https`` URL with a host and no path, query, or fragment, and ``url_rule(value)`` the reading of a setting value the runtime and ``check_site_settings`` share.
``site_indexable(request=None)`` answers whether search engines may index the site for the request, ``"auto"`` reading ``DEBUG`` on every call, a bool standing as it is, and a callable receiving the request or ``None``, one that raises answering false.
``debug_closed()`` answers whether only ``DEBUG`` closes the site under ``"auto"``, the case where the sitemap and the robots file are still served under ``noindex`` for a preview, and ``site_closed_to_crawlers(request)`` whether the SEO routes serve the closed documents.
``indexable_without_request()`` is the answer the system checks read, a callable rule never called and read as open.
``site_config()`` answers the ``SiteConfig`` read once per settings reload.

.. automodule:: next.site.config
   :members: SiteConfig, site_config, site_origin, site_url, site_url_failed, site_indexable, debug_closed, site_closed_to_crawlers, indexable_without_request, url_origin, url_rule, SITE_KEYS

.. automodule:: next.site.errors
   :members:

The robots header
~~~~~~~~~~~~~~~~~

``stamp_site_robots(response, request)`` overwrites ``X-Robots-Tag`` with ``noindex, nofollow`` while the site is closed for the request, and ``site_robots`` wraps a view so everything it answers is stamped.
The page responses, the zone responses, and the SEO routes are stamped this way.
``RobotsHeaderMiddleware`` stamps every response that passes it, for the form dispatch, the streams, the admin, an API, and the media files.

.. automodule:: next.site.headers
   :members:

.. automodule:: next.site.middleware
   :members:

Checks
~~~~~~

``check_site_settings`` reports an unusable ``SITE`` value, a ``URL`` with a path among them, a callable ``URL`` or ``INDEXABLE`` whose signature takes no request alone, and an unknown key, and it never calls either callable.
``check_site_url_for_deploy`` carries ``deploy=True`` and the ``seo`` tag and reports a missing ``URL`` unless ``SITE_ID`` pins a ``django.contrib.sites`` row, more sharply under ``ALLOWED_HOSTS = ["*"]``.
The check on a closed site that still publishes a sitemap or a robots source lives in ``next.seo.checks``, see :doc:`seo`.
The checks register through ``next.checks``, since ``next.pages`` imports ``next.site`` early, and :doc:`system-checks` lists their codes.

.. automodule:: next.site.checks
   :members:

See also
--------

.. seealso::

   :doc:`/content/topics/seo/site` for the site scope.
   :doc:`csrf` for the CSRF delivery.
   :doc:`settings` for ``SITE``.
