.. _topics-forms-signals:

Form Signals
============

The forms subsystem emits ``action_registered``, ``action_dispatched``, ``form_validation_failed``, ``wizard_step_submitted``, ``wizard_completed``, and ``form_access_denied`` from ``next.forms.signals``.
All six import equivalently from the owning module or from the aggregator ``next.signals``.
Register receiver imports from ``AppConfig.ready`` so receivers exist before the first request.

Every dispatch-time signal (``action_dispatched``, ``form_validation_failed``, ``wizard_step_submitted``, ``wizard_completed``, ``form_access_denied``) carries two shared keyword arguments.
``uid`` is the registry identity of the action, the same value the dispatch URL and the ``data-next-action`` markup attribute carry, or ``None`` when a custom backend stores no uid in its meta.
``request`` is the live ``HttpRequest`` being dispatched.
Read what you need from it inside the receiver and do not retain the object past the receiver call.

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
           from notes import receivers  # noqa: F401, PLC0415

.. _topics-forms-signals-action-registered:

action_registered
-----------------

Fires once per form class, ``FormWizard`` class, or ``@action`` call when the backend stores the action target.
This happens at import time, before any request lands.
The sender is the backend class.

The payload carries ``action_name``, ``uid``, ``form_class``, ``wizard_class``, ``file_path``, ``scope``, and ``handler``.
Exactly one of ``handler``, ``form_class``, or ``wizard_class`` identifies the registered target, except the ``@action(form_class=...)`` path which supplies a handler and a form factory together.
``form_class`` is the form class for a class-bound registration, a factory callable for a dynamic formset action, or ``None`` otherwise.
``wizard_class`` is the ``FormWizard`` subclass for a wizard registration, ``None`` otherwise.
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
It also fires once per valid wizard step: a step advance runs no handler and reports ``duration_ms`` as ``0.0``, and the finalising step times the ``done`` call.
The sender is ``FormActionDispatch``.

The payload carries ``action_name``, ``uid``, ``request``, ``form``, ``url_kwargs``, ``duration_ms``, ``response_status``, and ``dep_cache``.

``uid``.
   The registry identity of the action, matching the dispatch URL and the ``data-next-action`` attribute, or ``None`` for a backend without meta.

``request``.
   The live ``HttpRequest``. Do not retain it past the receiver call.

``form``.
   The bound form after the handler returns normally and the response has been coerced, or ``None`` for a handler-only action registered without a ``form_class``.
   A handler that raises an exception aborts the dispatch and the signal does not fire.

``url_kwargs``.
   A copy of the URL kwargs the dispatcher resolved before invoking the handler.

``duration_ms``.
   Wall-clock time the handler itself took, in milliseconds. It does not include form validation or dependency resolution.
   A wizard step advance runs no handler and reports ``0.0``, so receivers averaging handler latency should skip zero-duration wizard events.

``dep_cache``.
   A snapshot of the dispatch dependency cache. Receivers can read named ``Depends("name")`` values resolved during the dispatch without re-running their providers.
   The dict is a shallow copy taken when the signal fires, so mutating it does not change the live dispatch cache.

A receiver that needs request data reads it from ``request`` directly or from ``dep_cache``, without keeping a reference after it returns.

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

The payload carries ``action_name``, ``uid``, ``request``, ``error_count``, and ``field_names``.
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
The sender is the wizard class, so a receiver connected with ``sender=MyWizard`` fires for that wizard only.

The payload carries ``step``, ``cleaned_data``, ``uid``, and ``request``.
``step`` is the step name from ``Meta.steps``.
``cleaned_data`` is a copy of the validated cleaned data for that step.

.. code-block:: python
   :caption: access/receivers.py

   import logging
   from django.dispatch import receiver
   from access.wizards import AccessRequestWizard
   from next.forms.signals import wizard_step_submitted

   logger = logging.getLogger("access.wizard")

   @receiver(wizard_step_submitted, sender=AccessRequestWizard)
   def log_step(sender, *, step, cleaned_data, **kwargs) -> None:
       logger.info("step %s submitted for %s", step, sender.__name__)

The signal fires once per validated step, so a multi-step submission emits one event per step.
Connect without ``sender`` to observe every wizard and read the class from the ``sender`` argument.

.. _topics-forms-signals-wizard-completed:

wizard_completed
----------------

Fires after the wizard ``done`` method runs for the final step and its response shapes below HTTP 400.
The sender is the wizard class, so a receiver connected with ``sender=MyWizard`` fires for that wizard only.

The payload carries ``cleaned_data``, ``uid``, and ``request``.
``cleaned_data`` is the merged mapping passed to ``done``, flattening the keys of every step form.

.. code-block:: python
   :caption: access/receivers.py

   from django.dispatch import receiver
   from next.forms.signals import wizard_completed

   @receiver(wizard_completed)
   def log_completion(sender, *, cleaned_data, **kwargs) -> None:
       logger.info("wizard %s completed", sender.__name__)

The signal fires once per completed wizard, after ``done`` returns a success response.
An error response from ``done``, status 400 or above, skips the signal and keeps the saved drafts for retry.

.. _topics-forms-signals-form-access-denied:

form_access_denied
------------------

Fires only when a dynamic permission hook denies a request, never on the static ``ActionGuard`` path.
The sender is ``FormActionDispatch``.
See :ref:`topics-forms-actions-dynamic-guards` for the hooks themselves.

The payload carries ``action_name``, ``uid``, ``request``, ``layer``, and ``reason``.
``layer`` is ``"view"`` for a ``check_permissions`` denial or ``"object"`` for a ``has_object_permission`` denial.
``reason`` is ``"raised"`` when the hook raised :exc:`~django.core.exceptions.PermissionDenied`, ``"denied"`` when it returned ``False``, or ``"response"`` when it returned an ``HttpResponse`` short-circuit.

The dispatcher builds the payload and sends the signal only when at least one receiver is connected, so an audit receiver adds no cost to an allowed request.

.. code-block:: python
   :caption: notes/receivers.py

   import logging
   from django.dispatch import receiver
   from next.forms.signals import form_access_denied

   logger = logging.getLogger("notes.access")

   @receiver(form_access_denied)
   def audit_denial(sender, *, action_name, layer, reason, request, **kwargs) -> None:
       logger.warning(
           "access denied for %s at %s layer (%s) for user %s",
           action_name,
           layer,
           reason,
           request.user,
       )

A receiver runs inside the dispatch, so keep it cheap and do not retain the request past the call.

See Also
--------

.. seealso::

   :doc:`actions` for handler patterns.
   :doc:`validation-rerender` for the failure pipeline.
   :doc:`/content/topics/signals` for the full catalog and testing helpers.
   :doc:`/content/ref/signals` for the public API.
