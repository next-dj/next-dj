.. _ref-deps:

Dependency injection reference
==============================

Module summary
--------------

``next.deps`` exposes the resolver, the parameter providers and their registry, the dependency cache, the public markers used in annotations, ``DObject`` among them, and the async machinery, the request scope, the ``Loader``, and the segment runner.

Public API
----------

Resolver
~~~~~~~~

.. automodule:: next.deps.resolver
   :members:
   :exclude-members: resolver

.. data:: next.deps.resolver.resolver

   The shared resolver holder used by pages, form actions, and component renderers throughout the framework.
   It forwards every attribute to the resolver in force, so a reference taken before a swap reads the object that replaced it.
   ``DEPENDENCY_RESOLVER`` builds that object at startup and again on every settings reload, and ``next.deps.resolver.current_resolver`` returns it for a caller that would otherwise pay the forwarding per attribute.
   Import it as ``from next.deps import resolver`` when you need to call ``resolver.resolve_dependencies`` from a custom provider or a test helper.

``DEPENDENCY_RESOLVER`` selects the class the singleton takes on, and it accepts any ``DependencyResolver`` subclass.
Two classes ship with the framework, the default ``next.deps.DependencyResolver`` that compiles an injection plan per callable and ``next.deps.linear.LinearDependencyResolver``, the plan-free reference implementation the compiled path is checked against.
See :doc:`settings` for the key and its default.

Providers
~~~~~~~~~

``ParameterProvider`` is the protocol the resolver consumes, and it declares ``can_handle``, ``resolve``, and ``static_can_handle``.
``RegisteredParameterProvider`` is the auto-registered base used by the built-in providers, and it answers ``static_can_handle`` with ``None`` unless a subclass overrides it.
Subclasses join ``provider_registry`` through ``__init_subclass__``, so the resolver instantiates them on first use without an explicit import.
``static_can_handle`` is the context-free verdict the resolver compiles into a callable's plan, and ``None`` leaves every decision to ``can_handle``.
A subclass reaches the active resolver through the ``resolver`` class attribute, which is bound to the shared singleton.

``static_can_handle`` is required, and a provider that defines no callable one is refused with a ``TypeError`` naming the class as it joins a resolver.
The parameter a provider receives arrives with its type hint already resolved, so a provider never re-derives the hint of a ``request: "HttpRequest"`` parameter written under a deferred annotation.
The hint keeps the extras of an ``Annotated[...]`` annotation, so a provider may match on the metadata a caller attached.

``compile_resolve`` is the optional hook that folds the work of ``resolve`` into a call the plan makes with the context alone.
The compiler asks it once, and only about a parameter ``static_can_handle`` claimed with ``True``, so whatever the answer reads off the signature is paid per plan rather than per resolve.
The hook returns either ``None`` or a ``next.deps.ParameterFiller``, a callable that takes the ``ResolutionContext`` and returns the parameter value, and an answer that is neither raises a ``TypeError`` naming the provider while the plan compiles.
Returning ``None``, which is what the base class answers, leaves the parameter on the plain ``resolve`` path, and so does leaving the hook undefined altogether.
The hook belongs to ``CompilingParameterProvider`` rather than to ``ParameterProvider``, so a provider written against the mandatory contract alone still passes an ``isinstance`` check against it.
A ``compile_resolve`` that is present but not callable is refused with a ``TypeError`` naming the class, the way a missing ``static_can_handle`` is.

``compile_aresolve`` is the async counterpart, declared by ``AsyncCompilingParameterProvider``.
The async replay of a plan, the one an async page or component render runs, asks it once per parameter and awaits the ``AsyncParameterFiller`` it returns, and ``None`` keeps the parameter on the sync filler.
``RegisteredParameterProvider`` answers ``None`` and declares ``blocking``, ``True`` unless a provider that only reads memory sets it ``False``, which ``static_blocking`` reads to decide whether the async replay runs the filler as a blocking segment on the request thread.

``static_reach`` is declared by ``ReachingParameterProvider`` and names each callable that filling a parameter calls, as a pair of the key it is memoised under and the callable, with ``None`` as the key of a callable resolved inline such as the source of ``Context(callable)``.
The compiler reads it once per async plan, so a key two consumers of one render reach is shared through the request scope and runs once, whether the dependency is sync or async.
``RegisteredParameterProvider`` answers an empty tuple, and a provider without the hook still resolves correctly while the keys it reaches are computed in its own segment.

``static_answers`` is declared by ``AnsweringParameterProvider`` and tells whether filling a parameter raises ``Http404`` or ``PermissionDenied`` by contract, as ``ObjectProvider`` does for a ``DObject`` row the scope does not see.
A ``@context`` callable with such a parameter, directly or through a callable ``static_reach`` names, runs before the shell of a deferred page boundary, see :ref:`topics-boundaries-loading`.
``RegisteredParameterProvider`` answers ``False``, and a provider without the hook never makes a callable run before the shell.

``static_reads`` is declared by ``ReadingParameterProvider`` and returns a ``ContextReads`` that tells the context graph of a page what filling a parameter reads, the context ``keys`` the parameter names itself, the ``nested`` keys it reads under another name, the named ``dependencies`` it resolves, and the ``calls`` it runs as ``static_reach`` pairs, whose own parameters the graph reads in turn.
The graph orders the ``@context`` callables of a page by these reads, so two callables that read nothing of each other run side by side in an async render.
``READS_NOTHING`` stands for a parameter filled from the request alone, as the URL kwargs, the query, the form, and the consent providers answer.
``RegisteredParameterProvider`` answers ``READS_EVERY``, and so does a provider without the hook, which orders its callable after every one before it.

.. automodule:: next.deps.providers
   :members:

Introspection
~~~~~~~~~~~~~

.. automodule:: next.deps.introspect
   :members:
   :exclude-members: HINT_ERRORS, IntrospectKey

``cached_signature``, ``cached_type_hints`` and ``cached_accepts_var_keyword`` read one fact off a callable and memoise it under that callable, so a plan compile and a provider asking the same question pay for the read once.
The memos are bounded, because a dev reload mints a fresh function object per save and each one would otherwise stay pinned with its globals.
``prepared_parameter`` is the parameter preparation both the plan compiler and the plan-free resolver fill from, so the second opinion they hold each other to is the choice of provider rather than the shape of the parameter.
``IntrospectKey`` is the key all three memos and the plan cache share, a callable paired with a bound flag, and ``HINT_ERRORS`` is the tuple of everything an annotation expression is allowed to fail with.
Both are type aliases or plain constants rather than classes, so they carry no members of their own.

Plan
~~~~

.. automodule:: next.deps.plan
   :members:
   :exclude-members: ParameterFiller, ParameterPlan, InjectionPlan

``ParameterFiller`` is a callable taking the ``ResolutionContext`` and returning one parameter value.
``ParameterPlan`` pairs a parameter name with the providers the signature could not rule out, its marker, and the ``inspect.Parameter`` itself, and ``InjectionPlan`` is the tuple of those the resolver replays.
The three are type aliases rather than classes, so they carry no members of their own.

Registry
~~~~~~~~

.. automodule:: next.deps.registry
   :members:

``provider_registry`` is the ordered list of provider classes that register themselves, and ``version`` is a read-only counter that moves on every registration.
A class registering under an address another class already holds replaces it in place, so a dev reload of the module declaring a provider adds no duplicate.
The address pairs the class name with the file its body was compiled from, because every ``page.py`` is loaded under one module name and two page files declaring the same provider name are two providers, not one.
A resolver compares that counter before it replays a plan, so a provider class imported after the first resolve joins the auto-registered instances on the next one and the plans compiled without it recompile.
Registering a provider also sends ``provider_registered``.

Markers
~~~~~~~

.. automodule:: next.deps.markers
   :members:

Other areas declare their own markers on the same base.
``DUrl`` and ``DQuery`` of ``next.urls`` read the captured path and the query string, see :doc:`urls`.
``Paging`` of ``next.partial`` is a plain class rather than a marker, named as ``Annotated[Paging, <pager>]``, and hands a context callable the pager of an infinite list bound to the request, see :ref:`ref-partial-infinite-lists`.

Objects
~~~~~~~

.. automodule:: next.deps.objects
   :members: DObject, ObjectProvider

``DObject`` is re-exported from ``next.deps``.
``DObject[Model]`` loads the row whose primary key the URL kwarg of the parameter name holds, and ``DObject[Model, "parent"]`` also filters by the field ``parent`` against the context value of the same name, which may be spelled ``Literal["parent"]``.
Every lookup reads ``Model._default_manager.visible_to(scope)``, where ``scope`` is the value of the named dependency ``"scope"``, which the project registers through ``resolver.dependency`` and the framework never defaults.
A hidden, missing, or unparsable row raises :exc:`~django.http.Http404`, and a parent value of ``None`` does the same.
``ObjectProvider`` fills the parameter at priority ``0``, ahead of every other shipped provider, and keeps each row in the per-render dependency cache.
See :ref:`topics-di-dobject` for the topic guide.

Cache
~~~~~

``DependencyCache`` stores what a named ``Depends("name")`` and a ``Depends(factory)`` returned and the rows ``DObject`` loads, so each of them runs once per cache, within one render unit on a sync and an async render alike, see :ref:`topics-dependency-injection`.
A form action dispatch publishes its cache on the request under the attribute ``REQUEST_DEP_CACHE_ATTR`` names, and ``render_dep_cache`` hands a render that cache, so a hook and the render it triggers share their providers.

.. automodule:: next.deps.cache
   :members:

Errors
~~~~~~

.. automodule:: next.deps.errors
   :members:

``AsyncDependencyError`` is raised for an async callable a resolve cannot await, an async generator or an ``async def`` handed to the sync ``resolve_dependencies``, and it subclasses ``AsyncCallableError``, so a module load reports it as ``next.E218``.
``MissingKeyError`` is raised by a ``Loader`` for a key its batch function returned no value for, and it is a ``LookupError`` rather than a ``KeyError``, so a template lookup cannot hide it.
``DependencyCycleError`` carries the chain that closed the loop on ``cycle``, and ``UnknownDependencyError`` carries the missing ``name``, the ``param_name`` it was asked for, the ``suggestion`` a close registered name produced, and the ``func`` the failed resolve attributed it to.
``UnboundObjectError`` subclasses :exc:`~django.core.exceptions.ImproperlyConfigured` and carries the ``param_name`` of the ``DObject`` parameter, the ``key`` it found nothing under, and ``parent``, which is ``True`` when that key is a missing parent context value rather than a missing URL kwarg.
For an ``obj=DObject[Model]`` of an access declaration, ``param_name`` holds the label of the model and ``key`` its ``model_name``, and the dispatcher answers that variant with 400 as well.
The form dispatcher answers an ``UnboundObjectError`` for a missing URL kwarg with 400 Bad Request and a logged warning, since the origin the client sent lacks the kwarg, and lets the parent variant raise.
Every error is re-exported from ``next.deps``, so a caller never imports the module directly.

Context
~~~~~~~

.. automodule:: next.deps.context
   :members:

``RESERVED_KEYS`` lists the names (``request``, ``form``, ``cleaned_data``, ``_cache``, ``_stack``, ``_context_data``) that name-based resolution refuses.
A context key cannot shadow a reserved resolver input.
``resolve_dependencies`` reads the same frozenset to tell its fixed inputs from the URL kwargs, so one list governs both the split and the refusal.
A context mapping reaches resolution as it is, on the page path and on the component path alike, and the reserved names are invisible to the name-based providers rather than stripped from a copy.
See :doc:`/content/internals/di-resolver` for the resolution detail.

The request scope an async render shares its dependencies through and the runner of its blocking steps are framework-internal, see :ref:`internals-di-request-scope`.

Loader
~~~~~~

``Loader(batch, *, max_batch=None, window=None)`` gathers the keys one request scope loads into one call of ``batch`` per window.
``batch`` is a plain ``def`` or an ``async def`` that takes a sequence of keys and returns a mapping by key or a sequence in key order.
``await loader.load(key)`` loads on the event loop, and ``loader.load_sync(key)`` on a thread, refusing an event loop thread.
A ``max_batch`` or ``window`` left at ``None`` follows ``LOADER["MAX_BATCH"]`` and ``LOADER["WINDOW"]``, see :doc:`settings`.
An ``async def`` batch function runs on the event loop and may not reach the ORM or a thread-sensitive ``sync_to_async`` call, which raises ``RuntimeError``.
``rows(Model)`` returns the shared loader of ``Model`` rows by primary key, one ``pk__in`` query per batch through the default manager with no access scoping.
See :doc:`/content/topics/boundaries/async` for the guide.

.. automodule:: next.deps.loader
   :members: Loader, rows

Signals
-------

See :doc:`signals` for the ``provider_registered`` signal.

Checks
------

``next.deps.checks`` registers the ``DObject`` checks, which read annotations, routes, and registries without running a query.

- ``next.E153`` reports a model whose default manager has no ``visible_to``, named by a ``DObject`` parameter or by an ``obj=DObject[Model]`` of an access declaration.
- ``next.E154`` reports a page ``DObject`` whose parent no earlier context callable provides.
- ``next.E155`` reports a ``DObject`` with a parent on a form handler, a form factory, a form hook, ``has_object_permission`` included, or a ``FormWizard`` hook.
- ``next.E156`` reports a ``DObject`` on a ``@component.context`` callable.
- ``next.E157`` reports a project that declares a ``DObject`` and registers no ``scope`` dependency.
- ``next.E158`` reports a ``DObject`` on a callable whose type hints do not resolve, which raises ``TypeError`` on every resolve.
- ``next.W126`` reports a page ``DObject`` whose full route, with every prefix and include around the tree, hands it no kwarg of its name.
- ``next.E162`` reports an ``obj=DObject[Model]`` of a ``page.py`` when the route of a page the declaration binds captures no kwarg named after the ``model_name`` of the model.

A ``Depends`` name nothing registered raises ``UnknownDependencyError`` at resolve time, with a ``Did you mean`` hint naming the closest registered dependency.
See :doc:`system-checks` for the exact conditions.

See also
--------

.. seealso::

   :doc:`/content/topics/dependency-injection` for the topic guide.
   :doc:`/content/internals/di-resolver` for the resolver internals.
   :doc:`/content/security/di-and-untrusted-input` for treating an injected URL or query value as untrusted.
