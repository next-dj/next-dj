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

Fires once per asset when discovery records it in the registry.

Payload.
   ``sender`` is the asset discovery class.
   ``asset`` is the ``Asset`` instance.
   ``owner`` is the page module, component name, or layout path.

.. code-block:: python
   :caption: enumerate registered assets

   from django.dispatch import receiver

   from next.static.signals import asset_registered


   @receiver(asset_registered)
   def log_asset(sender, **kwargs) -> None:
       print(kwargs["asset"].relative_path, kwargs["owner"])

collector_finalized
-------------------

Fires once per request after the collector has gathered every asset for the response.
The signal runs before any template tag emits its bucket so receivers can mutate the set.

Payload.
   ``sender`` is the static manager class.
   ``request`` is the HTTP request.
   ``collector`` is the request scoped collector.

.. code-block:: python
   :caption: adding a runtime asset

   from django.dispatch import receiver

   from next.static.assets import Asset
   from next.static.signals import collector_finalized


   @receiver(collector_finalized)
   def add_runtime_pixel(sender, **kwargs) -> None:
       collector = kwargs["collector"]
       collector.add(Asset.inline("js", "console.log('hello');"))

Use this signal sparingly.
Most assets should be co-located, the signal is for runtime decisions that depend on the request body.

backend_loaded
--------------

Fires once at startup for each registered backend.

Payload.
   ``sender`` is the static manager class.
   ``backend`` is the backend class.
   ``options`` is the configuration dict.

.. code-block:: python
   :caption: log the active backend

   from django.dispatch import receiver

   from next.static.signals import backend_loaded


   @receiver(backend_loaded)
   def log_backend(sender, **kwargs) -> None:
       print("backend", kwargs["backend"].__name__)

html_injected
-------------

Fires after the template tags have rendered the collected output.
Subscribers receive the final HTML string for the bucket so they can post process the markup.

Payload.
   ``sender`` is the static manager class.
   ``bucket`` is the bucket name (``styles`` or ``scripts``).
   ``html`` is the rendered HTML string.
   ``request`` is the HTTP request.

.. code-block:: python
   :caption: validation hook

   from django.dispatch import receiver

   from next.static.signals import html_injected


   @receiver(html_injected)
   def assert_module_loads(sender, **kwargs) -> None:
       html = kwargs["html"]
       if kwargs["bucket"] == "scripts" and "type=\"module\"" not in html:
           raise RuntimeError("Expected at least one module script.")

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
