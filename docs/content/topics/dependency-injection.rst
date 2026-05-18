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
   ``@context("key")`` and unkeyed ``@context`` callables in ``page.py``.

Page render function.
   The optional ``render`` function on a page module.

Component context functions.
   ``@component.context("key")`` callables in ``component.py``.

Form action handlers.
   ``@action`` callables registered through :doc:`next.forms <forms/index>`.

Every call site uses the same providers and the same markers.
For each parameter the resolver walks its provider list and picks the first provider whose ``can_handle`` returns ``True``.
A parameter that no provider claims falls back to its declared default, or stays ``None`` when it has none.
Functions written for unit tests can therefore declare ``request: HttpRequest | None = None`` and still work both inside and outside the framework.

Built In Providers
------------------

The framework registers eight providers.
They are consulted in a fixed order and the first match wins, so the list below is also the priority order.

1. Named dependency provider.
   A parameter with default ``Depends(...)`` receives the resolved dependency.
2. Context by default provider.
   A parameter with a ``Context(...)`` default receives the named context value.
3. Context by name provider.
   A parameter whose name matches a context key receives that context value.
4. Form provider.
   A parameter named ``form`` or annotated ``DForm[FormClass]`` receives the bound form during action dispatch.
5. HttpRequest provider.
   A parameter annotated ``HttpRequest`` or ``HttpRequest | None`` receives the current request.
6. URL annotation provider.
   A parameter annotated ``DUrl[T]`` reads the captured URL segment and coerces it to ``T``.
7. URL kwargs provider.
   A parameter whose name matches a captured URL segment resolves to that value.
8. Query string provider.
   A parameter annotated ``DQuery[T]`` reads ``request.GET`` by parameter name and coerces to ``T``.

The order makes the marker-driven providers narrow and decisive.
``Depends`` and ``Context`` look only at the parameter default, ``DForm``, ``DUrl``, and ``DQuery`` look only at the annotation, so they never compete with one another.
The context-by-name and URL-kwargs providers are the broad fallbacks that match on the bare parameter name.
They run after the marker providers so an explicit marker always wins over an accidental name collision.

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

``DUrl[T]`` coerces the captured string value to ``T`` when ``T`` is ``int``, ``bool``, or ``float``. For any other type, including ``str``, the value is returned as-is.
``bool`` treats ``"1"``, ``"true"``, and ``"yes"`` as ``True`` and everything else as ``False``.
For wildcard ``[[name]]`` segments the captured value is the matched path string. Annotate as ``DUrl[str]`` or leave it unannotated.

.. note::

   ``[slug:name]`` and ``[uuid:name]`` in directory names are Django URL *converter* labels that control routing and validation (see :doc:`file-router`).
   They are not Python type annotations.
   The captured value for a slug segment is a ``str``. Annotate as ``DUrl[str]``.
   The captured value for a UUID segment is a ``uuid.UUID`` object already converted by Django. Name the parameter to match the segment (e.g. ``my_id``) and let the URL kwargs provider supply it directly, or annotate ``DUrl[str]`` if you only need the string form.

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

``Context("key")``.
   Returns the value of the named context key produced by an ancestor layout or by a context function earlier in the chain.
   See :doc:`context` for the full set of ``Context`` shapes and how it relates to plain name matching.

``Depends("name")``.
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

Diagnosing a Dependency Cycle
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A named dependency may itself ask for other named dependencies through ``Depends``.
When two of them ask for each other, directly or through a longer chain, resolution cannot terminate.

.. code-block:: python
   :caption: notes/deps.py — a cycle

   from next.deps import Depends, resolver


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
Break the cycle by removing one ``Depends`` edge.
Here ``settings`` does not need ``profile`` at all, so the fix is to drop that parameter.

.. code-block:: python
   :caption: notes/deps.py — the fix

   from next.deps import Depends, resolver


   @resolver.dependency("settings")
   def settings() -> dict:
       return {"theme": "light"}


   @resolver.dependency("profile")
   def profile(settings: dict = Depends("settings")) -> dict:
       return {"theme": settings["theme"]}

When both dependencies genuinely need shared data, move that data into a third dependency and have both depend on it.

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

More recipes for diagnosing missing markers and CSRF or dispatch errors live in :doc:`/content/faq/troubleshooting`.

Avoid ``from __future__ import annotations`` in DI Modules
----------------------------------------------------------

The resolver inspects real annotations, not strings.
A ``from __future__ import annotations`` import in a ``page.py`` or ``component.py`` turns every annotation into a string and ``typing.get_origin`` returns ``None``.

Two rules.

Do not use future annotations in modules with DI parameters.
   ``page.py``, ``component.py``, and ``providers.py`` need real annotations.
   Plain Python files that only import the framework can use future annotations freely.

Keep DI types runtime importable.
   The resolver evaluates string annotations through ``typing.get_type_hints``.
   Types hidden behind ``if TYPE_CHECKING`` are not available at evaluation time.

Resolver Lifecycle
------------------

A custom provider joins the resolver by importing its module, which runs ``RegisteredParameterProvider.__init_subclass__``.
The resolver instantiates the full registry the first time it resolves a callable, then reuses that list.
Register custom providers from ``AppConfig.ready`` so they exist before that first resolution.
The ``provider_registered`` signal fires once per provider as the class enters the registry.

Common Patterns
---------------

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
   :doc:`/content/faq/troubleshooting` for concrete resolver and dispatch errors.
   :doc:`/content/howto/share-context-across-pages` for the inherited context pattern.
   :doc:`/content/internals/di-resolver` for the resolver internals.
   :doc:`/content/ref/deps` for the public API and cache contract.
