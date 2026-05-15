.. _ref-decorators:

Decorators and Markers
======================

Module Summary
--------------

This page lists every public decorator and dependency marker that the framework exposes.
Each entry includes a short usage hint plus a pointer to the topic guide that covers the broader pattern.

Decorators
----------

@context
~~~~~~~~

.. autofunction:: next.pages.context
   :no-index:

Registers a context function on a page module or layout module.
Pass ``inherit_context=True`` to publish the value to every descendant page.

@component.context
~~~~~~~~~~~~~~~~~~

.. autofunction:: next.components.context
   :no-index:

Registers a component context function inside ``component.py``.
The decorator publishes a named value into the component template scope.

@action
~~~~~~~

.. autofunction:: next.forms.action
   :no-index:

Registers a form action handler.
Pass ``form_class=`` for forms that need validation, ``namespace=`` to scope short names.

Dependency Markers
------------------

Depends
~~~~~~~

.. autoclass:: next.deps.Depends
   :no-index:

Default argument that injects a named dependency registered through ``resolver.dependency``.

Context
~~~~~~~

.. autoclass:: next.pages.Context
   :no-index:

Default argument that injects a context value by key.

DUrl
~~~~

.. autoclass:: next.urls.markers.DUrl
   :no-index:

Type annotation that injects the captured URL parameter with the matching name.
Pass a generic argument to coerce the value to a type.

DQuery
~~~~~~

.. autoclass:: next.urls.markers.DQuery
   :no-index:

Type annotation that injects a query string value.
Supports ``DQuery[str]``, ``DQuery[int]``, ``DQuery[bool]``, ``DQuery[float]``, and ``DQuery[list[T]]``.

DForm
~~~~~

.. autoclass:: next.forms.markers.DForm
   :no-index:

Type annotation that injects a form instance during action dispatch.

FormProvider
~~~~~~~~~~~~

.. autoclass:: next.forms.markers.FormProvider
   :no-index:

Provider class for injecting bound forms.

DDependencyBase
~~~~~~~~~~~~~~~

.. autoclass:: next.deps.DDependencyBase
   :no-index:

Base class for custom DI markers.
Subclass with a parameterised generic to declare a typed parameter.

RegisteredParameterProvider
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. autoclass:: next.deps.RegisteredParameterProvider
   :no-index:

Base class for custom parameter providers.
Implement ``can_handle`` and ``resolve`` to plug a custom data source into the resolver.

See Also
--------

.. seealso::

   :doc:`/content/topics/context` for ``@context`` semantics.
   :doc:`/content/topics/components` for ``@component.context``.
   :doc:`/content/topics/forms/actions` for ``@action`` handlers.
   :doc:`/content/topics/dependency-injection` for the resolver and providers.
