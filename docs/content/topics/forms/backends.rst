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

How Backends Are Instantiated
-----------------------------

``FormActionManager`` builds one backend instance per entry in ``DEFAULT_FORM_ACTION_BACKENDS`` through ``FormActionFactory``.
The factory imports the ``BACKEND`` dotted path and calls the class with the whole config dict, including ``BACKEND`` and any ``OPTIONS``.

.. code-block:: python
   :caption: what the factory does per entry

   backend_class = import_class(config["BACKEND"])
   backend = backend_class(config)

The constructor therefore receives the full entry, not just ``OPTIONS``.
``RegistryFormActionBackend.__init__`` accepts the config and ignores it, which is why a subclass that reads no options needs no constructor at all.
A backend that reads an option declares a constructor and pulls ``OPTIONS`` out of the passed dict.

.. code-block:: python
   :caption: reading OPTIONS in a custom backend

   from typing import Any
   from next.forms import RegistryFormActionBackend

   class ThrottledBackend(RegistryFormActionBackend):
       def __init__(self, config: dict[str, Any] | None = None) -> None:
           super().__init__(config)
           options = (config or {}).get("OPTIONS", {})
           self._rate = options.get("RATE_PER_MINUTE", 60)

Always forward ``config`` to ``super().__init__`` so the registry is set up.

The Backend Contract
--------------------

A backend subclasses ``next.forms.FormActionBackend``, an abstract base class with four abstract methods.

``register_action(registration)``.
   Records an action from the ``@action`` decorator, a class-bound form, or a ``FormWizard``.
   The single argument is an ``ActionRegistration`` carrying the ``name``, the declaration-site ``file_path``, the ``scope``, and the action target as one of ``handler``, ``form_class``, or ``wizard_class``.

``get_action_url(action_name, *, page_path=None)``.
   Returns the reverse URL for an action name.
   With ``page_path=None`` the lookup uses the global name index.
   Passing the declaring page file path narrows the lookup to that page's scope, which disambiguates page-scoped actions that share a name across pages.

``generate_urls()``.
   Returns the URLconf entries for every registered action.

``dispatch(request, uid)``.
   Runs the handler for the given action UID and returns an ``HttpResponse``.

The base class also offers two optional override points, ``get_meta`` and ``render_form_fragment``.

``get_meta`` and Multi-Backend Routing
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``get_meta(action_name, page_path=None)`` returns the stored ``ActionMeta`` for a name, or ``None`` when this backend does not own it.
``ActionMeta`` is a ``TypedDict`` carrying the action target (``handler``, ``form_class``, ``wizard_class``), the derived ``uid``, the declaration ``file_path``, and the ``scope``.

The base implementation returns ``None``.
``RegistryFormActionBackend`` returns the meta from its in-memory registry.

``get_meta`` is the routing key when more than one backend is configured.
``FormActionManager`` asks each backend's ``get_meta`` in order and routes the URL reverse and the namespace build to the first backend that answers with a non-``None`` meta.
A custom backend that owns its own actions must return a truthy meta for those names, or the manager skips it and the ``{% form %}`` tag cannot find the action.
A backend that defers entirely to ``RegistryFormActionBackend`` need not override ``get_meta``.

Building a Backend From Scratch
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Most backends subclass ``RegistryFormActionBackend``.
Implementing ``FormActionBackend`` directly is for a backend that stores actions somewhere other than the in-memory registry, such as a database or an external service.
The four abstract methods must all be present.

.. code-block:: python
   :caption: a minimal from-scratch backend skeleton

   from typing import Any
   from django.http import HttpRequest, HttpResponse, HttpResponseNotFound
   from django.urls import path
   from next.forms import ActionRegistration, FormActionBackend
   from next.forms.dispatch import FormActionDispatch

   class CustomBackend(FormActionBackend):
       def __init__(self, config: dict[str, Any] | None = None) -> None:
           self._actions: dict[str, ActionRegistration] = {}

       def register_action(self, registration: ActionRegistration) -> None:
           self._actions[registration.name] = registration

       def get_action_url(self, action_name: str, *, page_path: str | None = None) -> str:
           ...

       def generate_urls(self) -> list:
           return [path("_next/custom/<str:uid>/", self.dispatch)]

       def dispatch(self, request: HttpRequest, uid: str) -> HttpResponse:
           meta = self._meta_for_uid(uid)
           if meta is None:
               return HttpResponseNotFound()
           return FormActionDispatch.dispatch(self, request, meta["name"], meta)

``dispatch`` must always return an ``HttpResponse``.
Return ``HttpResponseNotFound`` for an unknown UID rather than raising, so a stale URL gets a clean 404.
Delegate the validation pipeline to ``FormActionDispatch.dispatch`` to reuse form binding, handler invocation, and error re-render.

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

A custom ``dispatch`` that drives the pipeline by hand reuses two static helpers on ``FormActionDispatch``.

``form_response(backend, request, action_name, form)``.
   Returns the full-page HTML for an invalid form by re-rendering the origin page with the bound failing form.
   This is the error path a custom ``dispatch`` calls when ``form.is_valid()`` returns false.

``ensure_http_response(response, request=None, action_name=None, backend=None)``.
   Coerces a handler return value into an ``HttpResponse``.
   A string becomes a body, an object with a ``url`` becomes a redirect, and ``None`` re-renders through ``form_response`` when ``request``, ``action_name``, and ``backend`` are passed, otherwise it returns a 204.

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

The companion ``build_form_namespace_for_action(action_name, request, page_path=None)`` builds the ``{form, wizard}`` namespace the ``{% form %}`` tag normally publishes.
Call it only when rendering that namespace outside the tag, such as a custom template tag or a view that injects a form into a non-form template.
It returns ``None`` when no backend owns the action.

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
The override signature is ``render_form_fragment(request, action_name, form, page_file_path=None)``.
The abstract base returns an empty string.
The bundled ``RegistryFormActionBackend`` re-renders the origin page through the page-template loader.
When no action meta or template body is found, it falls back to rendering the form with its ``<p>`` layout template.
Override ``render_form_fragment`` to replace this behaviour entirely.

See Also
--------

.. seealso::

   :doc:`actions` for handler patterns.
   :doc:`signals` for the events the dispatcher emits.
   :doc:`/content/howto/write-a-form-action-backend` for a recipe.
   :doc:`/content/internals/action-dispatch` for the dispatch pipeline.
