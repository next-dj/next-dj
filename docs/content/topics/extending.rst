.. _topics-extending:

Extending
=========

next.dj exposes six extension mechanisms.
Each section below states what its mechanism replaces and where to register it.

.. contents::
   :local:
   :depth: 2

The six mechanisms
------------------

Backend.
   Replace or augment a complete subsystem.
   Used for URL routing, components, forms dispatch, the :doc:`static pipeline <static-assets/index>`, and the :doc:`partial patch protocol <partial-rendering/index>`.

Registry.
   Add new entries to a global list at startup.
   Used for asset kinds, custom stems, and :doc:`dependency injection <dependency-injection>` providers.

Protocol.
   Implement a runtime contract.
   Used for template loaders and JS context serializers.

Strategy.
   Swap an internal algorithm.
   Used for static deduplication, the JS context conflict policy, the URL resolver, the dependency resolver, and the component template loader.
   A strategy is named by a dotted path in settings and satisfies the contract its settings key declares.

Signal.
   Observe a lifecycle event without changing it.
   Used for audit, observability, cache invalidation, and cross-app coordination.

Port.
   Replace the implementation one subsystem calls another through.
   Used for the page-tree scan, the partial response shaping, the router access seam, and the static asset surface a render reaches.

Backends
--------

A backend implements a complete subsystem.
Subclass the base class listed for its family and register the dotted path in ``NEXT_FRAMEWORK``.

.. list-table::
   :header-rows: 1
   :widths: 30 35 35

   * - Subsystem
     - Setting
     - Base class
   * - URL routing
     - ``PAGE_BACKENDS``
     - ``next.urls.RouterBackend``
   * - Components
     - ``COMPONENT_BACKENDS``
     - ``next.components.ComponentsBackend``
   * - Forms dispatch
     - ``FORM_ACTION_BACKENDS``
     - ``next.forms.FormActionBackend``
   * - Static pipeline
     - ``STATIC_BACKENDS``
     - ``next.static.StaticBackend``
   * - Partial protocol
     - ``PARTIAL_BACKENDS``
     - ``next.partial.PartialProtocolBackend``
   * - Form wizard drafts
     - ``FORM_WIZARD_BACKEND``
     - ``next.forms.FormWizardBackend``

Each base is spelled through the curated package path, which is the import a project writes.
The class also lives at a deeper module path, and that path carries no stability promise.

A backend always implements the full contract.
Every family checks the configured class against the base named above, so a class that does not subclass it is rejected with :class:`~django.core.exceptions.ImproperlyConfigured`.
A custom backend usually subclasses the default so it inherits every default behaviour.

Every base is an abstract base class that declares the methods a subclass must implement.
``PartialProtocolBackend`` requires ``serialize_envelope``, which returns the HTTP response body for one patch envelope, ``sse_event``, which returns the same envelope as a server-sent-events frame, and ``deserialize_envelope``, which reads a response body back into an ``Envelope`` so a reader such as the test client parses the format its writer produced.
It also expects the ``content_type`` class attribute that names the media type of the body, and it supplies the ``options`` property that reads ``OPTIONS`` out of the settings entry.
The shipped ``next.partial.JsonPartialProtocolBackend`` implements that contract as compact JSON, and a subclass of it that only changes part of the wire format overrides both public methods rather than reaching through the internal ``_dumps`` helper, which carries no stability promise.

``PARTIAL_BACKENDS`` differs from the other backend lists in that only its first entry is active.
``FORM_WIZARD_BACKEND`` is singular rather than a list, so the key holds one configuration dict instead of a list of them.
That shape puts it outside ``extend_default_backend``, which patches an entry of a backend list and rejects any other key.
The settings merge overlays a user dict on the default dict key by key, so an entry that names only ``BACKEND`` keeps the default ``OPTIONS``.
``manage.py check`` reports a bad shape, an unimportable path, or a class that does not subclass ``FormWizardBackend`` as ``next.E051``, and a misconfiguration that survives to the first wizard request raises :class:`~django.core.exceptions.ImproperlyConfigured` there rather than being skipped.

Beyond the abstract methods, a base class carries optional hooks whose defaults decline.

``ComponentsBackend`` carries the widest set, six hooks beside its two abstract methods, and leaving one alone keeps the backend out of the diagnostics that hook feeds.
:doc:`components` states what each of the six answers and works through a backend that opts into three.

.. code-block:: python
   :caption: registering a custom backend

   NEXT_FRAMEWORK = {
       "FORM_ACTION_BACKENDS": [
           {"BACKEND": "notes.backends.AuditedFormActionBackend"},
       ]
   }

To patch one key of a default backend entry rather than replace it, use ``extend_default_backend``.

.. code-block:: python
   :caption: patching a default entry

   from next.conf import extend_default_backend

   NEXT_FRAMEWORK = {
       "PAGE_BACKENDS": extend_default_backend(
           "PAGE_BACKENDS",
           PAGES_DIR="routes",
       )
   }

Registries
----------

A registry is a process wide map populated at startup.
Register entries in ``AppConfig.ready`` or through a settings key.

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Target
     - How to register
   * - Asset kinds
     - ``default_kinds.register(...)`` in ``AppConfig.ready``.
       Import ``default_kinds`` from ``next.static`` (top-level re-export).
   * - Asset stems
     - ``default_stems.register(...)`` in ``AppConfig.ready``.
       Import ``default_stems`` from ``next.static.discovery`` (deep import).
   * - Placeholder slots
     - ``default_placeholders.register(...)`` in ``AppConfig.ready``.
       Import ``default_placeholders`` from ``next.static`` (top-level re-export).
   * - Dependency providers
     - Subclass ``RegisteredParameterProvider``, imported in ``AppConfig.ready``.
   * - Named dependencies
     - ``@resolver.dependency("name")`` decorator.
       Import ``resolver`` from ``next.deps``.
   * - Patch verbs
     - ``register_patch_op("name")`` in ``AppConfig.ready``.
       Import ``register_patch_op`` from ``next.partial``.
   * - Template loaders
     - The ``TEMPLATE_LOADERS`` settings key.

The registry pattern is the right choice when the framework already knows how to consume the values and only needs to learn about a new entry.

The asset-stem registry is the extension point for teaching the static discovery scanner about a new asset filename next to a page, layout, or component.
Call ``default_stems.register(...)`` from ``AppConfig.ready`` so the new stem is known before the first render discovers assets.

.. code-block:: python
   :caption: notes/apps.py

   from django.apps import AppConfig

   from next.static.discovery import default_stems

   class NotesConfig(AppConfig):
       name = "notes"

       def ready(self) -> None:
           default_stems.register("component", "theme")

App order in ``INSTALLED_APPS``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Django runs every ``AppConfig.ready`` during application population, in ``INSTALLED_APPS`` order, so an app listed above ``next`` registers before ``NextFrameworkConfig.ready`` runs and an app listed below it registers after.
For the registries on this page that ordering does not change the outcome.
``NextFrameworkConfig.ready`` installs the autoreload, template, staticfiles, and component hooks and runs component discovery, and none of those steps read the kind, stem, placeholder, provider, named-dependency, or patch-verb registries.
Each of those registries is read on the first render, the first dispatch, or the first ``manage.py check``, and the watch specs are resolved when the autoreload watcher starts, all of which happen after application population finishes.
What matters is that the call sits in ``ready`` rather than in request-time code, not where the app sits in the list.

One conflict does depend on the order.
``default_kinds`` and ``default_placeholders`` reject a second registration of an existing name with different parameters, and the framework registers ``css``, ``js``, ``module``, ``styles``, and ``scripts`` from its own ``ready``.
An app that reuses one of those names with different parameters fails at startup either way, and its position in ``INSTALLED_APPS`` only decides which ``ready`` call raises.

Autoreload watch specs
~~~~~~~~~~~~~~~~~~~~~~

The development reloader watches the page and component trees by default.
Call ``register_autoreload_watch_spec`` from ``next.server`` to add a directory of your own.
It takes a ``path``, the filesystem root to watch, and a ``glob``, a pattern relative to that root that selects the files whose changes trigger a reload.
See :doc:`/content/ref/server` for the full signature.
Register the spec from ``AppConfig.ready`` so it is in place before the watcher starts.

.. code-block:: python
   :caption: notes/apps.py

   from pathlib import Path

   from django.apps import AppConfig

   from next.server import register_autoreload_watch_spec

   class NotesConfig(AppConfig):
       name = "notes"

       def ready(self) -> None:
           register_autoreload_watch_spec(
               Path(__file__).resolve().parent / "rules",
               "**/*.yaml",
           )

Edits to any ``*.yaml`` file under ``notes/rules`` now restart the development server.
Duplicate ``(path, glob)`` pairs are dropped, so registering the same spec twice is safe.

``register_autoreload_watch_spec`` is how a project adds a tree with a glob of its own, and a custom components backend reaches the watcher instead through its ``watch_roots`` hook.
``iter_all_autoreload_watch_specs`` from ``next.server`` resolves the final spec set and sends the ``watch_specs_ready`` signal with ``sender`` set to the function itself.
Subscribe to that signal to observe or audit the resolved spec set.
See :doc:`/content/internals/autoreload` for the full watcher pipeline.

Protocols and abstract base classes
-----------------------------------

A protocol is a structural contract.
Implement the methods listed in the protocol and pass the class to the framework.

.. list-table::
   :header-rows: 1
   :widths: 40 60

   * - Protocol
     - Defined in
   * - Dedup strategy
     - ``next.static.collector.DedupStrategy``
   * - JS context serializer
     - ``next.static.JsContextSerializer``

Select a serializer implementation with the ``JS_CONTEXT_SERIALIZER`` setting.
The setting defaults to ``None``, which selects the built-in ``JsonJsContextSerializer``.

``next.pages.loaders.TemplateLoader`` is an abstract base class rather than a protocol.
Subclass it explicitly and register the subclass through ``TEMPLATE_LOADERS``.

Protocols differ from backends in that they implement a single hook.
A template loader handles file discovery for one extension.
A backend coordinates an entire subsystem.

Strategies
----------

A strategy is a swappable algorithm.
The framework calls the strategy at a well known point in the pipeline.

.. list-table::
   :header-rows: 1
   :widths: 35 35 30

   * - Strategy
     - Configured through
     - Default
   * - Static dedup
     - ``DEDUP_STRATEGY`` in the first static backend ``OPTIONS``
     - ``UrlDedup``
   * - JS context conflict policy
     - ``JS_CONTEXT_POLICY`` in the first static backend ``OPTIONS``
     - ``FirstWinsPolicy``
   * - URL resolver
     - ``URL_RESOLVER`` at the top level of ``NEXT_FRAMEWORK``
     - ``next.urls.TrieURLResolver``
   * - Dependency resolver
     - ``DEPENDENCY_RESOLVER`` at the top level of ``NEXT_FRAMEWORK``
     - ``next.deps.DependencyResolver``
   * - Component template loader
     - ``COMPONENT_TEMPLATE_LOADER`` at the top level of ``NEXT_FRAMEWORK``
     - ``next.components.CachedComponentTemplateLoader``
   * - Metadata renderer
     - ``RENDERER`` inside ``NEXT_FRAMEWORK["METADATA"]``
     - ``next.pages.HtmlMetadataRenderer``

Use a strategy when the customisation is a single algorithm rather than a complete subsystem.

Three strategies are configured at the top level of ``NEXT_FRAMEWORK`` rather than inside a backend ``OPTIONS`` mapping, and the metadata renderer inside the ``METADATA`` scope.
``URL_RESOLVER``, ``DEPENDENCY_RESOLVER``, ``COMPONENT_TEMPLATE_LOADER``, and ``METADATA["RENDERER"]`` each hold a single dotted path, and ``next.backends.resolve_setting_class`` reads all four against the base class its key declares, the last one through its ``scope`` argument.
A value that is not a string is dropped by the settings merge for any of the three, which leaves the default in place with no error, and :ref:`next.E076 <ref-system-checks>` reports the dropped value on ``manage.py check``.

``URL_RESOLVER`` names a ``django.urls.resolvers.URLResolver`` subclass, and the framework builds one instance of that class around the lazy list of page and form-action patterns.
Swap it to change how a request path is matched against those patterns, for example to trade the default trie for a different index.
A path that cannot be imported, and a class that is not a ``URLResolver`` subclass, both raise :class:`~django.core.exceptions.ImproperlyConfigured` while the URL configuration is built.

``DEPENDENCY_RESOLVER`` names a ``next.deps.DependencyResolver`` subclass, and that class performs every injection the framework makes, from page views and ``@context`` callables to form actions and component renderers.
The framework holds one resolver singleton behind a shared holder and adopts the named class by building an instance of it, so the subclass runs its own ``__init__`` and every reference reads the resolver in force.
Widening the public ``skips`` predicate is the usual reason to subclass, because it decides which parameters a compiled injection plan carries at all.
See :doc:`dependency-injection` for the resolver contract and :doc:`/content/ref/settings` for what a swap takes with it.

``COMPONENT_TEMPLATE_LOADER`` names a ``next.components.ComponentTemplateLoader`` subclass, and the components manager builds one instance of it around the shared module loader.
The loader decides where a component body comes from and how long a compiled template is reused, so the shipped ``CachedComponentTemplateLoader`` is the subclass to start from when only the caching policy changes.

``METADATA["RENDERER"]`` names a ``next.pages.MetadataRenderer`` subclass, whose ``render`` turns the folded ``Metadata`` of a page and the request into the markup ``{% metadata %}`` writes into the head.
Subclass ``HtmlMetadataRenderer`` and extend the markup its ``render`` answers to add a tag while keeping every stock one, see :doc:`/content/ref/pages` for the contract.

Signals
-------

A signal is an observation point.
Connect a receiver to react to a framework event.

The signal catalog lives in :doc:`signals`.
The patterns are uniform across the framework.

Ports
-----

A port is the narrow surface one subsystem calls another through.
``next.ports`` declares each as a ``Protocol`` beside a slot object that holds the one implementation, and the caller imports the slot rather than the subsystem behind it, so the two areas stay decoupled while the call still lands on real code.

.. list-table::
   :header-rows: 1
   :widths: 24 34 42

   * - Slot
     - Shipped implementation
     - What it answers
   * - ``component_tags_slot``
     - ``next.components.ports.ComponentTagsImpl``
     - Names every ``{% component %}`` tag a compiled template holds.
   * - ``page_scan_slot``
     - ``next.pages.ports.PageScanImpl``
     - Executes every routed ``page.py`` and answers the ones that loaded.
   * - ``partial_shaper_slot``
     - ``next.partial.ports.PartialShaperImpl``
     - Reads the partial intent off a request and shapes page and form responses into envelopes.
   * - ``router_access_slot``
     - ``next.urls.ports.RouterAccessImpl``
     - Builds router backends and managers and answers the URL pattern parser.
   * - ``seo_routes_slot``
     - ``next.seo.ports.SeoRoutesImpl``
     - Answers the sitemap and robots routes the lazy urlpatterns append, each only while its source exists.
   * - ``static_assets_slot``
     - ``next.static.ports.StaticAssetsImpl``
     - Creates a collector, discovers page and component assets, and injects the placeholder tags.

``NextFrameworkConfig.ready()`` binds all six, ahead of every step that imports user code.
Replace one by subclassing the shipped implementation and calling ``set`` on its slot from the ``ready()`` of an application listed after ``next`` in ``INSTALLED_APPS``, since a slot holds one implementation and the last binding wins.

.. code-block:: python
   :caption: notes/apps.py

   from django.apps import AppConfig
   from notes.audit import record_injection

   from next.ports import static_assets_slot
   from next.static.ports import StaticAssetsImpl

   class AuditedAssets(StaticAssetsImpl):
       def inject(self, html: str, collector, *, page_path, request) -> str:
           rendered = super().inject(
               html, collector, page_path=page_path, request=request
           )
           record_injection(page_path, len(rendered) - len(html))
           return rendered

   class NotesConfig(AppConfig):
       name = "notes"

       def ready(self) -> None:
           static_assets_slot.set(AuditedAssets())

Subclass the shipped implementation rather than the ``Protocol``, so a method the subclass leaves alone keeps doing what the framework expects of it.

A port differs from a backend in who reads it.
A backend family is configured in ``NEXT_FRAMEWORK`` and reloads itself when the settings change, while a slot is bound once at startup and nothing rebinds it, which is why replacing one is a startup decision rather than a configuration key.
Reach for a port only when no backend, strategy, or protocol covers the seam, because the protocols are narrower than the subsystems behind them and a replacement takes on whatever the shipped implementation was doing for every caller.
See :doc:`/content/ref/ports` for the method contract of each.

Choosing between mechanisms
---------------------------

- **Add a new URL pattern source.** Subclass ``RouterBackend`` and register it under ``PAGE_BACKENDS``.
- **Change how a path is matched against the built patterns.** Name a ``URLResolver`` subclass under ``URL_RESOLVER``.
- **Change how an injected parameter is filled.** Name a ``DependencyResolver`` subclass under ``DEPENDENCY_RESOLVER``.
  See :doc:`dependency-injection`.
- **Change how a component template is read or cached.** Name a ``ComponentTemplateLoader`` subclass under ``COMPONENT_TEMPLATE_LOADER``.
- **Recognise a new asset extension.** Register through the kind registry (``default_kinds``).
- **Recognise a new asset filename next to a page, layout, or component.** Register a custom stem (``default_stems``).
- **Validate every dispatch.** Implement a form action backend.
- **Log every dispatch.** Subscribe to the ``action_dispatched`` signal.
- **Change the patch wire format.** Register a partial protocol backend under ``PARTIAL_BACKENDS``.
- **Add a custom patch verb.** Call ``register_patch_op``.
  See :doc:`/content/topics/partial-rendering/extending`.
- **Persist wizard drafts elsewhere.** Subclass ``FormWizardBackend`` and name it under ``FORM_WIZARD_BACKEND``.
- **Change how URLs land in HTML.** Customise a static backend.
- **Vary URLs by request.** Use a request-aware static backend.
- **Inspect every rendered page.** Subscribe to the ``page_rendered`` signal.
- **Watch extra directories during development.** Call ``register_autoreload_watch_spec``.
  See *Autoreload watch specs* above.
- **Change what one subsystem hands another.** Subclass the shipped port implementation and ``set`` it on its slot from ``AppConfig.ready``.
  See *Ports* above.

Packaging an extension
----------------------

A reusable extension ships as an ordinary Django application, and its ``AppConfig`` is where the registration runs.

.. code-block:: python
   :caption: next_audit/apps.py

   from django.apps import AppConfig

   class NextAuditConfig(AppConfig):
       name = "next_audit"

       def ready(self) -> None:
           from next_audit import providers, receivers  # noqa: F401

Register through ``ready`` rather than at module import, because the app registry is not populated when the module body runs.
Everything a package registers imperatively goes there, the provider and named-dependency registrations, the asset kinds and stems, the patch verbs, the autoreload watch specs, the signal receivers, and a port replacement.
What a package cannot register for its adopter is a backend or a strategy, since both are named by a dotted path in ``NEXT_FRAMEWORK``, so the package documents the path and the adopter adds the entry.

Position the app relative to ``next`` in ``INSTALLED_APPS`` by what it registers.

.. code-block:: python
   :caption: config/settings.py

   INSTALLED_APPS = [
       "next",
       "next_audit",
   ]

For every registry on this page the position does not change the outcome, as *App order in* ``INSTALLED_APPS`` above explains.
A port replacement is the case that does, because ``NextFrameworkConfig.ready()`` binds all six slots and the last binding wins, so a package that replaces a port has to be listed after ``next``.
A package that reuses an existing kind or placeholder name with different parameters fails at startup either way, and its position only decides which ``ready`` call raises.

Declare the dependency on next.dj under its distribution name, and constrain it from below only.

.. code-block:: toml
   :caption: pyproject.toml

   [project]
   name = "next-audit"
   dependencies = ["next.dj"]

The lower bound names the release the extension was developed against, which is what keeps an adopter from installing it beside a framework that has none of the names it imports.
The framework is the host application's choice, so a ceiling on it strands the adopter on an old release for as long as the extension goes unmaintained.
Add one only for an extension built on a deep module path or another surface outside the curated package ``__all__``, since those move without notice.
An extension that stays on the curated package paths, the backend base classes, and the signal catalog needs no ceiling at all.

Worked examples
---------------

The repository ``examples/`` tree ships complete projects for every major extension mechanism.
:doc:`/content/misc/examples` lists each folder, a one-line focus, links to GitHub, and the sections of this manual that explain the same techniques.

See also
--------

.. seealso::

   :doc:`signals` for the observation points.
   :doc:`dependency-injection` for the provider registry and custom markers.
   :doc:`/content/howto/extend-a-default-backend` for the helper details.
   :doc:`/content/ref/conf` for the configuration surface.
   :doc:`/content/internals/index` for how each subsystem composes.
