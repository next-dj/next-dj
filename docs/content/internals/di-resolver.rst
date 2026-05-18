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
   The ``ParameterProvider`` protocol and the ``RegisteredParameterProvider`` base class.

``next.deps.cache``.
   ``REQUEST_DEP_CACHE_ATTR`` constant, ``DependencyCycleError`` exception, ``get_request_dep_cache`` accessor.

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
A subclass joins the chain when its module is imported, in subclass definition order.

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

Form validation failures re-render the origin page.
``FormActionDispatch.dispatch`` attaches the dispatch cache dict to the request under the attribute named ``REQUEST_DEP_CACHE_ATTR``.
The page context and component context renderers read it back through ``get_request_dep_cache`` and rejoin the same cache.
That attribute carries the cache only for the form-failure re-render path, not for an ordinary page request.

Two consequences flow from the cache.

Idempotent providers.
   Custom providers must not depend on producing a fresh value between two invocations within the same request.
   The cache holds the first result.

Shared across re-render.
   Form validation failures reuse the cache from the initial render to keep re-render cheap.

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
