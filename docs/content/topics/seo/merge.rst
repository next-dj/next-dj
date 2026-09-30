.. _topics-seo-merge:

Merging along the tree
======================

Every page merges ``DEFAULTS`` and each ancestor ``page.py`` into one ``Metadata`` value.
The merge is deep, so a page names only the keys it changes and keeps every nested value an ancestor set.
This page covers the merge rule of every key and the way to take an inherited value back.

.. contents::
   :local:
   :depth: 2

Unset and nearest
-----------------

``None`` means unset at every level, so a key a page leaves at ``None`` keeps whatever the ancestors set.
A callable returning ``{"description": post.summary}`` for a post with no summary therefore keeps the site description rather than erasing it.
Among the layers that do set a key, the one nearest to the page wins, because a page knows its own title better than the root does.

This is the opposite of inherited ``@context``, where the outermost ancestor wins.
:doc:`/content/topics/caching` tabulates what every ``page.py`` name passes down the tree.

Merge rules
-----------

Each key merges by the rule of its field, and the table below is the whole set.

.. list-table::
   :header-rows: 1
   :widths: 34 66

   * - Key
     - How the nearer layer merges over the ancestors
   * - ``title``
     - Its own walk, the template in force applied to the nearest title, see :ref:`the title template <topics-metadata>`.
   * - ``description``, ``site_name``, ``canonical``, ``keywords``, ``manifest``, ``viewport``, ``theme_color``, ``color_scheme``, ``icons``, ``links``
     - Replaced whole by the nearest layer that sets them.
   * - ``robots``
     - Merged per directive when both sides are dicts, so a page adding ``{"index": False}`` keeps the ancestor's ``max_image_preview``.
       A string on either side replaces the other whole, and ``googlebot`` merges the same way inside the block.
   * - ``alternates``
     - Merged per field, where ``languages``, ``x_default``, and ``feeds`` are each replaced whole.
       An empty ``languages`` mapping, or one holding only ``x-default``, keeps the languages of the parent.
   * - ``og``
     - Merged per field.
       ``images``, ``videos``, and ``audio`` are replaced whole, and ``article``, ``profile``, and ``book`` merge per field in turn.
   * - ``twitter``
     - Merged per field, ``images`` and ``player`` replaced whole.
   * - ``verification``
     - Merged per engine, each engine's token list replaced whole and ``other`` merged by name.
   * - ``other``, ``properties``
     - Merged by name, a name the nearer layer sets replacing every value of that name while the ancestor's order stays and new names append.
   * - ``jsonld``
     - Merged by ``@id``, a node whose raw ``@id`` an ancestor declared replacing that node in place and a node without one appending.
   * - ``breadcrumb``
     - Never inherited, read from each page on its own, see :doc:`breadcrumbs`.

``DEFAULTS`` that declare an Organization node and an ``og`` block with the site image therefore reach a landing page that adds a Product node and an ``og`` title, and the page renders both nodes, the site image, and its own title.

.. code-block:: python
   :caption: shop/pages/lp/rocket/page.py

   from next.pages import MetadataDict

   metadata: MetadataDict = {
       "title": "Acme Rocket",
       "og": {"title": "Acme Rocket, ready to launch"},
       "jsonld": [{"@type": "Product", "@id": "#rocket", "name": "Acme Rocket"}],
   }

Dropping an inherited value
---------------------------

``RESET`` drops what the ancestors set, at any depth of the dict.

.. code-block:: python
   :caption: shop/pages/lp/rocket/print/page.py

   from next.pages import RESET, MetadataDict

   metadata: MetadataDict = {
       "canonical": RESET,
       "og": {"images": RESET},
       "other": {"rating": RESET},
   }

The page above renders no canonical link, no Open Graph image, and no ``rating`` meta, while every other inherited value stays.
``"title": RESET`` drops the inherited title, default, and template together, so the page renders no ``<title>`` until a descendant sets one.
``RESET`` is the only way to remove a canonical, and ``"canonical": False`` is a ``PageMetadataShapeError`` whose message names ``RESET``.
``DEFAULTS`` has nothing above it to drop, so ``RESET`` there does nothing.

.. note::

   ``Replace(value)`` takes ``value`` whole instead of merging it, and ``RESET`` is ``Replace()`` with no value.
   ``"jsonld": Replace([...])`` swaps the inherited graph for the given nodes, and ``"title": Replace("Offer")`` renders ``Offer`` free of every template above it.

``manage.py showmetadata /notes/42/`` prints which layer settles each key of one page, see :doc:`auditing`.

See also
--------

.. seealso::

   :doc:`metadata` for the declaration forms.
   :doc:`/content/ref/metadata` for ``Replace``, ``RESET``, and the value objects.
   :doc:`/content/internals/seo-pipeline` for how the layers are collected, merged, and resolved per request.
