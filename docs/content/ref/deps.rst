.. _ref-deps:

Dependency Injection Reference
==============================

Module Summary
--------------

``next.deps`` exposes the resolver, the parameter providers, the dependency cache, and the public markers used in annotations.

Public API
----------

Resolver
~~~~~~~~

.. automodule:: next.deps.resolver
   :members:
   :exclude-members: resolver

.. data:: next.deps.resolver.resolver

   The shared ``DependencyResolver`` singleton used by pages, form actions, and component renderers throughout the framework.
   Import it as ``from next.deps import resolver`` when you need to call ``resolver.resolve_dependencies`` from a custom provider or a test helper.

Providers
~~~~~~~~~

``ParameterProvider`` is the minimal protocol the resolver consumes.
``RegisteredParameterProvider`` is the auto-registered base used by the built-in providers.
Subclasses join the resolver's registry through ``__init_subclass__``, so the resolver instantiates them on first use without an explicit import.

.. automodule:: next.deps.providers
   :members:

Markers
~~~~~~~

.. automodule:: next.deps.markers
   :members:

Cache
~~~~~

.. automodule:: next.deps.cache
   :members:

Context
~~~~~~~

.. automodule:: next.deps.context
   :members:

``RESERVED_KEYS`` lists the names (``request``, ``form``, ``cleaned_data``, ``_cache``, ``_stack``, ``_context_data``) stripped from name-based resolution.
A context key cannot shadow a reserved resolver input.
``DependencyResolver.EXPLICIT_RESOLVE_KEYS`` is the class-level alias of the same frozenset.
The resolver reads ``self.EXPLICIT_RESOLVE_KEYS``, which a subclass may override.
See :doc:`/content/internals/di-resolver` for the resolution detail.

Signals
-------

See :doc:`signals` for the ``provider_registered`` signal.

Checks
------

The dependency injection layer registers no Django system checks.

See Also
--------

.. seealso::

   :doc:`/content/topics/dependency-injection` for the topic guide.
   :doc:`/content/internals/di-resolver` for the resolver internals.
