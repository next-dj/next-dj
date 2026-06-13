.. _topics-static-deduplication:

Deduplication
=============

The collector emits each asset once per request even when several components reference the same file.
This page covers the bundled dedup strategies, how the collector applies them, and how to plug a custom strategy.

.. contents::
   :local:
   :depth: 2

Overview
--------

The collector holds a dedup strategy.
A strategy reduces a ``StaticAsset`` to a hashable key.
The collector ignores any asset whose key was already recorded.

The framework ships three strategies in ``next.static.collector``.

``UrlDedup``.
   The default.
   Keys URL assets by their URL and kind.
   Keys inline assets by their body and kind.

``HashContentDedup``.
   Keys URL assets by the SHA-256 hash of the file at ``source_path``.
   Two files with identical content deduplicate even at different paths.
   Falls back to URL keying when ``source_path`` is absent.
   Inline assets are keyed by their rendered body, identical to ``UrlDedup``.

``IdentityDedup``.
   Disables deduplication.
   Every registration yields a unique key, so every asset is emitted.

Choosing a Strategy
-------------------

The collector takes the strategy through its constructor.
The static manager builds the collector per request and reads the strategy dotted path from the first static backend ``OPTIONS``.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "STATIC_BACKENDS": [
           {
               "BACKEND": "next.static.StaticFilesBackend",
               "OPTIONS": {
                   "DEDUP_STRATEGY": "next.static.collector.HashContentDedup",
               },
           }
       ]
   }

The ``DEDUP_STRATEGY`` value is the dotted path to a dedup strategy class.
The collector instantiates it once per request.
When the key is absent the collector uses ``UrlDedup``.

Inline Assets
-------------

Inline ``{% #use_style %}`` and ``{% #use_script %}`` blocks participate in deduplication.
The strategy keys them by the rendered body, so two identical inline blocks collapse to one.
Inline blocks always append.
They never prepend.

Writing a Custom Strategy
-------------------------

A strategy implements the ``DedupStrategy`` protocol.
The protocol has one method, ``key``, which returns a hashable value.

.. code-block:: python
   :caption: notes/dedup.py

   from collections.abc import Hashable

   from next.static import StaticAsset
   from next.static.collector import UrlDedup

   class PathDedup:
       """Deduplicate URL assets by absolute source path."""

       def __init__(self) -> None:
           self._fallback = UrlDedup()

       def key(self, asset: StaticAsset) -> Hashable:
           if asset.source_path is not None:
               return ("path", str(asset.source_path), asset.kind)
           return self._fallback.key(asset)

Point the backend ``OPTIONS`` at the new strategy.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "STATIC_BACKENDS": [
           {
               "BACKEND": "next.static.StaticFilesBackend",
               "OPTIONS": {
                   "DEDUP_STRATEGY": "notes.dedup.PathDedup",
               },
           }
       ]
   }

The collector instantiates the strategy once per request.
A strategy can therefore hold per request state.

Common Patterns
---------------

Shared Vendor File
~~~~~~~~~~~~~~~~~~

The default ``UrlDedup`` already collapses two references to the same vendor URL.
No configuration is needed for the common case.

Content Aware Dedup
~~~~~~~~~~~~~~~~~~~

Switch to ``HashContentDedup`` when the same content ships from two different paths and should emit once.

Disable Dedup
~~~~~~~~~~~~~

Switch to ``IdentityDedup`` for debugging, to confirm exactly which owners registered which assets.

See Also
--------

.. seealso::

   :doc:`overview` for the collector trace.
   :doc:`backends` for the backend ``OPTIONS`` surface.
   :doc:`/content/ref/static` for the public API.
