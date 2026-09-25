.. _topics-seo-social-and-canonical:

Social and canonical tags
=========================

The keys past the title and the description describe a page to crawlers and to the cards a link unfurls into.
This page covers each block of the metadata schema, the tag it emits, and the two settings that shape the result site-wide, ``NOINDEX`` and ``CANONICAL_QUERY``.
:doc:`metadata` covers where the keys are declared and how the segments fold.

.. contents::
   :local:
   :depth: 2

Absolute URLs and the base origin
---------------------------------

A canonical link, an hreflang alternate, and a social image are only useful as absolute URLs, so every URL field of the fold is made absolute before it renders.
An ``http`` or ``https`` URL passes through, a root-relative path such as ``/notes/`` is joined to the ``base`` origin of the fold, and a path without a leading slash is resolved against the request path first.
A URL with any other scheme is a ``PageMetadataShapeError``, which ``next.E109`` reports ahead of the render.

``base`` is an origin and nothing more, a scheme and a host with no path, query, or fragment, which ``next.E101`` enforces.
Declare it once in ``NEXT_FRAMEWORK["METADATA"]["DEFAULTS"]`` so the whole tree shares it.
Without a base the renderer falls back to :meth:`~django.http.HttpRequest.build_absolute_uri`, and a fold that has neither a base nor a request raises ``PageMetadataURLError``.
The fallback follows whatever host the request arrived on, which is why ``next.W086`` asks for a base once ``DEBUG`` is off.

Canonical
---------

The canonical link is opt-in.
``"canonical": True`` names the page itself, built from ``request.path`` and the query parameters ``CANONICAL_QUERY`` allows, in the order the setting lists them.
A ``page=1`` pair is dropped even when ``page`` is allowed, so the first page of a listing and the unpaginated listing share one canonical.
A string names another URL, absolute or root-relative, for a page that mirrors content published elsewhere.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "METADATA": {
           "DEFAULTS": {"base": "https://notes.example"},
           "CANONICAL_QUERY": ("category", "page"),
       },
   }

.. code-block:: python
   :caption: notes/pages/notes/page.py

   metadata = {"title": "Notes", "canonical": True}

A request for ``/notes/?category=work&sort=date&page=2`` renders ``<link rel="canonical" href="https://notes.example/notes/?category=work&page=2">``.
``next.W094`` warns about a literal canonical on a dynamic route, because every match would claim the same URL, and ``next.W088`` about a ``noindex`` page pointing its canonical at another origin.

Robots
------

``robots`` is a dict of flags and limits, or a ready string.
The flags ``index``, ``follow``, ``noarchive``, ``nosnippet``, ``noimageindex``, and ``notranslate`` are booleans, and ``unavailable_after``, ``max_snippet``, ``max_image_preview``, and ``max_video_preview`` carry their values.
A ``googlebot`` entry inside the block, a dict or a string of the same shape, renders as a second ``<meta name="googlebot">`` tag.

.. code-block:: python
   :caption: notes/pages/notes/[int:note_id]/edit/page.py

   metadata = {"robots": {"index": False, "follow": True}}

The page renders ``<meta name="robots" content="noindex, follow">``.

``NOINDEX`` in ``NEXT_FRAMEWORK["METADATA"]`` overrides the robots block of every page with ``noindex, nofollow``.
Set it on a staging host so a crawler that finds the deployment indexes none of it, and leave it off in production.

hreflang alternates
-------------------

``alternates`` describes the language variants of a page.
``languages`` is either a mapping of language codes to URLs or ``True``.
The mapping is rendered as written, and ``x_default`` names the fallback variant.

``True`` walks ``LANGUAGES`` and translates the canonical path, or the self path when no canonical is set, into each language through :func:`~django.urls.translate_url`.
It needs the page routes wrapped in :func:`~django.conf.urls.i18n.i18n_patterns`, otherwise every code translates to the same URL and ``next.W087`` says so.
The ``x-default`` alternate defaults to the ``LANGUAGE_CODE`` variant and always renders last.
:doc:`/content/howto/internationalize-routes` shows the settings side.

Open Graph
----------

``og`` is the Open Graph block with ``title``, ``description``, ``url``, ``type``, ``site_name``, ``locale``, ``images``, and ``article``.
Each image is a URL string or a dict with ``url``, ``width``, ``height``, and ``alt``, and the dimensions render as ``og:image:width`` and ``og:image:height`` beside the image.
``article`` carries ``published_time``, ``modified_time``, ``authors``, ``section``, and ``tags``, and a :class:`~datetime.datetime` renders in ISO 8601.

.. code-block:: python
   :caption: notes/pages/notes/[int:note_id]/page.py

   from notes.models import Note

   from next import page
   from next.pages import MetadataDict
   from next.urls import DUrl

   @page.metadata
   def note_metadata(note_id: DUrl[int]) -> MetadataDict:
       note = Note.objects.get(pk=note_id)
       return {
           "title": note.title,
           "description": note.summary,
           "canonical": True,
           "og": {
               "type": "article",
               "images": [{"url": note.cover.url, "width": 1200, "height": 630}],
               "article": {"published_time": note.created_at},
           },
       }

Derivation happens only when the fold carries an ``og`` block, and it fills only the fields the block leaves empty.
``og:title`` and ``og:description`` come from the folded title and description, ``og:site_name`` from ``site_name``, ``og:url`` from the canonical link, and ``og:locale`` from the active language.
A fold without an ``og`` block renders no Open Graph tag at all, so an empty ``"og": {}`` in the settings tier is the cheapest way to turn derivation on site-wide.

Twitter card
------------

``twitter`` carries ``card``, ``site``, ``creator``, ``title``, ``description``, and ``images``.
The card is one of ``summary``, ``summary_large_image``, ``app``, and ``player``, which ``next.E104`` enforces.
Nothing is copied from the Open Graph block, because the card readers already fall back to ``og:*`` themselves, so a page that wants a ``twitter:title`` different from its ``og:title`` names it and every other page leaves the block out.

JSON-LD
-------

``jsonld`` is one mapping or a sequence of mappings, each rendered as its own ``<script type="application/ld+json">``.
The mapping is serialised with :class:`~django.core.serializers.json.DjangoJSONEncoder`, so a :class:`~datetime.datetime` and a :class:`~decimal.Decimal` need no conversion, and the ``<``, ``>``, and ``&`` characters are escaped inside the script so a value can never close the tag early.

.. code-block:: python
   :caption: notes/pages/page.py

   metadata = {
       "jsonld": {
           "@context": "https://schema.org",
           "@type": "WebSite",
           "name": "Notes",
           "url": "https://notes.example/",
       },
   }

Verification and other tags
---------------------------

``verification`` holds the ownership tokens of ``google``, ``yandex``, and ``bing``, each a string or a sequence of strings, rendered as ``google-site-verification``, ``yandex-verification``, and ``msvalidate.01`` metas.

``other`` is the escape hatch for a named meta the schema does not know.
It maps a name to a text or a sequence of texts, and every text renders as its own ``<meta name="..." content="...">``.

.. code-block:: python
   :caption: notes/pages/page.py

   metadata = {
       "verification": {"google": "abc123"},
       "other": {"theme-color": "#1d4ed8", "keywords": ["notes", "markdown"]},
   }

See also
--------

.. seealso::

   :doc:`metadata` for the declaration forms and the merge order.
   :doc:`auditing` for the checks that read these keys.
   :doc:`/content/ref/pages` for the table of every key and the tag it emits.
   :doc:`/content/ref/settings` for ``NOINDEX`` and ``CANONICAL_QUERY``.
