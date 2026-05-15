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
   Exposes ``resolve``, ``resolve_dependencies``, and ``resolve_with_template_context`` to run a callable with resolved parameters.

``next.deps.providers``.
   The ``ParameterProvider`` protocol, the ``RegisteredParameterProvider`` base class, and the ``ProviderRegistry``.

``next.deps.cache``.
   ``REQUEST_DEP_CACHE_ATTR`` constant, ``DependencyCycleError`` exception, ``get_request_dep_cache`` accessor.

``next.deps.context``.
   ``ResolutionContext`` value object passed to every provider.

``next.deps.markers``.
   ``Depends``, ``DDependencyBase``, and the ``DependsProvider`` that resolves ``Depends`` markers.

Provider Order
--------------

The resolver iterates providers in registration order.
Each provider declares whether it can handle a parameter through ``can_handle``.
The first provider that returns ``True`` produces the value.

The built in providers are.

- ``HttpRequestProvider`` for ``HttpRequest`` parameters.
- ``UrlByAnnotationProvider`` for ``DUrl`` annotated parameters.
- ``UrlKwargsProvider`` for plain parameters that match a captured URL kwarg.
- ``QueryParamProvider`` for ``DQuery`` annotated parameters.
- ``ContextByDefaultProvider`` and ``ContextByNameProvider`` for ``Context`` markers and context keys.
- ``DependsProvider`` for parameters whose default is a ``Depends`` marker.
- ``FormProvider`` for ``DForm`` annotated parameters.

Custom providers register through ``RegisteredParameterProvider``.
A subclass joins the chain when its module is imported, in subclass definition order.

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
