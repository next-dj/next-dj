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

.. py:decorator:: @context(func_or_key=None, *, inherit_context=False, serialize=False, serializer=None)

Registers a context function on a page module (``page.py``).
The first positional argument is ``func_or_key``.
Called bare as ``@context`` it receives the decorated function and merges its returned dict into the template context.
Called as ``@context("greeting")`` it receives a key string and binds the function's return value to that key.
Pass ``inherit_context=True`` to publish the value to every descendant page.
Pass ``serialize=True`` to expose the return value to the browser under ``window.Next.context``.
The value must be JSON-encodable by the active serializer.
See :ref:`Serialization for the Browser <topics-context-serialization>` for the contract.
Pass ``serializer=`` to route that key through a custom ``JsContextSerializer``.

@component.context
~~~~~~~~~~~~~~~~~~

.. py:decorator:: @component.context(func_or_key=None, *, serialize=False, serializer=None)

Registers a component context function inside ``component.py``.
The first positional argument is ``func_or_key``.
Called bare as ``@component.context`` it merges the function's returned dict into the component template scope.
Called as ``@component.context("greeting")`` it binds the function's return value to that key.
Pass ``serialize=True`` to include the return value in ``window.Next.context``.
The value must be JSON-encodable by the active serializer, the same contract documented under :ref:`Serialization for the Browser <topics-context-serialization>`.
Pass ``serializer=`` to route that key through a custom ``JsContextSerializer``.

@action
~~~~~~~

.. py:decorator:: @action(name, *, form_class=None)

Registers a plain callable as a named form-less action under ``name``.
``name`` is the first positional argument and must be unique across the project.
Use ``@action`` only for handlers that need no form validation — for example delete confirmations or logout buttons.
Form classes register automatically through ``__init_subclass__`` and must not use ``@action``.
Pass ``form_class=`` only when the action requires a dynamically constructed form class supplied by a factory callable.
Passing a static ``Form`` subclass as ``form_class`` raises ``TypeError`` immediately.
Applying ``@action`` to a class also raises ``TypeError`` and triggers ``next.E053``.

Dependency Markers
------------------

Depends
~~~~~~~

.. autoclass:: next.deps.Depends
   :no-index:

Default argument that injects a named, callable, or constant dependency.
See :doc:`/content/topics/dependency-injection` for all resolution modes and the registration API.

Context
~~~~~~~

.. autoclass:: next.pages.Context
   :no-index:

Default argument that reads a value from the current page context data by key.
See :doc:`/content/topics/context` for all source forms and the resolution order.

DUrl
~~~~

.. autoclass:: next.urls.markers.DUrl
   :no-index:

Type annotation that injects the captured URL parameter with the matching name.
Pass a generic argument to coerce the value to a type.
The named form ``DUrl["key"]`` targets a segment by name and delivers it in string form, without extra type coercion.
The named-and-typed form ``DUrl["key", Type]`` targets a segment by name and coerces the value to ``Type``.
See :doc:`/content/topics/dependency-injection` for the supported coercion types.

DQuery
~~~~~~

.. autoclass:: next.urls.markers.DQuery
   :no-index:

Type annotation that injects a query string value.
Supports ``DQuery[str]``, ``DQuery[int]``, ``DQuery[bool]``, ``DQuery[float]``, ``DQuery[UUID]``, ``DQuery[Decimal]``, ``DQuery[date]``, and ``DQuery[datetime]``.
``DQuery[list[T]]`` accepts any of those scalars as the element type.

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
Matches a ``DForm[...]`` annotation or any parameter named ``form``.

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
