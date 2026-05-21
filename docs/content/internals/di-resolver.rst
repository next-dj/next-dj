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
       Markers -- form or DForm or class --> FormProv[Form provider]
       Markers -- name match --> NameProv[Context or URL kwargs by name]

Modules
-------

``next.deps.resolver``.
   ``DependencyResolver`` plus the singleton ``resolver`` instance.
   Exposes ``resolve``, ``resolve_dependencies``, and ``resolve_with_template_context`` to run a callable with resolved parameters.
   ``resolve_with_template_context`` is the component entry point.
   Like ``resolve_dependencies``, it strips ``EXPLICIT_RESOLVE_KEYS`` from the injectable context so a context key cannot shadow a dedicated provider such as ``request`` or ``form``.

``next.deps.providers``.
   The ``ParameterProvider`` protocol, the ``RegisteredParameterProvider`` base class, and the ``ProviderRegistry`` list-style helper.

``next.deps.cache``.
   ``DependencyCache`` accumulator, the ``REQUEST_DEP_CACHE_ATTR`` constant, the ``DependencyCycleError`` exception, and the ``get_request_dep_cache`` accessor.

``next.deps.context``.
   ``ResolutionContext`` value object passed to every provider, plus the ``RESERVED_KEYS`` frozenset of names excluded from name-based resolution.

``next.deps.markers``.
   ``Depends``, ``DDependencyBase``, and the ``DependsProvider`` that resolves ``Depends`` markers.

Provider Order
--------------

The resolver iterates providers in ascending ``priority`` order.
Each provider declares whether it can handle a parameter through ``can_handle``.
The first provider that returns ``True`` produces the value.

Every ``RegisteredParameterProvider`` subclass carries a ``priority`` class attribute, and the resolver sorts the registry by it.
The eight built-in providers pin the values ``10`` through ``80``, which yields
``DependsProvider``, ``ContextByDefaultProvider``, ``ContextByNameProvider``, ``FormProvider``,
``HttpRequestProvider``, ``UrlByAnnotationProvider``, ``UrlKwargsProvider``, and ``QueryParamProvider``.

See :doc:`/content/topics/dependency-injection` for the single source of truth on this order and what each provider matches.

Custom providers register through ``RegisteredParameterProvider``.
A subclass that does not set ``priority`` inherits the default ``100``, so it is consulted after every built-in provider.
The resolver sorts the registry by ``priority`` as the primary key and by subclass definition order as the stable tie-break.

Depends Forms
-------------

``DependsProvider`` handles a parameter whose default is a ``Depends`` marker.
The ``dependency`` argument of ``Depends`` selects one of four forms.

- ``Depends("name")``. The argument is a string. The resolver looks up the callable registered under that name and invokes it with its own parameters resolved.
- ``Depends(callable)``. The argument is a callable. The resolver resolves the callable's own parameters and calls it as a factory.
- ``Depends(value)``. The argument is any other object. That object is injected directly as a constant.
- ``Depends()``. No argument. The marker falls back to the parameter name and resolves it as the named form.

ResolutionContext
-----------------

Each call builds a fresh ``ResolutionContext``.
It carries the current request, the captured URL kwargs, the template scope carried as ``context_data``, the bound form when one exists, the dependency cache, and the resolution stack.
Query-string values are read off the request by the query provider.
Providers read what they need and never mutate the context.

The names in ``RESERVED_KEYS`` (``request``, ``form``, ``_cache``, ``_stack``, ``_context_data``) are stripped from name-based resolution.
A context key called ``request`` cannot shadow the ``HttpRequest`` provider, and the other four names stay reserved for the resolver's own inputs.

Cache
-----

Each resolution pass owns a ``DependencyCache``.
It lives on the ``ResolutionContext`` for that pass and holds named dependency values.
The cache key is the dependency name string alone, with no type component.

``FormActionDispatch.dispatch`` creates a fresh dispatch cache dict on every POST and attaches it to the request under the attribute named ``REQUEST_DEP_CACHE_ATTR``.
The cache is shared across each stage of the dispatch: ``get_initial``, the factory resolution, the handler call, and any re-render after validation failure.
On a re-render the page context and component context renderers read it back through ``get_request_dep_cache`` and rejoin the same cache.
An ordinary page request that does not pass through the form dispatcher never sees this attribute.

Two consequences flow from the cache.

Idempotent providers.
   Custom providers must not depend on producing a fresh value between two invocations within the same request.
   The cache holds the first result.

Shared across the dispatch.
   The dispatch cache attaches on every form-dispatch POST and is consumed on the validation-failure re-render to keep the second pass cheap.

Cycle Detection
---------------

``DependencyCycleError`` is raised when a named dependency re-enters a name already being resolved, directly or through a longer ``Depends`` chain.
The error message lists the chain of named dependencies that closed the loop, read left to right.

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
