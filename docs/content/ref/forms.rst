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

Advanced.
   ``FormActionBackend``, ``RegistryFormActionBackend``,
   ``ActionOutcome``, ``ActionOutcomeKind``,
   ``ActionRegistration``, ``ComponentWidget``,
   ``FormWizardBackend``, ``SessionFormWizardBackend``, ``CacheFormWizardBackend``,
   the frozen specs (``FieldSpec``, ``FormsetSpec``, ``FormSpec``, ``FormSectionSpec``,
   ``FormsetRowSpec``, ``FieldKind``), the spec helpers (``field_spec``, ``form_spec``,
   ``formset_spec``), the formset helper ``cleanup_extra_initial``,
   and the ``signals`` and ``checks`` submodules.
   Use these when writing a custom backend or a form renderer.

Framework machinery.
   The wiring lives on the owning submodules and is not re-exported at the package level.
   ``FormActionDispatch`` lives in ``next.forms.dispatch``.
   ``FormActionManager``, the ``form_action_manager`` instance, and
   ``build_form_namespace_for_action`` live in ``next.forms.manager``.
   ``ActionMeta``, ``FormActionFactory``, and ``file_to_dotted_module`` live in
   ``next.forms.backends``.
   ``WizardBackendManager`` and the ``wizard_backend_manager`` instance live in
   ``next.forms.wizard``.
   ``FormProvider`` lives in ``next.forms.markers``.
   The UID helpers ``FORM_ACTION_REVERSE_NAME``, ``URL_NAME_FORM_ACTION``,
   ``reverse_form_action``, and ``validated_origin_path`` live in ``next.forms.uid``.
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

The test isolation helper :func:`next.testing.reset_form_registration_state` clears the
discovery memo together with the registries, see :doc:`/content/ref/testing`.

Decorator
~~~~~~~~~

.. autofunction:: next.forms.action

Exceptions
~~~~~~~~~~

``FormActionNotFound`` is raised when no registered action matches a requested name.
``FormActionManager.get_action_url``, the ``{% form %}`` tag, and the testing helper ``resolve_action_url`` all raise it.
It subclasses ``LookupError`` and carries the failing ``name``, the ``page_path`` that was searched, and optional name ``suggestions``.

.. autoexception:: next.forms.FormActionNotFound
   :members:

Form Base Classes
~~~~~~~~~~~~~~~~~

.. autoclass:: next.forms.Form
   :members:

.. autoclass:: next.forms.ModelForm
   :members:

.. autoclass:: next.forms.BaseForm
   :members: get_initial, on_valid

.. autoclass:: next.forms.BaseModelForm
   :members: get_initial, on_valid

Form Wizard
~~~~~~~~~~~

``FormWizard`` routes a sequence of step forms across requests.
``FormWizardBackend`` is the draft-persistence contract, ``SessionFormWizardBackend`` is the bundled default, and ``CacheFormWizardBackend`` is the cache-backed alternative.
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

.. automodule:: next.forms.base
   :members:
   :exclude-members: BaseForm, BaseModelForm, Form, ModelForm

``ComponentWidget`` renders a field through a registered next.dj component instead of a Django widget template.
See :doc:`/content/topics/forms/field-components` for the topic guide.

.. autoclass:: next.forms.ComponentWidget
   :members:

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

.. automodule:: next.forms.manager
   :members:

Backends
~~~~~~~~

``ActionRegistration`` is the value object passed to ``register_action``.
It carries the action ``name``, the declaration-site ``file_path``, the ``scope``, and the action target.
The target is one of ``handler``, ``form_class``, or ``wizard_class``, which lets a single ``register_action`` call serve the ``@action`` decorator, a class-bound form, and a ``FormWizard``.
``ActionMeta``, ``FormActionFactory``, and ``file_to_dotted_module`` import from ``next.forms.backends`` directly.
``FormActionFactory`` instantiates one backend per ``FORM_ACTION_BACKENDS`` entry, passing the whole config dict to the backend constructor.
``FormActionManager`` calls it, so application code rarely does.

.. automodule:: next.forms.backends
   :members:

Action URL Helpers
~~~~~~~~~~~~~~~~~~

``reverse_form_action`` resolves the dispatch URL for an action UID under either URL wiring,
the namespaced ``next:form_action`` route or the bare ``form_action`` route.
It lives in ``next.forms.uid`` and is not re-exported at the package level.

.. automodule:: next.forms.uid
   :members:
   :exclude-members: _make_uid

Formset Helpers
~~~~~~~~~~~~~~~

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
``wizard_step_submitted``, ``wizard_completed``).

See Also
--------

.. seealso::

   :doc:`/content/topics/forms/index` for the topic subtree.
   :doc:`/content/topics/extending` for plugging custom backends.
   :doc:`/content/topics/testing` for helpers used when asserting handlers.
   :doc:`/content/internals/action-dispatch` for the dispatch pipeline.
