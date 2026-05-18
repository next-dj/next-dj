.. _ref-signals:

Signals Reference
=================

Module Summary
--------------

``next.signals`` is an aggregator that re-exports every signal emitted by the framework.
Importing a signal from ``next.signals`` is equivalent to importing it from its subpackage.

Signal Catalog
--------------

Every signal below is a Django ``Signal``. The ``sender`` column lists the value
passed to ``Signal.send``. Receivers connected with a matching ``sender`` only
fire for that sender.

.. list-table::
   :header-rows: 1
   :widths: 18 18 34 30

   * - Signal
     - Sender
     - Keyword arguments
     - When it fires
   * - ``action_dispatched``
     - ``FormActionDispatch``
     - ``action_name``, ``form``, ``url_kwargs``, ``duration_ms``, ``response_status``, ``dep_cache``
     - After an action handler runs and the response is coerced. ``form`` is the bound form, or ``None`` for handler-only actions. ``duration_ms`` times the handler call. ``dep_cache`` is a copy of the dispatch dependency-injection cache.
   * - ``action_registered``
     - Form action backend class
     - ``action_name``, ``uid``, ``form_class``, ``namespace``, ``handler``
     - After the backend stores a handler for an action name.
   * - ``asset_registered``
     - The ``StaticAsset`` instance
     - ``collector``, ``backend``
     - After a file is registered with a backend and added to the collector.
   * - ``backend_loaded``
     - The static backend class
     - ``config``, ``instance``
     - After the static factory instantiates a backend.
   * - ``collector_finalized``
     - The static collector
     - ``page_path``, ``request``
     - When the static manager begins injection, after template rendering completes. ``page_path`` may be ``None`` for partial renders. ``request`` may be ``None`` outside a request.
   * - ``component_backend_loaded``
     - ``ComponentsManager``
     - ``backend``, ``config``
     - After a component backend is created from its configuration entry.
   * - ``component_registered``
     - ``ComponentRegistry``
     - ``info``
     - After a single component is registered. Not fired from the bulk path.
   * - ``component_rendered``
     - ``ComponentsManager``
     - ``info``, ``template_path``
     - After a component is rendered to HTML. ``template_path`` may be ``None`` for components without a template file.
   * - ``components_registered``
     - ``ComponentRegistry``
     - ``infos``
     - After a batch of components is registered. ``infos`` is the tuple of added components.
   * - ``context_registered``
     - ``PageContextRegistry``
     - ``file_path``, ``key``
     - After a context callable is attached to a page module.
   * - ``form_validation_failed``
     - ``FormActionDispatch``
     - ``action_name``, ``error_count``, ``field_names``
     - When the bound form fails validation during dispatch.
   * - ``html_injected``
     - A ``StaticManager`` instance
     - ``html_before``, ``html_after``, ``collector``, ``placeholders_replaced``, ``injected_bytes``, ``request``
     - After placeholder replacement completes. ``placeholders_replaced`` is the tuple of replaced slot names. ``injected_bytes`` is the length delta.
   * - ``page_rendered``
     - ``Page``
     - ``file_path``, ``duration_ms``, ``styles_count``, ``scripts_count``, ``context_keys``
     - After ``Page.render`` produces HTML and injects static assets. ``duration_ms`` times the render. ``context_keys`` is the tuple of context keys.
   * - ``provider_registered``
     - The ``RegisteredParameterProvider`` subclass
     - none
     - When a ``RegisteredParameterProvider`` subclass is added to the auto-registry.
   * - ``route_registered``
     - ``FileRouterBackend``
     - ``url_path``, ``file_path``
     - After a URL pattern is created for a discovered page.
   * - ``router_reloaded``
     - The router manager class
     - none
     - After the router manager rebuilds its pattern set.
   * - ``settings_reloaded``
     - ``NextFrameworkSettings``
     - none
     - After ``NextFrameworkSettings.reload`` drops its caches.
   * - ``template_loaded``
     - ``Page``
     - ``file_path``
     - After a template source is registered on a page.
   * - ``watch_specs_ready``
     - ``iter_all_autoreload_watch_specs``
     - ``specs``
     - After the reloader resolves the full list of watch specs.

Aggregated Signals
------------------

.. automodule:: next.signals
   :members:
   :imported-members:

Subpackage Signals
------------------

The aggregator simply forwards from these modules.

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

See Also
--------

.. seealso::

   :doc:`/content/topics/signals` for the catalog with payload tables.
