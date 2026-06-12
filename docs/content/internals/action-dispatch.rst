.. _internals-action-dispatch:

Action Dispatch
===============

This page covers the form dispatch pipeline.
It traces a submission from the template ``{% form %}`` tag through the validation chain to the handler and the re-render path.

.. contents::
   :local:
   :depth: 2

Overview
--------

The dispatcher runs at ``/_next/form/<uid>/`` where the UID is the first 16 hex characters of a SHA-256 digest of the scope key and the action name.
The dispatcher loads the action handler, enforces the declared access guard, builds the form, runs the validation chain, and either calls the handler or re-renders the origin page.
Any non-POST method short-circuits before that work and returns HTTP 405.

Pipeline
--------

.. mermaid::

   flowchart TB
       Template["form tag in template"] --> Endpoint["form dispatch endpoint"]
       Endpoint -- "non-POST" --> NotAllowed["HTTP 405"]
       Endpoint -- POST --> Lookup["Resolve action by UID"]
       Lookup -- unknown UID --> NotFound["HTTP 404"]
       Lookup -- found --> Guard{"Access guard"}
       Guard -- anonymous --> LoginRedirect["HTTP 302 to LOGIN_URL"]
       Guard -- "missing permission" --> Forbidden["HTTP 403"]
       Guard -- "pass, no form_class" --> HandlerOnly["Run handler only"]
       Guard -- "pass, form_class" --> Build["Build form"]
       Guard -- "pass, wizard_class" --> WizardStep["Validate current wizard step"]
       HandlerOnly --> HandlerOnlyResponse["Handler response or HTTP 204"]
       HandlerOnly --> ActionDispatched["action_dispatched signal"]
       Build --> Validate{"Form valid"}
       Validate -- yes --> Handler["Run handler"]
       Handler --> Response["Handler response"]
       Handler --> ActionDispatched
       Validate -- no --> Origin{"Origin resolves"}
       Origin -- no --> BadRequest["HTTP 400"]
       Origin -- yes --> ShareCache["Reuse dep cache on request"]
       ShareCache --> RenderOrigin["Render origin page"]
       RenderOrigin --> RerenderHTML["HTTP 200 with bound form"]
       Validate -- no --> FormFailed["form_validation_failed signal"]
       WizardStep -- invalid --> Origin
       WizardStep -- invalid --> FormFailed
       WizardStep -- valid --> SaveStep["Save step draft"]
       SaveStep --> StepSubmitted["wizard_step_submitted signal"]
       SaveStep --> StepsLeft{"Steps remaining"}
       StepsLeft -- yes --> Advance["HTTP 302 to next step"]
       StepsLeft -- no --> Done["Run done with merged data"]
       Done -- "status < 400" --> Completed["Clear drafts, wizard_completed signal"]
       Done -- "status >= 400" --> KeepDrafts["Drafts kept for retry"]
       Advance --> ActionDispatched
       Done --> ActionDispatched

Modules
-------

``next.forms.decorators``.
   ``@action`` decorator implementation.
   An ``@action`` may also live in a ``component.py``, which the components backend imports during component discovery, so the action registry is populated before the first request regardless of where the decorator runs.
   See :doc:`/content/internals/component-pipeline` for the discovery walk.

``next.forms.manager``.
   ``FormActionManager`` aggregates the configured backends and yields their URL patterns.
   The per-action registry lives on each ``RegistryFormActionBackend``, not on the manager.

``next.forms.dispatch``.
   ``FormActionDispatch`` runs the pipeline per request.
   Manages the bound form, the dependency cache reuse, and the response selection.

``next.forms.backends``.
   ``FormActionBackend`` abstract contract, ``RegistryFormActionBackend`` default implementation, ``FormActionFactory``, and the ``FormActionNotFound`` exception.

``next.forms.uid``.
   ``redirect_to_origin``, ``reverse_form_action``, and ``validated_origin_path`` helpers for the origin page round trip, plus the ``ORIGIN_FIELD_NAME`` wire constant.

``next.forms.origin``.
   Resolution of the posted origin path into the page module and the typed URL kwargs, memoised per request.

``next.forms.wizard``.
   ``FormWizard`` base class, the ``FormWizardBackend`` contract with the session and cache implementations, and the ``WizardBackendManager`` holder.

``next.forms.widgets``.
   ``ComponentWidget`` and the ``bind_component_widgets`` binder the ``{% form %}`` tag calls before rendering.

``next.forms.markers``.
   ``DForm`` annotation plus the ``FormProvider`` and ``CleanedDataProvider`` classes.

``next.forms.diagnostics``.
   ``RegistrationDiagnostics`` buffers that the registration paths fill and the system checks read.

``next.forms.serializers``.
   ``FormSpec``, ``FormsetSpec``, ``FormsetRowSpec``, ``FormSectionSpec``, ``FieldSpec`` plus the builders ``form_spec``, ``formset_spec``, ``field_spec``.

``next.forms.formsets``.
   ``cleanup_extra_initial`` helper for blank extra rows.

Access Guard
------------

An action that declares ``login_required`` or ``permission_required`` carries an ``ActionGuard`` in its registry metadata under the ``guard`` key.
The shared pipeline enforces it right after the method check, ahead of origin resolution, ``get_initial``, and form binding, so no application code or database access runs for a denied request.
An anonymous user receives a redirect to ``LOGIN_URL`` whose ``next`` is the validated posted origin, and an authenticated user missing a permission raises ``PermissionDenied``.
Every backend that delegates to ``FormActionDispatch.dispatch`` inherits the enforcement.

Origin Resolution
-----------------

The hidden ``_next_form_origin`` field on every rendered form carries the URL path of the origin page.
At dispatch the field is validated as a same-site path, the script prefix from :func:`django.urls.get_script_prefix` is stripped, and the remainder is resolved through :func:`django.urls.resolve` with the per-request URLconf from ``request.urlconf`` when one is set.
The resolved match yields two things: the typed URL kwargs through the real URL converters, and the origin page source from the ``next_page_path`` attribute that the file router sets on every routed view, including the synthesised ``page.py`` location of virtual ``template.djx`` routes.
The result is memoised on the request, because the invalid re-render reads it from the dispatcher and from every ``{% form %}`` tag on the page.

A missing field, an off-site value, a path that does not resolve, or a resolved view without ``next_page_path`` all yield no origin match.
The invalid branch then returns ``HTTP 400 Missing or invalid _next_form_origin``.
A hand-written view opts into re-rendering by carrying its own ``next_page_path`` attribute, see :ref:`topics-forms-templates-handwritten-views`.

Backends
--------

The ``FORM_ACTION_BACKENDS`` setting lists the active backends.
Each backend is a full implementation of the ``FormActionBackend`` contract, not a step in a middleware chain.
A backend owns the registry, the URL generation, and the dispatch for every action it registers.

The default value registers ``RegistryFormActionBackend``.
Its ``dispatch`` method resolves the UID to an action and forwards the request to ``FormActionDispatch``, which builds the form, runs the validation chain, and resolves the posted origin when re-rendering.

A project customises dispatch by subclassing ``RegistryFormActionBackend`` and overriding ``dispatch``.
The override calls ``super().dispatch`` to keep the standard pipeline.

Shared Dependency Cache
-----------------------

The dispatcher creates a fresh dependency cache on every POST and shares it across each stage of the dispatch.
``get_initial``, the factory resolution, the handler call, and any re-render after validation failure all read and write the same cache.
Two consequences flow from this.

- Custom providers are idempotent across the dispatch stages.
- Re-render after a validation failure is cheap because layouts and context functions reuse the values cached during the initial bind.

The cache hangs on ``request`` under the attribute named ``REQUEST_DEP_CACHE_ATTR``.
Read it through ``next.deps.get_request_dep_cache(request)`` rather than the raw attribute.

The origin-page re-render also reuses the compiled page template.
The page manager caches the composed template source and its compiled ``Template`` keyed by source mtime, so a warm re-render performs no file reads and no template parsing.

Signals
-------

One signal fires at import time, the other four fire per request.

- ``action_registered`` fires at import time, once per registration when the registry stores the action target: a handler, a form class, or a wizard class.
- ``form_validation_failed`` fires at request time, once per failing submission, including a failing wizard step.
- ``action_dispatched`` fires at request time, once per successful handler invocation and once per valid wizard step, with the action name, the action uid, the live request, the bound form (``None`` for form-less actions), the URL kwargs, the handler duration, the response status, and the dispatch dependency cache in the payload.
  A wizard step advance runs no handler and reports ``duration_ms`` as ``0.0``.
- ``wizard_step_submitted`` fires at request time after a wizard step validates, with the wizard class as the sender and the step name plus a copy of its cleaned data in the payload.
- ``wizard_completed`` fires at request time after the wizard ``done`` method returns a response below HTTP 400, with the wizard class as the sender and the merged cleaned data in the payload.
  An error response from ``done`` skips the signal and keeps the saved drafts.

All four request-time signals carry ``uid`` and ``request``.
``uid`` is the registry identity also stamped on the ``data-next-action`` markup attribute, ``None`` for a backend whose meta stores no uid.
``request`` is the live ``HttpRequest`` and receivers must not retain it past the call.

Extension Points
----------------

- Subclass ``RegistryFormActionBackend`` and override ``dispatch`` to wrap the standard pipeline.
- Override ``render_invalid_page`` for custom validation-error HTML, or ``shape_response`` for a custom response envelope.
- Register the custom backend through ``FORM_ACTION_BACKENDS``.
- Subscribe to ``action_dispatched`` for audit and cache invalidation.
- Subscribe to ``form_validation_failed`` for alerting on failure rates.

See Also
--------

.. seealso::

   :doc:`/content/topics/forms/index` for the topic subtree.
   :doc:`/content/topics/forms/validation-rerender` for the failure flow.
