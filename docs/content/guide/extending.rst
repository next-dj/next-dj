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

The snippets below each implement one of the five mechanisms. The
``examples/markdown-blog``, ``examples/feature-flags``, and
``examples/audit-forms`` projects in the source repository contain
working custom ``TemplateLoader``, ``RegisteredParameterProvider``, and
``FormActionBackend`` implementations respectively, while the other
subsystems use inline examples here.

**Custom RouterBackend.** Extend ``FileRouterBackend`` and append an
extra URL pattern after the filesystem walk.

.. code-block:: python

   from django.urls import path
   from django.http import JsonResponse
   from next.urls import FileRouterBackend


   class TaggedFileRouterBackend(FileRouterBackend):
       """Append a /_router-info/ diagnostic URL after the filesystem patterns."""

       def get_url_patterns(self):
           patterns = list(super().get_url_patterns())
           patterns.append(
               path("_router-info/", lambda r: JsonResponse({"count": len(patterns)})),
           )
           return patterns

**Custom ComponentsBackend.** Subclass ``FileComponentsBackend`` to
observe every component lookup.

.. code-block:: python

   from collections import Counter
   from next.components import FileComponentsBackend


   class CountingFileComponentsBackend(FileComponentsBackend):
       """Record how many times each component is resolved."""

       def __init__(self, *args, **kwargs):
           super().__init__(*args, **kwargs)
           self.lookups: Counter[str] = Counter()

       def resolve(self, name, *args, **kwargs):
           self.lookups[name] += 1
           return super().resolve(name, *args, **kwargs)

**Custom FormActionBackend.** Extend ``RegistryFormActionBackend`` and
keep an audit log of dispatched UIDs.

.. code-block:: python

   import logging
   from next.forms import RegistryFormActionBackend


   class AuditedRegistryFormActionBackend(RegistryFormActionBackend):
       """Log every dispatched UID for audit trails."""

       _log = logging.getLogger("forms.audit")

       def dispatch(self, uid, request, *args, **kwargs):
           self._log.info("form action dispatched: uid=%s path=%s", uid, request.path)
           return super().dispatch(uid, request, *args, **kwargs)

Wire the subclass through ``NEXT_FRAMEWORK`` so the framework's lazy
factory picks it up on first dispatch:

.. code-block:: python

   NEXT_FRAMEWORK = {
       "DEFAULT_FORM_ACTION_BACKENDS": [
           {"BACKEND": "myapp.backends.AuditedRegistryFormActionBackend"},
       ],
   }

The ``examples/audit-forms`` project ships a complete subclass that
records request payloads and response statuses into a Django model and
runs a parallel signal-receiver channel for comparison. See
:doc:`forms` (section "Configuring form-action backends") for the
settings-driven loading rules.

**Custom TemplateLoader.** See ``examples/markdown-blog/blog/loaders.py``
for a real ``MarkdownTemplateLoader`` that reads a sibling
``template.md`` and renders it as the page body. The pattern:
``can_load(file_path) -> bool``, ``load_template(file_path) -> str | None``,
``source_path(file_path) -> Path | None`` for stale-cache detection.

**Custom RegisteredParameterProvider.** See
``examples/feature-flags/flags/providers.py`` (``FlagProvider`` /
``DFlag[T]``) and ``examples/shortener/shortener/providers.py``
(``LinkProvider`` / ``DLink[T]``). Both implement ``can_handle`` /
``resolve`` against a ``ResolutionContext`` and use the
``DDependencyBase`` marker to drive annotation-based injection.

**Custom StaticBackend.** See :doc:`static-assets` for the static-asset
pipeline. The minimal contract:

.. code-block:: python

   from next.static import StaticBackend


   class AttributedStaticFilesBackend(StaticBackend):
       """Add a `data-served-by` attribute to every asset URL this backend owns."""

       def url_for(self, asset):
           base = super().url_for(asset)
           return f"{base}?served-by=attributed"

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
