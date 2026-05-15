.. _ref-forms:

Forms Reference
===============

Module Summary
--------------

``next.forms`` exposes form action registration, dispatch, formset helpers, frozen field and form specs, and a complete re-export of every Django form field and widget.

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

.. automodule:: next.forms.dispatch
   :members:

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
   :doc:`/content/internals/action-dispatch` for the dispatch pipeline.
