.. _topics-seo-sitemap-sections:

Sitemap sections and backends
=============================

``/sitemap.xml`` is one document while the site has one section that fits in a page, and an index of every section otherwise.
The sections come from sitemap backends, the page trees by default and any source a project adds, such as a CMS table.
This page covers sections, the index, backends, the origin of every URL, caching, and mounting the routes at the host root.

.. contents::
   :local:
   :depth: 2

Sections
--------

Each page root with a ``sitemap.py`` is one section, served at ``/sitemap-<section>.xml``.
The name is the ``section`` attribute of the file when it declares one, else the label of the innermost installed application whose directory holds the root, else the slugified directory name.
Two trees that would take one name each declare a ``section`` of their own, and the checks warn until they do.

``@sitemap.items(..., section="products")`` moves the URLs of one route into a section of their own, so a large catalog pages separately from the static pages.

.. code-block:: python
   :caption: shop/pages/sitemap.py

   from shop.models import Product

   from next.seo import sitemap

   section = "shop"

   @sitemap.items("products/[slug]", kwargs=lambda product: {"slug": product.slug}, lastmod="updated_at", section="products")
   def products():
       return Product.objects.live().order_by("pk")

The tree serves ``/sitemap-shop.xml`` for its static pages and ``/sitemap-products.xml`` for the products.
Two sources serving one section name, two trees or a tree and a backend, list only the first, and ``manage.py check`` reports the collision.

The index
---------

With several sections, or a section whose URLs exceed its ``limit``, ``/sitemap.xml`` answers an index listing every section and every ``?p=N`` page of it.
The index lists the newest ``lastmod`` of each section, and both documents carry a ``Last-Modified`` header when every listed entry carries a ``lastmod``.
Every sitemap response carries ``X-Robots-Tag: noindex, noodp, noarchive``, so the XML itself never ranks.
A section reads its template from its ``template_name`` attribute, ``sitemap.xml`` by default, which is the seam for an image, video, or news sitemap template.

Backends
--------

``NEXT_FRAMEWORK["SEO"]["SITEMAP_BACKENDS"]`` lists the sources of sections, and the default is the page trees alone.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "SEO": {
           "SITEMAP_BACKENDS": [
               {"BACKEND": "next.seo.PageTreeSitemapBackend", "OPTIONS": {}},
               {"BACKEND": "cms.seo.CmsSitemapBackend", "OPTIONS": {}},
           ],
       },
   }

A backend subclasses ``SitemapBackend`` and answers fresh Django sitemaps keyed by section name.
``sections(request)`` receives ``None`` when a system check asks, and a section may be any :class:`django.contrib.sitemaps.Sitemap`, :class:`~django.contrib.sitemaps.GenericSitemap` included.

.. code-block:: python
   :caption: cms/seo.py

   from typing import Any

   from cms.models import CmsPage
   from django.contrib.sitemaps import GenericSitemap, Sitemap
   from django.http import HttpRequest

   from next.seo import SitemapBackend

   class CmsSitemapBackend(SitemapBackend):
       def sections(self, request: HttpRequest | None) -> dict[str, Sitemap[Any]]:
           pages = CmsPage.objects.live().order_by("pk")
           if request is not None:
               pages = pages.filter(site__hostname=request.get_host())
           return {"cms": GenericSitemap({"queryset": pages, "date_field": "updated_at"})}

``serves()`` tells the route whether the backend has anything, ``True`` by default, and ``cache_control()`` names the cache its responses ask for, ``None`` by default.
The sections of every backend merge in list order, the first holder of a name winning.
Each backend built sends ``sitemap_backend_loaded``.

The origin of every URL
-----------------------

A sitemap lists absolute URLs, so every location needs a scheme and a host.
The sitemap reads ``site_origin(request)`` of :doc:`site`, the one origin the canonical links and the robots file read as well.
``SITE["URL"]`` supplies both parts first.
Without it the host comes from :func:`~django.contrib.sites.shortcuts.get_current_site`, the ``Site`` row with ``django.contrib.sites`` installed and the request host without it.
The scheme is ``protocol`` when the file declares one, then the scheme of the site URL, then the scheme of the request, and a build with neither a request nor a site URL raises ``SiteOriginError`` from ``next.site``.

.. warning::

   Set ``SITE["URL"]`` on every deployment that installs ``django.contrib.sites``.
   The ``Site`` row ships as ``example.com`` and no check reads the table, so a sitemap of ``example.com`` URLs is caught by nothing before a crawler reads it.

Caching
-------

``cache`` in ``sitemap.py`` takes the forms ``cache`` takes in a ``page.py``, an int, ``False``, or a ``CacheDict``, and the sitemap responses carry that ``Cache-Control``.
When the cache lets a copy be kept for a number of seconds, the views are also wrapped in :func:`~django.views.decorators.cache.cache_page` on the default cache for that long, keyed on a fingerprint of the source files, so an edited ``sitemap.py`` never serves a stale copy.
The index and every section share the wrapper, and with several trees the shortest declared ``cache`` wins, one that forbids storing beating every age.
A 404 is never cached, and ``cache`` in ``robots.py`` works the same way for ``/robots.txt``.

The walk of the page tree is kept until the SEO routes reset, while the items callables run on every uncached request, so a large table pays one ``COUNT`` and one page query per crawler visit.
Under ``DEBUG`` every SEO route first checks the source files at the top of each page root, and one that appeared, went, or moved resets the routes, so an edit shows on the next request without a reload.

.. _topics-seo-host-root:

Mounting at the host root
-------------------------

Crawlers read ``/robots.txt`` and ``/sitemap.xml`` at the root of the host, and ``include("next.urls")`` mounts both on its own, so a router at the root of the URLconf needs nothing more.
A router under a prefix, or inside :func:`~django.conf.urls.i18n.i18n_patterns`, takes the routes with it, and ``manage.py check`` reports the addresses the host root no longer resolves.
``next.seo.urls`` then mounts the same routes at the root, under the ``next_seo`` namespace.

.. code-block:: python
   :caption: config/urls.py

   from django.conf.urls.i18n import i18n_patterns
   from django.urls import include, path

   urlpatterns = [
       path("", include("next.seo.urls")),
       *i18n_patterns(path("", include("next.urls")), prefix_default_language=False),
   ]

Both includes list only the routes whose source exists, so a project view mounted below keeps answering the addresses the framework does not serve.
The copies ``include("next.urls")`` mounts under a prefix answer 404 once ``next.seo.urls`` serves the host root, so each document has one address.
The views behind the routes are no public API, and ``next.seo.urls`` is the one way to mount them anywhere else.
Every SEO route answers ``GET`` and ``HEAD`` alone, and a miss answers a plain-text 404 rather than the project's 404 page, its body ``Not found`` unless ``DEBUG`` is on, which names the reason, a broken source file among them.
A page directory named after a served address, or a urlpattern ahead of the include on it, shadows the route, and the checks report it.

See also
--------

.. seealso::

   :doc:`sitemaps` for the file and the items.
   :doc:`/content/ref/seo` for ``SitemapBackend`` and the routes.
   :doc:`/content/internals/seo-pipeline` for the discovery, the manager, and the resets.
