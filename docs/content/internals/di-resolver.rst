.. _internals-di-resolver:

Dependency resolver
===================

This page covers how the dependency resolver inspects a callable, picks providers, fills parameters, and caches results across a request.

.. contents::
   :local:
   :depth: 2

Overview
--------

The resolver is the ``DependencyResolver`` singleton in ``next.deps``.
It compiles each callable once into an injection plan and replays that plan on every call.
Every page context function, every page render, and every component context function is invoked through the resolver.
The form dispatch adds its own call sites.
These are the form-class factory, ``get_initial``, the ``@action`` handler, ``on_valid``, and ``wizard.done``.

Pipeline
--------

.. mermaid::

   flowchart TB
       Callable[Decorated callable] --> Sig[Inspect signature]
       Sig --> Plan[Compile injection plan]
       Plan --> Providers[Replay plan candidates]
       Providers --> Resolution[ResolutionContext]
       Resolution --> Cache{Cache hit}
       Cache -- yes --> Value[Cached value]
       Cache -- no --> Provider[Provider.resolve]
       Provider --> Value
       Value --> Inject[Inject parameter]
       Providers -- Depends --> NamedDep[Named dependency]
       Providers -- Context --> CtxByKey[Context by key]
       Providers -- DUrl --> UrlProv[URL provider]
       Providers -- DQuery --> QueryProv[Query provider]
       Providers -- form or DForm or class --> FormProv[Form provider]
       Providers -- name match --> NameProv[Context or URL kwargs by name]

Modules
-------

``next.deps.resolver``.
   ``DependencyResolver``, the ``UnknownDependencyError`` exception, and the singleton ``resolver`` instance.
   Exposes ``resolve``, ``resolve_dependencies``, and ``resolve_with_template_context`` to run a callable with resolved parameters.
   The singleton is built at import time and never replaced, so every reference to it stays valid.
   ``resolve_with_template_context`` is the component entry point.
   It hands the template context over as it is, without copying it, and the reserved names stay invisible to the providers that read the context by name.
   A context key called ``request`` or ``form`` therefore cannot shadow the dedicated provider, on the component path and on the page path alike.

``next.deps.providers``.
   The ``ParameterProvider`` protocol and the ``RegisteredParameterProvider`` base class.
   A subclass of the base class enters ``provider_registry`` from ``__init_subclass__``, and the resolver instantiates the built-in providers from there on first use.

``next.deps.registry``.
   The ``ProviderRegistry`` class and the ``provider_registry`` singleton, an ordered list of provider classes with a ``version`` counter.
   A class registering under an address another class already holds takes its place in the list, so a dev reload of the declaring module leaves one entry rather than a pile of dead ones.
   The address pairs the class name with the file the body was compiled from, so two ``page.py`` files declaring the same provider name keep one entry each.
   A resolver keeps the version it last saw, and a change rebuilds every auto-registered instance from scratch in priority order under a lock.
   An abstract intermediate base is skipped by that rebuild, because it is a legitimate class to register and no instance of it exists to place.

``next.deps.cache``.
   ``DependencyCache`` accumulator, the ``REQUEST_DEP_CACHE_ATTR`` constant, the ``DependencyCycleError`` exception, and the ``get_request_dep_cache`` accessor.

``next.deps.context``.
   ``ResolutionContext`` value object passed to every provider, plus the ``RESERVED_KEYS`` frozenset of names excluded from name-based resolution.

``next.deps.markers``.
   ``Depends``, ``DDependencyBase``, and the ``DependsProvider`` that resolves ``Depends`` markers.

``next.deps.plan``.
   ``compile_plan``, the ``ParameterPlan`` entry, and the ``InjectionPlan`` tuple the resolver replays.
   An entry carries the parameter name, the runtime candidates, the fallback value, the parameter with its annotation already resolved, and the filler the terminal provider compiled.
   The filler stands for that provider, so a parameter no signature settled is exactly one whose filler is ``None``.
   The resolved annotation keeps the metadata of an ``Annotated[...]`` hint, so a provider matching on a marker looks past that wrapper and one matching on the metadata reads it.

The signature, type-hint, and plan caches are bounded and evict the least recently used entry once they are full, and a hit moves its entry back to the fresh end.
The dev reloader re-executes a ``page.py`` on every save and mints fresh function objects, so an unbounded memo keyed by callable would pin every dead function for the life of the process.
A router reload clears every one of them through ``router_reloaded``, so a rebuilt route tree never replays a plan compiled against the previous generation of callables.

Provider order
--------------

Providers are ordered by ascending ``priority``, and the first one that claims a parameter produces its value.
Each provider claims a parameter through ``can_handle`` at resolve time, or settles it ahead of time through ``static_can_handle``.
The compiled plan keeps that order, but a static verdict removes the check from every parameter the provider provably never owns.

Every ``RegisteredParameterProvider`` subclass carries a ``priority`` class attribute, and the resolver sorts the registry by it.
The nine built-in providers pin the values ``10`` through ``80``, which yields ``DependsProvider``, ``ContextByDefaultProvider``, ``ContextByNameProvider``, ``FormProvider``, ``CleanedDataProvider``, ``HttpRequestProvider``, ``UrlByAnnotationProvider``, ``UrlKwargsProvider``, and ``QueryParamProvider``.
``FormProvider`` and ``CleanedDataProvider`` share priority ``40``.

See :doc:`/content/topics/dependency-injection` for the single source of truth on this order and what each provider matches.

Custom providers register through ``RegisteredParameterProvider``.
A subclass that does not set ``priority`` inherits the default ``100``, so it is consulted after every built-in provider.
The resolver sorts the registry by ``priority`` as the primary key and by subclass definition order as the stable tie-break.

A provider handed over by hand sits outside that sort.
``prepend_provider`` puts it at the head of the list and ``add_provider`` appends it to the tail, and the auto-registered instances always sit between the two in priority order.
A prepended provider therefore outranks every built-in one, whatever priority it declares.
``remove_provider`` drops the instance from whichever of the three it sits in, and an auto-registered one has its class held out of every later rebuild, so a resync started by an unrelated registration does not hand back what the caller took out.

Injection plan
~~~~~~~~~~~~~~

The resolver asks each provider once per callable instead of once per call.
On the first resolve of a callable it walks the signature and calls ``static_can_handle`` on every provider with the parameter, whose annotation the compiler has already resolved to the real type hint with its ``Annotated`` metadata kept.
A ``False`` verdict drops the provider for that parameter, ``None`` keeps it as a runtime candidate, and the first ``True`` becomes the terminal provider and ends the walk.
A verdict outside those three raises ``TypeError`` from the compile rather than changing injection semantics silently, and since no plan is cached the next resolve raises again.
A provider that defines no callable ``static_can_handle`` at all is refused with a ``TypeError`` naming the class as it joins the resolver, rather than failing later from inside the compiler.
A marker parameter such as ``Depends`` or ``Context`` ends in its terminal with no candidates, so its resolve calls no ``can_handle`` at all.
A parameter the signature cannot settle, such as ``theme: HttpRequest``, keeps every provider that returned ``None`` in list order and replays their ``can_handle`` at resolve time, so the priority verdict is the same one a full scan would reach.

The compiled plan is cached per callable together with the providers version it saw.
Every mutation of the provider list, including ``add_provider``, ``prepend_provider``, ``remove_provider``, and a rebuild of the auto-registered instances, bumps that version, so the next resolve recompiles the plan.
The version is read before the compile starts, so a plan built while another thread was replacing the list is stamped stale and compiled again rather than cached for good.
A plan whose type hints did not resolve is never cached, so a name a later import defines is picked up on the next resolve.
A resolve compares the registry version before it trusts a cached plan, which is what lets a provider class imported after the first resolve take effect.
Registering a named dependency does not touch the plan, because the ``DependsProvider`` reads the dependency map at resolve time.
``provides`` answers from the same plan, so the system checks and the resolver never disagree on which parameters a provider fills.

Compiled fillers
~~~~~~~~~~~~~~~~

A terminal provider is asked once more, through the optional ``compile_resolve`` hook, for the call that fills the parameter from a context alone.
Whatever that call needs from the signature is read at compile time, so the replay of a ``DUrl["id", int]`` parameter is left with one lookup in the URL kwargs and the coercion itself, rather than taking the annotation apart again on every request.
The three marker providers implement it, and a provider that returns ``None`` from it, or defines no hook at all, keeps its plain ``resolve`` on the replay path.
The fillers belong to the plan, so every recompile builds them again from the provider list the plan saw.

Depends forms
-------------

``DependsProvider`` handles a parameter whose default is a ``Depends`` marker, see :doc:`/content/topics/dependency-injection` for the four marker forms.
A dependency whose own signature offers nothing to inject is noted the first time it is called and afterwards called directly, because no provider can add a parameter to a signature that has none.
The note is held per name and checked by identity, so rebinding the name goes back through the full path.

ResolutionContext
-----------------

Each call builds a fresh ``ResolutionContext``.
It carries the current request, the captured URL kwargs, the template scope as ``context_data``, the bound form when one exists, the merged wizard cleaned data when a wizard supplies it, the dependency cache, and the resolution stack.
Query-string values are read off the request by the query provider.
The object is not frozen, because it already owns a mutable stack and cache and a frozen ``__init__`` costs more on every resolve, so leaving the other fields alone is a provider convention.

The context names no owner, so a provider cannot ask which callable is being filled.
Nothing needs to, because the plan hands the provider the parameter it was compiled for, and a failed resolve attaches the callable to the error on its way out.

The names in ``RESERVED_KEYS`` (``request``, ``form``, ``cleaned_data``, ``_cache``, ``_stack``, ``_context_data``) are refused by name-based resolution.
``ContextByNameProvider`` rules them out from its static verdict and the ``Context`` marker reads none of them, so a context key called ``request`` cannot shadow the ``HttpRequest`` provider and the other five stay reserved for the resolver's own inputs.

Cache
-----

Two caches with different lifetimes sit behind a resolve.
The introspection memos live for the process, and the ``DependencyCache`` lives for one resolution pass.

``cached_signature`` and ``cached_type_hints`` in ``next.deps.resolver`` hold the inspected signature and the resolved type hints of a callable, and a third memo of the same shape holds whether it declares ``**kwargs``.
A callable is inspected once per process rather than once per call, so neither the plan compile nor anything the replay asks later reads its annotations again.
The memo key is the callable itself, or its underlying ``__func__`` paired with a bound flag when it is a method, because a bound method object is recreated on every attribute access and would otherwise miss the memo each time.
The same key carries the compiled plan, see `Injection plan`_ for the compile and the providers version that invalidates it.
A callable no mapping can key is inspected afresh instead of being refused.
The bounds these memos are held under, and the reload that clears them, are described under `Modules`_.

Each resolution pass owns a ``DependencyCache``.
It lives on the ``ResolutionContext`` for that pass and holds named dependency values.
The cache key is the dependency name string alone, with no type component.

``FormActionDispatch.dispatch`` creates a fresh dispatch cache dict on every POST and attaches it to the request under the attribute named ``REQUEST_DEP_CACHE_ATTR``.
The cache is shared across each stage of the dispatch, from ``get_initial`` and the factory resolution to the handler call and any re-render after validation failure.
On a re-render the page context and component context renderers read it back through ``get_request_dep_cache`` and rejoin the same cache.
An ordinary page request that does not pass through the form dispatcher never sees this attribute.

Two consequences flow from the cache.

Provider results are never cached.
   The framework cache memoises only named ``Depends("name")`` values.
   A provider that must return one value per request keeps its own request-scoped store.

Shared across the dispatch.
   The dispatch cache attaches on every form-dispatch POST and is consumed on the validation-failure re-render to keep the second pass cheap.

Cycle detection
---------------

``DependencyCycleError`` is raised when a named dependency re-enters a name already being resolved, directly or through a longer ``Depends`` chain.
The error message lists the chain of named dependencies that closed the loop, read left to right.

Unknown names
-------------

``UnknownDependencyError`` is raised when the string branch of ``Depends`` names a dependency nothing has registered.
Both ``Depends("name")`` and the bare ``Depends()`` that reads the parameter name take that branch.
It subclasses ``LookupError`` and its message names the dependency, the parameter, and the callable being resolved with its source path.
Every other parameter that no provider claims keeps the declared default, or ``None`` without one, because an optional parameter is a legitimate pattern.

Signals
-------

``provider_registered`` fires once per provider when the subclass enters ``provider_registry``.
The registration bumps the registry version, so the next resolve rebuilds every auto-registered instance in priority order and recompiles the plans that were compiled without the newcomer.
Subscribe to track custom providers across reloads.

Extension points
----------------

- Subclass ``DDependencyBase`` to introduce a typed marker.
- Subclass ``RegisteredParameterProvider`` to handle a custom marker or a custom annotation.
- Override ``static_can_handle`` on a provider to give the plan compiler a context-free verdict.
  The ``RegisteredParameterProvider`` default returns ``None``, which keeps the provider as a runtime candidate for every parameter, so a subclass that does not override it is consulted on every resolve.
- Override ``compile_resolve`` on a provider that claims parameters with ``True`` to fold the work its ``resolve`` repeats into a call the plan holds.
  The default returns ``None``, which leaves the parameter on the plain ``resolve`` path.
- Use ``resolver.dependency("name")`` to register a callable for ``Depends("name")``.
- Raise ``UnknownDependencyError`` from a provider that resolves by name.
  The resolve that was replaying the plan attaches the callable it was filling, so the message names the innermost one of a nested chain.

See also
--------

.. seealso::

   :doc:`/content/topics/dependency-injection` for the topic guide.
   :doc:`request-lifecycle` for the surrounding request pipeline.
