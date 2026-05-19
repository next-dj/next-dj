.. _ref-forms:

Forms Reference
===============

Module Summary
--------------

``next.forms`` exposes form action registration, dispatch, formset helpers, frozen field and form specs, and a complete re-export of every Django form field and widget.
It also re-exports ``page`` from ``next.pages`` so that a single ``from next.forms import action, page`` covers the two most common decorators in a ``page.py``.

API Tiers
---------

``next.forms`` groups its symbols into three tiers that describe the intended audience for each name.
The lists below are representative.
The autodoc blocks under `Public API`_ are the exhaustive surface.

Stable.
   ``@action``, ``page``, ``Form``, ``ModelForm``, ``BaseForm``, ``BaseModelForm``, ``DForm``, ``FormActionManager``, ``form_action_manager``, and the UID helpers (``FORM_ACTION_REVERSE_NAME``, ``URL_NAME_FORM_ACTION``, ``redirect_to_origin``, ``validated_next_form_page_path``).
   Use these in application code.

Advanced.
   ``FormProvider``, ``FormActionBackend``, ``FormActionFactory``, ``RegistryFormActionBackend``, ``FormActionDispatch``, ``FormActionOptions``, ``ActionMeta``, ``build_form_namespace_for_action``, the frozen specs (``FieldSpec``, ``FormsetSpec``, ``FormSpec``, ``FormSectionSpec``, ``FormsetRowSpec``, ``FieldKind``), the spec helpers (``field_spec``, ``form_spec``, ``formset_spec``), the formset helper ``cleanup_extra_initial``, and the ``signals`` and ``checks`` submodules.
   Use these when writing a custom backend or a form renderer.
   ``FormProvider`` is the DI provider the framework auto-registers to resolve the bound ``form`` parameter, so application code never instantiates it.

Internal hooks.
   Symbols with a leading underscore are implementation details re-exported for testing and advanced backend authoring.
   The complete list lives in ``next.forms.__all__``: ``_bind_form_for_post``, ``_filter_reserved_url_kwargs``, ``_form_action_context_callable``, ``_form_from_initial_data``, ``_get_caller_path``, ``_make_uid``, ``_normalize_handler_response``, ``_url_kwargs_from_post``, and ``_url_kwargs_from_resolver_or_post``.
   Do not import them in application code.

.. note::

   ``next.forms.__all__`` includes the underscore-prefixed internal hooks so that test helpers and custom backends can reach them through a documented path.
   Application code should import only from the Stable tier.
   The same tier vocabulary is summarised for every package in :doc:`/content/faq/general`.

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
   :members:

.. autoclass:: next.forms.BaseModelForm
   :members:

Fields and Widgets
~~~~~~~~~~~~~~~~~~

The framework re-exports the full Django field and widget catalog through ``next.forms`` so a single import covers a form definition.

.. automodule:: next.forms.base
   :members:
   :exclude-members: BaseForm, BaseModelForm, Form, ModelForm

Markers
~~~~~~~

.. automodule:: next.forms.markers
   :members:

Dispatch
~~~~~~~~

``FormActionDispatch`` and ``build_form_namespace_for_action`` are the public members of this module, in the Advanced tier described above.
The underscore-prefixed helpers (``_bind_form_for_post``, ``_normalize_handler_response``, and similar symbols) are internal hooks and stay off the autodoc surface below.
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

See :doc:`signals` and :doc:`/content/topics/forms/signals` for the form signals (``action_registered``, ``action_dispatched``, ``form_validation_failed``).

See Also
--------

.. seealso::

   :doc:`/content/topics/forms/index` for the topic subtree.
   :doc:`/content/topics/extending` for plugging custom backends.
   :doc:`/content/topics/testing` for helpers used when asserting handlers.
   :doc:`/content/internals/action-dispatch` for the dispatch pipeline.
