.. _topics-static-signals:

Static Signals
==============

The static pipeline emits four Django signals that cover discovery, collector lifecycle, backend loading, and HTML injection.
Subscribe to them to integrate with observability tools, asset caches, or content delivery hooks.

.. contents::
   :local:
   :depth: 2

Overview
--------

Every signal lives in ``next.static.signals``.
Connect receivers from ``AppConfig.ready`` so they exist before the first request.

asset_registered
----------------

Fires once per asset when discovery records it on the collector.

Payload keyword arguments.
   ``sender`` is the ``StaticAsset`` instance.
   ``collector`` is the request scoped collector.
   ``backend`` is the active static backend.

.. code-block:: python
   :caption: enumerate registered assets

   from django.dispatch import receiver

   from next.static.signals import asset_registered


   @receiver(asset_registered)
   def log_asset(sender, **kwargs) -> None:
       print(sender.kind, sender.url or "inline")

collector_finalized
-------------------

Fires once per request after the collector has gathered every asset for the response.
The signal runs before the static manager replaces the placeholder slots.

Payload keyword arguments.
   ``sender`` is the request scoped collector.
   ``page_path`` is the page module path.
   ``request`` is the HTTP request.

.. code-block:: python
   :caption: inspecting the collected set

   from django.dispatch import receiver

   from next.static.signals import collector_finalized


   @receiver(collector_finalized)
   def count_assets(sender, **kwargs) -> None:
       styles = sender.assets_in_slot("styles")
       scripts = sender.assets_in_slot("scripts")
       print(f"{len(styles)} styles, {len(scripts)} scripts")

backend_loaded
--------------

Fires once for each registered backend when the factory builds it.

Payload keyword arguments.
   ``sender`` is the backend class.
   ``config`` is the config dict.
   ``instance`` is the backend instance.

.. code-block:: python
   :caption: log the active backend

   from django.dispatch import receiver

   from next.static.signals import backend_loaded


   @receiver(backend_loaded)
   def log_backend(sender, **kwargs) -> None:
       print("backend", sender.__name__)

html_injected
-------------

Fires after the static manager replaces the placeholder slots with rendered tags.

Payload keyword arguments.
   ``sender`` is the static manager.
   ``html_before`` and ``html_after`` are the HTML around injection.
   ``collector`` is the collector.
   ``placeholders_replaced`` is a tuple of the slot names that were replaced.
   ``injected_bytes`` is the size delta.
   ``request`` is the HTTP request.

.. code-block:: python
   :caption: size metric

   from django.dispatch import receiver

   from metrics import emit

   from next.static.signals import html_injected


   @receiver(html_injected)
   def record_injection(sender, **kwargs) -> None:
       emit("static.injected_bytes", value=kwargs["injected_bytes"])

The receiver runs inside the request lifecycle.
Heavy work should defer to a background task.

Connect Once
------------

Place receiver registration inside ``AppConfig.ready``.
A receiver registered from a module that is imported lazily may not run until the first import of that module.

.. code-block:: python
   :caption: notes/apps.py

   from django.apps import AppConfig


   class NotesConfig(AppConfig):
       name = "notes"

       def ready(self) -> None:
           from notes import receivers  # noqa: F401

Common Patterns
---------------

Asset Audit
~~~~~~~~~~~

Subscribe to ``asset_registered`` at startup to build a list of every shipped asset.
Useful for documentation generation or for compliance reports.

Runtime Asset Injection
~~~~~~~~~~~~~~~~~~~~~~~

Subscribe to ``collector_finalized`` to add assets that depend on per-request data.
A feature flag pixel that fires only when a flag is enabled is the canonical example.

Header Injection
~~~~~~~~~~~~~~~~

Subscribe to ``html_injected`` and add ``Link: rel=preload`` HTTP headers based on the rendered asset list.

See Also
--------

.. seealso::

   :doc:`backends` for static backend customisation.
   :doc:`deduplication` for the dedup strategy.
   :doc:`/content/topics/signals` for the full signal catalog.
   :doc:`/content/ref/signals` for the public API.
