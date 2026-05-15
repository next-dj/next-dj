.. _topics-forms-backends:

Action Backends
===============

The forms subsystem dispatches every submission through a chain of backends.
A backend can validate, transform, log, or block a submission before the handler runs.
This page covers the default chain, the ``DEFAULT_FORM_ACTION_BACKENDS`` setting, the ``FormActionBackend`` contract, and the pattern for adding a custom backend without losing the defaults.

.. contents::
   :local:
   :depth: 2

Overview
--------

A dispatch goes through every backend in order.
Each backend can short circuit the chain by raising a Django ``ValidationError`` or by returning an early response.
The default chain validates the action name, the origin page, the form data, and emits the relevant signals.

The setting ``NEXT_FRAMEWORK["DEFAULT_FORM_ACTION_BACKENDS"]`` lists the active backends as dotted paths.
The default value is sufficient for most projects.
Override the list when you need to insert audit logging, custom rate limiting, or per tenant restrictions.

Default Backends
----------------

The default chain holds two entries.

``next.forms.backends.OriginPageBackend``.
   Validates the ``_next_form_page`` field, resolves the origin page, and prepares the cache that supports re-render.

``next.forms.backends.FormDispatchBackend``.
   Constructs the form, runs ``is_valid``, and either calls the handler or triggers the re-render pipeline.

Two more backends are bundled but not enabled by default.

``next.forms.backends.RateLimitBackend``.
   Throttles submissions by IP and action name.
   Enable when public endpoints accept form submissions.

``next.forms.backends.AuditBackend``.
   Records every dispatch in an audit log.
   Useful for admin flows where every action needs a trace.

FormActionBackend Contract
--------------------------

A custom backend subclasses ``next.forms.backends.FormActionBackend``.

.. code-block:: python
   :caption: notes/backends.py

   from django.http import HttpRequest, HttpResponse

   from next.forms.backends import FormActionBackend
   from next.forms.dispatch import DispatchContext


   class GeoBlockBackend(FormActionBackend):
       blocked_countries = {"XX"}

       def dispatch(
           self,
           request: HttpRequest,
           context: DispatchContext,
       ) -> HttpResponse | None:
           country = request.META.get("HTTP_X_GEO_COUNTRY", "")
           if country in self.blocked_countries:
               return HttpResponse(status=403)
           return None

A backend implements one method.

``dispatch(request, context)``.
   Receives the HTTP request and the dispatch context.
   Returns ``None`` to continue the chain, an ``HttpResponse`` to short circuit, or raises ``ValidationError`` to surface a form error.

The chain stops on the first ``HttpResponse``.
The remaining backends do not run.

Activating a Custom Backend
---------------------------

Add the dotted path to ``DEFAULT_FORM_ACTION_BACKENDS``.

.. code-block:: python
   :caption: config/settings.py

   from next.conf import extend_default_backend

   NEXT_FRAMEWORK = {
       "DEFAULT_FORM_ACTION_BACKENDS": extend_default_backend(
           "DEFAULT_FORM_ACTION_BACKENDS",
           "notes.backends.GeoBlockBackend",
           position="before",
           target="next.forms.backends.FormDispatchBackend",
       ),
   }

The ``extend_default_backend`` helper merges with the framework defaults instead of replacing them.
Supported positions are ``before``, ``after``, ``first``, and ``last``.

You can also write the list directly.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_FORM_ACTION_BACKENDS": [
           "next.forms.backends.OriginPageBackend",
           "notes.backends.GeoBlockBackend",
           "next.forms.backends.FormDispatchBackend",
       ],
   }

A direct list gives you full control over ordering, at the cost of having to track framework defaults yourself across upgrades.

DispatchContext
---------------

The context object carries everything the backend needs.

action_name.
   The registered action name including namespace prefix.

handler.
   The decorated callable that will run on a valid form.

form_class.
   The form class registered with the action, or ``None``.

url_kwargs.
   The captured URL parameters from the dispatch URL.

dep_cache.
   The dependency cache for the request.
   Shared with the page render and the potential re-render.

origin_path.
   The absolute path to the origin ``page.py``, set by the ``OriginPageBackend``.

A custom backend that runs after ``OriginPageBackend`` can read all of these fields.
A backend that runs before ``OriginPageBackend`` sees a partially populated context.

Per Action Backends
-------------------

A handler can opt into extra backends through a decorator argument.

.. code-block:: python
   :caption: per action backends

   from next.forms import action


   @action(
       "create_note",
       form_class=NoteForm,
       backends=["notes.backends.AuditBackend"],
   )
   def create_note(form: NoteForm): ...

These backends run in addition to ``DEFAULT_FORM_ACTION_BACKENDS``.
The order is project defaults first, per action backends second.

Custom Validation Backend
-------------------------

A backend can also enforce validation that does not fit on the form class.
Raise ``ValidationError`` and the dispatcher routes the error through the standard re-render pipeline.

.. code-block:: python
   :caption: per request validation

   from django.core.exceptions import ValidationError
   from django.http import HttpRequest, HttpResponse

   from next.forms.backends import FormActionBackend


   class WorkingHoursBackend(FormActionBackend):
       def dispatch(self, request, context) -> HttpResponse | None:
           if context.action_name == "publish_post":
               hour = self._current_hour(request)
               if hour < 9 or hour > 18:
                   raise ValidationError("Publishing is allowed during business hours only.")
           return None

The user sees the error rendered on the form as a non field error.

System Checks
-------------

The framework validates the configured backends at startup.

- ``next.E060`` reports a backend dotted path that cannot be imported.
- ``next.E061`` reports a backend class that does not inherit from ``FormActionBackend``.

Run ``uv run python manage.py check`` after editing the backend list.

Common Patterns
---------------

Audit Log
~~~~~~~~~

Enable ``AuditBackend`` to record every dispatch.
See ``examples/audit-forms`` for the implementation.

Rate Limiting
~~~~~~~~~~~~~

Enable ``RateLimitBackend`` on endpoints exposed to anonymous users.
Configure the throttle through Django cache.

Tenant Isolation
~~~~~~~~~~~~~~~~

Write a backend that blocks dispatches when the form belongs to another tenant.
The backend reads ``request.user`` and ``form.instance`` and raises ``ValidationError`` on mismatch.

See Also
--------

.. seealso::

   :doc:`actions` for handler patterns.
   :doc:`signals` for the events the dispatcher emits.
   :doc:`/content/howto/write-a-form-action-backend` for a step-by-step recipe.
   :doc:`/content/howto/extend-a-default-backend` for the helper details.
   :doc:`/content/internals/action-dispatch` for the dispatcher internals.
