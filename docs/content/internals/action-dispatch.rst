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

Pipeline
--------

.. mermaid::

   flowchart TB
       Template[{% form %} tag] -- POST --> Endpoint[/_next/form/uid/]
       Endpoint --> Lookup[Resolve action]
       Lookup --> Origin{Origin page valid}
       Origin -- no --> BadRequest[HTTP 400]
       Origin -- yes --> Build[Build form]
       Build --> Validate{Form valid}
       Validate -- yes --> Handler[Run handler]
       Handler --> Response[Handler response]
       Validate -- no --> FrozenSpec[Save frozen FormSpec]
       FrozenSpec --> ShareCache[Reuse dep cache]
       ShareCache --> RenderOrigin[Render origin page]
       RenderOrigin --> RerenderHTML[HTTP 200 with bound form]
       Handler --> ActionDispatched[(action_dispatched)]
       Validate -- no --> FormFailed[(form_validation_failed)]

Modules
-------

``next.forms.decorators``.
   ``@action`` decorator implementation.

``next.forms.manager``.
   ``FormActionManager`` holds the registry of actions plus the lookup helpers.

``next.forms.dispatch``.
   ``FormActionDispatch`` runs the pipeline per request.
   Manages the bound form, the dependency cache reuse, and the response selection.

``next.forms.backends``.
   ``FormActionBackend`` contract plus the bundled backends ``OriginPageBackend``, ``FormDispatchBackend``, ``RateLimitBackend``, ``AuditBackend``.
   ``RegistryFormActionBackend`` is the default chain entry that ties the manager to the dispatcher.

``next.forms.uid``.
   ``action_url``, ``redirect_to_origin``, ``validated_next_form_page_path``, ``FORM_ACTION_REVERSE_NAME``, ``URL_NAME_FORM_ACTION``.

``next.forms.markers``.
   ``DForm`` annotation and ``FormProvider`` class.

``next.forms.serializers``.
   ``FormSpec``, ``FormsetSpec``, ``FormSectionSpec``, ``FieldSpec`` plus the builders ``form_spec``, ``formset_spec``, ``field_spec``.

``next.forms.formsets``.
   ``cleanup_extra_initial`` helper for blank extra rows.

Origin Page Validation
----------------------

The hidden ``_next_form_page`` field on every rendered form carries the absolute path to the origin ``page.py``.
The dispatcher resolves the path to an existing file under ``BASE_DIR``.
Anything else returns HTTP 400.

Backend Chain
-------------

The dispatcher iterates the configured backends in order.
Each backend implements ``dispatch(request, context)`` and can return ``HttpResponse`` to short circuit, raise ``ValidationError`` to surface a form error, or return ``None`` to continue.

The default chain holds two entries.

1. ``OriginPageBackend`` checks the hidden field.
2. ``FormDispatchBackend`` builds the form, runs validation, and either calls the handler or fires the re-render path.

A project that adds ``RateLimitBackend`` or ``AuditBackend`` does so through ``extend_default_backend``.

Frozen Form Spec
----------------

Before re-rendering on failure the dispatcher builds a ``FormSpec`` describing the bound failing form.
The spec lives on the request and is available to context functions that want to introspect the form layout.

Shared Dependency Cache
-----------------------

The dispatcher reuses the dependency cache from the initial render path on the re-render path.
Two consequences flow from this.

- Custom providers are idempotent across initial render and re-render.
- Re-render is cheap because layouts and context functions reuse cached values.

The cache hangs on ``request`` under the attribute named ``REQUEST_DEP_CACHE_ATTR``.

Signals
-------

The pipeline fires three signals.

- ``action_registered`` once per ``@action`` when the registry receives it.
- ``form_validation_failed`` once per failing submission.
- ``action_dispatched`` once per successful handler invocation, with the bound form and the URL kwargs in the payload.

Extension Points
----------------

- Subclass ``FormActionBackend`` to add a step to the chain.
- Use ``extend_default_backend`` to inject the backend at a precise position.
- Subscribe to ``action_dispatched`` for audit and cache invalidation.
- Subscribe to ``form_validation_failed`` for alerting on failure rates.

See Also
--------

.. seealso::

   :doc:`/content/topics/forms/index` for the topic subtree.
   :doc:`/content/topics/forms/validation-rerender` for the failure flow.
