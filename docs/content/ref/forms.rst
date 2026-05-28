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
   ``FormActionManager``, ``form_action_manager``, ``DForm``,
   and the UID helpers (``FORM_ACTION_REVERSE_NAME``, ``URL_NAME_FORM_ACTION``).
   Use these in application code.

Advanced.
   ``FormProvider``, ``FormActionBackend``, ``FormActionFactory``, ``RegistryFormActionBackend``,
   ``FormActionDispatch``, ``FormActionOptions``, ``ActionMeta``,
   ``build_form_namespace_for_action``, ``validated_next_form_page_path``,
   the frozen specs (``FieldSpec``, ``FormsetSpec``, ``FormSpec``, ``FormSectionSpec``,
   ``FormsetRowSpec``, ``FieldKind``), the spec helpers (``field_spec``, ``form_spec``,
   ``formset_spec``), the formset helper ``cleanup_extra_initial``,
   and the ``signals`` and ``checks`` submodules.
   Use these when writing a custom backend or a form renderer.
   ``FormProvider`` auto-registers through the ``__init_subclass__`` hook on
   ``RegisteredParameterProvider`` and resolves the bound ``form`` parameter, so application
   code never instantiates it.

Internal hooks.
   Symbols with a leading underscore are implementation details re-exported for testing and
   advanced backend authoring.
   The full set lives in ``next.forms.__all__``, which is the source of truth for the internal
   hook surface.
   Do not import these names in application code.

Public API
----------

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

Fields and Widgets
~~~~~~~~~~~~~~~~~~

The framework re-exports a curated set of commonly used Django form fields and widgets through
``next.forms`` so a single import covers most form definitions.
Import any other Django field or widget directly from ``django.forms``.

.. automodule:: next.forms.base
   :members:
   :exclude-members: BaseForm, BaseModelForm, Form, ModelForm

Markers
~~~~~~~

.. automodule:: next.forms.markers
   :members:

Dispatch
~~~~~~~~

``FormActionDispatch`` and ``build_form_namespace_for_action`` are the public members of this
module, in the Advanced tier described above.
The underscore-prefixed helpers are internal hooks and stay off the autodoc surface below.
Treat them as the ``Internal hooks`` tier described above.

.. automodule:: next.forms.dispatch
   :members:
   :exclude-members: _bind_form_for_post, _filter_reserved_url_kwargs, _form_action_context_callable, _form_from_initial_data, _get_caller_path, _normalize_handler_response, _url_kwargs_from_post, _url_kwargs_from_resolver_or_post

Manager
~~~~~~~

.. automodule:: next.forms.manager
   :members:

Backends
~~~~~~~~

``FormActionOptions`` is a reserved dataclass.
It is accepted by ``register_action`` for forward compatibility but carries no fields currently.

.. automodule:: next.forms.backends
   :members:

Action URL Helpers
~~~~~~~~~~~~~~~~~~

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
(``action_registered``, ``action_dispatched``, ``form_validation_failed``).

See Also
--------

.. seealso::

   :doc:`/content/topics/forms/index` for the topic subtree.
   :doc:`/content/topics/extending` for plugging custom backends.
   :doc:`/content/topics/testing` for helpers used when asserting handlers.
   :doc:`/content/internals/action-dispatch` for the dispatch pipeline.
