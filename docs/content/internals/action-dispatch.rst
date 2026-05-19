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

The dispatcher runs at ``/_next/form/<uid>/`` where the UID is a 16 character hash of the action name.
The dispatcher loads the action handler, builds the form, runs the validation chain, and either calls the handler or re-renders the origin page.
Any non-POST method short-circuits before that work and returns HTTP 405.

Pipeline
--------

.. mermaid::

   flowchart TB
       Template["form tag in template"] -- POST --> Endpoint["form dispatch endpoint"]
       Template -- "non-POST" --> NotAllowed["HTTP 405"]
       Endpoint --> Lookup["Resolve action by UID"]
       Lookup -- unknown UID --> NotFound["HTTP 404"]
       Lookup -- "found, no form_class" --> HandlerOnly["Run handler only"]
       Lookup -- "found, form_class" --> Build["Build form"]
       HandlerOnly --> HandlerOnlyResponse["Handler response or HTTP 204"]
       HandlerOnly --> ActionDispatched["action_dispatched signal"]
       Build --> Validate{"Form valid"}
       Validate -- yes --> Handler["Run handler"]
       Handler --> Response["Handler response"]
       Handler --> ActionDispatched
       Validate -- no --> Origin{"Origin page valid"}
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
   ``redirect_to_origin`` and ``validated_next_form_page_path`` helpers for the origin page round trip.

``next.forms.markers``.
   ``DForm`` annotation and ``FormProvider`` class.

``next.forms.serializers``.
   ``FormSpec``, ``FormsetSpec``, ``FormsetRowSpec``, ``FormSectionSpec``, ``FieldSpec`` plus the builders ``form_spec``, ``formset_spec``, ``field_spec``.

``next.forms.formsets``.
   ``cleanup_extra_initial`` helper for blank extra rows.

Origin Page Validation
----------------------

The hidden ``_next_form_page`` field on every rendered form carries the absolute path to the origin ``page.py``.
The dispatcher resolves the path and accepts it only when the basename is ``page.py``, the resolved path lives under ``BASE_DIR``, and either the file exists or a sibling ``template.djx`` does.
The sibling ``template.djx`` clause is the virtual-route exception: a directory routed by its template alone has no ``page.py`` on disk yet still resolves a valid origin.
A blank field, an ``OSError`` while resolving, an unset ``BASE_DIR``, or a path outside ``BASE_DIR`` all return HTTP 400.

Backends
--------

The ``DEFAULT_FORM_ACTION_BACKENDS`` setting lists the active backends.
Each backend is a full implementation of the ``FormActionBackend`` contract, not a step in a middleware chain.
A backend owns the registry, the URL generation, and the dispatch for every action it registers.

The default value registers ``RegistryFormActionBackend``.
Its ``dispatch`` method resolves the UID to an action and forwards the request to ``FormActionDispatch``, which builds the form, runs the validation chain, and validates the origin page when re-rendering.

A project customises dispatch by subclassing ``RegistryFormActionBackend`` and overriding ``dispatch``.
The override calls ``super().dispatch`` to keep the standard pipeline.

Shared Dependency Cache
-----------------------

The dispatcher reuses the dependency cache from the initial render path on the re-render path.
Two consequences flow from this.

- Custom providers are idempotent across initial render and re-render.
- Re-render is cheap because layouts and context functions reuse cached values.

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
- Register the custom backend through ``DEFAULT_FORM_ACTION_BACKENDS``.
- Subscribe to ``action_dispatched`` for audit and cache invalidation.
- Subscribe to ``form_validation_failed`` for alerting on failure rates.

See Also
--------

.. seealso::

   :doc:`/content/topics/forms/index` for the topic subtree.
   :doc:`/content/topics/forms/validation-rerender` for the failure flow.
