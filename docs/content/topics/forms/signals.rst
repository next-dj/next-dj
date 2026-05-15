.. _topics-forms-signals:

Form Signals
============

The forms subsystem emits three Django signals during action lifecycle and dispatch.
Subscribe to them to integrate with audit logs, metrics, websockets, or any other side channel.
This page lists every signal, its payload, and the typical patterns for receiver functions.

.. contents::
   :local:
   :depth: 2

Overview
--------

Every signal lives in ``next.forms.signals``.
Receivers connect through the standard Django pattern.

.. code-block:: python
   :caption: notes/receivers.py

   from django.dispatch import receiver

   from next.forms.signals import action_dispatched


   @receiver(action_dispatched)
   def log_action(sender, **kwargs) -> None:
       print(kwargs["name"])

Connect receivers from ``AppConfig.ready`` so they exist before the first request.

action_registered
-----------------

Fires when ``@action`` joins the registry.
Useful for discovery tools that build a catalogue of actions at startup.

Payload.
   ``sender`` is the form action manager class.
   ``name`` is the registered name including namespace prefix.
   ``handler`` is the decorated callable.
   ``form_class`` is the form class associated with the action, or ``None``.
   ``backends`` is the per action backend list, or an empty tuple.

.. code-block:: python
   :caption: catalog receiver

   from collections import defaultdict

   from django.dispatch import receiver

   from next.forms.signals import action_registered


   _catalog = defaultdict(list)


   @receiver(action_registered)
   def record(sender, **kwargs) -> None:
       _catalog[kwargs["form_class"]].append(kwargs["name"])

action_dispatched
-----------------

Fires after a handler runs successfully.
The signal is the primary integration point for audit logs and metrics.

Payload.
   ``sender`` is the form action manager class.
   ``name`` is the action name.
   ``request`` is the HTTP request.
   ``response`` is the handler return value.
   ``form`` is the bound form when the action used ``form_class``, or ``None``.
   ``url_kwargs`` is the captured URL parameters.

.. code-block:: python
   :caption: audit receiver

   from django.dispatch import receiver

   from notes.models import AuditEntry

   from next.forms.signals import action_dispatched


   @receiver(action_dispatched)
   def write_audit(sender, **kwargs) -> None:
       AuditEntry.objects.create(
           user=kwargs["request"].user,
           action=kwargs["name"],
           data=kwargs["form"].cleaned_data if kwargs["form"] else {},
       )

The handler return value is available through ``response``.
Use it for cross-cutting analytics that need to inspect the redirect target or response status.

form_validation_failed
----------------------

Fires when ``form.is_valid()`` returns false.
Use it for alerting on suspicious failure rates, for analytics on form abandonment, or for tests that verify the failure path.

Payload.
   ``sender`` is the form action manager class.
   ``name`` is the action name.
   ``request`` is the HTTP request.
   ``form`` is the bound form with errors.
   ``url_kwargs`` is the captured URL parameters.

.. code-block:: python
   :caption: failure rate metric

   from django.dispatch import receiver

   from metrics import emit

   from next.forms.signals import form_validation_failed


   @receiver(form_validation_failed)
   def record_failure(sender, **kwargs) -> None:
       emit("form.validation_failed", tags={"action": kwargs["name"]})

The signal fires once per failed submission, regardless of how many fields failed.

Sender Identity
---------------

Every signal uses the same ``sender``, the form action manager class.
Filter on ``name`` instead of ``sender`` to react to a specific action.

.. code-block:: python
   :caption: targeted receiver

   from django.dispatch import receiver

   from next.forms.signals import action_dispatched


   @receiver(action_dispatched)
   def on_save_note(sender, **kwargs) -> None:
       if kwargs["name"] != "create_note":
           return
       send_notification(kwargs["form"].instance)

Connect Once
------------

Place receiver registration inside ``AppConfig.ready``.
A receiver registered from a module that is imported lazily may not run until the first import of that module, which can hide signals during startup.

.. code-block:: python
   :caption: notes/apps.py

   from django.apps import AppConfig


   class NotesConfig(AppConfig):
       name = "notes"

       def ready(self) -> None:
           from notes import receivers  # noqa: F401

Disconnecting
-------------

Use ``signal.disconnect(receiver, sender)`` when a receiver should only listen for a part of the request lifecycle.
Tests can also rely on the helper ``SignalRecorder`` from :doc:`/content/topics/testing` to subscribe and unsubscribe automatically.

Common Patterns
---------------

Audit Trail
~~~~~~~~~~~

Subscribe to ``action_dispatched`` to keep a row per successful action.
Subscribe to ``form_validation_failed`` to record rejected attempts in a different table.

Cache Invalidation
~~~~~~~~~~~~~~~~~~

Subscribe to ``action_dispatched`` and invalidate cached pages whose data depends on the action.

Real Time Updates
~~~~~~~~~~~~~~~~~

Subscribe to ``action_dispatched`` and publish an event to a websocket channel.
See ``examples/live-polls`` for a worked SSE setup.

See Also
--------

.. seealso::

   :doc:`actions` for handler patterns.
   :doc:`validation-rerender` for the failure pipeline.
   :doc:`/content/topics/signals` for every signal across the framework.
   :doc:`/content/ref/signals` for the public API.
