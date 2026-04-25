.. _api-signals:

Aggregated signals (next.signals)
=================================

``next.signals`` re-exports every signal emitted by next.dj subsystems
so application code can import them from a single place. Each signal is
also available from its owning subsystem's ``signals`` module, and both
import paths refer to the same :class:`django.dispatch.Signal` instance.

Typical usage connects receivers in :meth:`AppConfig.ready`.

.. code-block:: python

   from django.apps import AppConfig
   from django.dispatch import receiver

   from next.signals import action_dispatched, page_rendered


   class MyAppConfig(AppConfig):
       name = "myapp"

       def ready(self) -> None:
           @receiver(action_dispatched)
           def _on_action(sender, **kwargs):
               ...  # record metrics, forward to logger, etc.

           @receiver(page_rendered)
           def _on_render(sender, **kwargs):
               ...

Signal list
-----------

.. automodule:: next.signals
   :members:
   :undoc-members:
   :show-inheritance:

Per-subsystem details
---------------------

Each signal's sender and kwargs are documented on its subsystem page:

- :mod:`next.pages.signals` — ``template_loaded``, ``context_registered``,
  ``page_rendered``.
- :mod:`next.components.signals` — ``component_registered``,
  ``component_backend_loaded``, ``component_rendered``.
- :mod:`next.forms.signals` — ``action_registered``, ``action_dispatched``,
  ``form_validation_failed``.
- :mod:`next.static.signals` — ``asset_registered``, ``backend_loaded``,
  ``collector_finalized``, ``html_injected``.
- :mod:`next.urls.signals` — ``route_registered``, ``router_reloaded``.
- :mod:`next.deps.signals` — ``provider_registered``.
- :mod:`next.conf.signals` — ``settings_reloaded``.
- :mod:`next.server.signals` — ``watch_specs_ready``.
