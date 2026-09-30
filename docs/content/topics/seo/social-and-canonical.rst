.. _topics-seo-social-and-canonical:

Social and canonical tags
=========================

The canonical link, the robots directives, the hreflang alternates, and the social blocks describe a page to crawlers and to the cards a link unfurls into.
This page covers how every URL becomes absolute, then each of these keys and the tags it emits.
:doc:`metadata` covers where the keys are declared, and :doc:`head-tags` the remaining keys.

.. contents::
   :local:
   :depth: 2

Absolute URLs
-------------

A canonical link, an hreflang alternate, and a social image are only useful as absolute URLs, so the framework makes every URL field absolute before it renders.
An ``http`` or ``https`` URL passes through, a root-relative path such as ``/notes/`` is joined to the site origin, and a path without a leading slash is resolved against the request path first.
The origin is ``site_origin(request)`` of :doc:`site`, ``SITE["URL"]`` first, then the ``Site`` row of ``django.contrib.sites``, then the request host, the same origin the sitemap lists the page under.
A protocol-relative URL such as ``//cdn.notes.example/cover.png`` keeps its own host and takes the scheme of that origin.
A relative URL with neither a site URL nor a request raises ``SiteOriginError``.
A URL with any other scheme, ``javascript:`` or ``data:`` among them, is a ``PageMetadataShapeError`` naming the key path, which ``manage.py check`` reports ahead of the render.

Canonical
---------

The canonical link is opt-in.
``"canonical": True`` names the page itself, built from the escaped request path and the query parameters ``METADATA["CANONICAL_QUERY"]`` allows, in the order the setting lists them.
A ``page=1`` pair is dropped even when ``page`` is allowed, so the first page of a listing and the unpaginated listing share one canonical.
A string names another URL, absolute or root-relative, for a page that mirrors content published elsewhere, and ``RESET`` removes an inherited canonical.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "SITE": {"URL": "https://notes.example"},
       "METADATA": {
           "DEFAULTS": {"canonical": True},
           "CANONICAL_QUERY": ("category", "page"),
       },
   }

A request for ``/notes/?category=work&sort=date&page=2`` renders ``<link rel="canonical" href="https://notes.example/notes/?category=work&page=2">``.
A literal canonical in the ``metadata`` dict of a dynamic route names one URL for every match, so a dynamic route either keeps ``True`` or builds its canonical in a ``@page.metadata`` callable.

Robots
------

``robots`` is a dict of flags and limits, or a ready string.
The flags ``index``, ``follow``, ``noarchive``, ``nosnippet``, ``noimageindex``, and ``notranslate`` are booleans, and ``unavailable_after``, ``max_snippet``, ``max_image_preview``, and ``max_video_preview`` carry their values.
A ``googlebot`` entry inside the block, a dict or a string of the same shape, renders as a second ``<meta name="googlebot">``.

.. code-block:: python
   :caption: notes/pages/notes/[int:note_id]/edit/page.py

   metadata = {"robots": {"index": False, "follow": True}}

The page renders ``<meta name="robots" content="noindex, follow">``.
A robots string counts as ``noindex`` when any comma-separated token is ``noindex`` or ``none``, in any case, so ``"NONE"`` and ``"noindex, follow"`` both keep the page out of the sitemap.
On a site closed to search every page renders ``noindex, nofollow`` whatever it declares, see :doc:`site`.

The response repeats blocking directives as a header, for the crawlers and the non-HTML clients that act on headers alone.
A page whose resolved robots contain ``noindex``, ``nofollow``, or ``none`` answers ``X-Robots-Tag`` with the same directives, so the page above answers ``X-Robots-Tag: noindex, follow``.
A ``googlebot`` block that blocks while the general one does not answers ``X-Robots-Tag: googlebot: noindex``, and an open page carries no header.
A ``render()`` that returns its own response resolves no head, so the header follows the ``metadata`` dict of the page, and a ``@page.metadata`` callable of such a page never reaches it.
A header the response already carries wins.

hreflang alternates
-------------------

``alternates`` describes the language variants of a page.
``languages`` is either a mapping of language codes to URLs or ``True``, and ``x_default`` names the fallback variant.
An ``"x-default"`` key inside the mapping moves to ``x_default``, and a mapping that names it both ways is a ``PageMetadataShapeError``.

``True`` walks ``LANGUAGES`` and translates the canonical path, or the self path when no canonical is set, into each language through :func:`~django.urls.translate_url`.
The translation runs under the language of the path rather than the active one, so the result never depends on who asked.
A language whose translation fails is left out, and fewer than two languages render no alternate at all.
It needs the page routes inside :func:`~django.conf.urls.i18n.i18n_patterns`, otherwise nothing translates and ``manage.py check`` says so.
``x-default`` defaults to the ``LANGUAGE_CODE`` variant, the same URL the sitemap names, and renders exactly once, last.
:doc:`/content/howto/internationalize-routes` shows the settings side.

Open Graph
----------

``og`` is the Open Graph block with ``title``, ``description``, ``url``, ``type``, ``site_name``, ``locale``, ``locale_alternates``, ``determiner``, ``images``, ``videos``, ``audio``, ``article``, ``profile``, and ``book``.
Each image is a URL or a dict of ``url``, ``secure_url``, ``type``, ``width``, ``height``, and ``alt``, rendered as ``og:image`` followed by its details, and a video or an audio track takes the same form.
``article`` carries ``published_time``, ``modified_time``, ``authors``, ``section``, and ``tags``, ``profile`` and ``book`` their own ``profile:*`` and ``book:*`` fields.
A time is a :class:`~datetime.datetime`, a :class:`~datetime.date`, or a string, and a naive datetime reads in the current time zone before it renders in ISO 8601.

.. code-block:: python
   :caption: blog/pages/posts/[slug]/page.py

   from blog.models import Post

   from next import page
   from next.pages import MetadataDict

   @page.metadata
   def post_metadata(post: Post) -> MetadataDict:
       return {
           "title": post.title,
           "description": post.summary,
           "og": {
               "type": "article",
               "images": [{"url": post.cover.url, "width": 1200, "height": 630, "alt": post.title}],
               "article": {"published_time": post.published_at, "tags": post.tag_names},
           },
       }

Derivation happens only when the merged metadata carries an ``og`` block, and it fills only the fields the block leaves empty.
``og:title`` and ``og:description`` come from the merged title and description, ``og:site_name`` from ``site_name``, and ``og:url`` from the canonical link.
``og:locale`` comes from the active language in the ``ll_CC`` form Facebook reads, ``en_US`` for ``en`` and ``zh_CN`` for ``zh-hans``, and ``locale_alternates=True`` lists every other language of ``LANGUAGES`` the same way.
An ``og`` block in ``DEFAULTS``, even an empty one, is the way to turn derivation on for the whole site.
Without any ``og`` block along the tree no Open Graph tag renders at all, and :doc:`icons-and-images` covers the site-wide card image.

Twitter card
------------

``twitter`` carries ``card``, ``site``, ``site_id``, ``creator``, ``creator_id``, ``title``, ``description``, ``images``, and ``player``.
The card is one of ``summary``, ``summary_large_image``, ``app``, and ``player``, and a player card needs the ``player`` block.
An image is a URL or a dict of ``url`` and ``alt``, and the alt renders as ``twitter:image:alt``.
Nothing is copied from the Open Graph block, because card readers fall back to ``og:*`` themselves, so a page names a Twitter field only when it differs.

See also
--------

.. seealso::

   :doc:`head-tags` for the remaining keys.
   :doc:`structured-data` for JSON-LD.
   :doc:`auditing` for the checks that read these keys.
   :doc:`/content/ref/metadata` for the table of every key and the tag it emits.
