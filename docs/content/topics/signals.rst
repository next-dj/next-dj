.. _topics-signals:

Signals
=======

next.dj emits :doc:`Django signals <django:topics/signals>` from every subsystem.
A signal is the right integration point when external code needs to react to a framework event without subclassing or monkey patching.
This page lists every signal, its payload, and the typical patterns for receiver functions.

.. contents::
   :local:
   :depth: 2

Import surface
--------------

Every signal lives in the subpackage that emits it.
The aggregator ``next.signals`` re-exports every name so handlers can pull from a single import.

.. code-block:: python
   :caption: through the aggregator

   from next.signals import action_dispatched, page_rendered

.. code-block:: python
   :caption: through the subpackage modules

   from next.forms.signals import action_dispatched
   from next.pages.signals import page_rendered

Both styles are valid.
Use the aggregator when a single module subscribes to events from several subsystems.

Catalog
-------

Every signal the framework emits is listed below with the subsystem that emits it and the moment it fires.
:doc:`/content/ref/signals` holds the canonical payload table, the ``sender`` value and the keyword arguments for each signal.

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
     - After a context function is attached to a page module.
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
   * - ``router_backend_loaded``
     - URLs
     - After a router backend is created from its configuration entry.
   * - ``action_registered``
     - Forms
     - After the backend stores an action target for a name.
   * - ``action_dispatched``
     - Forms
     - After an action handler runs and the response is coerced, and once per valid wizard step.
   * - ``form_validation_failed``
     - Forms
     - When a bound form fails validation during dispatch.
   * - ``wizard_step_submitted``
     - Forms
     - After a ``FormWizard`` step validates during dispatch.
   * - ``wizard_completed``
     - Forms
     - After the wizard ``done`` method returns a response below HTTP 400 for the final step.
   * - ``form_access_denied``
     - Forms
     - When the origin page or a dynamic permission hook denies a request, never on the static guard path.
   * - ``form_backend_loaded``
     - Forms
     - After a form action backend is created from its configuration entry.
   * - ``wizard_backend_loaded``
     - Forms
     - After the wizard storage backend is created from its configuration entry.
   * - ``asset_registered``
     - Static
     - After a file is registered with a backend and added to the collector.
   * - ``collector_finalized``
     - Static
     - When the static manager begins injection, after rendering completes.
   * - ``html_injected``
     - Static
     - After placeholder replacement completes.
   * - ``static_backend_loaded``
     - Static
     - After the static factory instantiates a backend.
   * - ``zone_registered``
     - Partial
     - When a compiled page template's named zones are first read.
   * - ``zone_rendered``
     - Partial
     - After a zone body renders for a partial request.
   * - ``patch_op_registered``
     - Partial
     - After a custom patch verb is registered through ``register_patch_op``.
   * - ``field_validated``
     - Partial
     - After a validate-only blur pass runs, always behind the action guard.
   * - ``sse_stream_opened``
     - Partial
     - When a patch event stream starts.
   * - ``sse_stream_closed``
     - Partial
     - When a patch event stream ends.
   * - ``partial_backend_loaded``
     - Partial
     - After the partial protocol backend is created from its configuration entry.
   * - ``watch_specs_ready``
     - Server
     - After the reloader resolves the full list of watch specs.
   * - ``settings_reloaded``
     - Configuration
     - After the settings layer drops its caches.

Every settings-driven backend family announces its load through a ``*_backend_loaded`` signal carrying the configuration entry and the instance built from it, so a receiver sees what each entry produced.

The forms and static signals have dedicated topic pages with worked receiver examples, see :doc:`/content/topics/forms/signals` and :doc:`/content/topics/static-assets/signals`.
The partial-rendering stream signals appear in context in :doc:`/content/topics/partial-rendering/sse`.

Receiver patterns
-----------------

Connect once at startup.

Django's app registry is not fully initialised at module import time, so the receiver import lives inside ``ready`` rather than at module level.

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

Several receivers can connect to one signal and they run in connection order.

Failure contract
~~~~~~~~~~~~~~~~

A receiver that raises takes the sender down with it.
Every signal in the catalog above but one is sent with ``Signal.send``, which offers a raising receiver no isolation, so the exception leaves the send and reaches whatever the framework was doing.
An exception from a ``page_rendered`` receiver therefore surfaces as a failed page render, one from an ``action_dispatched`` receiver as a failed form dispatch, and one from a ``component_rendered`` receiver as a failed component.
Django runs receivers in connection order and stops at the first failure, so a receiver that raises also keeps every receiver behind it from running.

Guard the receiver body against this.
A receiver is an observation point, and its failure should cost an observation rather than a response, so wrap the work in ``try`` and log what it raised instead of letting it out.

.. code-block:: python
   :caption: notes/receivers.py

   import logging

   from django.dispatch import receiver
   from notes.metrics import record_timing

   from next.signals import page_rendered

   logger = logging.getLogger(__name__)

   @receiver(page_rendered)
   def record_render(sender, **kwargs) -> None:
       try:
           record_timing("pages.render", kwargs["duration_ms"])
       except (KeyError, OSError):
           logger.exception("page_rendered receiver failed")

``settings_reloaded`` is the single exception.
It is sent with ``Signal.send_robust``, so every receiver runs even after one raises, and the framework re-raises the first error once the chain is done.
A settings reload has to reach every manager that cached something from ``NEXT_FRAMEWORK``, and leaving the managers behind a failed receiver holding stale state would be worse than the error itself.

Disconnecting
~~~~~~~~~~~~~

Call ``signal.disconnect(receiver)`` when a receiver should stop firing.
The same ``dispatch_uid`` passed to ``connect`` can be supplied to ``disconnect`` so a receiver wired by string identifier can also be removed by that identifier.

.. code-block:: python
   :caption: notes/receivers.py

   import logging

   from next.signals import action_dispatched

   logger = logging.getLogger(__name__)

   def log_dispatch(sender, **kwargs) -> None:
       logger.info("dispatched")

   action_dispatched.connect(log_dispatch, dispatch_uid="notes.log_dispatch")

The teardown lives wherever the receiver should stop firing, such as a test fixture or a shutdown hook.

.. code-block:: python
   :caption: notes/teardown.py

   from next.signals import action_dispatched

   action_dispatched.disconnect(dispatch_uid="notes.log_dispatch")

The ``SignalRecorder`` from ``next.testing.capture`` disconnects its receivers on context-manager exit, or when ``stop()`` is called explicitly.

Test helpers
------------

The ``SignalRecorder`` from ``next.testing.capture`` captures events for assertions.
Each captured event is a ``SignalEvent`` with ``signal``, ``sender``, and ``kwargs`` attributes.

.. code-block:: python
   :caption: test using a recorder

   from next.signals import action_dispatched
   from next.testing.capture import SignalRecorder
   from next.testing.client import NextClient

   def test_emits_action(db) -> None:
       with SignalRecorder(action_dispatched) as recorder:
           NextClient().post_action("create_note", {"title": "Hello"})

       assert len(recorder.events) == 1
       assert recorder.first_for(action_dispatched).kwargs["action_name"] == "create_note"

``SignalRecorder`` also provides ``events_for(signal)``, ``first_for(signal)``, and ``last_for(signal)`` for targeted access.

See :doc:`/content/topics/testing` for the full testing surface.

Common patterns
---------------

:doc:`/content/howto/observe-framework-signals` walks through the audit trail, cache invalidation, hot reload, and observability patterns with production-sized receiver code.

See also
--------

.. seealso::

   :doc:`/content/topics/forms/signals` for the forms-specific signals.
   :doc:`/content/topics/static-assets/signals` for the static-specific signals.
   :doc:`/content/topics/testing` for ``SignalRecorder`` and other helpers.
   :doc:`/content/howto/observe-framework-signals` for production sized receivers.
   :doc:`/content/ref/signals` for the public API.
