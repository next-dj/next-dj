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
       print(kwargs["action_name"])

Connect receivers from ``AppConfig.ready`` so they exist before the first request.

action_registered
-----------------

Fires when ``@action`` joins the registry.
Useful for discovery tools that build a catalogue of actions at startup.

Payload keyword arguments.
   ``sender`` is the form action backend class.
   ``action_name`` is the registered name including the namespace prefix.
   ``uid`` is the dispatch UID.
   ``form_class`` is the form class associated with the action, or ``None``.
   ``namespace`` is the namespace prefix.
   ``handler`` is the decorated callable.

.. code-block:: python
   :caption: catalog receiver

   from collections import defaultdict

   from django.dispatch import receiver

   from next.forms.signals import action_registered


   _catalog = defaultdict(list)


   @receiver(action_registered)
   def record(sender, **kwargs) -> None:
       _catalog[kwargs["form_class"]].append(kwargs["action_name"])

action_dispatched
-----------------

Fires after a handler runs successfully.
The signal is the primary integration point for audit logs and metrics.

Payload keyword arguments.
   ``sender`` is ``FormActionDispatch``.
   ``action_name`` is the action name.
   ``form`` is the bound form when the action used ``form_class``, or ``None``.
   ``url_kwargs`` is the captured URL parameters dict.
   ``duration_ms`` is the handler run time.
   ``response_status`` is the HTTP status code of the response.
   ``dep_cache`` is the request dependency cache dict.

.. code-block:: python
   :caption: audit receiver

   from django.dispatch import receiver

   from notes.models import AuditEntry

   from next.forms.signals import action_dispatched


   @receiver(action_dispatched)
   def write_audit(sender, **kwargs) -> None:
       form = kwargs["form"]
       AuditEntry.objects.create(
           action=kwargs["action_name"],
           status=kwargs["response_status"],
           data=form.cleaned_data if form else {},
       )

The signal does not carry the request.
Use ``response_status`` and ``duration_ms`` for cross cutting analytics.

form_validation_failed
----------------------

Fires when ``form.is_valid()`` returns false.
Use it for alerting on suspicious failure rates, for analytics on form abandonment, or for tests that verify the failure path.

Payload keyword arguments.
   ``sender`` is ``FormActionDispatch``.
   ``action_name`` is the action name.
   ``error_count`` is the total number of field errors.
   ``field_names`` is a tuple of the failing field names.

.. code-block:: python
   :caption: failure rate metric

   from django.dispatch import receiver

   from metrics import emit

   from next.forms.signals import form_validation_failed


   @receiver(form_validation_failed)
   def record_failure(sender, **kwargs) -> None:
       emit("form.validation_failed", tags={"action": kwargs["action_name"]})

The signal fires once per failed submission, regardless of how many fields failed.

Sender Identity
---------------

The dispatch signals use ``FormActionDispatch`` as ``sender``.
Filter on ``action_name`` to react to a specific action.

.. code-block:: python
   :caption: targeted receiver

   from django.dispatch import receiver

   from next.forms.signals import action_dispatched


   @receiver(action_dispatched)
   def on_save_note(sender, **kwargs) -> None:
       if kwargs["action_name"] != "create_note":
           return
       form = kwargs["form"]
       if form is not None:
           send_notification(form.instance)

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
