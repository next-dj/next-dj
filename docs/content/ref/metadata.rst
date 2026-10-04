.. _ref-metadata:

Metadata reference
==================

Module summary
--------------

``next.pages.metadata`` holds the page metadata of :doc:`/content/topics/seo/metadata`, the input dicts a ``page.py`` and the settings declare, the frozen value objects the merge produces, the ``Replace`` and ``RESET`` markers, the resolve stage, the renderer contract, and the JSON-LD nodes of ``next.pages.metadata.ld``.
``next.pages`` re-exports the names a page module uses most, ``MetadataDict``, ``ResolvedMetadata``, ``Replace``, ``RESET``, ``MetadataRenderer``, ``HtmlMetadataRenderer``, and ``ld``, while ``Metadata`` and ``SiteMetadataDict`` are imported from ``next.pages.metadata``.
Everything else of the package, the ``chain``, ``fold``, ``registry``, and ``normalize`` modules among them, is internal, see :doc:`/content/internals/seo-pipeline`.

Keys and tags
-------------

The table names the tag each key emits, in the order ``HtmlMetadataRenderer`` writes them, and the merge rule of each key is in :doc:`/content/topics/seo/merge`.

.. list-table::
   :header-rows: 1
   :widths: 22 78

   * - Key
     - Emitted markup
   * - ``title``
     - ``<title>``, the title template in force applied.
   * - ``viewport``
     - ``<meta name="viewport">``, a dict joined into one ``content``.
   * - ``theme_color``
     - One ``<meta name="theme-color">`` per color, with its ``media``.
   * - ``color_scheme``
     - ``<meta name="color-scheme">``.
   * - ``description``
     - ``<meta name="description">``.
   * - ``keywords``
     - One ``<meta name="keywords">``, the texts joined by commas.
   * - ``robots``
     - ``<meta name="robots">`` and, for a ``googlebot`` entry, ``<meta name="googlebot">``.
       A site closed to search replaces both with ``noindex, nofollow``.
   * - ``canonical``
     - ``<link rel="canonical">``, the self URL for ``True`` and the declared URL for a string, made absolute.
   * - ``alternates``
     - One ``<link rel="alternate" hreflang="...">`` per language with ``x-default`` last, and one ``<link rel="alternate" type="...">`` per feed.
   * - ``icons``
     - One ``<link>`` per icon, ``icon``, then ``apple-touch-icon``, then the other rels, with ``type``, ``sizes``, ``media``, and ``color``.
   * - ``manifest``
     - ``<link rel="manifest">``.
   * - ``links``
     - One ``<link>`` per entry with its attributes.
   * - ``verification``
     - ``google-site-verification``, ``yandex-verification``, ``msvalidate.01``, ``p:domain_verify``, and ``facebook-domain-verification`` metas per token, then the ``other`` names.
   * - ``other``
     - One ``<meta name="...">`` per name and text.
   * - ``og``
     - ``<meta property="og:*">``, each image, video, and audio track followed by its details, and the ``article:*``, ``profile:*``, and ``book:*`` properties.
   * - ``properties``
     - One ``<meta property="...">`` per name and text.
   * - ``twitter``
     - ``<meta name="twitter:*">`` for the fields the block names, each image with its ``twitter:image:alt``.
   * - ``jsonld``
     - One ``<script type="application/ld+json">`` holding the ``@graph``, and one more per node under a foreign ``@context``.
   * - ``site_name``
     - No tag of its own, it fills ``{site_name}`` and ``og:site_name``.
   * - ``breadcrumb``
     - No head tag, it labels the crumb of ``{% breadcrumbs %}`` and the ``BreadcrumbList`` node.

Input dicts
-----------

``MetadataDict`` is what a ``page.py`` declares and a ``@page.metadata`` callable returns, and ``SiteMetadataDict`` is what ``DEFAULTS`` declares, the same keys less ``breadcrumb`` and with the title limited to ``template`` and ``default``.
Every value of a nested block may be wrapped in ``Replace``, ``Text`` is a string or a lazy translation, and ``Url`` is a string or a lazy URL from :func:`~django.urls.reverse_lazy` or ``page_reverse_lazy``.
A lazy URL stays unforced in the merge and is forced and checked for its scheme on every render, so a static ``metadata`` dict names a route before the URLconf loads.
A forced URL on a scheme outside http and https leaves out the tag carrying it, logged at most once every ten minutes, and a whole hreflang set when it is one of the alternates.
``RobotsDict`` takes the directives of ``GooglebotDict`` and a ``googlebot`` key, a string or a ``GooglebotDict``, so the Googlebot directives nest one level and no deeper.

.. automodule:: next.pages.metadata.dicts
   :members:
   :exclude-members: type

Markers and value objects
-------------------------

``Replace(value)`` takes a value whole instead of merging it, and ``RESET`` is ``Replace()``, which drops the inherited value.
``Metadata`` is the merged metadata of one page without a request, and ``ResolvedMetadata`` the resolve of one response, every URL absolute, the site rule applied to the robots, and ``og`` carrying its fallbacks.
``Alternates.languages`` holds the hreflang alternates as a tuple of ``(code, url)`` pairs, so the value hashes like the rest of the dataclass.
``Crumb`` is one breadcrumb of the merge, with the route it names, and ``Breadcrumb`` one resolved crumb with its ``label``, ``url``, and ``current`` flag.
``ResolvedMetadata.crumbs`` holds the crumbs of the page unreversed, and its ``breadcrumbs`` property reverses their URLs on the first read, so a render that never shows a crumb never reverses one.

.. automodule:: next.pages.metadata.markers
   :members: Replace, RESET, Metadata, ResolvedMetadata, Robots, OpenGraph, OpenGraphImage, OpenGraphVideo, OpenGraphAudio, Article, Profile, Book, Twitter, TwitterImage, TwitterPlayer, Alternates, Feed, Verification, Icon, Link, Viewport, ThemeColor, Crumb, Breadcrumb
   :exclude-members: type

Resolve and predicates
----------------------

``resolve_metadata(meta, request=...)`` turns a ``Metadata`` into the ``ResolvedMetadata`` of one response, and ``absolute_url(url, request=...)`` makes one URL absolute on ``site_origin(request)`` of :doc:`site`, the site URL, then the current ``Site`` row, then the request host, the same origin the sitemap and the robots file write on.
``noindexed(meta, request=...)`` answers whether a page stays out of the index, by the site rule or its own robots, the one predicate the sitemap, the checks, and the robots header share.

.. autofunction:: next.pages.metadata.resolve_metadata

.. autofunction:: next.pages.metadata.absolute_url

.. autofunction:: next.pages.metadata.noindexed

Renderer
--------

The renderer is the last stage of the metadata, and it receives the ``ResolvedMetadata`` alone, so a custom one cannot lose a ``noindex`` the site rule set or undo any other policy the resolve stage applied.
It answers the head markup as a ``SafeString``.
``HtmlMetadataRenderer`` renders one section per name in ``sections`` through the ``render_<name>`` hook, so a subclass reorders or extends ``sections`` and overrides single hooks.

.. code-block:: python
   :caption: site/metadata.py

   from collections.abc import Iterable

   from django.utils.html import format_html
   from django.utils.safestring import SafeString

   from next.pages import HtmlMetadataRenderer, ResolvedMetadata

   class SiteRenderer(HtmlMetadataRenderer):
       sections = (*HtmlMetadataRenderer.sections, "generator")

       def render_generator(self, resolved: ResolvedMetadata) -> Iterable[SafeString]:
           return (format_html('<meta name="generator" content="{}">', "Acme CMS"),)

``NEXT_FRAMEWORK["METADATA"]["RENDERER"] = "site.metadata.SiteRenderer"`` installs it, and the framework builds it without arguments once and again on every ``settings_reloaded``.
A path that does not import, names no concrete subclass, or a class whose constructor raises leaves the page on ``HtmlMetadataRenderer``, logged at most once every ten minutes, and ``next.E107`` reports the path before the first render.
Under ``DEBUG`` or ``STRICT_LOADING`` the render raises instead.
A ``render`` that raises leaves the head empty, logged at most once every ten minutes per renderer class and exception type, and the response is sent with ``Cache-Control: private, no-store``.
Under ``DEBUG`` or ``STRICT_LOADING`` it raises with a note naming the renderer.

.. automodule:: next.pages.metadata.backends
   :members: MetadataRenderer, HtmlMetadataRenderer

Structured data
---------------

``next.pages.ld`` is ``next.pages.metadata.ld``, the ``Node`` base, ``Ref``, and the ``BreadcrumbList`` of :doc:`/content/topics/seo/structured-data`.

.. automodule:: next.pages.metadata.ld
   :members:
   :exclude-members: type

Page accessors
--------------

``Page.metadata`` is the decorator behind ``@page.metadata``, with ``inherit=True`` running the callable for the descendants too.
``Page.static_metadata(path)`` returns a ``StaticMetadata`` pair, the ``Metadata`` readable without a request, ``DEFAULTS`` and every ``metadata`` dict of the page and its ancestors, and whether the schema refused that chain.
A refused chain reads as ``DEFAULTS`` under ``noindex`` with the other robots directives of ``DEFAULTS`` kept, the pair reads as refused when ``DEFAULTS`` itself is refused, and the refusal raises under ``DEBUG`` or ``STRICT_LOADING`` and is logged at most once every ten minutes otherwise.
The metadata a request resolves is read from a rendered response, through ``assert_metadata`` of :doc:`testing`.
``metadata_declaration``, ``metadata_chain``, ``metadata_registrations``, and ``fold_metadata`` serve the checks, ``showmetadata``, and the ``meta`` patch verb as part of the cross-area contract of :doc:`pages`.

Errors
------

The metadata errors name the source that misbehaved and are exported from ``next.pages``.
``PageMetadataShapeError`` is a key or a value the schema refuses, with the key path, ``PageMetadataConflictError`` a ``page.py`` declaring both a dict and a callable or two callables, and ``PageMetadataTemplateError`` a title template the safe substitution rejects.
``PageMetadataRequestError`` is a self canonical or ``"languages": True`` resolved without a request, its ``key`` naming the key that asked.
A relative URL with neither a request nor a site URL to resolve against raises ``SiteOriginError`` of :doc:`site`, the one origin error the metadata and the sitemap share.

.. autoclass:: next.pages.PageMetadataShapeError
   :members:

.. autoclass:: next.pages.PageMetadataConflictError
   :members:

.. autoclass:: next.pages.PageMetadataTemplateError
   :members:

.. autoclass:: next.pages.PageMetadataRequestError
   :members:

See also
--------

.. seealso::

   :doc:`/content/topics/seo/index` for the topic guides.
   :doc:`settings` for the ``METADATA`` and ``SITE`` scopes.
   :doc:`template-tags` for ``{% metadata %}`` and ``{% breadcrumbs %}``.
