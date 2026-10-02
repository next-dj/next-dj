.. _topics-seo:

SEO
===

A page declares its metadata next to its body, in ``page.py``, and one ``{% metadata %}`` tag in the root layout renders the whole head.
``NEXT_FRAMEWORK["SITE"]`` names the public origin of the site and whether search engines may index it, and every head tag, response header, sitemap, and robots file reads that one answer.
A ``sitemap.py`` and a ``robots.py`` at the top of the page root serve the crawler documents from the same tree.
Metadata belongs to the pages the file router serves, and a plain Django view renders none.

.. rubric:: Start here

:doc:`quickstart`
   The settings, the head tag, a title per page, a sitemap, and a robots file, in five minutes.

.. rubric:: Declaring

:doc:`metadata`
   The ``metadata`` dict, the ``@page.metadata`` callable, the title template, the ``{% metadata %}`` tag, and the ``meta`` patch verb.

:doc:`merge`
   The merge rule of every key, ``None`` as unset, and ``RESET`` to drop an inherited value.

:doc:`site`
   The ``SITE`` scope, the origin every absolute URL is built on, and the indexability rule for production, staging, and preview hosts.

.. rubric:: Rendering

:doc:`social-and-canonical`
   Absolute URLs, the canonical link, robots directives and their header, hreflang alternates, Open Graph, and Twitter cards.

:doc:`head-tags`
   Viewport, theme color, color scheme, keywords, verification tokens, feeds, free-form links, and the ``other`` and ``properties`` escape hatches.

:doc:`structured-data`
   JSON-LD as plain mappings or ``next.pages.ld`` nodes, the single ``@graph``, and the ``@id`` rules.

:doc:`breadcrumbs`
   The ``breadcrumb`` key, the ``{% breadcrumbs %}`` tag, and the ``BreadcrumbList`` the graph gains.

:doc:`icons-and-images`
   The ``icons`` key, the social card images, and a card drawn per page.

.. rubric:: Crawlers

:doc:`sitemaps`
   The ``sitemap.py`` convention, static and dynamic routes, lazy items, and the module attributes.

:doc:`sitemap-sections`
   Sections, the index, sitemap backends, the origin of every URL, caching, and mounting at the host root.

:doc:`robots`
   ``robots.py`` with static or per-request rules, blocking AI crawlers, the static ``robots.txt``, and a site closed to search.

.. rubric:: Checking

:doc:`auditing`
   The system checks, ``showmetadata``, and the test helpers.

.. toctree::
   :hidden:
   :maxdepth: 1

   quickstart
   metadata
   merge
   site
   social-and-canonical
   head-tags
   structured-data
   breadcrumbs
   icons-and-images
   sitemaps
   sitemap-sections
   robots
   auditing

.. seealso::

   :doc:`/content/topics/caching` for ``cache`` and ``headers`` in ``page.py`` and the shared-cache rules.
   :doc:`/content/howto/audit-seo-before-deploy` for the checks and the tests in CI.
   :doc:`/content/ref/metadata` for the metadata value objects and the renderer contract, and :doc:`/content/ref/seo` for the sitemap and robots API.
   :repo:`markdown-blog <tree/main/examples/markdown-blog>` for ``DEFAULTS`` and a feed, :repo:`wiki <tree/main/examples/wiki>` for a title read off a resolved row and a database-fed sitemap, and :repo:`search-catalog <tree/main/examples/search-catalog>` for a canonical with ``CANONICAL_QUERY`` and the ``meta`` verb.
