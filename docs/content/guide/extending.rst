Extending next.dj
=================

Every subsystem in next.dj exposes the same small set of mechanisms
for replacing or augmenting its behaviour. This guide covers that
model once, so each per-subsystem guide can link here instead of
repeating it.

Five extension mechanisms
-------------------------

The framework uses the same five mechanisms in every subsystem.

* **Backend.** A dotted path written into ``NEXT_FRAMEWORK`` under ``DEFAULT_PAGE_BACKENDS``, ``DEFAULT_COMPONENT_BACKENDS``, or ``DEFAULT_STATIC_BACKENDS``. The subsystem's factory imports the class, instantiates it with the entry dict, and emits a ``backend_loaded`` signal when that is supported.
* **Registry.** A call such as ``RouterFactory.register_backend(name, cls)`` that binds a dotted path to an implementation without editing the factory.
* **Protocol or ABC.** A contract such as ``TemplateLoader``, ``ComponentRenderStrategy``, or ``RegisteredParameterProvider``. Implement it and pass the instance into the manager's constructor or rely on ``__init_subclass__`` for auto-registration.
* **Strategy.** A constructor argument on the manager. For example ``ComponentsManager(strategies=[...])`` swaps the render strategy list at startup.
* **Signal.** A Django ``Signal`` in ``next.<pkg>.signals``. Subscribe in ``AppConfig.ready`` to observe without subclassing.

What-with-what matrix
---------------------

The matrix below maps a concrete need to the mechanism that fits.

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - What you want to change
     - Mechanism
   * - Serve URL routes from something other than the filesystem
     - Custom ``RouterBackend`` (see :doc:`file-router`)
   * - Augment URL patterns while keeping the filesystem walk
     - Subclass ``FileRouterBackend``
   * - Source components from a database or remote registry
     - Custom ``ComponentsBackend`` (see :doc:`components`)
   * - Render a component with a different engine or escaping rule
     - Custom ``ComponentRenderStrategy``
   * - Replace the action dispatch layer
     - Custom ``FormActionBackend`` (see :doc:`forms`)
   * - Read page template source from a non-filesystem source
     - Custom ``TemplateLoader`` (see :doc:`pages-and-templates`)
   * - Inject a value into any DI-resolved callable
     - Custom ``RegisteredParameterProvider`` (see :doc:`dependency-injection`)
   * - Resolve co-located static assets through a custom storage
     - Custom ``StaticBackend`` (see :doc:`static-assets`)
   * - Observe a framework event without changing behaviour
     - Subscribe to a signal from :mod:`next.<pkg>.signals`

Backend contract
----------------

Every backend entry in ``NEXT_FRAMEWORK`` follows the same shape.

.. code-block:: python

   {
       "BACKEND": "dotted.path.to.MyBackend",
       "OPTIONS": {"key": "value"},
       # subsystem-specific keys such as DIRS, APP_DIRS, PAGES_DIR, COMPONENTS_DIR
   }

The subsystem's factory imports ``BACKEND`` on startup, instantiates the class, and stores it in the manager's backend list. For backends that publish a ``backend_loaded`` signal, subscribers can observe the creation without patching the factory.

Worked examples by subsystem
----------------------------

All examples below live in the ``examples/`` directory of the source repository. Each one showcases the extension model relevant to its subsystem.

* **Custom RouterBackend** in ``examples/file-routing/myapp/custom_router.py``. ``TaggedFileRouterBackend`` extends ``FileRouterBackend`` and appends a diagnostic URL to the generated patterns.
* **Custom ComponentsBackend** in ``examples/components/myapp/custom_backend.py``. ``CountingFileComponentsBackend`` extends ``FileComponentsBackend`` to record every component lookup.
* **Custom FormActionBackend** in ``examples/forms/todos/custom_backend.py``. ``AuditedRegistryFormActionBackend`` extends ``RegistryFormActionBackend`` and keeps an audit log of dispatched UIDs.
* **Custom TemplateLoader** in ``examples/pages/catalog/custom_loader.py``. ``InMemoryTemplateLoader`` resolves page source from a ``{path -> source}`` mapping.
* **Custom RegisteredParameterProvider** in ``examples/layouts/layouts/custom_provider.py``. ``LayoutStampProvider`` fills any parameter named ``layout_stamp`` with a fixed value.
* **Custom StaticBackend.** See the dedicated section in :doc:`static-assets` and the ``AttributedStaticFilesBackend`` in ``examples/static/myapp/custom_backend.py``.

Signals as an extension point
-----------------------------

Every subsystem publishes a ``signals`` submodule. The signals below are safe to subscribe to from ``AppConfig.ready``.

* :mod:`next.conf.signals` publishes ``settings_reloaded`` after the merged settings cache is dropped. Managers that cache derived values should reset here.
* :mod:`next.deps.signals` publishes ``provider_registered`` when a ``RegisteredParameterProvider`` subclass joins the auto-registry.
* :mod:`next.pages.signals` publishes ``template_loaded``, ``context_registered``, and ``page_rendered``.
* :mod:`next.urls.signals` publishes ``route_registered`` and ``router_reloaded``.
* :mod:`next.components.signals` publishes ``component_registered``, ``component_backend_loaded``, and ``component_rendered``.
* :mod:`next.forms.signals` publishes ``action_registered``, ``action_dispatched``, and ``form_validation_failed``.
* :mod:`next.server.signals` publishes ``watch_specs_ready`` after the dev reloader resolves its watch specs.
* :mod:`next.static.signals` publishes ``asset_registered``, ``collector_finalized``, ``html_injected``, and ``backend_loaded``. See :doc:`static-assets` for the static-asset lifecycle.

Example subscription.

.. code-block:: python

   from django.apps import AppConfig
   from django.dispatch import receiver

   from next.components.signals import component_registered


   class MyAppConfig(AppConfig):
       name = "myapp"

       def ready(self) -> None:
           @receiver(component_registered)
           def _on_component_registered(sender, **kwargs):
               info = kwargs.get("info")
               # record, validate, or forward the event

What not to do
--------------

A few patterns look tempting but break invariants the framework relies on.

* Do not mutate the dictionaries returned by managers such as ``collector.styles()``, ``component_registry.snapshot()``, or ``form_action_manager.actions``. They are read-only views, and callers may share them across requests.
* Do not reassign a singleton directly (``next.pages.page = MyPage()``). The singletons are ``LazyObject`` wrappers and have dedicated reset helpers such as ``reset_default_manager`` and ``reset_default_components_manager``.
* Do not import a symbol with a leading underscore from another subsystem. Those are internal contracts and change between releases.
* Do not rely on side effects from ``@context`` outside the page module that defines it. Context functions are scoped to their file path.

Where to go next
----------------

* :doc:`file-router` for the URL routing guide and ``RouterBackend`` extension surface.
* :doc:`components` for components, backends, and render strategies.
* :doc:`pages-and-templates` for pages, layouts, and template loaders.
* :doc:`forms` for form actions and dispatch backends.
* :doc:`dependency-injection` for the resolver and parameter providers.
* :doc:`autoreload` for the dev reloader and watch-spec contributors.
* :doc:`static-assets` for the static-asset pipeline and storage backends.
