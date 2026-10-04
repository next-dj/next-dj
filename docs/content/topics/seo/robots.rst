.. _topics-robots:

Robots
======

``/robots.txt`` has two sources, a ``robots.py`` that declares its groups in Python and a static ``robots.txt`` served as written.
Both sit at the top of a page root, beside the ``sitemap.py`` of :doc:`sitemaps`, and a site has one source.
This page covers the declared and the static forms, blocking AI crawlers, and why a ``noindex`` page never becomes a ``Disallow``.

.. contents::
   :local:
   :depth: 2

The declared form
-----------------

``robots.py`` declares ``rules``, a list of ``RobotsRule`` groups or a callable answering one, and optionally ``sitemaps`` and ``cache``.
Each ``RobotsRule`` names one user agent or several, the paths it allows and disallows, and a crawl delay, and a bare string counts as one value.

.. code-block:: python
   :caption: notes/pages/robots.py

   from next.seo import RobotsRule

   cache = 3600
   sitemaps = ["https://cdn.notes.example/sitemap-news.xml"]

   rules = [
       RobotsRule(disallow=["/search/", "/account/"]),
       RobotsRule(user_agent=("GPTBot", "ClaudeBot"), disallow="/"),
   ]

The view renders one block per group, the ``User-agent`` lines first, then ``Allow``, ``Disallow``, and ``Crawl-delay``, with a blank line between groups, and the ``Sitemap:`` lines last.
A group with neither ``allow`` nor ``disallow`` writes an empty ``Disallow:``, which allows everything explicitly, and an empty ``robots.py`` answers ``User-agent: *`` with an empty ``Disallow:``.
The response is ``text/plain; charset=utf-8``.

.. code-block:: text
   :caption: GET /robots.txt

   User-agent: *
   Disallow: /search/
   Disallow: /account/

   User-agent: GPTBot
   User-agent: ClaudeBot
   Disallow: /

   Sitemap: https://notes.example/sitemap.xml
   Sitemap: https://cdn.notes.example/sitemap-news.xml

``RobotsRule`` validates on construction and raises ``RobotsRuleError`` for an agent that is no token, a path that does not start with ``/`` or ``*`` or carries a space, a control character, or a ``$`` before its end, and a crawl delay that is no finite number of seconds.
A rule that raises at import keeps the route answering 503 with ``Retry-After`` and ``manage.py check`` reports the reason, as it reports a ``rules``, ``sitemaps``, or ``cache`` of the wrong shape.

Rules per request
-----------------

``rules`` may be a callable, resolved through dependency injection with the request on every uncached response, for rules that depend on the host.

.. code-block:: python
   :caption: docs/pages/robots.py

   from django.http import HttpRequest

   from next.seo import RobotsRule

   def rules(request: HttpRequest) -> list[RobotsRule]:
       groups = [RobotsRule(disallow="/search/")]
       if request.get_host().startswith("beta."):
           groups.append(RobotsRule(disallow="/"))
       return groups

``robots.py`` is read by the dependency resolver, so it keeps ``from __future__ import annotations`` out.
A ``rules`` callable that raises answers 503 with ``Retry-After`` and logs the failure once, and under ``DEBUG`` or ``STRICT_LOADING`` the exception reaches the technical page with a note naming the callable.
The checks read a static ``rules`` list alone, so a callable's groups are never compared against the sitemap.

The Sitemap lines
-----------------

The project's own ``Sitemap:`` line is written while the project serves a sitemap, and the ``sitemaps`` the file lists follow it.
Its URL is absolute, ``SITE["URL"]`` followed by the path of the sitemap route, and without a site URL the origin of the request.
The path is reversed in the namespace the request reached the robots route through, so a robots served from ``include("next.seo.urls")`` names a sitemap under the same include.
A listed URL is an absolute ``http`` or ``https`` URL on one line.

Blocking AI crawlers
--------------------

AI vendors name their crawlers by what they fetch for, training, search, or a fetch a user asked for, and each one reads its own ``User-agent`` group.
A project that keeps the training crawlers out while answer engines may still cite it lists the training agents in one rule.

.. code-block:: python
   :caption: notes/pages/robots.py

   from next.seo import RobotsRule

   AI_TRAINING = ("GPTBot", "ClaudeBot", "Google-Extended", "Applebot-Extended", "Meta-ExternalAgent", "CCBot")

   rules = [
       RobotsRule(disallow="/search/"),
       RobotsRule(user_agent=AI_TRAINING, disallow="/"),
   ]

Vendors add agents over time, so the tuple belongs to the project and follows the vendors' own documentation.

The static form
---------------

A ``robots.txt`` at the top of the page root is served byte for byte as ``text/plain; charset=utf-8``.
The file is re-read when its mtime moves and answers 404 once it is gone.
A read that fails answers the last good bytes, or a 503 with ``Retry-After`` when there are none, so a crawler reads the file as unreachable rather than as permission to crawl everything.

Nothing is appended to a static ``robots.txt``, so its ``Sitemap:`` line is written by hand, and ``manage.py check`` reports one missing while the project serves a sitemap, and a file that does not decode as UTF-8.

One source per address
----------------------

``/robots.txt`` is one address, so the site has one source for it.
A ``robots.py`` and a ``robots.txt`` in one root, or a source in two roots, is an error of ``manage.py check``.
At runtime the first source in router order answers, ``robots.py`` ahead of ``robots.txt`` within a root, and under ``DEBUG`` a warning names the ones ignored, once.
A ``robots.py`` that fails to import keeps the route and answers 503 with ``Retry-After`` rather than falling back to another source.
A 404 would be wrong here, since RFC 9309 reads a 4xx on ``/robots.txt`` as no restriction at all, which would open a staging host to every crawler.
RFC 9309 reads a 5xx as an unreachable file, which a crawler answers by crawling nothing or by keeping the rules it cached before.

A closed site
-------------

On a site closed to search, ``/robots.txt`` answers ``User-agent: *`` with an empty ``Disallow:`` and no ``Sitemap:`` line, whatever the sources declare, see :doc:`site`.
The crawler may then fetch every page and read its ``noindex``.
A site only ``DEBUG`` closes serves its own robots under ``X-Robots-Tag: noindex, nofollow`` instead, so the file can be read during development.

No Disallow from noindex
------------------------

A page that declares ``"robots": {"index": False}`` is not added to ``Disallow`` by either form.
``Disallow`` stops the fetch, and a crawler that never fetches the page never sees the ``noindex`` inside it, so a page linked from elsewhere stays in the index under its URL.
A private area therefore keeps its ``noindex`` and stays crawlable, and ``manage.py check`` reports a ``Disallow`` that covers a ``noindex`` page.
It also reports the opposite mistake, a ``Disallow`` covering a URL the sitemap lists or the sitemap address itself.
Both checks read only the groups that name ``*``, since a crawler named in a group of its own follows that group alone, so a group that keeps AI crawlers out of the whole site warns about nothing.

See also
--------

.. seealso::

   :doc:`sitemaps` for the document the ``Sitemap:`` line points at.
   :doc:`quickstart` for the first robots file of a site.
   :doc:`/content/ref/seo` for ``RobotsRule`` and the ``next.seo.urls`` include.
   :repo:`wiki <tree/main/examples/wiki>` for ``rules`` per request.
