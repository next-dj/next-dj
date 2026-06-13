.. _topics-forms-backends:

Action Backends
===============

A form action backend stores registered actions, generates their URL patterns, and dispatches submissions to handlers.
The framework ships ``RegistryFormActionBackend`` and lets a project subclass it.
This page covers the backend contract, the ``FORM_ACTION_BACKENDS`` setting, and the pattern for a custom backend.

.. contents::
   :local:
   :depth: 2

Overview
--------

A backend owns the full lifecycle of every action it registers.
It is not a middleware step in a chain.
The setting ``NEXT_FRAMEWORK["FORM_ACTION_BACKENDS"]`` lists the active backends, each as a dict with a ``BACKEND`` dotted path.

The default value registers one backend.

.. code-block:: python
   :caption: framework default

   NEXT_FRAMEWORK = {
       "FORM_ACTION_BACKENDS": [
           {"BACKEND": "next.forms.RegistryFormActionBackend", "OPTIONS": {}},
       ]
   }

Most projects never change this.
A custom backend is the right tool when every dispatch needs an extra step such as audit logging or rate limiting.

How Backends Are Instantiated
-----------------------------

``FormActionManager`` builds one backend instance per entry in ``FORM_ACTION_BACKENDS`` through ``FormActionFactory``.
The factory imports the ``BACKEND`` dotted path and calls the class with the whole config dict, including ``BACKEND`` and any ``OPTIONS``.

.. code-block:: python
   :caption: what the factory does per entry

   backend_class = import_class_cached(config["BACKEND"])
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

The base class also offers four optional override points: ``get_meta``, ``iter_actions``, ``render_invalid_page``, and ``shape_response``.

``get_meta`` and Multi-Backend Routing
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``get_meta(action_name, page_path=None)`` returns the stored ``ActionMeta`` for a name, or ``None`` when this backend does not own it.
``ActionMeta`` is a ``TypedDict`` carrying the action ``name``, the action target (``handler``, ``form_class``, ``wizard_class``), the derived ``uid``, the declaration ``file_path``, the ``scope``, and the access ``guard``.
The ``guard`` value is the ``ActionGuard`` built from the declared ``login_required`` and ``permission_required`` requirements, ``None`` when the action declares none, so a custom backend can inspect the access rules of any action it stores.

The base implementation returns ``None``.
``RegistryFormActionBackend`` returns the meta from its in-memory registry.

``get_meta`` is the routing key for the namespace build and the uid stamping when more than one backend is configured.
``FormActionManager`` asks each backend's ``get_meta`` in order and routes those two concerns to the first backend that answers with a non-``None`` meta.
The URL reverse takes a different path: the manager calls each backend's ``get_action_url`` in order and returns the first answer, collecting ``FormActionNotFound`` suggestions along the way, so ``get_meta`` plays no part in it.
The proxy method ``FormActionManager.get_action_meta`` exposes the meta resolution, and the ``{% form %}`` tag calls it to stamp the meta's ``uid`` key onto the ``data-next-action`` attribute of the rendered ``<form>`` element.
A backend whose meta omits ``uid`` therefore renders forms without that attribute, and the dispatch-time signals carry ``uid=None`` for its actions.
A custom backend whose ``get_meta`` returns ``None`` for its own actions still resolves their URLs through ``get_action_url``, so the ``{% form %}`` tag renders the form element.
That form just renders without ``data-next-action`` and without a bound form instance, and the dispatch-time signals carry ``uid=None``.
Return a truthy meta for owned names to restore the attribute, the bound form, and the uid.
A backend that defers entirely to ``RegistryFormActionBackend`` need not override ``get_meta``.

``iter_actions`` and the System Checks
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``iter_actions()`` yields the ``ActionMeta`` of every action the backend owns.
The base implementation yields nothing, and ``RegistryFormActionBackend`` yields its registry entries in registration order.
The forms system checks that inspect registered actions, the wizard checks including ``next.E054``, the component-widget checks, the guard check, and the message check, walk every configured backend through this hook, so a backend that stores its own actions should implement it, or those actions stay invisible to ``manage.py check``.
A subclass of ``RegistryFormActionBackend`` inherits a working implementation.

Shaping the Response
~~~~~~~~~~~~~~~~~~~~

Every outcome of the dispatch pipeline leaves through exactly one call to the backend's ``shape_response(request, outcome)``.
The ``outcome`` is an ``ActionOutcome``, a frozen keyword-only dataclass whose ``kind`` field is an ``ActionOutcomeKind`` member: ``RESULT`` for a handler return value, ``INVALID`` for a failed validation, and ``WIZARD_ADVANCE`` for a wizard step that moved forward.
A successful submission is always a ``RESULT`` outcome, including a handler that returns ``None``, so a backend keying off ``INVALID`` only ever sees genuine validation failures.
The other fields carry what each kind needs: the ``action_name`` and ``uid``, the raw handler return value, the bound failing ``form``, the resolved ``url_kwargs``, the wizard ``redirect_to`` target, and the live ``wizard`` instance.
On ``INVALID`` outcomes the ``page_path`` and ``origin`` fields carry the identity of the origin page that the dispatcher resolved from the posted ``_next_form_origin`` field, and a ``page_path`` of ``None`` makes the default envelope answer HTTP 400.
Fields may be added in future versions, so construct an ``ActionOutcome`` with keywords only.

The base implementation delegates to ``FormActionDispatch.shape_response``, the default envelope.
An invalid submission re-renders the origin page with HTTP 200, the ``X-Next-Form: invalid`` header, and the ``X-Next-Action`` header when the outcome carries a ``uid``.
A wizard advance redirects with HTTP 302.
Both are behaviour of the default backend, not a guarantee of the endpoint.
A custom backend may answer with any envelope.
The ``X-Next-*`` header namespace is reserved for the framework.

A ``RESULT`` outcome whose ``raw`` value is ``None`` makes the default envelope re-render the origin page internally, through ``render_invalid_page`` with ``form=None``.
That success re-render carries no ``X-Next-*`` headers and never re-enters ``shape_response``, so an envelope override observes it only as the original ``RESULT`` call.

Customisation splits into two layers.
Override ``render_invalid_page`` when only the page HTML changes.
The default envelope calls it on the invalid branch with the bound failing form, and on the success re-render of a ``None`` handler result with ``form=None``.
Override ``shape_response`` when the envelope itself changes, such as a different status code, extra headers, or a response other than a redirect on a wizard advance.

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
           entry = self._entry_for_uid(uid)
           if entry is None:
               return HttpResponseNotFound()
           action_name, meta = entry
           return FormActionDispatch.dispatch(self, request, action_name, meta)

``dispatch`` answers an unknown UID with a 404, either by returning ``HttpResponseNotFound`` or by raising :exc:`~django.http.Http404` with a message.
The bundled backend raises ``Http404`` explaining that the page which rendered the form may be stale after a rename or restart.
Delegate the validation pipeline to ``FormActionDispatch.dispatch``, which takes the action name and its stored ``ActionMeta``, to reuse form binding, handler invocation, and error re-render.

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
An unknown UID raises ``Http404`` from the parent dispatch, so no audit row is written for a stale URL.
See :doc:`/content/howto/write-a-form-action-backend` for the guarded pattern that recovers the action name from the UID index.

A ``dispatch`` override that needs to know which page initiated the submission reads the validated origin from the pipeline rather than parsing POST fields.
On the invalid branch the ``ActionOutcome`` passed to ``shape_response`` carries the resolved ``page_path`` and ``origin``, and a custom redirect target keyed off the origin page is one such use.

Registering a Custom Backend
----------------------------

List the dotted path in ``FORM_ACTION_BACKENDS``.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "FORM_ACTION_BACKENDS": [
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

A custom ``dispatch`` that drives the pipeline by hand reuses one static helper on ``FormActionDispatch``.

``ensure_http_response(response, request=None, action_name=None, backend=None)``.
   Coerces a handler return value into an ``HttpResponse``.
   A string becomes a body, an object with a ``url`` becomes a redirect, and ``None`` re-renders the origin page when ``request``, ``action_name``, and ``backend`` are passed, otherwise it returns a 204.
   The ``None`` re-render goes through ``backend.render_invalid_page`` directly, without the ``X-Next-*`` headers and without re-entering ``shape_response``.

Every outcome of the standard pipeline funnels into exactly one call to the backend hook ``shape_response(request, outcome)`` described under `Shaping the Response`_.
A layer that must reshape responses globally overrides that one hook instead of patching each dispatch branch.

Backend vs Signal
-----------------

An override runs inside the dispatch and can change or block the response.
A signal receiver runs decoupled and only observes.
Use the override when the side effect must be transactional with the dispatch.

FormActionManager
-----------------

The module-level ``form_action_manager`` instance holds the active backends behind a thin facade.
Application code reaches it through ``from next.forms.manager import form_action_manager``.
The ``backends`` property returns the configured backends in consultation order, and ``default_backend`` returns the first one.
The system checks iterate ``form_action_manager.backends`` when they collect action metadata, which is why every backend's ``iter_actions`` participates.
See :doc:`/content/ref/forms` for the full member list of ``next.forms.manager``.

The companion ``build_form_namespace_for_action(action_name, request, page_path=None)`` builds the ``{form, wizard}`` namespace the ``{% form %}`` tag normally publishes.
Call it only when rendering that namespace outside the tag, such as a custom template tag or a view that injects a form into a non-form template.
It returns ``None`` when no backend owns the action.

Testing
-------

Tests that register actions through ``@action`` must drop the global registry between cases so action names from one test do not leak into the next.
Call :func:`next.testing.reset_form_actions` from a pytest fixture or a ``setUp`` method.
The helper invokes ``form_action_manager._reload_config()``, which rebuilds the backend list from the current ``NEXT_FRAMEWORK["FORM_ACTION_BACKENDS"]`` setting and discards any actions registered against the previous backend instances.

See :doc:`/content/topics/testing` for the surrounding helpers and fixtures.

System Checks
-------------

The framework validates the backend configuration at startup.

- ``next.E044`` reports a malformed or non-importable ``FORM_ACTION_BACKENDS`` entry, including a non-string ``BACKEND`` path.
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

Custom Invalid-Page HTML
~~~~~~~~~~~~~~~~~~~~~~~~

Override ``render_invalid_page`` to return custom HTML for the validation error path.
The override signature is ``render_invalid_page(request, action_name, form, page_file_path=None, url_kwargs=None)``.
The dispatcher passes the page source path and the URL kwargs it resolved from the posted origin, so the hook never parses the request itself.
The same hook renders the success re-render of a handler that returns ``None``, where ``form`` is ``None``, so an override must tolerate the missing form.
The abstract base returns an empty string, and the bundled implementation does the same when ``page_file_path`` is ``None``.
The bundled ``RegistryFormActionBackend`` re-renders the origin page through the page-template loader.
When no action meta or template body is found, it falls back to rendering the form with its ``<p>`` layout template.
Override ``render_invalid_page`` to replace this behaviour entirely.

See Also
--------

.. seealso::

   :doc:`actions` for handler patterns.
   :doc:`signals` for the events the dispatcher emits.
   :doc:`/content/howto/write-a-form-action-backend` for a recipe.
   :doc:`/content/internals/action-dispatch` for the dispatch pipeline.
