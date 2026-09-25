.. _topics-seo:

SEO
===

A page declares its metadata next to its body, in ``page.py``, and one ``{% metadata %}`` tag in the root layout renders the whole head.
A ``sitemap.py`` and a ``robots.py`` at the top of the page root serve ``/sitemap.xml`` and ``/robots.txt`` from the same tree.
The section covers the two declaration forms and their merge along the page tree, the social and canonical tags the head carries, the sitemap and the robots file, and the system checks that audit the result before a deploy.

.. rubric:: Declaring

:doc:`metadata`
   The ``metadata`` dict, the ``@page.metadata`` callable, the title template, the merge along the tree, and the ``meta`` patch verb.

.. rubric:: Rendering

:doc:`social-and-canonical`
   Open Graph, Twitter cards, the canonical link, robots directives, hreflang alternates, JSON-LD, verification tokens, and free-form ``other`` tags.

.. rubric:: Crawlers

:doc:`sitemaps`
   The ``sitemap.py`` convention, static and dynamic routes, the module attributes, the index, the ``base`` origin, and caching.

:doc:`robots`
   The declared and the static ``/robots.txt``, the ``Sitemap:`` line, the one-source rule, and mounting at the host root.

.. rubric:: Auditing

:doc:`auditing`
   The checks a plain ``manage.py check`` runs, the opt-in content audits behind ``--deploy --tag seo``, and the thresholds behind them.

.. toctree::
   :hidden:
   :maxdepth: 1

   metadata
   social-and-canonical
   sitemaps
   robots
   auditing

.. seealso::

   :doc:`/content/howto/set-page-titles-and-seo-tags` for the recipe that wires a site up from scratch, and :doc:`/content/howto/publish-a-sitemap` for the sitemap and robots files.
   :doc:`/content/ref/pages` for the ``Metadata`` value object and the renderer contract, and :doc:`/content/ref/seo` for the sitemap and robots API.
   :repo:`markdown-blog <tree/main/examples/markdown-blog>` for the settings tier, hreflang alternates, and a per-post callable, :repo:`wiki <tree/main/examples/wiki>` for a title read off a resolved row and ``noindex`` on the editing pages, and :repo:`search-catalog <tree/main/examples/search-catalog>` for a canonical with ``CANONICAL_QUERY`` and the ``meta`` verb.
