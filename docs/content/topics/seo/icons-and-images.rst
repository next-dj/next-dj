.. _topics-seo-icons-and-images:

Icons and social images
=======================

The favicon, the touch icon, and the card image a shared link unfurls into are metadata keys like any other.
``icons`` declares the icon links, and ``og.images`` and ``twitter.images`` the card images, so they merge along the tree and a section overrides them by naming them again.
This page covers both keys and a card drawn per page.

.. contents::
   :local:
   :depth: 2

The icons key
-------------

``icons`` takes ``icon`` and ``apple``, each one URL, one dict of ``url``, ``sizes``, ``type``, and ``media``, or a list of either, and ``other``, a list of dicts that also name their ``rel`` and an optional ``color``.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "METADATA": {
           "DEFAULTS": {
               "icons": {
                   "icon": [
                       {"url": "/static/site/icon.svg", "type": "image/svg+xml", "sizes": "any"},
                       {"url": "/static/site/icon-32.png", "sizes": "32x32", "type": "image/png"},
                   ],
                   "apple": "/static/site/apple-icon.png",
                   "other": [{"rel": "mask-icon", "url": "/static/site/mask.svg", "color": "#1d4ed8"}],
               },
           },
       },
   }

The settings load before staticfiles can answer a URL, so a path in ``DEFAULTS`` names the file as collected.
A project on a hashed storage names the fingerprinted URL from an inherited callable in the root ``page.py`` instead, where :func:`~django.templatetags.static.static` runs per request.

.. code-block:: python
   :caption: notes/pages/page.py

   from django.templatetags.static import static

   from next import page
   from next.pages import MetadataDict

   @page.metadata(inherit=True)
   def site_icons() -> MetadataDict:
       return {"icons": {"icon": {"url": static("site/icon.svg"), "type": "image/svg+xml", "sizes": "any"}}}

Every page renders one ``<link>`` per icon, the ``icon`` rels first, then ``apple-touch-icon``, then the other rels.
The URL is made absolute on the site origin like every other URL field.
``manage.py check`` reports ``sizes`` other than ``any`` or ``WxH``, a type outside ``image/*``, a ``mask-icon`` without a color, and the same rel, sizes, and media declared twice.

Social images
-------------

``og.images`` lists the Open Graph images and ``twitter.images`` the Twitter card images, each a URL or a dict, see :doc:`social-and-canonical`.
A site-wide card belongs in ``DEFAULTS``, and a page replaces it by naming its own, since ``images`` is replaced whole.

.. code-block:: python
   :caption: blog/pages/posts/[slug]/page.py

   from blog.models import Post

   from next import page
   from next.pages import MetadataDict

   @page.metadata
   def post_metadata(post: Post) -> MetadataDict:
       return {"og": {"images": [{"url": post.cover.url, "width": 1200, "height": 630, "alt": post.title}]}}

Give the width and the height when they are known, because a crawler that reads them lays the card out before it fetches the image.

Dynamic social images
---------------------

``og.images`` takes the URL of any view, so a card drawn per page is a Django view that answers a PNG, named from a ``@page.metadata`` callable.

.. code-block:: python
   :caption: blog/pages/posts/[slug]/page.py

   from blog.models import Post
   from django.urls import reverse

   from next import page
   from next.pages import MetadataDict

   @page.metadata
   def post_metadata(post: Post) -> MetadataDict:
       card = reverse("blog:card", kwargs={"slug": post.slug})
       return {"og": {"images": [{"url": card, "width": 1200, "height": 630}]}}

The web app manifest
--------------------

The ``manifest`` key names the URL of a web app manifest, rendered as ``<link rel="manifest">``.
The manifest itself is a static file or a view of the project.

See also
--------

.. seealso::

   :doc:`social-and-canonical` for the Open Graph and Twitter blocks.
   :doc:`head-tags` for the remaining keys.
   :doc:`/content/topics/static-assets/index` for the staticfiles pipeline.
