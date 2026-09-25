.. _topics-robots:

Robots
======

``/robots.txt`` has two sources, a ``robots.py`` that declares its groups in Python and a static ``robots.txt`` served as written.
Both sit at the top of a page root, beside the ``sitemap.py`` of :doc:`sitemaps`, and a site has exactly one of them.
This page covers the two forms, the ``Sitemap:`` line, the one-source rule, and why a ``noindex`` page never becomes a ``Disallow``.

.. contents::
   :local:
   :depth: 2

The declared form
-----------------

``robots.py`` declares ``rules``, a list of ``Rule`` groups, and an optional ``host``.
Each ``Rule`` names one user agent or several, the paths it allows and disallows, and a crawl delay.
A bare string counts as one value for ``user_agent``, ``allow``, and ``disallow`` alike, so ``disallow="/private/"`` disallows that one path.

.. code-block:: python
   :caption: notes/pages/robots.py

   from next.seo import Rule

   rules = [
       Rule(user_agent="*", disallow=["/private/", "/search/"]),
       Rule(user_agent=["Googlebot", "Bingbot"], allow=["/"], crawl_delay=5),
   ]
   host = "notes.example"

The view renders one block per group, the ``User-agent`` lines first, then ``Allow``, ``Disallow``, and ``Crawl-delay``, with a blank line between groups.
``Host:`` and ``Sitemap:`` follow as a trailing block.
The response is ``text/plain; charset=utf-8``.

.. code-block:: text
   :caption: GET /robots.txt

   User-agent: *
   Disallow: /private/
   Disallow: /search/

   User-agent: Googlebot
   User-agent: Bingbot
   Allow: /
   Crawl-delay: 5

   Host: notes.example
   Sitemap: https://notes.example/sitemap.xml

An empty ``robots.py`` declares allow-all, ``User-agent: *`` and ``Allow: /``, plus the ``Sitemap:`` line when a sitemap exists, which is the shortest way to point a crawler at the document.
A ``rules`` value that is not a list of ``Rule`` reads as none and ``next.E113`` reports it, as it does a ``host`` that is not a string.
Like ``sitemap.py``, the file is loaded once when the framework discovers the tree and kept until the SEO routes reset, and the development server restarts on an edit.

The Sitemap line
----------------

The ``Sitemap:`` line is written while the project serves a sitemap, and left out when no page root declares a ``sitemap.py`` or ``NOINDEX`` keeps the sitemap unserved, see :doc:`/content/ref/settings`.
Its URL is absolute, ``base`` from ``NEXT_FRAMEWORK["METADATA"]["DEFAULTS"]`` followed by the path of the sitemap route, and without a base the origin of the request.
The path is reversed in the namespace the request reached the robots route through, so a robots served from ``include("next.seo.urls")`` names a sitemap under the same include.

The static form
---------------

A ``robots.txt`` at the top of the page root is served byte for byte as ``text/plain; charset=utf-8``.
The file is re-read when its mtime moves and answers 404 when it is gone.

.. code-block:: text
   :caption: notes/pages/robots.txt

   User-agent: *
   Allow: /

   Sitemap: https://notes.example/sitemap.xml

Nothing is appended to a static file, so the ``Sitemap:`` line is written by hand with the absolute URL, and ``NOINDEX`` leaves it in place.
``next.W103`` reports a static file that names no sitemap while the project serves one, and ``next.E117`` a file that does not decode as UTF-8.
The static form suits a project whose robots never changes, and the declared form a project that wants the sitemap URL to follow ``base``.

One source per site
-------------------

``/robots.txt`` is one address, so the site has one source for it.
A ``robots.py`` and a ``robots.txt`` in one root, or a robots file of either form in two roots, is ``next.E114``, and the message lists every path.
At runtime the first source in router order answers, ``robots.py`` ahead of ``robots.txt`` within a root, and a warning names the ones ignored.

The route ``next:robots`` exists only while a source does, like the sitemap routes, so a project without a robots file keeps whatever ``path("robots.txt", ...)`` it mounts after ``include("next.urls")``.
A page directory named ``robots.txt`` or a urlpattern ahead of the include on the same address is ``next.E115``.
A router under a prefix or inside :func:`~django.conf.urls.i18n.i18n_patterns` takes the route away from the host root, and :ref:`topics-seo-host-root` brings it back through ``next.seo.urls``.

No Disallow from noindex
------------------------

A page that declares ``"robots": {"index": False}`` in its metadata is not added to ``Disallow`` by either form.
The two directives do different jobs.
``Disallow`` stops the fetch, and a crawler that never fetches the page never sees the ``noindex`` tag inside it, so a page linked from elsewhere stays in the index under its URL.
``noindex`` needs the fetch and then removes the page.
A private area therefore keeps its ``noindex`` and stays crawlable, and ``next.W101`` reports a ``Disallow`` that covers a ``noindex`` page.
``next.W100`` reports the opposite mistake, a ``Disallow`` covering a URL the sitemap lists or the sitemap address itself.
Both checks read a ``Disallow`` value the way a crawler does, as a path prefix with ``*`` matching anything and a trailing ``$`` anchoring the end.

See also
--------

.. seealso::

   :doc:`sitemaps` for the document the ``Sitemap:`` line points at.
   :doc:`social-and-canonical` for the ``robots`` metadata block and the ``NOINDEX`` setting.
   :doc:`/content/howto/publish-a-sitemap` for the recipe that adds both files.
   :doc:`/content/ref/seo` for ``Rule`` and the ``next.seo.urls`` include.
   :repo:`wiki <tree/main/examples/wiki#12-a-sitemap-fed-by-the-database>` for a ``robots.py`` that closes the search beside a database-fed sitemap, :repo:`shortener <tree/main/examples/shortener#14-robotspy-at-the-page-root>` for a one-line ``robots.py`` without a sitemap, and :repo:`markdown-blog <tree/main/examples/markdown-blog#13-sitemap-and-robots-from-the-page-root>` for the static file and the root-level include.
