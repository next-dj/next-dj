.. _topics-forms-backends:

Action Backends
===============

A form action backend stores registered actions, generates their URL patterns, and dispatches submissions to handlers.
The framework ships ``RegistryFormActionBackend`` and lets a project subclass it.
This page covers the backend contract, the ``DEFAULT_FORM_ACTION_BACKENDS`` setting, and the pattern for a custom backend.

.. contents::
   :local:
   :depth: 2

Overview
--------

A backend owns the full lifecycle of every action it registers.
It is not a middleware step in a chain.
The setting ``NEXT_FRAMEWORK["DEFAULT_FORM_ACTION_BACKENDS"]`` lists the active backends, each as a dict with a ``BACKEND`` dotted path.

The default value registers one backend.

.. code-block:: python
   :caption: framework default

   NEXT_FRAMEWORK = {
       "DEFAULT_FORM_ACTION_BACKENDS": [
           {"BACKEND": "next.forms.RegistryFormActionBackend"},
       ]
   }

Most projects never change this.
A custom backend is the right tool when every dispatch needs an extra step such as audit logging or rate limiting.

The Backend Contract
--------------------

A backend subclasses ``next.forms.FormActionBackend``, an abstract base class with four abstract methods.

``register_action(name, handler, *, options)``.
   Records an action from the ``@action`` decorator.

``get_action_url(action_name)``.
   Returns the reverse URL for an action name.

``generate_urls()``.
   Returns the URLconf entries for every registered action.

``dispatch(request, uid)``.
   Runs the handler for the given action UID and returns an ``HttpResponse``.

The base class also offers two optional override points, ``get_meta`` and ``render_form_fragment``.

RegistryFormActionBackend
-------------------------

``RegistryFormActionBackend`` is the bundled implementation.
It keeps an in memory registry, builds dispatch URLs of the form ``/_next/form/<uid>/``, and runs the validation pipeline through ``FormActionDispatch``.

Subclass it to keep all of that behaviour and add your own step.

Writing a Custom Backend
------------------------

The most common customisation overrides ``dispatch`` to wrap the standard dispatch with extra work.

.. code-block:: python
   :caption: notes/backends.py

   from django.http import HttpRequest, HttpResponse

   from next.forms import RegistryFormActionBackend

   from notes.models import AuditEntry


   class AuditedFormActionBackend(RegistryFormActionBackend):
       """Registry backend that writes an audit row per dispatch."""

       def dispatch(self, request: HttpRequest, uid: str) -> HttpResponse:
           action_name = self._uid_to_name.get(uid)
           if action_name is None:
               return super().dispatch(request, uid)
           response = super().dispatch(request, uid)
           AuditEntry.objects.create(
               action_name=action_name,
               response_status=response.status_code,
           )
           return response

The override calls ``super().dispatch`` to run the standard pipeline.
The ``self._uid_to_name`` mapping resolves a UID to an action name.
An unknown UID returns 404 from the parent dispatch, so the override skips it.

Registering a Custom Backend
----------------------------

List the dotted path in ``DEFAULT_FORM_ACTION_BACKENDS``.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "DEFAULT_FORM_ACTION_BACKENDS": [
           {"BACKEND": "notes.backends.AuditedFormActionBackend"},
       ]
   }

The backend replaces ``RegistryFormActionBackend`` because the custom class already inherits every default behaviour.

FormActionDispatch
------------------

The validation pipeline lives in ``next.forms.dispatch.FormActionDispatch``.
``RegistryFormActionBackend.dispatch`` calls into it to bind the form, run ``is_valid``, invoke the handler, and emit the signals.
A custom backend that overrides ``dispatch`` reuses this pipeline through ``super().dispatch``.

Override the validation pipeline itself only when subclassing ``RegistryFormActionBackend`` is not enough, which is rare.

Backend vs Signal
-----------------

Two channels observe a dispatch.

Backend override.
   Runs inside the dispatch call.
   Sees the raw request and the response.
   Can change the response or block the dispatch.

Signal receiver.
   Runs decoupled from the dispatch.
   Receives the ``action_dispatched`` payload.
   Cannot change the response.

Use a backend override when the audit row must be transactional with the dispatch.
Use a signal receiver when the side effect is independent and can tolerate eventual consistency.
See ``examples/audit-forms`` for a project that uses both channels side by side.

System Checks
-------------

The framework validates the backend configuration at startup.

- ``next.E044`` reports a malformed ``DEFAULT_FORM_ACTION_BACKENDS`` entry.
- ``next.E045`` reports a backend that does not subclass ``FormActionBackend``.

Run ``uv run python manage.py check`` after editing the backend list.

Common Patterns
---------------

Audit Log
~~~~~~~~~

Subclass ``RegistryFormActionBackend`` and override ``dispatch`` to write an audit row.
See ``examples/audit-forms``.

Rate Limiting
~~~~~~~~~~~~~

Override ``dispatch`` to check a rate limit before calling ``super().dispatch`` and return an ``HttpResponse`` with status 429 when the limit is exceeded.

Custom Error Fragment
~~~~~~~~~~~~~~~~~~~~~

Override ``render_form_fragment`` to return custom HTML for the validation error path.

See Also
--------

.. seealso::

   :doc:`actions` for handler patterns.
   :doc:`signals` for the events the dispatcher emits.
   :doc:`/content/howto/write-a-form-action-backend` for a recipe.
   :doc:`/content/internals/action-dispatch` for the dispatch pipeline.
