.. _ref-signals:

Signals reference
=================

Module summary
--------------

``next.signals`` is an aggregator that re-exports every framework signal.
Importing a signal from ``next.signals`` is equivalent to importing it from its subpackage.

Signal catalog
--------------

Every signal below is a Django ``Signal``.
The ``sender`` column lists the value passed to ``Signal.send``.
Receivers connected with a matching ``sender`` only fire for that sender.

Every signal but ``settings_reloaded`` is sent with ``Signal.send``, so an exception from a receiver leaves the send and reaches the render, the dispatch, or the startup step that was under way, and the receivers connected behind it never run.
``settings_reloaded`` alone is sent with ``Signal.send_robust``, which runs the whole chain and re-raises the first error afterwards.
A receiver body therefore guards itself, see :ref:`topics-signals` for the pattern.

Most of the catalog is built with ``use_caching=True``, which makes Django key the receiver lookup on a weak reference to the sender.
Code that sends one of those signals itself has to pass a weak-referenceable sender, so ``None``, a string, and an instance of a slots class without ``__weakref__`` all raise ``TypeError``.
Four signals stay uncached and accept any sender.
``asset_registered`` sends the ``StaticAsset`` itself, which carries no ``__weakref__``, ``collector_finalized`` sends a collector built for a single render, and ``provider_registered`` and ``settings_reloaded`` fire rarely enough that the weak-key bookkeeping buys nothing.

The dispatch-time form signals (``action_dispatched``, ``form_validation_failed``, ``wizard_step_submitted``, ``wizard_completed``, ``form_access_denied``) share two keyword arguments.
``uid`` is the registry identity of the action, the value the dispatch URL and the ``data-next-action`` markup attribute carry, or ``None`` when a custom backend stores no uid in its meta.
``request`` is the live ``HttpRequest`` being dispatched and must not be retained past the receiver call.

Six signals announce a backend the shared loader built, one per settings-driven family.
``component_backend_loaded``, ``form_backend_loaded``, ``partial_backend_loaded``, ``router_backend_loaded``, ``static_backend_loaded``, and ``wizard_backend_loaded`` all send the resolved backend class as the sender and carry ``config``, a copy of the settings entry, and ``instance``, the object the loader built from it.
Each family owns its own signal rather than sharing one, so a receiver connected with ``sender=`` does not have to sort one family's classes out of another's.
``router_backend_loaded`` and ``component_backend_loaded`` are skipped on a reload that asks not to notify, which is what a caller reloading from inside a receiver passes.

.. list-table::
   :header-rows: 1
   :widths: 18 18 34 30

   * - Signal
     - Sender
     - Keyword arguments
     - When it fires
   * - ``action_dispatched``
     - ``FormActionDispatch``
     - ``action_name``, ``uid``, ``request``, ``form``, ``url_kwargs``, ``duration_ms``, ``response_status``, ``dep_cache``
     - After an action handler runs and the response is coerced, and once per valid wizard step.
       ``form`` is the bound form, or ``None`` for handler-only actions.
       ``duration_ms`` times the handler call and is ``0.0`` on a wizard step advance, which runs no handler.
       ``dep_cache`` is a copy of the dispatch dependency-injection cache.
   * - ``action_registered``
     - Form action backend class
     - ``action_name``, ``uid``, ``form_class``, ``wizard_class``, ``file_path``, ``scope``, ``handler``
     - After the backend stores an action target for a name.
       Exactly one of ``handler``, ``form_class``, or ``wizard_class`` identifies the target, except the ``@action(form_class=...)`` path which supplies a handler and a form factory together.
       ``file_path`` is the module the form, wizard, or handler was declared in and ``scope`` is ``"page"`` or ``"shared"``, which together give a receiver a grouping key under the file-scoped model.
   * - ``asset_registered``
     - The ``StaticAsset`` instance
     - ``collector``, ``backend``
     - After a file is registered with a backend and added to the collector.
   * - ``collector_finalized``
     - The static collector
     - ``page_path``, ``request``
     - When the static manager begins injection, after template rendering completes.
       ``page_path`` is the file path of the rendered page.
       A standalone zone render does not fire this signal.
       ``request`` may be ``None`` outside a request.
   * - ``component_backend_loaded``
     - The component backend class
     - ``config``, ``instance``
     - After a component backend is created from its ``COMPONENT_BACKENDS`` entry.
       A ``reload(notify=False)`` builds the backends without sending it.
   * - ``component_registered``
     - ``ComponentRegistry``
     - ``info``
     - After a single component is registered.
       Not fired from the bulk path.
   * - ``component_rendered``
     - ``ComponentsManager``
     - ``info``, ``template_path``
     - After a component is rendered to HTML.
       ``template_path`` may be ``None`` for components without a template file.
   * - ``components_registered``
     - ``ComponentRegistry``
     - ``infos``
     - After a batch of components is registered.
       ``infos`` is the tuple of added components.
   * - ``context_registered``
     - ``PageContextRegistry``
     - ``file_path``, ``key``
     - After a context function is attached to a page module.
   * - ``field_validated``
     - The active partial protocol backend class
     - ``action_name``, ``uid``, ``request``, ``field_names``, ``error_count``
     - After a validate-only blur pass runs, always behind the action guard so unauthenticated validate traffic never reaches telemetry.
       ``field_names`` is the validated subset.
       ``error_count`` is the number of fields that failed.
   * - ``form_access_denied``
     - ``FormActionDispatch``
     - ``action_name``, ``uid``, ``request``, ``layer``, ``reason``
     - When the origin page or a dynamic permission hook denies a request, never on the static guard path.
       ``layer`` is ``"page"``, ``"view"``, or ``"object"``.
       ``reason`` is ``"raised"``, ``"denied"``, or ``"response"``, and a ``"page"`` denial is always ``"response"``.
   * - ``form_backend_loaded``
     - The form action backend class
     - ``config``, ``instance``
     - After one ``FORM_ACTION_BACKENDS`` entry is instantiated, once per entry on every manager reload.
   * - ``form_validation_failed``
     - ``FormActionDispatch``
     - ``action_name``, ``uid``, ``request``, ``error_count``, ``field_names``
     - When the bound form fails validation during dispatch.
   * - ``html_injected``
     - A ``StaticManager`` instance
     - ``html_before``, ``html_after``, ``collector``, ``placeholders_replaced``, ``injected_bytes``, ``request``
     - After placeholder replacement completes.
       ``placeholders_replaced`` is the tuple of replaced slot names.
       ``injected_bytes`` is the length delta.
   * - ``metadata_registered``
     - ``PageMetadataRegistry``
     - ``file_path``, ``inherit``
     - After a ``@page.metadata`` callable is attached to a page module.
       ``inherit`` is the flag the registration carried.
   * - ``page_rendered``
     - ``Page``
     - ``file_path``, ``duration_ms``, ``styles_count``, ``scripts_count``, ``context_keys``
     - After ``Page.render`` produces HTML and injects static assets.
       ``duration_ms`` times the render.
       ``context_keys`` is the tuple of context keys.
       Fired only when a receiver for ``Page`` is connected, and the ``duration_ms`` timer runs under the same gate.
   * - ``partial_backend_loaded``
     - The partial protocol backend class
     - ``config``, ``instance``
     - After the single configured protocol backend is built, on the first read of it and again after a settings reload drops the cached one.
   * - ``patch_op_registered``
     - ``PatchOpRegistry``
     - ``name``
     - After a custom patch verb is registered through ``register_patch_op``.
   * - ``provider_registered``
     - The ``RegisteredParameterProvider`` subclass
     - none
     - When a ``RegisteredParameterProvider`` subclass is added to the auto-registry.
   * - ``route_registered``
     - ``FileRouterBackend``
     - ``url_path``, ``file_path``
     - After a URL pattern is created for a discovered page.
   * - ``router_backend_loaded``
     - The router backend class
     - ``config``, ``instance``
     - After one ``PAGE_BACKENDS`` entry is instantiated, once per entry on every manager reload.
       A ``reload(notify=False)`` builds the backends without sending it.
   * - ``router_reloaded``
     - The router manager class
     - none
     - After the router manager rebuilds its pattern set.
   * - ``settings_reloaded``
     - ``NextFrameworkSettings``
     - none
     - After ``NextFrameworkSettings.reload`` drops its caches.
       Every receiver runs even when one raises, so a receiver that validates a settings value never leaves the managers behind it holding state built from the settings that reload discarded.
       The first error reaches the caller that asked for the reload once the chain is done.
   * - ``sitemap_items_registered``
     - ``SitemapItemsRegistry``
     - ``file``, ``trail``, ``func``
     - After ``@sitemap.items`` binds a callable to a route trail of the tree of the running file, and again when a re-executed ``sitemap.py`` replaces the binding.
   * - ``sse_stream_closed``
     - ``PatchEventStream``
     - ``request``, ``duration_ms``, ``envelopes_sent``
     - When a patch event stream ends, its source exhausted or the client gone.
       ``envelopes_sent`` counts the envelopes flushed over the connection.
   * - ``sse_stream_opened``
     - ``PatchEventStream``
     - ``request``
     - When a patch event stream starts.
   * - ``static_backend_loaded``
     - The static backend class
     - ``config``, ``instance``
     - After one ``STATIC_BACKENDS`` entry is instantiated.
       An empty setting falls back to one default entry, which is announced the same way.
   * - ``template_loaded``
     - ``Page``
     - ``file_path``
     - After a template source is registered on a page.
   * - ``watch_specs_ready``
     - ``iter_all_autoreload_watch_specs``
     - ``specs``
     - After the reloader resolves the full list of watch specs.
   * - ``wizard_backend_loaded``
     - The wizard backend class
     - ``config``, ``instance``
     - After the single ``FORM_WIZARD_BACKEND`` entry is built, on the first read of it and again after a settings reload drops the cached one.
   * - ``wizard_completed``
     - The wizard class
     - ``cleaned_data``, ``uid``, ``request``
     - After the wizard ``done`` method runs for the final step and its response is below HTTP 400.
       An error response from ``done`` skips the signal and keeps the saved drafts.
       ``cleaned_data`` is the merged mapping passed to ``done``.
   * - ``wizard_step_submitted``
     - The wizard class
     - ``step``, ``cleaned_data``, ``uid``, ``request``
     - After a ``FormWizard`` step validates during dispatch.
       ``cleaned_data`` is a copy of that step's validated data.
   * - ``zone_registered``
     - The compiled page template class
     - ``template``, ``zone_name``, ``lazy``, ``poll``
     - Once per compiled composed template, when its named zones are first read.
       ``lazy`` is the trigger string or ``None``, ``poll`` the interval in milliseconds or ``None``.
   * - ``zone_rendered``
     - ``ZoneRenderResult``
     - ``zone_name``, ``page_path``, ``request``, ``duration_ms``
     - After a zone body renders for a partial request.
       ``duration_ms`` times the zone render.

Subpackage signals
------------------

The aggregator ``next.signals`` forwards from the modules below.

Pages
~~~~~

.. automodule:: next.pages.signals
   :members:

Components
~~~~~~~~~~

.. automodule:: next.components.signals
   :members:

URLs
~~~~

.. automodule:: next.urls.signals
   :members:

Forms
~~~~~

.. automodule:: next.forms.signals
   :members:

Static
~~~~~~

.. automodule:: next.static.signals
   :members:

Partial rendering
~~~~~~~~~~~~~~~~~

.. automodule:: next.partial.signals
   :members:

SEO
~~~

.. automodule:: next.seo.signals
   :members:

Dependencies
~~~~~~~~~~~~

.. automodule:: next.deps.signals
   :members:

Server
~~~~~~

.. automodule:: next.server.signals
   :members:

Configuration
~~~~~~~~~~~~~

.. automodule:: next.conf.signals
   :members:

See also
--------

.. seealso::

   :doc:`/content/topics/signals` for receiver patterns and testing helpers.
