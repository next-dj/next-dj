.. _topics-static-deduplication:

Deduplication
=============

The collector emits each asset exactly once per request even when multiple components reference the same file.
This page covers the deduplication contract, how the framework computes asset identity, and how to write a custom deduplication strategy.

.. contents::
   :local:
   :depth: 2

Default Strategy
----------------

The framework ships a hash-based deduplication strategy.
Two assets are considered identical when their content hashes match.

The collector stores an ordered set keyed by hash.
A second add with the same hash is a no-op.
The same component referenced from a layout and a page therefore contributes its CSS exactly once.

Identity Sources
----------------

Three pieces of identity drive the dedup decision.

Content hash.
   A SHA-256 hash of the file content.
   Two files with the same bytes are dedup equal even when they live at different paths.

Asset kind.
   ``css``, ``js``, ``module``, and custom kinds are kept in separate buckets.
   A ``component.css`` and a ``component.js`` with identical bytes are not merged.

Owner.
   Each asset still remembers its owner.
   The dedup result keeps the earliest owner and the earliest insertion point in the order.

Inline Assets
-------------

Inline ``{% inline_style %}`` and ``{% inline_script %}`` blocks participate in deduplication.
The content hash drives identity, so the same snippet declared from two components emits once.

.. code-block:: jinja
   :caption: identical inline blocks

   {% #inline_script %}
     console.log("hello");
   {% /inline_script %}

   {% #inline_script %}
     console.log("hello");
   {% /inline_script %}

Only one ``<script>`` element appears in the output.

Custom Strategy
---------------

The default strategy lives in ``next.static.collector.HashContentDedup``.
Override it through ``NEXT_FRAMEWORK["STATIC_DEDUP"]``.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "STATIC_DEDUP": "notes.dedup.PathDedup",
   }

The class implements three methods.

``fingerprint(asset)``.
   Returns a hashable identity for the asset.

``register(fingerprint, asset)``.
   Stores the asset under the fingerprint.
   Returns ``True`` when the asset was new, ``False`` when it was already present.

``ordered()``.
   Returns the unique assets in insertion order.

The default ``HashContentDedup`` fingerprints by content hash.
A ``PathDedup`` strategy can fingerprint by absolute path when content hashing is too expensive for the workload.

.. code-block:: python
   :caption: path based strategy

   from pathlib import Path

   from next.static.collector import DedupStrategy
   from next.static.assets import Asset


   class PathDedup(DedupStrategy):
       def __init__(self) -> None:
           self._assets: dict[Path, Asset] = {}

       def fingerprint(self, asset: Asset) -> Path:
           return asset.path

       def register(self, fingerprint: Path, asset: Asset) -> bool:
           if fingerprint in self._assets:
               return False
           self._assets[fingerprint] = asset
           return True

       def ordered(self) -> list[Asset]:
           return list(self._assets.values())

The strategy is instantiated per request, so it can hold per-request state without leaking between users.

Manual Skip
-----------

Two attributes on the ``Asset`` dataclass let owners opt out of deduplication.

``dedup``.
   Set to ``False`` to always emit the asset, even when an identical fingerprint exists.
   Use for marketing pixels and other assets that intentionally fire once per occurrence.

``required``.
   Set to ``True`` to emit the asset first, before any other asset of the same kind.
   Use for critical CSS that needs to land before the rest of the bucket.

Both fields default to safe values, hash-based dedup with insertion order.

Hashing Costs
-------------

Hashing happens once per asset at discovery time, not per request.
The result lives in the asset registry so the per request cost is a dictionary lookup.

For very large repositories, ``NEXT_FRAMEWORK["STATIC_HASH"]`` switches to a faster algorithm such as ``blake2b`` or ``xxhash``.

.. code-block:: python
   :caption: alternative hash

   NEXT_FRAMEWORK = {
       "STATIC_HASH": "blake2b",
   }

The default ``sha256`` is appropriate for any size below a few thousand assets.

Common Patterns
---------------

Shared Vendor CSS
~~~~~~~~~~~~~~~~~

Place a vendor CSS file in the root layout.
Components that depend on the same file via a different path still deduplicate because the bytes are identical.

Section Specific Reset
~~~~~~~~~~~~~~~~~~~~~~

Use ``required=True`` on a layout asset that must load before component overrides.
The collector emits it first regardless of insertion order.

Tracking Pixel
~~~~~~~~~~~~~~

Mark the tracking pixel with ``dedup=False`` so the collector emits it every time the owner renders, useful when the pixel includes per-page query parameters.

See Also
--------

.. seealso::

   :doc:`overview` for the collector trace.
   :doc:`backends` for the rendered output.
   :doc:`/content/ref/static` for the public API.
