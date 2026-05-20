.. _howto-observe-framework-signals:

Observe Framework Signals
=========================

Problem
-------

You run next.dj in production and want metrics, tracing, or audit records driven by what the framework actually does on every request.
The events worth observing include page renders, component lookups, action dispatches, and asset injection.

Solution
--------

next.dj emits :doc:`Django signals <django:topics/signals>` from every subsystem.
Connect plain receivers from ``AppConfig.ready`` and forward each payload to your metrics store or tracer.
This is the production counterpart of ``SignalRecorder``, which captures the same signals inside tests.

This page stays task focused.
Use :doc:`/content/topics/signals` when you need the full catalog with payload tables, import paths, and testing notes.

Walkthrough
-----------

Wire One Receiver Per Signal Group
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Keep handlers thin.
Each receiver reads the payload keyword arguments it needs and leaves the rest in ``**_kwargs``.
The framework emits these signals on hot rendering paths, so receivers stay synchronous and fast.

.. code-block:: python
   :caption: obs/receivers.py

   from django.dispatch import receiver
   from next.pages.signals import page_rendered
   from next.components.signals import component_rendered
   from next.forms.signals import action_dispatched, form_validation_failed
   from .metrics import incr

   @receiver(page_rendered)
   def on_page_rendered(
       file_path: object = None,
       duration_ms: float | None = None,
       **_kwargs: object,
   ) -> None:
       incr("pages.rendered", str(file_path))
       if duration_ms is not None:
           incr("pages.duration_ms_total", str(file_path), by=int(duration_ms) or 1)

   @receiver(component_rendered)
   def on_component_rendered(info: object = None, **_kwargs: object) -> None:
       name = getattr(info, "name", "<unknown>")
       incr("components.rendered", str(name))

   @receiver(action_dispatched)
   def on_action_dispatched(action_name: str | None = None, **_kwargs: object) -> None:
       incr("forms.action_dispatched", str(action_name))

   @receiver(form_validation_failed)
   def on_form_validation_failed(
       action_name: str | None = None, **_kwargs: object
   ) -> None:
       incr("forms.validation_failed", str(action_name))

The keyword names are fixed by the framework.
``page_rendered`` carries ``file_path``, ``duration_ms``, ``styles_count``, ``scripts_count``, and ``context_keys``.
``action_dispatched`` carries ``action_name``, ``form``, ``url_kwargs``, ``duration_ms``, ``response_status``, and ``dep_cache``.
``form_validation_failed`` carries ``action_name``, ``error_count``, and ``field_names``.

Cover the Static Pipeline
~~~~~~~~~~~~~~~~~~~~~~~~~~

The static subsystem emits ``asset_registered``, ``backend_loaded``, ``collector_finalized``, and ``html_injected``.
The ``html_injected`` payload carries ``injected_bytes``, which is useful as a payload-size metric.

.. code-block:: python
   :caption: obs/receivers.py

   from django.dispatch import receiver
   from next.static.signals import (
       asset_registered,
       backend_loaded,
       collector_finalized,
       html_injected,
   )

   from .metrics import incr

   @receiver(asset_registered)
   def on_asset_registered(**_kwargs: object) -> None:
       incr("static", "asset_registered")

   @receiver(collector_finalized)
   def on_collector_finalized(**_kwargs: object) -> None:
       incr("static", "collector_finalized")

   @receiver(html_injected)
   def on_html_injected(injected_bytes: int | None = None, **_kwargs: object) -> None:
       incr("static", "html_injected")
       if injected_bytes:
           incr("static", "injected_bytes_total", by=int(injected_bytes))

   @receiver(backend_loaded)
   def on_static_backend_loaded(**_kwargs: object) -> None:
       incr("static", "backend_loaded")

Cover the Router
~~~~~~~~~~~~~~~~

The URL subsystem emits ``route_registered`` for each route discovered during a file router scan and ``router_reloaded`` for each rebuild.
``route_registered`` carries ``url_path`` and ``file_path``.
``router_reloaded`` carries no extra keyword arguments.

.. code-block:: python
   :caption: obs/receivers.py

   from django.dispatch import receiver
   from next.urls.signals import route_registered, router_reloaded
   from .metrics import incr

   @receiver(route_registered)
   def on_route_registered(url_path: str | None = None, **_kwargs: object) -> None:
       incr("urls.route", str(url_path))

   @receiver(router_reloaded)
   def on_router_reloaded(**_kwargs: object) -> None:
       incr("urls", "router_reloaded")

Connect Receivers at Startup
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Import the receivers module from ``AppConfig.ready`` so the ``@receiver`` decorators run once at startup.

.. code-block:: python
   :caption: obs/apps.py

   from django.apps import AppConfig

   class ObsConfig(AppConfig):
       name = "obs"

       def ready(self) -> None:
           from obs import receivers  # noqa: F401, PLC0415

Verification
------------

Walk a page that renders components and submits a form, then read your metrics store.
The ``pages.rendered``, ``components.rendered``, and ``forms.action_dispatched`` counters all move.

In a test, assert the same wiring with ``SignalRecorder`` from ``next.testing``, which records every framework signal without a production backend.

See Also
--------

.. seealso::

   :doc:`/content/topics/signals` for every signal name and payload.
   :doc:`/content/topics/testing` for capturing signals with ``SignalRecorder``.
