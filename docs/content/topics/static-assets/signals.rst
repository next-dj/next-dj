.. _topics-static-signals:

Static signals
==============

The static pipeline emits ``asset_registered``, ``collector_finalized``, ``html_injected``, and ``static_backend_loaded`` from ``next.static.signals``.

Import either from ``next.static.signals`` or from the aggregator ``next.signals``.
Import receiver modules from ``AppConfig.ready`` so receivers exist before the first request.

.. contents::
   :local:
   :depth: 2

Signals and payloads
--------------------

asset_registered
~~~~~~~~~~~~~~~~

Fires after a co-located file the backend registered lands in the collector, once per asset per collector.
An asset the collector deduplicates away, such as the second mount of the same component on one page, emits no signal.
The sender is the asset instance.
The payload carries ``collector`` and ``backend``.

The signal covers that one door.
A module-level ``styles`` or ``scripts`` list, the ``{% use_style %}``, ``{% use_script %}``, and ``{% use_module %}`` tags, and both inline block forms call ``collector.add`` directly and emit nothing, so a receiver counting assets sees the co-located files alone.
Those paths still resolve their reference through the backend first, so the collector holds a public URL whichever door an asset came through.

Registration with the backend is per render.
Discovery asks ``register_file`` for the URL of every discovered file on every render, so a backend free to resolve the same file to a different URL per request is asked every time, and ``asset_registered`` carries the asset that registration produced.

.. note::

   ``asset_registered`` fires once per discovered file inside the render hot path.
   Keep receivers cheap and synchronous, and avoid I/O or database queries in them.

.. code-block:: python
   :caption: count discovered assets per kind

   from collections import Counter

   from django.dispatch import receiver

   from next.static.signals import asset_registered

   asset_counts: Counter = Counter()

   @receiver(asset_registered)
   def track_asset(sender, **kwargs) -> None:
       asset_counts[sender.kind] += 1

collector_finalized
~~~~~~~~~~~~~~~~~~~

Fires as the first statement of injection, after template rendering has completed and before any slot is rendered.
Nothing seals the collector at that point, so ``add`` stays callable and an asset a receiver registers still reaches the slot loop that runs next.
The sender is the collector.
The payload carries ``page_path``, the file path of the rendered page, and ``request``, the active ``HttpRequest`` or ``None`` for renders outside a request lifecycle.
A standalone zone render never fires this signal because its assets travel in the patch envelope instead of through injection.

.. code-block:: python
   :caption: inspect collected slots per request

   from django.dispatch import receiver

   from next.static.signals import collector_finalized

   @receiver(collector_finalized)
   def count_assets(sender, **kwargs) -> None:
       styles = sender.assets_in_slot("styles")
       scripts = sender.assets_in_slot("scripts")
       print(f"{len(styles)} styles, {len(scripts)} scripts")

html_injected
~~~~~~~~~~~~~

Fires after placeholder replacement completes.
The sender is the static manager.

``html_before``
   The raw HTML string the manager received before injection.

``html_after``
   The HTML string after every slot token was replaced.

``collector``
   The ``StaticCollector`` this render accumulated into.

``placeholders_replaced``
   A tuple of slot names whose token appeared in ``html_before``.
   The whole ``html_injected`` dispatch is skipped when no receiver is connected, so the tuple is built only on the request paths where a listener is present.

``injected_bytes``
   A signed ``int`` equal to ``len(html_after) - len(html_before)``.
   Negative when injection shortens the document, for example when a slot token is longer than the rendered tags it replaces.
   The preload hint is added before the diff is measured, so under ``AUTO`` an empty collector can still produce a positive value because the preload link grows the document.

``request``
   The active ``HttpRequest`` or ``None``.

static_backend_loaded
~~~~~~~~~~~~~~~~~~~~~

Fires after the static manager instantiates a configured backend.
The sender is the backend class.
The payload carries ``config`` and ``instance``.

.. note::

   ``static_backend_loaded`` re-fires whenever the static manager rebuilds its backend chain.
   Tests that toggle ``STATIC_BACKENDS`` through ``override_settings`` or call ``reset_default_manager`` trigger the signal again on the next access.
   Make receivers idempotent.

Pipeline placement
------------------

See :doc:`/content/internals/static-pipeline` for where each signal fires relative to discovery and HTML injection.

See also
--------

.. seealso::

   :doc:`backends` for static backend customisation.
   :doc:`deduplication` for the dedup strategy.
   :doc:`/content/topics/signals` for the full catalog.
   :doc:`/content/ref/signals` for the public API.
