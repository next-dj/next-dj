.. _topics-signals:

Signals
=======

next.dj emits Django signals from every subsystem.
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
     - Payload
   * - ``template_loaded``
     - ``sender`` is the loader, ``template_path`` is the resolved path, ``page_module_path`` is the page module file.
   * - ``context_registered``
     - ``sender`` is the page manager class, ``key`` is the context name, ``callable`` is the function, ``inherit_context`` is the inheritance flag.
   * - ``page_rendered``
     - ``sender`` is the page manager class, ``request`` is the HTTP request, ``page_module_path`` is the page file, ``html`` is the rendered body.

Components
~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Name
     - Payload
   * - ``component_registered``
     - ``sender`` is the components manager, ``name`` is the component name, ``path`` is the component folder.
   * - ``components_registered``
     - ``sender`` is the components manager, ``components`` is a tuple of every component registered in the current bulk cycle.
   * - ``component_backend_loaded``
     - ``sender`` is the components manager, ``backend`` is the backend instance, ``options`` is the config dict.
   * - ``component_rendered``
     - ``sender`` is the components manager, ``name`` is the component, ``html`` is the rendered fragment, ``request`` is the HTTP request.

Dependency Injection
~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Name
     - Payload
   * - ``provider_registered``
     - ``sender`` is the resolver, ``provider`` is the class that joined the registry.

URLs
~~~~

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Name
     - Payload
   * - ``route_registered``
     - ``sender`` is the router manager class, ``pattern`` is the URL pattern, ``name`` is the URL name.
   * - ``router_reloaded``
     - ``sender`` is the router manager class.

Forms
~~~~~

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Name
     - Payload
   * - ``action_registered``
     - ``sender`` is the form action manager class, ``name`` is the action name, ``handler`` is the callable, ``form_class`` is the form class, ``backends`` is the per-action backend tuple.
   * - ``action_dispatched``
     - ``sender`` is the form action manager class, ``name`` is the action name, ``request`` is the HTTP request, ``response`` is the handler return value, ``form`` is the bound form, ``url_kwargs`` is the captured kwargs.
   * - ``form_validation_failed``
     - ``sender`` is the form action manager class, ``name`` is the action name, ``request`` is the HTTP request, ``form`` is the bound failing form, ``url_kwargs`` is the captured kwargs.

Static Pipeline
~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 30 70

   * - Name
     - Payload
   * - ``asset_registered``
     - ``sender`` is the discovery class, ``asset`` is the asset record, ``owner`` is the component or page that owns it.
   * - ``collector_finalized``
     - ``sender`` is the static manager class, ``request`` is the HTTP request, ``collector`` is the collector instance.
   * - ``html_injected``
     - ``sender`` is the static manager class, ``bucket`` is the bucket name, ``html`` is the rendered HTML, ``request`` is the HTTP request.
   * - ``backend_loaded``
     - ``sender`` is the static manager class, ``backend`` is the backend class, ``options`` is the config dict.

Server and Configuration
~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 35 65

   * - Name
     - Payload
   * - ``watch_specs_ready``
     - ``sender`` is the autoreload watcher class, ``specs`` is the collected watch spec list.
   * - ``settings_reloaded``
     - ``sender`` is the settings class, ``settings`` is the new ``NextFrameworkSettings`` snapshot.

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
       print(kwargs["name"])

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

The ``SignalRecorder`` from ``next.testing.signals`` captures payloads for assertions.

.. code-block:: python
   :caption: test using a recorder

   from next.signals import action_dispatched
   from next.testing.signals import SignalRecorder


   def test_emits_action(client) -> None:
       with SignalRecorder(action_dispatched) as recorder:
           client.post("/_next/form/abc/", {"title": "Hello"})

       assert len(recorder.calls) == 1
       assert recorder.calls[0].kwargs["name"] == "create_note"

See :doc:`testing` for the full testing surface.

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
   :doc:`/content/ref/signals` for the public API.
