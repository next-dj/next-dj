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

Every signal the framework emits is listed below with the subsystem that
emits it and the moment it fires. :doc:`/content/ref/signals` holds the
canonical payload table, the ``sender`` value and the keyword arguments for
each signal.

.. list-table::
   :header-rows: 1
   :widths: 26 18 56

   * - Signal
     - Subsystem
     - When it fires
   * - ``template_loaded``
     - Pages
     - After a template source is registered on a page.
   * - ``context_registered``
     - Pages
     - After a context callable is attached to a page module.
   * - ``page_rendered``
     - Pages
     - After the page renders to HTML and the static assets are injected.
   * - ``component_registered``
     - Components
     - After a single component is registered.
   * - ``components_registered``
     - Components
     - After a batch of components is registered.
   * - ``component_backend_loaded``
     - Components
     - After a component backend is created from its configuration entry.
   * - ``component_rendered``
     - Components
     - After a component is rendered to HTML.
   * - ``provider_registered``
     - Dependencies
     - When a ``RegisteredParameterProvider`` subclass joins the registry.
   * - ``route_registered``
     - URLs
     - After a URL pattern is created for a discovered page.
   * - ``router_reloaded``
     - URLs
     - After the router manager rebuilds its pattern set.
   * - ``action_registered``
     - Forms
     - After the backend stores a handler for an action name.
   * - ``action_dispatched``
     - Forms
     - After an action handler runs and the response is coerced.
   * - ``form_validation_failed``
     - Forms
     - When a bound form fails validation during dispatch.
   * - ``asset_registered``
     - Static
     - After a file is registered with a backend and added to the collector.
   * - ``collector_finalized``
     - Static
     - When the static manager begins injection, after rendering completes.
   * - ``html_injected``
     - Static
     - After placeholder replacement completes.
   * - ``backend_loaded``
     - Static
     - After the static factory instantiates a backend.
   * - ``watch_specs_ready``
     - Server
     - After the reloader resolves the full list of watch specs.
   * - ``settings_reloaded``
     - Configuration
     - After the settings layer drops its caches.

The forms and static signals have dedicated topic pages with worked receiver
examples: :doc:`/content/topics/forms/signals` and
:doc:`/content/topics/static-assets/signals`.

Receiver Patterns
-----------------

Connect once at startup.

The import sits inside ``ready`` on purpose.
Module-level imports of app code run before the app registry is ready.

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

   import logging
   from django.dispatch import receiver
   from next.signals import action_dispatched

   logger = logging.getLogger(__name__)

   @receiver(action_dispatched)
   def log_dispatch(sender, **kwargs) -> None:
       logger.info("action dispatched: %s", kwargs["action_name"])

Multiple Receivers
~~~~~~~~~~~~~~~~~~

Several receivers can connect to the same signal.
They run in registration order.

Disconnecting
~~~~~~~~~~~~~

Call ``signal.disconnect`` when a receiver should stop firing.
The ``SignalRecorder`` from ``next.testing.signals`` disconnects its receivers on context-manager exit, or when ``stop()`` is called explicitly.

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

:doc:`/content/howto/observe-framework-signals` walks through the audit trail, cache invalidation, hot reload, and observability patterns with production-sized receiver code.

See Also
--------

.. seealso::

   :doc:`/content/topics/forms/signals` for the forms-specific signals.
   :doc:`/content/topics/static-assets/signals` for the static-specific signals.
   :doc:`/content/topics/testing` for ``SignalRecorder`` and other helpers.
   :doc:`/content/howto/observe-framework-signals` for production sized receivers.
   :doc:`/content/ref/signals` for the public API.
