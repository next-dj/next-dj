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
The dispatcher loads the action handler, builds the form, runs the validation chain, and either calls the handler or re-renders the origin page.
Any non-POST method short-circuits before that work and returns HTTP 405.

Pipeline
--------

.. mermaid::

   flowchart TB
       Template["form tag in template"] --> Endpoint["form dispatch endpoint"]
       Endpoint -- "non-POST" --> NotAllowed["HTTP 405"]
       Endpoint -- POST --> Lookup["Resolve action by UID"]
       Lookup -- unknown UID --> NotFound["HTTP 404"]
       Lookup -- "found, no form_class" --> HandlerOnly["Run handler only"]
       Lookup -- "found, form_class" --> Build["Build form"]
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
   ``FormActionBackend`` abstract contract, ``RegistryFormActionBackend`` default implementation, and ``FormActionFactory``.

``next.forms.uid``.
   ``redirect_to_origin`` and ``reverse_form_action`` helpers for the origin page round trip.

``next.forms.markers``.
   ``DForm`` annotation and ``FormProvider`` class.

``next.forms.serializers``.
   ``FormSpec``, ``FormsetSpec``, ``FormsetRowSpec``, ``FormSectionSpec``, ``FieldSpec`` plus the builders ``form_spec``, ``formset_spec``, ``field_spec``.

``next.forms.formsets``.
   ``cleanup_extra_initial`` helper for blank extra rows.

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

Signals
-------

One signal fires at import time, the other two fire per request.

- ``action_registered`` fires at import time, once per ``@action`` when the registry receives it.
- ``form_validation_failed`` fires at request time, once per failing submission.
- ``action_dispatched`` fires at request time, once per successful handler invocation, with the action name, the bound form (``None`` for form-less actions), the URL kwargs, the handler duration, the response status, and the dispatch dependency cache in the payload.

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
