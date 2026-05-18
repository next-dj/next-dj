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

collector_finalized
~~~~~~~~~~~~~~~~~~~

Fires when the static manager begins injection, after template rendering has completed and the collector is sealed.
The sender is the collector.
The payload carries ``page_path``, which is ``None`` for partial renders, and ``request``, which is the active ``HttpRequest`` or ``None`` for renders outside a request lifecycle.

html_injected
~~~~~~~~~~~~~

Fires after placeholder replacement completes.
The sender is the static manager.
The payload carries ``html_before``, ``html_after``, ``collector``, ``placeholders_replaced``, ``injected_bytes``, and ``request``.
The ``request`` argument carries the active ``HttpRequest`` or ``None``.

backend_loaded
~~~~~~~~~~~~~~

Fires after the static factory instantiates a backend.
The sender is the backend class.
The payload carries ``config`` and ``instance``.

.. code-block:: python
   :caption: inspect collected slots per request

   from django.dispatch import receiver

   from next.static.signals import collector_finalized


   @receiver(collector_finalized)
   def count_assets(sender, **kwargs) -> None:
       styles = sender.assets_in_slot("styles")
       scripts = sender.assets_in_slot("scripts")
       print(f"{len(styles)} styles, {len(scripts)} scripts")

Register imports from ``AppConfig.ready`` so receivers exist before the first request.

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
