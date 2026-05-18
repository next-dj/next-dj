.. _topics-signals:

Signals
=======

next.dj emits :doc:`Django signals <django:topics/signals>` from every subsystem.
A signal is the right integration point when external code needs to react to a framework event without subclassing or monkey patching.
This page lists every signal, its payload, and the typical patterns for receiver functions.

.. contents::
   :local:
   :depth: 2

Import Surface
--------------

Every signal lives in the subpackage that emits it.
The aggregator ``next.signals`` re-exports every name so handlers can pull from a single import.

.. code-block:: python
   :caption: import patterns

   # Aggregator
   from next.signals import action_dispatched, page_rendered

   # Subpackage import
   from next.forms.signals import action_dispatched
   from next.pages.signals import page_rendered

Both styles are valid.
Use the aggregator when a single module subscribes to events from several subsystems.

Catalog
-------

Pages
~~~~~

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Name
     - Payload keyword arguments
   * - ``template_loaded``
     - ``sender`` is ``Page``. ``file_path`` is the resolved page module path.
   * - ``context_registered``
     - ``sender`` is ``PageContextRegistry``. ``file_path`` is the page module path. ``key`` is the context name.
   * - ``page_rendered``
     - ``sender`` is ``Page``. ``file_path`` is the page module path. ``duration_ms`` is the render time. ``styles_count`` and ``scripts_count`` are the collected asset counts. ``context_keys`` is a tuple of every key in the rendered context. It contains framework-internal keys such as ``request``, ``current_template_path``, ``_next_js_context``, and ``_static_collector`` alongside the user-declared context keys.

Components
~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Name
     - Payload keyword arguments
   * - ``component_registered``
     - ``sender`` is ``ComponentRegistry``. ``info`` is the ``ComponentInfo`` record.
   * - ``components_registered``
     - ``sender`` is ``ComponentRegistry``. ``infos`` is a tuple of every ``ComponentInfo`` registered in the current bulk cycle.
   * - ``component_backend_loaded``
     - ``sender`` is ``ComponentsManager``. ``backend`` is the backend instance. ``config`` is the config dict.
   * - ``component_rendered``
     - ``sender`` is the components manager class. ``info`` is the ``ComponentInfo``. ``template_path`` is the resolved template path.

Dependency Injection
~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Name
     - Payload keyword arguments
   * - ``provider_registered``
     - ``sender`` is the provider class that joined the registry. No extra keyword arguments.

URLs
~~~~

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Name
     - Payload keyword arguments
   * - ``route_registered``
     - ``sender`` is ``FileRouterBackend``. ``url_path`` is the URL path. ``file_path`` is the page module path.
   * - ``router_reloaded``
     - ``sender`` is the ``RouterManager`` class. No extra keyword arguments.

Forms
~~~~~

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Name
     - Payload keyword arguments
   * - ``action_registered``
     - ``sender`` is the form action backend class. ``action_name`` is the name. ``uid`` is the dispatch UID. ``form_class`` is the form class. ``namespace`` is the namespace prefix. ``handler`` is the callable.
   * - ``action_dispatched``
     - ``sender`` is ``FormActionDispatch``. ``action_name`` is the name. ``form`` is the bound form or ``None``. ``url_kwargs`` is the captured kwargs dict. ``duration_ms`` is the handler time. ``response_status`` is the HTTP status. ``dep_cache`` is the request dependency cache dict.
   * - ``form_validation_failed``
     - ``sender`` is ``FormActionDispatch``. ``action_name`` is the name. ``error_count`` is the total error count. ``field_names`` is a tuple of the failing field names.

Static Pipeline
~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Name
     - Payload keyword arguments
   * - ``asset_registered``
     - ``sender`` is the ``StaticAsset`` instance. ``collector`` is the collector. ``backend`` is the active static backend.
   * - ``collector_finalized``
     - ``sender`` is the collector. ``page_path`` is the page module path when the render comes from a page, or ``None`` for partial renders. ``request`` is the active ``HttpRequest`` or ``None`` outside a normal request cycle.
   * - ``html_injected``
     - ``sender`` is the static manager. ``html_before`` and ``html_after`` are the HTML around injection. ``collector`` is the collector. ``placeholders_replaced`` is a tuple of slot names. ``injected_bytes`` is the size delta. ``request`` is the active ``HttpRequest`` or ``None``.
   * - ``backend_loaded``
     - ``sender`` is the backend class. ``config`` is the config dict. ``instance`` is the backend instance.

Server and Configuration
~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Name
     - Payload keyword arguments
   * - ``watch_specs_ready``
     - ``sender`` is ``iter_all_autoreload_watch_specs``. ``specs`` is the collected watch spec list.
   * - ``settings_reloaded``
     - ``sender`` is the ``NextFrameworkSettings`` class. No extra keyword arguments.

Receiver Patterns
-----------------

Connect once at startup.

.. code-block:: python
   :caption: notes/apps.py

   from django.apps import AppConfig


   class NotesConfig(AppConfig):
       name = "notes"

       def ready(self) -> None:
           from notes import receivers  # noqa: F401

Use ``django.dispatch.receiver`` to connect a callable to a signal.

.. code-block:: python
   :caption: notes/receivers.py

   from django.dispatch import receiver

   from next.signals import action_dispatched


   @receiver(action_dispatched)
   def log_dispatch(sender, **kwargs) -> None:
       print(kwargs["action_name"])

Multiple Receivers
~~~~~~~~~~~~~~~~~~

Several receivers can connect to the same signal.
They run in registration order.

Disconnecting
~~~~~~~~~~~~~

Call ``signal.disconnect`` when a receiver should stop firing.
Test isolation utilities in ``next.testing.signals`` already disconnect for you between tests.

Test Helpers
------------

The ``SignalRecorder`` from ``next.testing.signals`` captures events for assertions.
Each captured event is a ``SignalEvent`` with ``signal``, ``sender``, and ``kwargs`` attributes.

.. code-block:: python
   :caption: test using a recorder

   from next.signals import action_dispatched
   from next.testing.client import NextClient
   from next.testing.signals import SignalRecorder


   def test_emits_action(db) -> None:
       with SignalRecorder(action_dispatched) as recorder:
           NextClient().post_action("create_note", {"title": "Hello"})

       assert len(recorder.events) == 1
       assert recorder.events[0].kwargs["action_name"] == "create_note"

See :doc:`/content/topics/testing` for the full testing surface.

Common Patterns
---------------

Audit Trail
~~~~~~~~~~~

Subscribe to ``action_dispatched`` and ``form_validation_failed`` to record every dispatched action and every rejection.

Cache Invalidation
~~~~~~~~~~~~~~~~~~

Subscribe to ``action_dispatched`` and invalidate downstream caches keyed on the affected model.

Hot Reload
~~~~~~~~~~

Subscribe to ``router_reloaded`` to refresh long-lived caches of URL references after a route change.

Observability
~~~~~~~~~~~~~

Subscribe to ``page_rendered`` and ``collector_finalized`` to emit per-request metrics for tracing and capacity planning.

See Also
--------

.. seealso::

   :doc:`/content/topics/forms/signals` for the forms-specific signals.
   :doc:`/content/topics/static-assets/signals` for the static-specific signals.
   :doc:`/content/topics/testing` for ``SignalRecorder`` and other helpers.
   :doc:`/content/howto/observe-framework-signals` for production sized receivers.
   :doc:`/content/ref/signals` for the public API.
