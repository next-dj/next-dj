.. _topics-dependency-injection:

Dependency Injection
====================

The dependency resolver inspects the signature of every callable that the framework invokes and fills each parameter from a registered provider.
A page context function asks for a query string value, a layout asks for the current request, an action handler asks for the form and a URL parameter.
The resolver answers all of those calls through the same pipeline.

This page covers the built in markers, how to read URL and query values, how to publish named dependencies, how to write a custom provider, and how the request-scoped cache prevents redundant work.

.. contents::
   :local:
   :depth: 2

Overview
--------

The resolver runs at four call sites.

Page context functions.
   ``@context("key")`` and unkeyed ``@context`` callables in ``page.py`` and ``layout.py``.

Page render function.
   The optional ``render`` function on a page module.

Component context functions.
   ``@component.context("key")`` callables in ``component.py``.

Form action handlers.
   ``@action`` callables registered through :doc:`next.forms <forms/index>`.

Every call site uses the same providers and the same markers.
The resolver scans parameters in order, picks the first provider that can handle each parameter, and runs the function with the resolved values.
A parameter that no provider claims falls back to its default value or stays ``None``.

Built In Providers
------------------

The framework registers eight providers by default.

HttpRequest provider.
   A parameter annotated with ``HttpRequest`` or ``HttpRequest | None`` receives the current request.

URL annotation provider.
   A parameter annotated ``DUrl[T]`` reads the captured URL segment with matching name and coerces it to ``T``.

URL kwargs provider.
   A parameter whose name matches a captured URL segment resolves to that value automatically.

Query string provider.
   A parameter annotated ``DQuery[T]`` reads ``request.GET`` by parameter name and coerces to ``T``.

Form provider.
   A parameter named ``form`` or annotated ``DForm[FormClass]`` receives the bound form during action dispatch.

Context by default provider.
   A parameter with a ``Context(...)`` default receives the named context value.

Context by name provider.
   A parameter whose name matches a context key receives that context value.

Named dependency provider.
   A parameter with default ``Depends("name")`` receives the result of the registered dependency callable.

A parameter that no provider claims keeps its default value.
Functions written for unit tests can therefore declare ``request: HttpRequest | None = None`` and still work both inside and outside the framework.

DUrl
~~~~

The URL path provider coerces the captured segment to the requested type.

.. code-block:: python
   :caption: notes/routes/notes/[int:note_id]/page.py

   from notes.models import Note

   from next.pages import context
   from next.urls.markers import DUrl


   @context("note")
   def note(note_id: DUrl[int]) -> Note:
       return Note.objects.get(pk=note_id)

Supported types include ``str``, ``int``, ``slug``, ``uuid.UUID``, and ``list[str]`` for wildcard segments.

DQuery
~~~~~~

The query provider reads from ``request.GET``.

.. code-block:: python
   :caption: notes/routes/search/page.py

   from next.pages import context
   from next.urls.markers import DQuery


   @context("results")
   def results(query: DQuery[str] = "") -> list:
       return search(query) if query else []

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

Context Markers
---------------

Two markers reach into the request-scoped context.

Context("key").
   Returns the value of the named context key produced by an ancestor layout or by a context function earlier in the chain.

Depends("name").
   Returns the result of a callable registered through ``next.deps.resolver.dependency``.

.. code-block:: python
   :caption: consuming context and depends

   from next.deps import Depends
   from next.pages import Context, context


   @context("ready_message")
   def ready_message(
       theme: dict | None = Depends("layout_theme"),
       user_name: str = Context("user_name"),
   ) -> str:
       return f"Hello {user_name}, theme is {theme}."

The ``Context("user_name")`` form is interchangeable with declaring the parameter ``user_name`` and letting the context-by-name provider fill it.
The explicit form makes the source visible at the call site.

Registering Named Dependencies
------------------------------

Use ``resolver.dependency`` to register a callable that any handler can ask for through ``Depends("name")``.

.. code-block:: python
   :caption: notes/deps.py

   from next.deps import resolver


   @resolver.dependency("layout_theme")
   def layout_theme() -> dict:
       return {"name": "Notes", "version": "1.0"}

Imports inside ``AppConfig.ready`` ensure that the decorator runs before the first request.
The registered callable can take any provider-resolved parameters because it is itself dispatched through the resolver.

Writing a Custom Provider
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

       def resolve(self, param, context):
           (model_cls,) = get_args(param.annotation)
           pk = context.url_kwargs["id"]
           try:
               return model_cls.objects.get(pk=pk)
           except model_cls.DoesNotExist as exc:
               raise Http404 from exc

Resolving From URL or POST
~~~~~~~~~~~~~~~~~~~~~~~~~~

One marker can serve both a page render and a form action handler.
A page render captures the identifier in the URL.
A form action carries it in the POST body.
The provider checks both sources so the same ``DNote[Note]`` parameter works in either call site.

.. code-block:: python
   :caption: notes/providers.py

   class NoteProvider(RegisteredParameterProvider):
       def can_handle(self, param, _context) -> bool:
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

The form template carries the identifier in a hidden input so the POST branch can read it.
See ``examples/kanban`` for a marker that serves both call sites.

Use the new marker.

.. code-block:: python
   :caption: notes/routes/notes/[id]/page.py

   from notes.models import Note
   from notes.providers import DNote

   from next.pages import context


   @context("note")
   def note(note: DNote[Note]) -> Note:
       return note

Two rules apply to the marker class.

Python 3.12 generic syntax.
   ``class DNote[T](DDependencyBase[T])`` makes ``DNote[Note]`` a parameterised generic whose ``get_origin`` is ``DNote``.
   A non generic ``class DNote(DDependencyBase[Note])`` does not behave like a parameter marker.

Import before resolution.
   Register the provider before the resolver caches its provider list.
   The natural place is ``AppConfig.ready`` of the application that owns the provider.

Request Scoped Cache
--------------------

The resolver caches every produced value on the request.
A second context function that asks for the same parameter receives the cached value, not a fresh call.

The cache is also shared between the initial render of a form page and the re-render on validation failure.
``FormActionDispatch`` attaches its dependency cache to the request, and ``get_request_dep_cache`` reads it back.
The function returns ``None`` outside a form dispatch, so callers handle the missing case.

.. code-block:: python
   :caption: reading the cache

   from next.deps.cache import get_request_dep_cache


   def render(request) -> str:
       cache = get_request_dep_cache(request)
       if cache is None:
           return "No form dispatch cache on this request."
       return f"Cache has {len(cache)} entries."

The constant ``REQUEST_DEP_CACHE_ATTR`` names the request attribute that holds the cache.
A ``DependencyCycleError`` is raised when the resolver detects a circular dependency between providers.

PEP 563 Caveat
--------------

The resolver inspects real annotations, not strings.
A ``from __future__ import annotations`` import in a ``page.py`` or ``component.py`` turns every annotation into a string and ``typing.get_origin`` returns ``None``.

Two rules.

Do not use future annotations in modules with DI parameters.
   ``page.py``, ``layout.py``, ``component.py``, and ``providers.py`` need real annotations.
   Plain Python files that only import the framework can use future annotations freely.

Keep DI types runtime importable.
   The resolver evaluates string annotations through ``typing.get_type_hints``.
   Types hidden behind ``if TYPE_CHECKING`` are not available at evaluation time.

Resolver Lifecycle
------------------

The resolver collects its provider list on import.
The ``provider_registered`` signal fires once per provider when the class enters the registry.

Provider order matters.
The first provider that returns ``True`` from ``can_handle`` wins.
Place narrower providers before broader ones to make resolution deterministic.

The framework runs every provider with the same ``ResolutionContext``.
The context carries the current request, captured URL kwargs, query string, current template context, and form instance when a form is bound.

Common Patterns
---------------

Optional Request
~~~~~~~~~~~~~~~~

Use ``HttpRequest | None`` so the function works both in a request and in unit tests.

.. code-block:: python
   :caption: unit testable context

   from django.http import HttpRequest

   from next.pages import context


   @context("greeting")
   def greeting(request: HttpRequest | None = None) -> str:
       if request is None:
           return "Hi tester."
       return f"Hi {request.user}."

Coerced Query Value
~~~~~~~~~~~~~~~~~~~

Use ``DQuery[int]`` with a default for pagination.

.. code-block:: python
   :caption: paged listing

   from next.pages import context
   from next.urls.markers import DQuery


   @context("page_index")
   def page_index(page: DQuery[int] = 1) -> int:
       return max(page, 1)

Shared Context Value
~~~~~~~~~~~~~~~~~~~~

A custom provider can publish a value once and let several context functions ask for it without duplicating queries.

.. code-block:: python
   :caption: custom provider

   from notes.providers import DNote

   from next.pages import context


   @context("breadcrumbs")
   def breadcrumbs(note: DNote[Note]) -> list:
       return [{"label": "Home", "href": "/"}, {"label": note.title}]


   @context("note_count")
   def note_count(note: DNote[Note]) -> int:
       return note.comments.count()

The provider is called once thanks to the request cache.
Each context function reads the same instance.

See Also
--------

.. seealso::

   :doc:`context` for the ``@context`` decorator and inheritance flow.
   :doc:`file-router` for ``DUrl`` and captured URL parameters.
   :doc:`/content/howto/share-context-across-pages` for the inherited context pattern.
   :doc:`/content/internals/di-resolver` for the resolver internals.
   :doc:`/content/ref/deps` for the public API and cache contract.
