.. _ref-deps:

Dependency injection reference
==============================

Module summary
--------------

``next.deps`` exposes the resolver, the parameter providers and their registry, the dependency cache, and the public markers used in annotations.

Public API
----------

Resolver
~~~~~~~~

.. automodule:: next.deps.resolver
   :members:
   :exclude-members: resolver

.. data:: next.deps.resolver.resolver

   The shared resolver singleton used by pages, form actions, and component renderers throughout the framework.
   It is built once at import time and never replaced, so every reference points at the same object.
   Import it as ``from next.deps import resolver`` when you need to call ``resolver.resolve_dependencies`` from a custom provider or a test helper.

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
Returning ``None``, which is what the base class answers, leaves the parameter on the plain ``resolve`` path, and so does leaving the hook undefined altogether.
The hook belongs to ``CompilingParameterProvider`` rather than to ``ParameterProvider``, so a provider written against the mandatory contract alone still passes an ``isinstance`` check against it.
A ``compile_resolve`` that is present but not callable is refused with a ``TypeError`` naming the class, the way a missing ``static_can_handle`` is.

.. automodule:: next.deps.providers
   :members:

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

Cache
~~~~~

.. automodule:: next.deps.cache
   :members:

Context
~~~~~~~

.. automodule:: next.deps.context
   :members:

``RESERVED_KEYS`` lists the names (``request``, ``form``, ``cleaned_data``, ``_cache``, ``_stack``, ``_context_data``) that name-based resolution refuses.
A context key cannot shadow a reserved resolver input.
``resolve_dependencies`` reads the same frozenset to tell its fixed inputs from the URL kwargs, so one list governs both the split and the refusal.
A context mapping reaches resolution as it is, on the page path and on the component path alike, and the reserved names are invisible to the name-based providers rather than stripped from a copy.
See :doc:`/content/internals/di-resolver` for the resolution detail.

Signals
-------

See :doc:`signals` for the ``provider_registered`` signal.

Checks
------

The layer registers no system check.
A ``Depends`` name nothing registered raises ``UnknownDependencyError`` at resolve time, with a ``Did you mean`` hint naming the closest registered dependency.
See :doc:`system-checks` for the checks the other layers register.

See also
--------

.. seealso::

   :doc:`/content/topics/dependency-injection` for the topic guide.
   :doc:`/content/internals/di-resolver` for the resolver internals.
