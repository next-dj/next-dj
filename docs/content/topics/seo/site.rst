.. _topics-seo-site:

Site identity and indexability
==============================

``NEXT_FRAMEWORK["SITE"]`` says where the site lives, what it is called, and whether search engines may index it.
Every absolute URL of the head, the sitemap, and the robots file is built on its origin, and every robots meta, ``X-Robots-Tag`` header, sitemap, and robots file follows its one indexability answer.
This page covers the three keys, the forms each takes, and what a site closed to search serves.

.. contents::
   :local:
   :depth: 2

The scope
---------

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "SITE": {
           "URL": "https://notes.example",
           "NAME": "Notes",
           "INDEXABLE": "auto",
       },
   }

``URL`` is the public origin, an ``http`` or ``https`` URL with a host and no path, query, or fragment, or a callable taking the request, or the dotted path of one.
A path would leak into every URL built on the origin, so ``manage.py check`` rejects ``https://notes.example/app`` as well as a relative value.
``NAME`` is the site name as text or a lazy translation, and it seeds the ``site_name`` metadata key when ``DEFAULTS`` sets none, which fills ``{site_name}`` in the title template and ``og:site_name``.
``INDEXABLE`` decides whether search engines may index the site, and it defaults to ``"auto"``.

The origin
----------

``site_origin(request)`` answers the scheme and the host every absolute URL of a request is built on, the canonical link, an Open Graph image, an hreflang alternate, a JSON-LD ``@id``, a sitemap location, and the ``Sitemap:`` line alike.
It takes the first of three answers.

#. The origin ``URL`` declares, the literal or what a callable answers for the request.
#. The ``domain`` of the current ``Site`` row, when :doc:`django.contrib.sites <django:ref/contrib/sites>` is installed and a row matches.
#. The ``Host`` of the request, with the request scheme.

The answer is kept on the request, so one response never mixes two origins.
Without ``URL`` and without a request, in a system check or a management command, no origin exists and ``SiteOriginError`` is raised.
Without ``URL`` every absolute URL follows the host of the request or the ``Site`` row, which a misconfigured proxy or a spoofed ``Host`` header turns into someone else's host.
``manage.py check --deploy`` asks for ``URL`` unless ``SITE_ID`` pins a ``Site`` row, and warns harder when ``ALLOWED_HOSTS`` holds ``"*"``.
``site_url(request)`` answers the declared origin alone as text, and ``None`` where ``URL`` declares none.

A callable ``URL`` answers per request, so one process serving several tenants names each tenant's origin.
It receives ``None`` where no request exists, in a system check or a sitemap build, and may answer ``None`` then.
Its answer goes through the same validation as a literal, so a value that is no origin, or ``None``, falls through to the ``Site`` row and the request host.

.. code-block:: python
   :caption: tenants/site.py

   from django.http import HttpRequest

   def tenant_origin(request: HttpRequest | None) -> str | None:
       if request is None:
           return None
       return f"https://{request.tenant.domain}"

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {"SITE": {"URL": "tenants.site.tenant_origin", "NAME": "Notes"}}

Indexability
------------

``site_indexable(request)`` is the one answer every robots output reads, and ``INDEXABLE`` takes three forms.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Value
     - Indexable when
   * - ``"auto"``
     - ``DEBUG`` is off.
       The host is not compared, because a production site silently closed behind a proxy that rewrites the host costs more than an indexed staging host.
   * - ``True`` or ``False``
     - Always, or never.
       ``manage.py check --deploy`` warns about ``False`` while the site still publishes a sitemap or a robots source.
   * - A callable
     - It answers true for the request, which is ``None`` in a static context such as a system check.

A callable is the recipe for preview deployments that share the production settings.

.. code-block:: python
   :caption: config/site.py

   from django.http import HttpRequest

   PUBLIC_HOSTS = frozenset({"notes.example", "www.notes.example"})

   def indexable(request: HttpRequest | None) -> bool:
       return request is None or request.get_host() in PUBLIC_HOSTS

.. code-block:: python
   :caption: config/settings.py

   from config.site import indexable

   NEXT_FRAMEWORK = {"SITE": {"URL": "https://notes.example", "INDEXABLE": indexable}}

A request for ``https://pr-42.preview.notes.example/`` renders ``noindex, nofollow`` on every page, while the production host renders the robots directives the pages declare.

A site closed to search
-----------------------

A site that is not indexable for a request answers that request as follows.

- Every page renders ``<meta name="robots" content="noindex, nofollow">`` in place of the robots directives its metadata declares.
- Every page response, zone response, and SEO route carries ``X-Robots-Tag: noindex, nofollow``.
- ``/robots.txt`` answers ``User-agent: *`` with an empty ``Disallow:``, ignoring the declared rules and the ``Sitemap:`` line, and it answers 404 when no robots source exists.
- ``/sitemap.xml`` answers 404.

The robots file never answers ``Disallow: /``, because a crawler kept out of a page never reads the ``noindex`` it carries, and a URL linked from elsewhere stays in the index under a bare title.
The site rule overrides the page, so a page cannot opt back into the index on a closed host.

A site that only ``DEBUG`` closes, under the ``"auto"`` rule, still serves ``/sitemap.xml`` and its own ``/robots.txt`` for a preview, both under ``X-Robots-Tag: noindex, nofollow``.
An explicit ``False`` or a callable answering false serves the closed documents above.

Responses the framework does not build, the admin, an API view, or the media files, get the same header from ``RobotsHeaderMiddleware``.

.. code-block:: python
   :caption: config/settings.py

   MIDDLEWARE = [
       "django.middleware.security.SecurityMiddleware",
       "next.site.middleware.RobotsHeaderMiddleware",
       "django.contrib.sessions.middleware.SessionMiddleware",
       "django.middleware.common.CommonMiddleware",
   ]

The middleware stamps the header only while the site is closed for the request, so production responses carry none.
The form dispatch and the streams take the header from it as well.

What the checks read
--------------------

The system checks run without a request, so a callable receives ``None`` and ``"auto"`` reads ``DEBUG`` at check time.
On a site closed at check time the checks treat every page as ``noindex`` and skip the sitemap checks, since no sitemap is served.

See also
--------

.. seealso::

   :doc:`social-and-canonical` for the page-level robots directives.
   :doc:`social-and-canonical` for the page-level ``X-Robots-Tag``.
   :doc:`robots` for the robots file on an open site.
   :doc:`/content/ref/site` for ``site_url``, ``site_indexable``, and the middleware.
   :doc:`/content/ref/settings` for the ``SITE`` defaults.
