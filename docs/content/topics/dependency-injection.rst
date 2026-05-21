.. _topics-dependency-injection:

Dependency Injection
====================

The dependency resolver inspects the signature of every callable that the framework invokes and fills each parameter from a registered provider.
A page context function asks for a query string value, a layout asks for the current request, an action handler asks for the form and a URL parameter.
The resolver answers all of those calls through the same pipeline.

.. contents::
   :local:
   :depth: 2

Overview
--------

The resolver runs at four call sites: page context functions, the page render function, component context functions, and ``@action`` form handlers.
Every call site shares one provider list and one set of markers.
Custom providers and tests can import ``resolver`` from ``next.deps`` and call ``resolver.resolve_dependencies``.

Built In Providers
------------------

The framework registers a fixed list of providers at startup.
Each one carries an explicit ``priority`` value, the resolver consults them from lowest to highest, and the first match wins.

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
``Depends`` and ``Context`` look only at the parameter default.
``DUrl`` and ``DQuery`` look only at the annotation.
The form provider is broader, it matches the parameter name ``form``, the marker ``DForm[FormClass]``, and any plain class annotation whose type the bound form is an instance of.
The context-by-name and URL-kwargs providers are the fallbacks that match on the bare parameter name.
They run after the marker providers so an explicit marker always wins over an accidental name collision.

DUrl
~~~~

The URL path provider coerces the captured segment to the requested type.

.. code-block:: python
   :caption: notes/pages/notes/[int:note_id]/page.py

   from notes.models import Note
   from next.pages import context
   from next.urls import DUrl

   @context("note")
   def note(note_id: DUrl[int]) -> Note:
       return Note.objects.get(pk=note_id)

In the simplest form ``DUrl[T]`` matches the captured segment whose name
equals the parameter name, then coerces the captured value to ``T``.
``T`` may be ``str``, ``int``, ``bool``, ``float``, ``UUID``, ``Decimal``, ``date``, or ``datetime``.
A value that already satisfies ``T`` passes through untouched, so a Django converter that pre-coerced the segment, such as ``[uuid:id]`` producing a :class:`~uuid.UUID`, reaches the handler in that shape.
A failed parse falls back to the raw captured value rather than raising.
``bool`` treats ``"1"``, ``"true"``, and ``"yes"`` as ``True`` and everything else as ``False``.
``date`` and ``datetime`` parse the ISO 8601 forms accepted by :meth:`date.fromisoformat <datetime.date.fromisoformat>` and :meth:`datetime.fromisoformat <datetime.datetime.fromisoformat>`.
For wildcard ``[[name]]`` segments the captured value is the matched path string. Annotate as ``DUrl[str]`` or leave it unannotated.

The marker has three forms.

``DUrl[T]``.
   Reads the captured segment that shares the parameter name and coerces
   it to ``T``. Use it when the parameter name already matches the
   directory segment.

``DUrl["segment"]``.
   Reads the named captured segment and returns it in string form.
   Use it when the parameter name differs from the segment name and no
   type coercion is needed.

``DUrl["segment", T]``.
   Reads the named captured segment and coerces it to ``T``. Use it when
   the parameter name differs from the segment name, for example
   ``note_id: DUrl["id", int]`` for an ``[id]`` directory.

The string in ``DUrl["segment"]`` and ``DUrl["segment", T]`` is the URL kwarg key the resolver looks up, not the Django converter label.
Hyphens in directory names are normalised to underscores in the kwarg, so a ``[my-id]`` directory is read as ``DUrl["my_id"]``.

.. note::

   A ``DUrl[T]`` annotation is not the same thing as a Django URL converter label.
   See :ref:`Converter Segments <topics-di-converter-segments>` below.

.. _topics-di-converter-segments:

Converter Segments
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
   from next.pages import context
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

Context Markers
---------------

Two markers fill parameters from distinct data sources.
``Context`` reads from the per-render context dictionary.
``Depends`` invokes a callable registered in the resolver's process-wide dependency map.

``Context("key")``.
   Returns the value of the named context key produced by an ancestor layout or by a context function earlier in the chain.
   See :doc:`context` for the full set of ``Context`` shapes and how it relates to plain name matching.

``Depends("name")``.
   Returns the result of a callable registered through the ``resolver.dependency`` decorator.
   ``Depends`` also accepts a callable factory, a constant value, and a bare ``Depends()`` that falls back to the parameter name.
   See :doc:`/content/internals/di-resolver` for the four forms.

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

Import the module that defines the dependency from ``AppConfig.ready`` so the decorator runs before the first request.
The registered callable can take any provider-resolved parameters because it is itself dispatched through the resolver.

Diagnosing a Dependency Cycle
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A named dependency may itself ask for other named dependencies through ``Depends``.
When two of them ask for each other, directly or through a longer chain, resolution cannot terminate.

.. code-block:: python
   :caption: notes/deps.py

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
Catch it with ``from next.deps import DependencyCycleError``. The path ``next.deps.cache`` is an implementation detail.
Break the cycle by removing one ``Depends`` edge.
Here ``settings`` does not need ``profile`` at all, so the fix is to drop that parameter.

.. code-block:: python
   :caption: notes/deps.py

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
           pk = context.url_kwargs.get("id")
           if pk is None and context.request is not None:
               pk = context.request.POST.get("note_id")
           try:
               return model_cls.objects.get(pk=pk)
           except model_cls.DoesNotExist as exc:
               raise Http404 from exc

One marker can serve both a page render and a form action handler.
A page render captures the identifier in the URL, while a form action carries
it in the POST body. The ``resolve`` method above checks both sources, so the
same ``DNote[Note]`` parameter works in either call site.
The form template carries the identifier in a hidden input so the POST branch can read it.
See ``examples/kanban`` for a marker that serves both call sites.

Use the new marker.

.. code-block:: python
   :caption: notes/pages/notes/[id]/page.py

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

A custom provider that does not declare ``priority`` inherits the ``RegisteredParameterProvider`` default of ``100``.
The built-in providers occupy the range ``10`` (named dependency) through ``80`` (query string), so the default keeps a custom provider after every built-in.
Set ``priority`` on the subclass when the new provider has to claim a parameter the built-ins would otherwise match, for example a value below ``50`` for an annotation that should outrank ``DUrl``.

Resolution Cache
----------------

The resolver creates a fresh ``DependencyCache`` for each resolution pass.
The cache memoises ``Depends("name")`` callables only, keyed by the registered name.
Parameter providers run once per parameter per resolution pass and their results are not stored, so a second context function that asks for the same ``DQuery`` or ``DUrl`` parameter triggers another provider call.

A second context function in the same page render that asks for the same ``Depends("name")`` dependency receives the memoised value, not a fresh call.

The same cache is shared between the initial render of a form page and the re-render on validation failure.
``FormActionDispatch`` attaches its dependency cache to the request, and ``get_request_dep_cache`` reads it back.
The function returns ``None`` outside a form dispatch, so callers handle the missing case.

.. code-block:: python
   :caption: reading the cache

   from next.deps import get_request_dep_cache

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

The resolver builds its provider registry on first use and reuses it, so register custom providers from ``AppConfig.ready`` and see :doc:`/content/internals/di-resolver` for the full lifecycle.

A custom provider that wants to share one value across several context functions in the same render should publish it through ``Depends("name")``, because the per-resolution cache memoises named dependencies but not raw provider calls.
Register a named callable through ``resolver.dependency("active_tenant")`` and have each ``@context`` function ask for ``Depends("active_tenant")``.
See :doc:`/content/howto/share-context-across-pages` for a worked example.

See Also
--------

.. seealso::

   :doc:`context` for the ``@context`` decorator and inheritance flow.
   :doc:`file-router` for ``DUrl`` and captured URL parameters.
   :doc:`/content/faq/troubleshooting` for concrete resolver and dispatch errors.
   :doc:`/content/howto/share-context-across-pages` for the inherited context pattern.
   :doc:`/content/internals/di-resolver` for the resolver internals.
   :doc:`/content/ref/deps` for the public API and cache contract.
