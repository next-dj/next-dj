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
           {"BACKEND": "next.forms.RegistryFormActionBackend", "OPTIONS": {}},
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

The public method ``clear_registry()`` drops every registered action and resets the UID index.
It exists for test isolation, so a test session that registers overlapping action names can start from an empty registry.
Production code never calls it.

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
           response = super().dispatch(request, uid)
           AuditEntry.objects.create(
               action_uid=uid,
               response_status=response.status_code,
           )
           return response

The override calls ``super().dispatch`` to run the standard pipeline and records the dispatch UID against the response status.
An unknown UID returns 404 from the parent dispatch, and the audit row still records that outcome.
See :doc:`/content/howto/write-a-form-action-backend` for the guarded pattern that recovers the action name from the UID index.

The ``validated_next_form_page_path(request)`` helper validates the hidden ``_next_form_page`` POST field and returns a trusted ``Path | None``.
Call it inside a ``dispatch`` override when the custom backend needs to know which page initiated the form submission.
A custom redirect target keyed off that page is one such case.

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

An override runs inside the dispatch and can change or block the response.
A signal receiver runs decoupled and only observes.
Use the override when the side effect must be transactional with the dispatch.

FormActionManager
-----------------

The module-level ``form_action_manager`` instance holds the active backends behind a thin facade.
Application code reaches it through ``from next.forms import form_action_manager``.
See :doc:`/content/ref/forms` for the full member list of ``next.forms.manager``.

Testing
-------

Tests that register actions through ``@action`` must drop the global registry between cases so action names from one test do not leak into the next.
Call :func:`next.testing.reset_form_actions` from a pytest fixture or a ``setUp`` method.
The helper invokes ``form_action_manager._reload_config()``, which rebuilds the backend list from the current ``NEXT_FRAMEWORK["DEFAULT_FORM_ACTION_BACKENDS"]`` setting and discards any actions registered against the previous backend instances.

See :doc:`/content/topics/testing` for the surrounding helpers and fixtures.

System Checks
-------------

The framework validates the backend configuration at startup.

- ``next.E044`` reports a malformed or non-importable ``DEFAULT_FORM_ACTION_BACKENDS`` entry, including a non-string ``BACKEND`` path.
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
The override signature is ``render_form_fragment(request, action_name, form, template_fragment=None, *, page_file_path=None)``.
The bundled ``RegistryFormActionBackend`` ignores ``template_fragment`` and always re-renders the origin page through the page-template loader.
The argument stays in the signature so an override can use it, but the default implementation does not consult it.
When no action meta or template body is found, the default implementation falls back to ``form.as_p()``.
Override ``render_form_fragment`` to replace this fallback entirely.

See Also
--------

.. seealso::

   :doc:`actions` for handler patterns.
   :doc:`signals` for the events the dispatcher emits.
   :doc:`/content/howto/write-a-form-action-backend` for a recipe.
   :doc:`/content/internals/action-dispatch` for the dispatch pipeline.
