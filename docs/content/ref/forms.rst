.. _ref-forms:

Forms Reference
===============

Module Summary
--------------

``next.forms`` exposes form base classes, a form-less action decorator, dispatch infrastructure,
formset helpers, frozen field and form specs,
and a curated set of commonly used Django form fields and widgets.

API Tiers
---------

``next.forms`` groups its symbols into three tiers that describe the intended audience for each name.
The lists below are representative.
The autodoc blocks under `Public API`_ are the exhaustive surface.

Stable.
   ``Form``, ``ModelForm``, ``BaseForm``, ``BaseModelForm``, ``@action``, ``redirect_to_origin``,
   ``FormWizard``, ``FormActionManager``, ``form_action_manager``, ``DForm``,
   the UID helpers (``FORM_ACTION_REVERSE_NAME``, ``URL_NAME_FORM_ACTION``),
   ``autodiscover_forms``, and ``reset_form_registration_state``.
   Use these in application code.

Advanced.
   ``FormProvider``, ``FormActionBackend``, ``FormActionFactory``, ``RegistryFormActionBackend``,
   ``FormActionDispatch``, ``ActionOutcome``, ``ActionOutcomeKind``,
   ``ActionRegistration``, ``ActionMeta``, ``ComponentWidget``,
   ``FormWizardBackend``, ``CacheFormWizardBackend``, ``WizardBackendManager``, ``wizard_backend_manager``,
   ``build_form_namespace_for_action``,
   the frozen specs (``FieldSpec``, ``FormsetSpec``, ``FormSpec``, ``FormSectionSpec``,
   ``FormsetRowSpec``, ``FieldKind``), the spec helpers (``field_spec``, ``form_spec``,
   ``formset_spec``), the formset helper ``cleanup_extra_initial``,
   and the ``signals`` and ``checks`` submodules.
   Use these when writing a custom backend or a form renderer.
   ``FormProvider`` auto-registers through the ``__init_subclass__`` hook on
   ``RegisteredParameterProvider`` and resolves the bound ``form`` parameter, so application
   code never instantiates it.
   ``FormActionFactory`` instantiates one backend per ``FORM_ACTION_BACKENDS`` entry,
   passing the whole config dict to the backend constructor. ``FormActionManager`` calls it,
   so application code rarely does.
   ``WizardBackendManager`` is the analogous lazy holder for the single wizard backend, exposed
   as the ``wizard_backend_manager`` instance. It reads ``FORM_WIZARD_BACKEND`` on first
   use and caches the result.
   ``build_form_namespace_for_action`` builds the ``{form, wizard}`` namespace the ``{% form %}``
   tag consumes. Call it only when rendering that namespace by hand outside the tag.

The ``page`` decorator is also re-exported from ``next.forms`` as a convenience.
It is the same object documented under :doc:`/content/ref/pages` and carries no forms-specific behaviour.

Internal hooks.
   Underscore-prefixed helpers inside the submodules, such as the form-building functions in
   ``next.forms.dispatch``, are implementation details.
   ``next.forms.__all__`` is the source of truth for the public surface and exports no
   underscore names.
   Do not import underscore names in application code.

Public API
----------

Autodiscover
~~~~~~~~~~~~

.. autofunction:: next.forms.autodiscover_forms

.. autofunction:: next.forms.reset_form_registration_state

Decorator
~~~~~~~~~

.. autofunction:: next.forms.action

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
``FormWizardBackend`` is the draft-persistence contract, and ``CacheFormWizardBackend`` is the bundled default.
See :doc:`/content/topics/forms/wizard` and :doc:`/content/topics/forms/wizard-backend` for the topic guides.

.. autoclass:: next.forms.FormWizard
   :members:

.. autoclass:: next.forms.FormWizardBackend
   :members:

.. autoclass:: next.forms.CacheFormWizardBackend
   :members:

.. autoclass:: next.forms.WizardBackendManager
   :members:

Fields and Widgets
~~~~~~~~~~~~~~~~~~

The framework re-exports a curated set of commonly used Django form fields and widgets through
``next.forms`` so a single import covers most form definitions.
Import any other Django field or widget directly from ``django.forms``.

.. automodule:: next.forms.base
   :members:
   :exclude-members: BaseForm, BaseModelForm, Form, ModelForm

``ComponentWidget`` renders a field through a registered next.dj component instead of a Django widget template.
See :doc:`/content/topics/forms/field-components` for the topic guide.

.. autoclass:: next.forms.ComponentWidget
   :members:

Markers
~~~~~~~

.. automodule:: next.forms.markers
   :members:

Dispatch
~~~~~~~~

``FormActionDispatch``, ``ActionOutcome``, and ``ActionOutcomeKind`` are the public members of this module, in the Advanced tier described above.
``ActionOutcome`` is the frozen keyword-only dataclass a backend's ``shape_response`` hook receives, with ``ActionOutcomeKind`` as its ``kind`` discriminator.
On ``INVALID`` outcomes the ``page_path`` and ``origin`` fields carry the resolved identity of the origin page: the source location of its ``page.py`` and the validated origin URL path.
``FormActionDispatch.shape_response`` builds the default envelope for one outcome, and the backend hook delegates to it unless overridden.
``ensure_http_response`` coerces a handler return value into an ``HttpResponse``, kept for custom backends that drive the pipeline by hand.
The underscore-prefixed helpers are internal hooks per the ``Internal hooks`` tier described above.

.. automodule:: next.forms.dispatch
   :members:

Manager
~~~~~~~

.. automodule:: next.forms.manager
   :members:

Backends
~~~~~~~~

``ActionRegistration`` is the value object passed to ``register_action``.
It carries the action ``name``, the declaration-site ``file_path``, the ``scope``, and the action target.
The target is one of ``handler``, ``form_class``, or ``wizard_class``, which lets a single ``register_action`` call serve the ``@action`` decorator, a class-bound form, and a ``FormWizard``.

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
