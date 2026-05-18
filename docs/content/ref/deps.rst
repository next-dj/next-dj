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

.. data:: next.deps.resolver

   The shared ``DependencyResolver`` singleton used by pages, form actions, and component renderers throughout the framework.
   Import it as ``from next.deps import resolver`` when you need to call ``resolver.resolve_dependencies`` from a custom provider or a test helper.

Providers
~~~~~~~~~

``ParameterProvider`` is the minimal protocol the resolver consumes. ``RegisteredParameterProvider`` is the auto-registered base used by the built-in providers. Subclasses join the resolver's registry through ``__init_subclass__``, so the resolver instantiates them on first use without an explicit import.

``ProviderRegistry`` is an explicit list-style registry of ``ParameterProvider`` instances. Unlike ``RegisteredParameterProvider``, it holds providers added by hand through ``register`` rather than collected automatically. It supports ``register``, ``get_providers``, and ``clear``, and iteration yields providers in registration order. It is intended for tests and external consumers that need a standalone provider list.

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

Signals
-------

See :doc:`signals` for the ``provider_registered`` signal.

See Also
--------

.. seealso::

   :doc:`/content/topics/dependency-injection` for the topic guide.
   :doc:`/content/internals/di-resolver` for the resolver internals.
