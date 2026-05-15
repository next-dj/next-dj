.. _internals-di-resolver:

Dependency Resolver
===================

This page covers how the dependency resolver inspects a callable, picks providers, fills parameters, and caches results across a request.

.. contents::
   :local:
   :depth: 2

Overview
--------

The resolver is a singleton instance of ``DependencyResolver``.
Every page context function, every page render, every component context function, and every form action handler is invoked through the resolver.

Pipeline
--------

.. mermaid::

   flowchart TB
       Callable[Decorated callable] --> Sig[Inspect signature]
       Sig --> Markers[Scan markers]
       Markers --> Providers[Provider registry]
       Providers --> Resolution[ResolutionContext]
       Resolution --> Cache{Cache hit}
       Cache -- yes --> Value[Cached value]
       Cache -- no --> Provider[Provider.resolve]
       Provider --> Value
       Value --> Inject[Inject parameter]
       Markers -- Depends --> NamedDep[Named dependency]
       Markers -- Context --> CtxByKey[Context by key]
       Markers -- DUrl --> UrlProv[URL provider]
       Markers -- DQuery --> QueryProv[Query provider]
       Markers -- DForm --> FormProv[Form provider]

Modules
-------

``next.deps.resolver``.
   ``DependencyResolver`` plus the singleton ``resolver`` instance.
   Implements the ``dispatch`` method that runs a callable with resolved parameters.

``next.deps.providers``.
   Provider base classes and the built in providers.

``next.deps.cache``.
   ``REQUEST_DEP_CACHE_ATTR`` constant, ``DependencyCycleError`` exception, ``get_request_dep_cache`` accessor.

``next.deps.context``.
   ``ResolutionContext`` value object passed to every provider.

``next.deps.markers``.
   ``Depends`` and ``DDependencyBase`` plus the registry of marker classes.

Provider Order
--------------

The resolver iterates providers in registration order.
Each provider declares whether it can handle a parameter through ``can_handle``.
The first provider that returns ``True`` produces the value.

The built in order, from first to last, is.

1. ``HttpRequestProvider``.
2. ``UrlParameterProvider``.
3. ``DQueryProvider``.
4. ``DFormProvider``.
5. ``ContextByNameProvider``.
6. ``DependsProvider``.
7. ``DDependencyProvider`` (handles ``DUrl``, ``DQuery``, ``DForm``, and any registered marker subclass).

Custom providers register through ``RegisteredParameterProvider``.
A subclass joins the chain at the position chosen by its ``priority`` class attribute.

ResolutionContext
-----------------

Each call builds a fresh ``ResolutionContext``.
It carries the current request, the captured URL kwargs, the query string, the template scope, and the bound form when one exists.
Providers read what they need and never mutate the context.

Cache
-----

The resolver caches every produced value on the request through an attribute named ``REQUEST_DEP_CACHE_ATTR``.
The cache key combines the parameter type and the parameter name.

Two consequences flow from the cache.

Idempotent providers.
   Custom providers must not depend on producing a fresh value between two invocations within the same request.
   The cache holds the first result.

Shared across re-render.
   Form validation failures reuse the cache from the initial render to keep re-render cheap.

Cycle Detection
---------------

``DependencyCycleError`` is raised when a provider calls back into the resolver and produces an infinite loop.
The error message lists the chain of providers that triggered the cycle.

Signals
-------

The resolver fires ``provider_registered`` once per provider when the class enters the registry.
Subscribe to track custom providers across reloads.

Extension Points
----------------

- Subclass ``DDependencyBase`` to introduce a typed marker.
- Subclass ``RegisteredParameterProvider`` to handle a custom marker or a custom annotation.
- Use ``resolver.dependency("name")`` to register a callable for ``Depends("name")``.

See Also
--------

.. seealso::

   :doc:`/content/topics/dependency-injection` for the topic guide.
   :doc:`request-lifecycle` for the surrounding request pipeline.
