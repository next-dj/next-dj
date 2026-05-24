.. _topics-static-signals:

Static Signals
==============

The static pipeline emits ``asset_registered``, ``collector_finalized``, ``html_injected``, and ``backend_loaded`` from ``next.static.signals``.

Import either from ``next.static.signals`` or from the aggregator ``next.signals``.

Signals and Payloads
--------------------

asset_registered
~~~~~~~~~~~~~~~~

Fires after a file is registered with a backend and added to the collector.
The sender is the asset instance.
The payload carries ``collector`` and ``backend``.

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

Fires when the static manager begins injection, after template rendering has completed and the collector is sealed.
The sender is the collector.
The payload carries ``page_path``, which is ``None`` for partial renders, and ``request``, which is the active ``HttpRequest`` or ``None`` for renders outside a request lifecycle.

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
   The sealed ``StaticCollector`` used for this render.

``placeholders_replaced``
   A tuple of slot names whose token appeared in ``html_before``.
   The whole ``html_injected`` dispatch is skipped when no receiver is connected, so the tuple is built only on the request paths where a listener is present.

``injected_bytes``
   A signed ``int`` equal to ``len(html_after) - len(html_before)``.
   Negative when injection shortens the document, for example when a slot token is longer than the rendered tags it replaces.
   The preload hint is added before the diff is measured, so under ``AUTO`` an empty collector can still produce a positive value because the preload link grows the document.

``request``
   The active ``HttpRequest`` or ``None``.

backend_loaded
~~~~~~~~~~~~~~

Fires after the static factory instantiates a backend.
The sender is the backend class.
The payload carries ``config`` and ``instance``.

Register imports from ``AppConfig.ready`` so receivers exist before the first request.

.. note::

   ``backend_loaded`` re-fires whenever the static manager rebuilds its backend chain.
   Tests that toggle ``DEFAULT_STATIC_BACKENDS`` through ``override_settings`` or call ``reset_default_manager`` trigger the signal again on the next access.
   Make receivers idempotent.

Pipeline placement
------------------

See :doc:`/content/internals/static-pipeline` for where each signal fires relative to discovery and HTML injection.

See Also
--------

.. seealso::

   :doc:`backends` for static backend customisation.
   :doc:`deduplication` for the dedup strategy.
   :doc:`/content/topics/signals` for the full catalog.
   :doc:`/content/ref/signals` for the public API.
