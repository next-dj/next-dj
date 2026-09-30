.. _topics-seo-head-tags:

Head tags
=========

Beyond the title and the social blocks, the head carries the viewport, the browser colors, keywords, ownership tokens, feeds, resource hints, and the odd vendor meta.
Each has a key of its own, so it merges along the tree and is checked on its own, and two escape hatches, ``other`` and ``properties``, cover the names the schema does not know.
This page covers each key and the tag it emits.

.. contents::
   :local:
   :depth: 2

Viewport and colors
-------------------

``viewport`` is a ready string or a dict of ``width``, ``height``, ``initial_scale``, ``minimum_scale``, ``maximum_scale``, ``user_scalable``, ``viewport_fit``, and ``interactive_widget``, joined into one ``content``.
``theme_color`` is one color or a list of ``{"color": ..., "media": ...}`` dicts, one meta per color, and ``color_scheme`` a string of the ``normal``, ``light``, ``dark``, and ``only`` tokens.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "METADATA": {
           "DEFAULTS": {
               "viewport": {"width": "device-width", "initial_scale": 1, "viewport_fit": "cover"},
               "theme_color": [
                   {"color": "#ffffff", "media": "(prefers-color-scheme: light)"},
                   {"color": "#0b1120", "media": "(prefers-color-scheme: dark)"},
               ],
               "color_scheme": "light dark",
           },
       },
   }

``DEFAULTS`` is the place for these, because every page shares them.
``manage.py check`` reports a scale outside 0.1 to 10, a viewport that keeps the page from zooming, and a layout that still writes a literal viewport or theme-color meta beside the key.

Keywords and verification
-------------------------

``keywords`` is one text or a list of texts, rendered as one ``<meta name="keywords">`` joined by commas.
``verification`` holds the ownership tokens of ``google``, ``yandex``, ``bing``, ``pinterest``, and ``facebook``, each a string or a list, rendered as ``google-site-verification``, ``yandex-verification``, ``msvalidate.01``, ``p:domain_verify``, and ``facebook-domain-verification``.
Its ``other`` mapping names the meta of an engine without a key of its own.

.. code-block:: python
   :caption: notes/pages/page.py

   metadata = {
       "keywords": ["notes", "markdown", "notebook"],
       "verification": {"google": "abc123", "other": {"baidu-site-verification": "xyz"}},
   }

Feeds
-----

``alternates.feeds`` lists the RSS, Atom, or JSON feeds of a page, each a dict of ``url``, ``type``, and an optional ``title``.
The ``type`` is ``rss``, ``atom``, ``json``, or a media type, and the short names expand to ``application/rss+xml``, ``application/atom+xml``, and ``application/feed+json``.

.. code-block:: python
   :caption: blog/pages/page.py

   metadata = {"alternates": {"feeds": [{"url": "/feed.xml", "type": "rss", "title": "Blog"}]}}

The page renders ``<link rel="alternate" type="application/rss+xml" title="Blog" href="https://blog.example/feed.xml">``.

Links
-----

``links`` is the list of free-form ``<link>`` tags, each a dict with ``rel`` and ``href`` and any of ``as``, ``type``, ``media``, ``sizes``, ``crossorigin``, ``hreflang``, ``title``, ``fetchpriority``, ``imagesrcset``, ``imagesizes``, ``referrerpolicy``, ``integrity``, and ``blocking``.
It carries the resource hints and the relations the schema has no key for.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "METADATA": {
           "DEFAULTS": {
               "links": [
                   {"rel": "preconnect", "href": "https://fonts.gstatic.com", "crossorigin": True},
                   {"rel": "preload", "href": "/static/site/inter.woff2", "as": "font", "crossorigin": True},
               ],
           },
       },
   }

The ``href`` of a ``preconnect`` or ``dns-prefetch`` is an origin and stays as written, every other ``href`` is made absolute, and ``crossorigin=True`` renders ``crossorigin="anonymous"``.
A rel that another key renders, ``canonical``, ``alternate``, ``icon``, ``apple-touch-icon``, ``mask-icon``, ``manifest``, or ``stylesheet``, is refused, and the check message names the key to use instead.
A resource hint whose ``href`` is not an origin, a ``preload`` without ``as``, and a rel no browser knows are reported too.

Other names and properties
--------------------------

``other`` maps a meta ``name`` to a text or a list of texts, and every text renders as its own ``<meta name="..." content="...">``.
``properties`` does the same for ``<meta property="...">``, for vocabularies such as ``fb:app_id`` or ``product:price:amount``.
Both merge by name along the tree, so a page that sets one name replaces every value of that name and keeps the rest, and ``RESET`` under a name drops it.

.. code-block:: python
   :caption: shop/pages/products/page.py

   metadata = {
       "other": {"format-detection": "telephone=no"},
       "properties": {"fb:app_id": "123456"},
   }

A name a typed key renders, ``description``, ``keywords``, ``robots``, ``viewport``, ``theme-color``, a verification meta, or a ``twitter:*`` name, is refused in ``other``, and so is an ``og:*``, ``article:*``, ``profile:*``, or ``book:*`` property in ``properties``.
The typed key keeps the value checked, merged by its strategy, and rendered once.

Render order
------------

The tags render in a fixed order, the title first and the JSON-LD graph last.
The order is title, viewport, theme color, color scheme, description, keywords, robots and googlebot, canonical, hreflang alternates, feeds, icons, manifest, links, verification, ``other``, Open Graph, ``properties``, Twitter, and JSON-LD.
A subclass of ``HtmlMetadataRenderer`` reorders or extends ``sections``, see :doc:`/content/ref/metadata`.

See also
--------

.. seealso::

   :doc:`social-and-canonical` for the canonical, robots, hreflang, and social keys.
   :doc:`icons-and-images` for ``icons`` and ``manifest``.
   :doc:`merge` for the merge strategy of each key.
