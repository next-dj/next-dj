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
  ``components_registered``, ``component_backend_loaded``,
  ``component_rendered``.
  ``component_registered`` fires on every singular
  :meth:`~next.components.registry.ComponentRegistry.register` call with the
  full :class:`~next.components.info.ComponentInfo` under the ``info``
  keyword. The bulk path
  :meth:`~next.components.registry.ComponentRegistry.register_many` follows
  the Django bulk convention and skips the per-item signal. It instead
  fires ``components_registered`` exactly once with every newly
  registered :class:`~next.components.info.ComponentInfo` as a tuple
  under the ``infos`` keyword. An empty batch stays silent. Receivers
  that must observe both paths subscribe to both signals.
  ``component_backend_loaded`` fires from
  :class:`~next.components.manager.ComponentsManager` after a backend is
  built from its config dict, carrying the live ``backend`` instance and
  the source ``config``.
- :mod:`next.forms.signals` — ``action_registered``, ``action_dispatched``,
  ``form_validation_failed``. ``action_dispatched`` carries the bound
  ``form`` (``None`` for handler-only actions) and a copy of the
  resolved ``url_kwargs`` so receivers can route on action payload
  without re-querying state.
- :mod:`next.static.signals` — ``asset_registered``, ``backend_loaded``,
  ``collector_finalized``, ``html_injected``. The latter two carry the
  active :class:`~django.http.HttpRequest` (or ``None`` for renders outside
  a request lifecycle) under the ``request`` keyword argument so receivers
  can correlate injection events with the originating request.
- :mod:`next.urls.signals` — ``route_registered``, ``router_reloaded``.
  ``route_registered`` fires from
  :class:`~next.urls.backends.FileRouterBackend` once per yielded
  :class:`~django.urls.URLPattern`, carrying the route ``url_path`` and the
  source ``file_path`` of the page module so receivers can correlate a
  route with its on-disk origin without re-walking the tree.
- :mod:`next.deps.signals` — ``provider_registered``.
- :mod:`next.conf.signals` — ``settings_reloaded``.
- :mod:`next.server.signals` — ``watch_specs_ready``.
