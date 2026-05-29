.. _topics-forms-signals:

Form Signals
============

The forms subsystem emits ``action_registered``, ``action_dispatched``, ``form_validation_failed``, ``wizard_step_submitted``, and ``wizard_completed`` from ``next.forms.signals``.
Import either from the owning module or from the aggregator ``next.signals``.
Register receiver imports from ``AppConfig.ready`` so receivers exist before the first request.

.. contents::
   :local:
   :depth: 2

Connecting Receivers
--------------------

Receivers live in a module that Django does not import on its own.
Import that module from ``AppConfig.ready`` so every receiver is connected before the first request.

.. code-block:: python
   :caption: notes/apps.py

   from django.apps import AppConfig

   class NotesConfig(AppConfig):
       name = "notes"

       def ready(self) -> None:
           from notes import receivers  # noqa: F401

.. _topics-forms-signals-action-registered:

action_registered
-----------------

Fires once per form class or ``@action`` call when the backend stores the handler.
This happens at import time, before any request lands.
The sender is the backend class.

The payload carries ``action_name``, ``uid``, ``form_class``, ``file_path``, ``scope``, and ``handler``.
``form_class`` is the form class for a class-bound registration, a factory callable for a dynamic formset action, or ``None`` for a form-less handler.
``file_path`` is the absolute path to the file where the class or function was declared.
``scope`` is ``"page"`` for anchor-file declarations (``page.py``, ``component.py``) and ``"shared"`` for all other files.

.. code-block:: python
   :caption: notes/receivers.py

   import logging
   from django.dispatch import receiver
   from next.forms.signals import action_registered

   logger = logging.getLogger("notes.actions")

   @receiver(action_registered)
   def record_action(sender, *, action_name, uid, **kwargs) -> None:
       logger.info("registered action %s at uid %s", action_name, uid)

Use this to build an inventory of every action in the project, for example a debug page that lists registered handlers.

Read ``file_path`` and ``scope`` from the payload to group actions by their registration source.

.. code-block:: python
   :caption: grouping by scope

   @receiver(action_registered)
   def group_by_scope(sender, *, action_name, file_path, scope, **kwargs) -> None:
       logger.info("action %s declared in %s (%s scope)", action_name, file_path, scope)

.. _topics-forms-signals-action-dispatched:

action_dispatched
-----------------

Fires after a handler runs and the response has been coerced.
The sender is ``FormActionDispatch``.

The payload carries ``action_name``, ``form``, ``url_kwargs``, ``duration_ms``, ``response_status``, and ``dep_cache``.

``form``.
   The bound form after the handler returns normally and the response has been coerced, or ``None`` for a handler-only action registered without a ``form_class``.
   A handler that raises an exception aborts the dispatch and the signal does not fire.

``url_kwargs``.
   A copy of the URL kwargs the dispatcher resolved before invoking the handler.

``duration_ms``.
   Wall-clock time the handler itself took, in milliseconds. It does not include form validation or dependency resolution.

``dep_cache``.
   A snapshot of the dispatch dependency cache. Receivers can read named ``Depends("name")`` values resolved during the dispatch without re-running their providers.
   The dict is a shallow copy taken when the signal fires, so mutating it does not change the live dispatch cache.

The signal does not pass ``request``. Read what you need from ``dep_cache`` or have the handler attach audit data.

.. code-block:: python
   :caption: notes/receivers.py

   from django.dispatch import receiver
   from next.forms.signals import action_dispatched

   SLOW_MS = 250.0

   @receiver(action_dispatched)
   def warn_on_slow_action(sender, *, action_name, duration_ms, **kwargs) -> None:
       if duration_ms > SLOW_MS:
           logger.warning("action %s took %.1fms", action_name, duration_ms)

Reading a named dependency from ``dep_cache`` lets a receiver reuse a value the handler already resolved.

.. code-block:: python
   :caption: reading a cached dependency

   @receiver(action_dispatched)
   def audit_tenant(sender, *, action_name, dep_cache, response_status, **kwargs) -> None:
       tenant = dep_cache.get("current_tenant")
       if tenant is not None:
           AuditLog.objects.create(
               tenant=tenant,
               action=action_name,
               status=response_status,
           )

Filter on ``action_name`` when a receiver should observe only one action.

.. _topics-forms-signals-form-validation-failed:

form_validation_failed
----------------------

Fires when the bound form fails validation during dispatch.
The sender is ``FormActionDispatch``.

The payload carries ``action_name``, ``error_count``, and ``field_names``.
``error_count`` is the total number of error messages, including non-field errors raised from ``clean``.
``field_names`` is a tuple of the keys that failed, with non-field errors appearing under ``__all__``.

The dispatcher only builds the payload and sends the signal when at least one receiver is connected, so an unused signal costs nothing.

.. code-block:: python
   :caption: notes/receivers.py

   from django.dispatch import receiver
   from next.forms.signals import form_validation_failed

   @receiver(form_validation_failed)
   def count_failures(sender, *, action_name, error_count, field_names, **kwargs) -> None:
       logger.info(
           "validation failed for %s: %d errors on %s",
           action_name,
           error_count,
           ", ".join(field_names),
       )

The signal fires once per failed submission, so log volume scales with the failure rate rather than the request rate.

.. _topics-forms-signals-wizard-step-submitted:

wizard_step_submitted
---------------------

Fires after a ``FormWizard`` step validates during dispatch.
The sender is ``FormActionDispatch``.

The payload carries ``wizard_class``, ``step``, and ``cleaned_data``.
``wizard_class`` is the wizard subclass that owns the step.
``step`` is the step name from ``Meta.steps``.
``cleaned_data`` is a copy of the validated cleaned data for that step.

.. code-block:: python
   :caption: access/receivers.py

   import logging
   from django.dispatch import receiver
   from next.forms.signals import wizard_step_submitted

   logger = logging.getLogger("access.wizard")

   @receiver(wizard_step_submitted)
   def log_step(sender, *, wizard_class, step, cleaned_data, **kwargs) -> None:
       logger.info("step %s submitted for %s", step, wizard_class.__name__)

The signal fires once per validated step, so a multi-step submission emits one event per step.

.. _topics-forms-signals-wizard-completed:

wizard_completed
----------------

Fires after the wizard ``done`` method runs for the final step.
The sender is ``FormActionDispatch``.

The payload carries ``wizard_class`` and ``cleaned_data``.
``cleaned_data`` is the merged mapping passed to ``done``, flattening the keys of every step form.

.. code-block:: python
   :caption: access/receivers.py

   from django.dispatch import receiver
   from next.forms.signals import wizard_completed

   @receiver(wizard_completed)
   def log_completion(sender, *, wizard_class, cleaned_data, **kwargs) -> None:
       logger.info("wizard %s completed", wizard_class.__name__)

The signal fires once per completed wizard, after ``done`` returns its response.

See Also
--------

.. seealso::

   :doc:`actions` for handler patterns.
   :doc:`validation-rerender` for the failure pipeline.
   :doc:`/content/topics/signals` for the full catalog and testing helpers.
   :doc:`/content/ref/signals` for the public API.
