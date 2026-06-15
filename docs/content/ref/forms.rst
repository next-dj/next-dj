.. _ref-forms:

Forms Reference
===============

Module Summary
--------------

``next.forms`` exposes form base classes, the ``@action`` decorator,
formset helpers, frozen field and form specs,
and a curated set of commonly used Django form fields and widgets.
Any public ``django.forms`` name is also importable from ``next.forms``,
see `Fields and Widgets`_ for the contract.

API Tiers
---------

The forms surface splits into tiers that describe the intended audience for each name.
The lists below are representative.
The autodoc blocks under `Public API`_ are the exhaustive surface.

Stable.
   ``Form``, ``ModelForm``, ``BaseForm``, ``BaseModelForm``, ``@action``, ``redirect_to_origin``,
   ``FormWizard``, ``DForm``, ``FormActionNotFound``, and ``autodiscover_forms``.
   Use these in application code.
   Form classes self-register, so reach for ``@action`` only for form-less handlers.

Advanced.
   ``FormActionBackend``, ``RegistryFormActionBackend``,
   ``ActionOutcome``, ``ActionOutcomeKind``,
   ``ActionRegistration``, ``ActionGuard``, ``ComponentWidget``,
   ``FormWizardBackend``, ``SessionFormWizardBackend``, ``CacheFormWizardBackend``,
   the frozen specs (``FieldSpec``, ``FormsetSpec``, ``FormSpec``, ``FormSectionSpec``,
   ``FormsetRowSpec``, ``FieldKind``), the spec helpers (``field_spec``, ``form_spec``,
   ``formset_spec``), the formset helper ``cleanup_extra_initial``,
   the ``PermissionOutcome`` type alias for the dynamic permission hooks,
   and the ``signals`` and ``checks`` submodules.
   Use these when writing a custom backend or a form renderer.

Framework machinery.
   The wiring lives on the owning submodules and is not re-exported at the package level.
   ``FormActionDispatch`` lives in ``next.forms.dispatch``.
   ``FormActionManager``, the ``form_action_manager`` instance, and
   ``build_form_namespace_for_action`` live in ``next.forms.manager``.
   ``ActionMeta``, ``FormActionFactory``, ``file_to_dotted_module``, ``scope_key_for``,
   ``build_action_guard``, and ``record_possible_collision`` live in ``next.forms.backends``.
   ``WizardBackendManager`` and the ``wizard_backend_manager`` instance live in
   ``next.forms.wizard``.
   ``FormProvider`` and ``CleanedDataProvider`` live in ``next.forms.markers``.
   ``bind_component_widgets`` lives in ``next.forms.widgets``.
   ``render_form_page_with_errors`` lives in ``next.forms.rendering``.
   ``RegistrationDiagnostics`` and the ``registration_diagnostics`` instance live in
   ``next.forms.diagnostics``.
   The UID helpers ``FORM_ACTION_REVERSE_NAME``, ``URL_NAME_FORM_ACTION``,
   ``ORIGIN_FIELD_NAME``, ``reverse_form_action``, and ``validated_origin_path``
   live in ``next.forms.uid``.
   The test isolation helper ``reset_form_registration_state`` belongs to ``next.testing``,
   documented under :doc:`/content/ref/testing`.

Internal hooks.
   Underscore-prefixed helpers inside the submodules, such as the form-building functions in
   ``next.forms.dispatch``, are implementation details.
   ``next.forms.__all__`` is the source of truth for the curated package surface and exports no
   underscore names.
   Do not import underscore names in application code.

Public API
----------

Autodiscover
~~~~~~~~~~~~

.. autofunction:: next.forms.autodiscover_forms

The helper wraps Django's :func:`~django.utils.module_loading.autodiscover_modules`, so re-runs are no-ops while Python caches the imported modules.
The test isolation helper :func:`next.testing.reset_form_registration_state` clears the registries and the registration diagnostics, see :doc:`/content/ref/testing`.

Decorator
~~~~~~~~~

.. autofunction:: next.forms.action

Exceptions
~~~~~~~~~~

``FormActionNotFound`` is raised when no registered action matches a requested name.
``FormActionManager.get_action_url``, the ``{% form %}`` and ``{% action_url %}`` tags, and the testing helpers ``resolve_action_url`` and ``build_form_for`` all raise it.
It subclasses ``LookupError`` and carries the failing ``name``, the ``page_path`` that was searched, the close-match ``suggestions`` tuple, and the ``registry_empty`` flag.
Every raising surface renders the suggestions into the message as ``Closest matches: 'x', 'y'``, computed by close-match comparison against the registered names.
The comparison and the message run on first render, so probing for an action by catching the exception costs no close-match work.
When ``registry_empty`` is true the message also explains that no actions are registered at all and points at autodiscovery.

.. autoexception:: next.forms.FormActionNotFound
   :members:

Form Base Classes
~~~~~~~~~~~~~~~~~

``check_permissions`` and ``has_object_permission`` are the opt-in dynamic permission hooks.
Both return ``PermissionOutcome``, the ``bool | HttpResponse | None`` alias re-exported from ``next.forms``.
See :ref:`topics-forms-actions-dynamic-guards` for the authoring contract and the ordering against the static guard.

.. autoclass:: next.forms.Form
   :members:

.. autoclass:: next.forms.ModelForm
   :members:

.. autoclass:: next.forms.BaseForm
   :members: get_initial, get_success_message, on_valid, check_permissions, has_object_permission

.. autoclass:: next.forms.BaseModelForm
   :members: get_initial, get_success_message, on_valid, check_permissions, has_object_permission

Form Wizard
~~~~~~~~~~~

``FormWizard`` routes a sequence of step forms across requests.
``FormWizardBackend`` is the draft-persistence contract, ``SessionFormWizardBackend`` is the bundled default, and ``CacheFormWizardBackend`` is the cache-backed alternative.
The wizard ``check_permissions`` classmethod is the view-level dynamic permission hook, enforced per step POST.
See :doc:`/content/topics/forms/wizard` and :doc:`/content/topics/forms/wizard-backend` for the topic guides.

.. autoclass:: next.forms.FormWizard
   :members:

.. autoclass:: next.forms.FormWizardBackend
   :members:

.. autoclass:: next.forms.SessionFormWizardBackend
   :members:

.. autoclass:: next.forms.CacheFormWizardBackend
   :members:

``WizardBackendManager`` is the lazy holder for the single configured wizard backend,
exposed as the ``wizard_backend_manager`` instance in ``next.forms.wizard``.
It reads ``FORM_WIZARD_BACKEND`` on first use and caches the result.

.. autoclass:: next.forms.wizard.WizardBackendManager
   :members:

Fields and Widgets
~~~~~~~~~~~~~~~~~~

The framework re-exports a curated set of commonly used Django form fields and widgets through
``next.forms`` so a single import covers most form definitions.
The package surface is a superset of ``django.forms`` by construction: any public ``django.forms``
name resolves through ``next.forms``, with the framework versions winning where next.dj overrides
a name such as ``Form`` or ``ModelForm``, and every other name resolving to the Django original.
The factories ``formset_factory``, ``modelformset_factory``, ``inlineformset_factory``, and
``modelform_factory`` plus ``BoundField`` are re-exported statically so type checkers see them,
the rest of the passthrough resolves at runtime through the module ``__getattr__``.
The submodules ``next.forms.widgets`` and ``next.forms.formsets`` carry the same passthrough for
the public names of ``django.forms.widgets`` and ``django.forms.formsets`` respectively.

.. automodule:: next.forms.base
   :members:
   :exclude-members: BaseForm, BaseModelForm, Form, ModelForm

``ComponentWidget`` renders a field through a registered next.dj component instead of a Django widget template.
See :doc:`/content/topics/forms/field-components` for the topic guide.

.. autoclass:: next.forms.ComponentWidget
   :members:

``bind_component_widgets`` injects the page scope path, the live request, the static collector,
and optionally the field errors onto every ``ComponentWidget`` of a form before rendering.
The ``{% form %}`` tag calls it, so application code needs it only when rendering a component-widget
form outside the tag.
It accepts a form or a formset and binds every member form of a formset, which is how formset
rendering through ``{% form %}`` carries component widgets.
It imports from ``next.forms.widgets`` directly.

.. autofunction:: next.forms.widgets.bind_component_widgets

Markers
~~~~~~~

``DForm`` is re-exported from ``next.forms``.
The provider classes import from ``next.forms.markers`` directly.
``FormProvider`` auto-registers through the ``__init_subclass__`` hook on ``RegisteredParameterProvider`` and resolves the bound ``form`` parameter, so application code never instantiates it.

.. automodule:: next.forms.markers
   :members:

Dispatch
~~~~~~~~

``FormActionDispatch``, ``ActionOutcome``, and ``ActionOutcomeKind`` are the public members of this module.
``ActionOutcome`` and ``ActionOutcomeKind`` are re-exported from ``next.forms``, while ``FormActionDispatch`` imports from ``next.forms.dispatch`` directly.
``ActionOutcome`` is the frozen keyword-only dataclass a backend's ``shape_response`` hook receives, with ``ActionOutcomeKind`` as its ``kind`` discriminator.
On ``INVALID`` outcomes the ``page_path`` and ``origin`` fields carry the resolved identity of the origin page: the source location of its ``page.py`` and the validated origin URL path.
``FormActionDispatch.shape_response`` builds the default envelope for one outcome, and the backend hook delegates to it unless overridden.
``ensure_http_response`` coerces a handler return value into an ``HttpResponse``, kept for custom backends that drive the pipeline by hand.
The underscore-prefixed helpers are internal hooks per the ``Internal hooks`` tier described above.

.. automodule:: next.forms.dispatch
   :members:

Manager
~~~~~~~

``FormActionManager`` holds the configured backends behind the module-level ``form_action_manager`` instance.
``build_form_namespace_for_action`` builds the ``{form, wizard}`` namespace the ``{% form %}`` tag consumes, for code rendering that namespace by hand outside the tag.
All three import from ``next.forms.manager``.
``FormActionManager.require_action_meta`` returns the resolved ``ActionMeta`` or raises ``FormActionNotFound`` with close-match suggestions, for callers that cannot proceed without the meta.

.. automodule:: next.forms.manager
   :members:

Backends
~~~~~~~~

``ActionRegistration`` is the value object passed to ``register_action``.
It carries the action ``name``, the declaration-site ``file_path``, the ``scope``, the optional access ``guard``, and the action target.
The target is one of ``handler``, ``form_class``, or ``wizard_class``, which lets a single ``register_action`` call serve the ``@action`` decorator, a class-bound form, and a ``FormWizard``.
``ActionGuard`` is the frozen access-requirement record built from ``Meta.login_required`` and ``Meta.permission_required`` or the matching ``@action`` keywords.
It is stored under the ``guard`` key of ``ActionMeta`` and enforced by the dispatch pipeline before the form is built, so custom backends see the declared requirements without extra wiring.
``iter_actions`` yields every stored ``ActionMeta``, including its ``name`` key, which is how the forms system checks inspect any configured backend.
``ActionMeta``, ``FormActionFactory``, and ``file_to_dotted_module`` import from ``next.forms.backends`` directly.
``FormActionFactory`` instantiates one backend per ``FORM_ACTION_BACKENDS`` entry, passing the whole config dict to the backend constructor.
``FormActionManager`` calls it, so application code rarely does.
``scope_key_for`` derives the registry scope key from a declaration file path and a scope, the same key that partitions actions and wizard storage.
``build_action_guard`` builds an ``ActionGuard`` from the declared ``login_required`` and ``permission_required`` values, or ``None`` when both are unset.
``record_possible_collision`` files a name collision into the registration diagnostics when a name is re-registered with a distinct handler, feeding the ``next.E041`` check.
All three import from ``next.forms.backends`` directly.

.. automodule:: next.forms.backends
   :members:

Rendering
~~~~~~~~~

``render_form_page_with_errors`` re-renders the origin page template with a bound form in context.
It is the body of ``FormActionBackend.render_invalid_page`` in the bundled backend and imports from ``next.forms.rendering`` directly.
The rendered HTML flows through the static-assets pipeline, so co-located CSS and JS land in the response.

.. automodule:: next.forms.rendering
   :members: render_form_page_with_errors

Registration Diagnostics
~~~~~~~~~~~~~~~~~~~~~~~~

``RegistrationDiagnostics`` buffers registration problems for the forms system checks, exposed as the module-level ``registration_diagnostics`` instance.
The registration paths write into it and ``next.forms.checks`` reads it when ``manage.py check`` runs.
Both import from ``next.forms.diagnostics`` directly.
The test isolation helper :func:`next.testing.reset_form_registration_state` clears the buffers between cases.

.. automodule:: next.forms.diagnostics
   :members:

Action URL Helpers
~~~~~~~~~~~~~~~~~~

``reverse_form_action`` resolves the dispatch URL for an action UID under either URL wiring,
the namespaced ``next:form_action`` route or the bare ``form_action`` route.
It lives in ``next.forms.uid`` and is not re-exported at the package level.
``ORIGIN_FIELD_NAME`` is the wire name of the hidden origin field every rendered form carries, ``"_next_form_origin"``.
``validated_origin_path`` accepts a posted origin value only as a same-site path.

.. automodule:: next.forms.uid
   :members:

Formset Helpers
~~~~~~~~~~~~~~~

The Django factories ``formset_factory``, ``modelformset_factory``, and ``inlineformset_factory`` re-export through ``next.forms`` unchanged.
``cleanup_extra_initial`` is the framework addition, and the module forwards every other public ``django.forms.formsets`` name at runtime.

.. automodule:: next.forms.formsets
   :members:

Frozen Specs
~~~~~~~~~~~~

.. automodule:: next.forms.serializers
   :members:

Signals
-------

See :doc:`signals` and :doc:`/content/topics/forms/signals` for the form signals
(``action_registered``, ``action_dispatched``, ``form_validation_failed``,
``wizard_step_submitted``, ``wizard_completed``, ``form_access_denied``).

See Also
--------

.. seealso::

   :doc:`/content/topics/forms/index` for the topic subtree.
   :doc:`/content/topics/extending` for plugging custom backends.
   :doc:`/content/topics/testing` for helpers used when asserting handlers.
   :doc:`/content/internals/action-dispatch` for the dispatch pipeline.
