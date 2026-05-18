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
       Template["form tag in template"] -- POST --> Endpoint["form dispatch endpoint"]
       Endpoint --> Lookup["Resolve action"]
       Lookup --> Origin{"Origin page valid"}
       Origin -- no --> BadRequest["HTTP 400"]
       Origin -- yes --> Build["Build form"]
       Build --> Validate{"Form valid"}
       Validate -- yes --> Handler["Run handler"]
       Handler --> Response["Handler response"]
       Validate -- no --> ShareCache["Attach dep cache to request"]
       ShareCache --> RenderOrigin["Render origin page"]
       RenderOrigin --> RerenderHTML["HTTP 200 with bound form"]
       Handler --> ActionDispatched["action_dispatched signal"]
       Validate -- no --> FormFailed["form_validation_failed signal"]

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
The dispatcher resolves the path to an existing file under ``BASE_DIR``.
Anything else returns HTTP 400.

Backends
--------

The ``DEFAULT_FORM_ACTION_BACKENDS`` setting lists the active backends.
Each backend is a full implementation of the ``FormActionBackend`` contract, not a step in a middleware chain.
A backend owns the registry, the URL generation, and the dispatch for every action it registers.

The default value registers ``RegistryFormActionBackend``.
Its ``dispatch`` method resolves the UID, validates the origin page, builds the form, and runs the pipeline through ``FormActionDispatch``.

A project customises dispatch by subclassing ``RegistryFormActionBackend`` and overriding ``dispatch``.
The override calls ``super().dispatch`` to keep the standard pipeline.

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

- Subclass ``RegistryFormActionBackend`` and override ``dispatch`` to wrap the standard pipeline.
- Register the custom backend through ``DEFAULT_FORM_ACTION_BACKENDS``.
- Subscribe to ``action_dispatched`` for audit and cache invalidation.
- Subscribe to ``form_validation_failed`` for alerting on failure rates.

See Also
--------

.. seealso::

   :doc:`/content/topics/forms/index` for the topic subtree.
   :doc:`/content/topics/forms/validation-rerender` for the failure flow.
