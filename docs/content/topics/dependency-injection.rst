.. _topics-dependency-injection:

Dependency injection
====================

The dependency resolver inspects the signature of every callable that the framework invokes and fills each parameter from a registered provider.
A page context function asks for a query string value, a layout asks for the current request, an action handler asks for the form and a URL parameter.
The resolver answers all of those calls through the same pipeline.

.. contents::
   :local:
   :depth: 2

Overview
--------

The resolver runs at several call sites.
Page context functions, the page render function, component context functions, and the form dispatch all pass through it.
The form dispatch resolves the form-class factory, ``get_initial``, ``@action`` handlers, ``on_valid``, and ``wizard.done``.
Every call site shares one provider list and one set of markers.
Custom providers and tests can import ``resolver`` from ``next.deps`` and call ``resolver.resolve_dependencies``.

Built in providers
------------------

The framework discovers a fixed list of providers and instantiates them on first use.
Each one carries an explicit ``priority`` value, the resolver consults them from lowest to highest, and the first match wins.

1. Named dependency provider (priority 10).
   A parameter with default ``Depends(...)`` receives the resolved dependency.
2. Context by default provider (priority 20).
   A parameter with a ``Context(...)`` default receives the named context value.
3. Context by name provider (priority 30).
   A parameter whose name matches a context key receives that context value.
4. Form provider (priority 40).
   A parameter named ``form`` or annotated ``DForm[FormClass]`` receives the bound form during action dispatch.
5. Cleaned data provider (priority 40).
   A parameter named ``cleaned_data`` receives the merged wizard cleaned data on a wizard ``done()`` handler.
6. HttpRequest provider (priority 50).
   A parameter annotated ``HttpRequest`` or ``HttpRequest | None`` receives the current request, and one annotated with a concrete subclass receives it only when the request is an instance of that subclass.
7. URL annotation provider (priority 60).
   A parameter annotated ``DUrl[T]`` reads the captured URL segment and coerces it to ``T``.
8. URL kwargs provider (priority 70).
   A parameter whose name matches a captured URL segment resolves to that value.
9. Query string provider (priority 80).
   A parameter annotated ``DQuery[T]`` reads ``request.GET`` by parameter name and coerces to ``T``.

The order makes the default-driven and marker-driven providers decisive.
``Depends`` and ``Context`` look only at the parameter default.
``DUrl`` and ``DQuery`` look only at the annotation.
The context-by-name provider sits ahead of the form, URL, and query providers because a context key under the same name is considered a deliberate publication.

The form provider matches the parameter name ``form``, the marker ``DForm[FormClass]``, and a plain annotation naming a ``django.forms.BaseForm`` or ``django.forms.BaseFormSet`` subclass the bound form is an instance of.
An annotation that names anything else is ruled out before the form is even consulted, so a parameter typed ``int`` or ``MyService`` never reaches this provider.
The request provider tests a concrete subclass annotation against the request in flight, so ``request: ASGIRequest`` under a WSGI server receives the parameter default instead of a request whose interface it would go on to call.
The bare ``HttpRequest`` annotation names no subclass and takes whatever the context carries.

The URL-kwargs provider is the by-name fallback after the ``Depends``, ``Context``, form, request, and ``DUrl`` providers.
It runs before the ``DQuery`` provider, so a ``DQuery`` parameter that shares a captured segment name receives the URL value, not the query value.

.. note::

   This ordering is a publish-once, read-by-name escape hatch.
   An ancestor that publishes a key under ``inherit_context=True`` lets a descendant page read it by bare parameter name in preference to a same-named URL kwarg.

   .. code-block:: python
      :caption: shadowing a same-named URL kwarg

      # routes/shop/[category]/page.py
      @context("category", inherit_context=True)
      def category() -> str:
          return "books"

      # routes/shop/[category]/products/page.py
      @context("listing")
      def products(category: str) -> str:
          return f"Listing for {category}."

   The ``products`` callable receives the published ``category`` value, not the raw ``[category]`` segment.
   See :doc:`/content/howto/share-context-across-pages` for the full pattern.

DUrl
~~~~

The URL path provider coerces the captured segment to the requested type.

.. code-block:: python
   :caption: notes/pages/notes/[int:note_id]/page.py

   from notes.models import Note
   from next import context
   from next.urls import DUrl

   @context("note")
   def note(note_id: DUrl[int]) -> Note:
       return Note.objects.get(pk=note_id)

In the simplest form ``DUrl[T]`` matches the captured segment whose name equals the parameter name, then coerces the captured value to ``T``.
``T`` may be ``str``, ``int``, ``bool``, ``float``, ``UUID``, ``Decimal``, ``date``, or ``datetime``.
A value that already satisfies ``T`` passes through untouched.
A Django converter that pre-coerced the segment, such as ``[uuid:id]`` producing a :class:`~uuid.UUID`, reaches the handler in that shape.
A failed parse falls back to the raw captured value rather than raising.
A segment the route never captured resolves to ``None``, not to the parameter default, because the marker claims the parameter on its annotation alone.
This differs from ``DQuery``, which falls back to the default when the key is absent.
``bool`` treats ``"1"``, ``"true"``, and ``"yes"`` as ``True`` and everything else as ``False``.
``date`` and ``datetime`` parse the ISO 8601 forms accepted by :meth:`date.fromisoformat <datetime.date.fromisoformat>` and :meth:`datetime.fromisoformat <datetime.datetime.fromisoformat>`.
For wildcard ``[[name]]`` segments the captured value is the matched path string.
Annotate as ``DUrl[str]`` or leave it unannotated.

The marker has three forms.

``DUrl[T]``.
   Reads the captured segment that shares the parameter name and coerces it to ``T``.
   Use it when the parameter name already matches the directory segment.

``DUrl["segment"]``.
   Reads the named captured segment and returns it in string form.
   Use it when the parameter name differs from the segment name and no type coercion is needed.

``DUrl["segment", T]``.
   Reads the named captured segment and coerces it to ``T``.
   Use it when the parameter name differs from the segment name, for example ``note_id: DUrl["id", int]`` for an ``[id]`` directory.

The string in ``DUrl["segment"]`` and ``DUrl["segment", T]`` is the URL kwarg key the resolver looks up, not the Django converter label.
Hyphens in directory names are normalised to underscores in the kwarg, so a ``[my-id]`` directory is read as ``DUrl["my_id"]``.

.. note::

   A ``DUrl[T]`` annotation is not the same thing as a Django URL converter label.
   See :ref:`Converter segments <topics-di-converter-segments>` below.

.. _topics-di-converter-segments:

Converter segments
^^^^^^^^^^^^^^^^^^

``[slug:name]`` and ``[uuid:name]`` in directory names are Django URL converter labels that control routing and validation.
See :doc:`file-router` for the routing detail.
They are not Python type annotations.
Django converts the captured value before the URL kwargs provider sees it.

.. list-table::
   :header-rows: 1
   :widths: 25 35 40

   * - Segment type
     - Captured Python type
     - Recommended annotation
   * - ``[slug:name]``
     - ``str``
     - ``DUrl[str]``
   * - ``[uuid:name]``
     - ``uuid.UUID``
     - ``DUrl[UUID]`` for the parsed form, ``DUrl[str]`` for the canonical string

DQuery
~~~~~~

The query provider reads from ``request.GET``.

.. code-block:: python
   :caption: notes/pages/search/page.py

   from notes.models import Note
   from next import context
   from next.urls import DQuery

   @context("results")
   def results(query: DQuery[str] = "") -> list[Note]:
       if not query:
           return []
       return list(Note.objects.filter(title__icontains=query))

The provider supports scalar types and lists.

.. list-table::
   :header-rows: 1
   :widths: 35 30 35

   * - Annotation
     - Wire format
     - Example URL
   * - ``DQuery[str]``
     - single key
     - ``?q=django``
   * - ``DQuery[int]``
     - single key with coercion
     - ``?page=2``
   * - ``DQuery[bool]``
     - single key, truthy values
     - ``?active=1``
   * - ``DQuery[list[str]]``
     - repeated keys
     - ``?tag=a&tag=b``
   * - ``DQuery[list[str]]``
     - bracket suffix
     - ``?tag[]=a&tag[]=b``
   * - ``DQuery[list[str]]``
     - comma format
     - ``?tag=a,b,c``

The provider returns the parameter default when the key is absent.

``DQuery`` accepts the same scalar set as ``DUrl``, namely ``str``, ``int``, ``bool``, ``float``, ``UUID``, ``Decimal``, ``date``, and ``datetime``, plus ``list[T]`` for any of those scalars.
A value that fails to parse falls back to the raw query string rather than raising.

Context markers
---------------

Two markers fill parameters from distinct data sources.
``Context`` reads from the per-render context dictionary.
``Depends`` invokes a callable registered in the resolver's process-wide dependency map.

``Context("key")``.
   Returns the value of the named context key produced by an ancestor layout or by a context function earlier in the chain.
   See :doc:`context` for the full set of ``Context`` shapes and how it relates to plain name matching.
   ``Context`` takes four forms, namely no argument that reads the parameter name, a string key, a callable, and a constant, with a ``default=`` fallback when the key is absent.

``Depends`` takes one of four forms, selected by its argument.

``Depends("name")``.
   The argument is a string.
   The resolver looks up the callable registered under that name through ``resolver.dependency`` and invokes it with its own parameters resolved.
   A name nothing has registered raises ``UnknownDependencyError`` at resolve time rather than injecting ``None``, and the message carries the dependency name, the parameter name, and the name and source path of the callable that asked for it.
   When a registered name is close to the missing one, the message ends with a ``Did you mean`` hint naming it.

``Depends(callable)``.
   The argument is a callable.
   The resolver resolves the callable's own parameters and calls it as a factory.

``Depends(value)``.
   The argument is any other object.
   That object is injected directly as a constant.

``Depends()``.
   No argument.
   The marker falls back to the parameter name and resolves it as the named form.
   An unregistered parameter name raises the same ``UnknownDependencyError``.

Import the exception from ``next.deps`` as ``from next.deps import UnknownDependencyError``.
See :doc:`/content/internals/di-resolver` for the cycle and cache mechanics.

.. code-block:: python
   :caption: consuming context and depends

   from next import Depends, context
   from next.pages import Context

   @context("ready_message")
   def ready_message(
       theme: dict | None = Depends("layout_theme"),
       user_name: str = Context("user_name"),
   ) -> str:
       return f"Hello {user_name}, theme is {theme}."

Registering named dependencies
------------------------------

Use ``resolver.dependency`` to register a callable that any handler can ask for through ``Depends("name")``.

.. code-block:: python
   :caption: notes/deps.py

   from next.deps import resolver

   @resolver.dependency("layout_theme")
   def layout_theme() -> dict:
       return {"name": "Notes", "version": "1.0"}

Import the module that defines the dependency from ``AppConfig.ready`` so the decorator runs before the first request.
The registered callable can take any provider-resolved parameters because it is itself dispatched through the resolver.

Diagnosing a dependency cycle
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A named dependency may itself ask for other named dependencies through ``Depends``.
When two of them ask for each other, directly or through a longer chain, resolution cannot terminate.

.. code-block:: python
   :caption: notes/deps.py

   from next import Depends
   from next.deps import resolver

   @resolver.dependency("profile")
   def profile(settings: dict = Depends("settings")) -> dict:
       return {"theme": settings["theme"]}

   @resolver.dependency("settings")
   def settings(profile: dict = Depends("profile")) -> dict:
       return {"theme": profile.get("theme", "light")}

The resolver records each name on a stack as it enters the dependency and marks the cache entry in progress.
Re-entering a name that is already on the stack raises ``DependencyCycleError``.

.. code-block:: text
   :caption: the error

   next.deps.cache.DependencyCycleError: Circular dependency: profile -> settings -> profile

The chain in the message is the resolution path that closed the loop, read left to right.
The traceback lists the fully qualified path ``next.deps.cache.DependencyCycleError`` because that is where the exception class is defined.
Import the exception from the public ``next.deps`` namespace with ``from next.deps import DependencyCycleError``.
The deeper ``next.deps.cache`` path is an implementation detail and is not part of the supported import surface.
Break the cycle by removing one ``Depends`` edge.
Here ``settings`` does not need ``profile`` at all, so the fix is to drop that parameter.

.. code-block:: python
   :caption: notes/deps.py

   from next import Depends
   from next.deps import resolver

   @resolver.dependency("settings")
   def settings() -> dict:
       return {"theme": "light"}

   @resolver.dependency("profile")
   def profile(settings: dict = Depends("settings")) -> dict:
       return {"theme": settings["theme"]}

When both dependencies genuinely need shared data, move that data into a third dependency and have both depend on it.

Writing a custom provider
-------------------------

For data sources that do not fit the built ins, register a parameter provider.
The base classes are ``RegisteredParameterProvider`` and ``DDependencyBase``.

.. code-block:: python
   :caption: notes/providers.py

   from typing import get_args, get_origin
   from django.http import Http404
   from notes.models import Note
   from next.deps import DDependencyBase, RegisteredParameterProvider

   class DNote[T](DDependencyBase[T]):
       __slots__ = ()

   class NoteProvider(RegisteredParameterProvider):
       def can_handle(self, param, _context) -> bool:
           return get_origin(param.annotation) is DNote

       def static_can_handle(self, param) -> bool | None:
           return get_origin(param.annotation) is DNote

       def resolve(self, param, context):
           (model_cls,) = get_args(param.annotation)
           pk = context.url_kwargs.get("id")
           if pk is None and context.request is not None:
               pk = context.request.POST.get("note_id")
           try:
               return model_cls.objects.get(pk=pk)
           except model_cls.DoesNotExist as exc:
               raise Http404 from exc

One marker can serve both a page render and a form action handler.
A page render captures the identifier in the URL, while a form action carries it in the POST body.
The ``resolve`` method above checks both sources, so the same ``DNote[Note]`` parameter works in either call site.
The form template carries the identifier in a hidden input so the POST branch can read it.
See ``examples/kanban`` for a marker that serves both call sites.

Use the new marker.

.. code-block:: python
   :caption: notes/pages/notes/[id]/page.py

   from notes.models import Note
   from notes.providers import DNote
   from next import context

   @context("note")
   def current_note(note: DNote[Note]) -> Note:
       return note

Two rules apply to the marker class.

Python 3.12 generic syntax.
   ``class DNote[T](DDependencyBase[T])`` makes ``DNote[Note]`` a parameterised generic whose ``get_origin`` is ``DNote``.
   A non generic ``class DNote(DDependencyBase[Note])`` does not behave like a parameter marker.

Import the provider module.
   A provider class registers itself when its module is imported, and the natural place to force that import is ``AppConfig.ready`` of the application that owns the provider.
   A class that registers later still joins the provider list by priority, and the plans compiled without it are recompiled.

A custom provider that does not declare ``priority`` inherits the ``RegisteredParameterProvider`` default of ``100``.
The nine built-in providers occupy the range ``10`` (named dependency) through ``80`` (query string), so the default keeps a custom provider after every built-in.
``FormProvider`` and ``CleanedDataProvider`` share priority ``40``.
Set ``priority`` on the subclass when the new provider has to claim a parameter the built-ins would otherwise match, for example a value below ``60`` for an annotation that should outrank ``DUrl``.

Static verdicts
~~~~~~~~~~~~~~~

The resolver asks each provider ``static_can_handle(param)`` once per callable and compiles the answers into that callable's injection plan.
``True`` claims the parameter in every context and ends the walk, ``False`` rules the provider out for that parameter for good, and ``None`` keeps ``can_handle`` running on every resolve.
``NoteProvider`` above answers from the annotation alone, so its marker parameters are settled at compile time and no ``can_handle`` runs per request.

The parameter reaches the hook with its type hint already resolved through ``typing.get_type_hints``, extras included, so a hook compares real types even where the annotation was written as a string and can match on the metadata of an ``Annotated[...]`` hint.
A hint the framework cannot evaluate falls back to the raw annotation as written, and the plan built from it is not cached, so an annotation that only a later import can resolve takes effect on the next resolve.
A provider that implements the ``ParameterProvider`` protocol directly declares the method itself, while a ``RegisteredParameterProvider`` subclass inherits the ``None`` default.
The method is not optional.
A provider handed to ``add_provider``, ``prepend_provider``, ``register``, or the resolver constructor without a callable ``static_can_handle`` is refused with a ``TypeError`` naming the class, rather than failing later from inside the plan compiler.

A provider that answers ``True`` may go one step further and implement ``compile_resolve(param)``, which returns the call the plan makes with the resolution context alone.
That hook is spelled by ``CompilingParameterProvider``, a second protocol extending ``ParameterProvider``, so a provider that stops at the mandatory three methods still satisfies the first one.
``NoteProvider`` above would read ``get_args(param.annotation)`` there, once per plan, and return a closure that is left with the lookup and the query.
Returning ``None`` from the hook keeps the parameter on the plain ``resolve`` path, and so does leaving the hook undefined.
See :doc:`/content/internals/di-resolver` for how a plan is compiled, cached, and invalidated.

Resolution cache
----------------

Each resolution pass wraps a per-render dependency cache in a fresh ``DependencyCache``.
The wrapper is new per call, but the backing store is shared across every ``@context`` callable in one page render, so a ``Depends("name")`` value resolved by one callable is reused by the next.
The cache memoises ``Depends("name")`` callables only, keyed by the registered name.

A second context function in the same page render that asks for the same ``Depends("name")`` dependency receives the memoised value, not a fresh call.
To share one value across several context functions in the same render, publish it through a named dependency such as ``Depends("active_tenant")``.
See :doc:`/content/howto/share-context-across-pages` for a worked example.

The cache lives for one form dispatch.
Every stage of that POST, from ``get_initial`` through the validation-failure re-render, shares it.
``FormActionDispatch`` attaches its dependency cache to the request, and ``get_request_dep_cache`` reads it back.
The function returns ``None`` outside a form dispatch, so callers handle the missing case.

.. code-block:: python
   :caption: reading the cache

   from django.http import HttpRequest
   from next.deps import get_request_dep_cache

   def render(request: HttpRequest) -> str:
       cache = get_request_dep_cache(request)
       if cache is None:
           return "No form dispatch cache on this request."
       return f"Cache has {len(cache)} entries."

The constant ``REQUEST_DEP_CACHE_ATTR`` names the request attribute that holds the cache.

More recipes for diagnosing missing markers and CSRF or dispatch errors live in :doc:`/content/faq/troubleshooting`.

Resolution performance
----------------------

The signature and the type hints of a callable are read once per process rather than once per request, and every later resolve reads that memo.
The provider that fills each parameter is settled at the same time and compiled into the injection plan of the callable, so a request replays that plan instead of asking every provider about every parameter.
A parameter the signature alone cannot settle keeps its remaining candidates and replays their ``can_handle``, which is the only provider walk left on the request path.
The cost of a resolve therefore follows the number of parameters the callable declares, not the number of pages, components, or forms in the project.
Values behind ``Depends("name")`` are reused for the rest of the pass, as `Resolution cache`_ above describes.

``DEPENDENCY_RESOLVER`` in ``NEXT_FRAMEWORK`` names the class that performs every injection.
It is an extension point for a ``DependencyResolver`` subclass rather than a switch between shipped implementations, and it is read at startup and on every settings reload, never per request.
See :doc:`/content/internals/di-resolver` for how a plan is compiled, cached, and invalidated, and :doc:`/content/ref/settings` for the setting.

Avoid ``from __future__ import annotations`` in DI modules
----------------------------------------------------------

The resolver resolves the annotations of each callable once through :func:`typing.get_type_hints`, so a string annotation carries only as far as its names are importable at runtime.
A hint that fails to evaluate leaves the raw string in the injection plan, and a marker such as ``DUrl[int]`` stops matching, because ``typing.get_origin`` returns ``None`` for a string.
Real annotations remove that failure mode outright, which is why a ``page.py`` or a ``component.py`` never carries the future import.

Two rules.

Do not use future annotations in modules with DI parameters.
   ``page.py``, ``component.py``, and ``providers.py`` need real annotations.
   Plain Python files that only import the framework can use future annotations freely.

Keep DI types runtime importable.
   A hint the resolver cannot evaluate never becomes the type a provider matches on.
   Types hidden behind ``if TYPE_CHECKING``, and types defined inside a function body, are invisible to that evaluation.
   Keep DI-touching annotations on classes that import at module top level.

Resolver lifecycle
------------------

The resolver instantiates the registered provider classes on first use and catches up with the ones that register later, so importing a custom provider from ``AppConfig.ready`` keeps its place in the order predictable.
See :doc:`/content/internals/di-resolver` for the full lifecycle.

See also
--------

.. seealso::

   :doc:`context` for the ``@context`` decorator and inheritance flow.
   :doc:`file-router` for ``DUrl`` and captured URL parameters.
   :doc:`testing` for the ``override_dependency`` and ``override_provider`` test helpers.
   :doc:`/content/faq/troubleshooting` for concrete resolver and dispatch errors.
   :doc:`/content/howto/share-context-across-pages` for the inherited context pattern.
   :doc:`/content/internals/di-resolver` for the resolver internals.
   :doc:`/content/ref/deps` for the public API and cache contract.
