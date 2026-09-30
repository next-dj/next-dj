.. _ref-management:

Management commands
===================

Module summary
--------------

The ``next`` application ships one management command, ``showmetadata``, a thin shell over the metadata of ``next.pages``.

showmetadata
------------

``manage.py showmetadata <path>`` resolves a URL path to its page and prints, for every key the static metadata settles, the source that settles it, ``DEFAULTS`` or a ``page.py``.
Each ``@page.metadata`` callable of the page follows under ``*``, since a callable answers only per request.

.. code-block:: text
   :caption: manage.py showmetadata /posts/launch-day/

   blog/pages/posts/[slug]/page.py
     description: NEXT_FRAMEWORK['METADATA']['DEFAULTS']
     jsonld: NEXT_FRAMEWORK['METADATA']['DEFAULTS']
     title: blog/pages/page.py
     *: post_metadata in blog/pages/posts/[slug]/page.py

A path that resolves to no URL, or to a view that is no page, is a ``CommandError``.

See also
--------

.. seealso::

   :doc:`/content/topics/seo/auditing` for where the command fits beside the checks and the tests.
   :doc:`metadata` for the metadata the command reads.
