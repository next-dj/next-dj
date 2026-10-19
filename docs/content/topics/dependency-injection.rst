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
The form dispatch resolves the form-class factory, ``get_initial``, ``get_form_kwargs``, ``check_permissions``, ``has_object_permission``, ``@action`` handlers, ``on_valid``, and ``wizard.done``.
Every call site shares one provider list and one set of markers.
Custom providers and tests can import ``resolver`` from ``next.deps`` and call ``resolver.resolve_dependencies``.

Django has no counterpart to this, so everything on this page is new rather than a rename of something a Django view already does.
A Django view receives ``request`` as its first positional argument and reads everything else off it, while here a callable declares what it wants and the resolver supplies it.

Two examples first
------------------

A parameter annotated :class:`~django.http.HttpRequest` receives the request being served.

.. code-block:: python
   :caption: notes/pages/page.py

   from django.http import HttpRequest

   from next import context

   @context("greeting")
   def greeting(request: HttpRequest) -> str:
       return f"Hello from {request.path}."

The annotation is what claims the parameter, so a bare ``request`` with no annotation is filled with ``None`` instead.

A parameter annotated ``DUrl[int]`` receives the URL segment of the same name, coerced to :class:`int`.

.. code-block:: python
   :caption: notes/pages/notes/[int:note_id]/page.py

   from notes.models import Note

   from next import context
   from next.urls import DUrl

   @context("note")
   def note(note_id: DUrl[int]) -> Note:
       return Note.objects.get(pk=note_id)

Both callables run through the same pipeline, and the list below is how it decides which one fills which parameter.

Built-in providers
------------------

The framework discovers a fixed list of providers and instantiates them on first use.
Each one carries an explicit ``priority`` value, the resolver consults them from lowest to highest, and the first match wins.

1. Object provider (priority 0).
   A parameter annotated ``DObject[Model]`` receives the row the URL addresses, read through the ``visible_to`` method of the model manager, see `Model rows with DObject`_.
2. Named dependency provider (priority 10).
   A parameter with default ``Depends(...)`` receives the resolved dependency.
3. Context by default provider (priority 20).
   A parameter with a ``Context(...)`` default receives the named context value.
4. Context by name provider (priority 30).
   A parameter whose name matches a context key receives that context value.
5. Form provider (priority 40).
   A parameter named ``form`` or annotated ``DForm[FormClass]`` receives the bound form during action dispatch.
6. Cleaned data provider (priority 40).
   A parameter named ``cleaned_data`` receives the merged wizard cleaned data on a wizard ``done()`` handler.
7. HttpRequest provider (priority 50).
   A parameter annotated ``HttpRequest`` or ``HttpRequest | None`` receives the current request, and one annotated with a concrete subclass receives it only when the request is an instance of that subclass.
8. Consent provider (priority 50).
   A parameter annotated ``Consent`` receives the visitor's consent, and an undecided visitor on a page a shared cache holds, see :doc:`/content/topics/scripts/consent`.
9. URL annotation provider (priority 60).
   A parameter annotated ``DUrl[T]`` reads the captured URL segment and coerces it to ``T``.
10. URL kwargs provider (priority 70).
    A parameter whose name matches a captured URL segment resolves to that value.
    The value is coerced to the parameter annotation when one is present and is left as the captured string otherwise.
    A plain ``note_id: int`` on a ``[note_id]`` route therefore arrives already parsed, so ``DUrl`` is only needed to read a segment under a different name.
11. Query string provider (priority 80).
    A parameter annotated ``DQuery[T]`` reads ``request.GET`` by parameter name and coerces to ``T``.

The object provider comes first so that nothing reaches a ``DObject`` parameter around ``visible_to``, neither a ``Depends`` default nor a context value or URL kwarg of the same name.
The context by name and URL kwargs providers refuse a ``DObject`` annotation as well, and a ``DObject`` parameter that no provider loads raises :exc:`~django.core.exceptions.ImproperlyConfigured` instead of receiving the raw kwarg.
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

      # pages/shop/[category]/page.py
      @context("category", inherit_context=True)
      def category() -> str:
          return "books"

      # pages/shop/[category]/products/page.py
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
The annotation is a hint and not a gate, because the value reached the callable through whatever the route already accepted.
A typed directory such as ``[int:id]`` is what refuses a malformed segment, with a 404 before any callable runs, see :ref:`Converter segments <topics-di-converter-segments>`.

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
A query string passes no converter, so nothing upstream can reject a bad value and the check belongs in the callable, see :doc:`/content/security/di-and-untrusted-input`.

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

Named dependencies and custom providers live in the ``deps.py`` of the application, see :doc:`project-layout`.
At startup the ``next`` application imports the ``deps.py`` of every installed application, ahead of any ``forms.py`` or ``component.py``, so the decorator runs before the first request with no ``AppConfig.ready`` code, see :doc:`/content/ref/settings`.
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
The callable form ``Depends(some_factory)`` runs under the same guard, with the dotted ``module.qualname`` of the factory standing in for the registered name on the stack, so two factories that ask for each other raise the same error rather than exhausting the stack.

.. code-block:: text
   :caption: the error

   next.deps.errors.DependencyCycleError: Circular dependency: profile -> settings -> profile

The chain in the message is the resolution path that closed the loop, read left to right.
The traceback lists the fully qualified path ``next.deps.errors.DependencyCycleError`` because that is where the exception class is defined.
Import the exception from the public ``next.deps`` namespace with ``from next.deps import DependencyCycleError``.
The deeper ``next.deps.errors`` path is an implementation detail and is not part of the supported import surface.
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

.. _topics-di-dobject:

Model rows with DObject
-----------------------

A parameter annotated ``DObject[Model]`` receives the row whose primary key the URL kwarg of the same name carries.
The lookup always goes through ``Model._default_manager.visible_to(scope)``, so the parameter only ever holds a row the current scope may see.
The model defines that method on its default manager, usually as a custom ``QuerySet`` method, see :doc:`application-layout`.

.. code-block:: python
   :caption: notes/models.py

   from django.conf import settings
   from django.db import models

   class NoteQuerySet(models.QuerySet):
       def visible_to(self, scope):
           if not scope.is_authenticated:
               return self.none()
           return self.filter(owner=scope)

   class Note(models.Model):
       owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
       title = models.CharField(max_length=200)

       objects = NoteQuerySet.as_manager()

.. code-block:: python
   :caption: notes/pages/notes/[int:note]/page.py

   from notes.models import Note

   from next import context
   from next.deps import DObject

   @context("note")
   def note(note: DObject[Note]) -> Note:
       return note

A row the scope does not see, a primary key that matches no row, and a value the primary key field cannot parse all answer 404, never 403, so a response never tells a hidden row from a missing one.
A missing URL kwarg is a wiring mistake rather than a missing row, and on a page it raises ``UnboundObjectError``, a subclass of :exc:`~django.core.exceptions.ImproperlyConfigured` that names the kwargs the route did capture.
The lookup takes the first matching row, so a ``visible_to`` that joins a many-valued relation and repeats a row needs no ``distinct()``.
A row loads once per render, so two callables that ask for the same row share one query.

The segment takes the name of the model, ``[int:note]`` for ``DObject[Note]`` and ``[int:wallet]`` for ``DObject[Wallet]``.
A ``DObject`` parameter reads the URL kwarg of its own name, and ``obj=DObject[Note]`` in an access declaration reads the kwarg named after the ``model_name`` of the model, so a segment named after the model serves both with one row and one query.

``visible_to`` answers one question, which rows the scope may see, and leaves the shape of the row to the default manager.
The manager decides the shape through its ``get_queryset``, which ``visible_to`` filters, the way :meth:`~django.views.generic.detail.SingleObjectMixin.get_queryset` shapes the row of a ``DetailView``.
A heavy column is deferred there with :meth:`~django.db.models.query.QuerySet.defer`, and a related row the page always shows is joined there with :meth:`~django.db.models.query.QuerySet.select_related`, never inside ``visible_to``.

.. code-block:: python
   :caption: notes/models.py, a manager that shapes the row

   class NoteManager(models.Manager.from_queryset(NoteQuerySet)):
       def get_queryset(self):
           return super().get_queryset().defer("body").select_related("owner")

   class Note(models.Model):
       ...

       objects = NoteManager()

The marker refuses a shape no lookup could serve.
A model class that is abstract or not a model at all raises ``TypeError`` where the annotation is written.
A bare ``DObject`` and a ``DObject`` inside a union such as ``DObject[Note] | None`` raise ``TypeError`` when the resolver first resolves the callable, since a missing row is a 404 rather than ``None``.

The scope dependency
~~~~~~~~~~~~~~~~~~~~

The framework does not decide what a scope is and ships no default for it.
The project registers the named dependency ``scope`` once, in the ``deps.py`` of one of its applications, and ``visible_to`` receives exactly the value it returns.
A site whose rows belong to users returns the user.

.. code-block:: python
   :caption: notes/deps.py

   from django.http import HttpRequest

   from next.deps import resolver

   @resolver.dependency("scope")
   def scope(request: HttpRequest):
       return request.user

A site whose rows belong to tenants returns the tenant the request acts under, and ``visible_to`` filters on it.

.. code-block:: python
   :caption: notes/deps.py, the tenant variant

   from django.http import HttpRequest
   from notes.models import Tenant

   from next.deps import resolver

   @resolver.dependency("scope")
   def scope(request: HttpRequest) -> Tenant | None:
       return getattr(request, "tenant", None)

:doc:`/content/howto/scope-requests-per-tenant` covers where ``request.tenant`` comes from and why the client must not choose it.
The scope is a named dependency like any other, so it resolves once per render, and a callable that lists rows asks for the same value through ``Depends("scope")``.
A project that declares a ``DObject`` and registers no ``scope`` fails ``manage.py check`` with ``next.E157``.

Nested rows
~~~~~~~~~~~

``DObject[Model, "parent"]`` also requires the row to belong to a parent the render loaded earlier.
``parent`` names a context value set before the parameter resolves, by an ancestor ``@context("parent", inherit_context=True)`` or by an earlier ``@context("parent")`` in the same ``page.py``, see :ref:`topics-layouts-inherited-context` for the run order.
The lookup filters the rows by the model field of that same name, so a comment URL under another post answers 404 even when both rows are visible.

.. code-block:: python
   :caption: blog/pages/posts/[int:post]/page.py

   from blog.models import Post

   from next import context
   from next.deps import DObject

   @context("post", inherit_context=True)
   def post(post: DObject[Post]) -> Post:
       return post

.. code-block:: python
   :caption: blog/pages/posts/[int:post]/comments/[int:comment]/page.py

   from blog.models import Comment

   from next import context
   from next.deps import DObject

   @context("comment")
   def comment(comment: DObject[Comment, "post"]) -> Comment:
       return comment

Each level loads its own row through ``visible_to``, so the chain never reaches the child of a parent the scope could not see.
A parent value of ``None`` answers 404 without a query, and a parent name the context does not hold raises ``UnboundObjectError``.
A linter that reads the string as a forward reference, such as ruff with ``F821``, accepts ``DObject[Comment, Literal["post"]]``, which the marker treats the same way.

Where DObject applies
~~~~~~~~~~~~~~~~~~~~~

Page context callables, ``@page.metadata`` callables, form handlers and form hooks, and named dependencies resolve a ``DObject``.
A form handler or hook reads the URL kwargs of the page the submission came from, but it resolves with no page context, so a ``DObject`` with a parent there is ``next.E155``.
A submission whose origin is missing or names a page without the kwarg comes from the client rather than from the wiring, so the dispatcher answers it with 400 Bad Request and logs a warning instead of raising.
A ``@component.context`` callable receives no URL kwargs at all, so any ``DObject`` there is ``next.E156``, and the component reads the row from the page context by name instead.
An access declaration of a ``page.py`` names its row as ``permission_required(perm, obj=DObject[Model])``, which loads by the URL kwarg named after the ``model_name`` of the model and shares the cached row with a ``DObject`` parameter of the same model and primary key, see :ref:`topics-access`.

``manage.py check`` reads the annotations without running a query.
``next.E153`` reports a model whose default manager has no ``visible_to``, ``next.W126`` a page ``DObject`` whose full route hands it no kwarg of its name, ``next.E162`` the same for an ``obj=DObject[Model]`` of an access declaration, and ``next.E154`` a parent no earlier context callable provides.
``next.E158`` reports a ``DObject`` written in a module whose type hints do not resolve, such as one under ``from __future__ import annotations`` that imports the model under ``TYPE_CHECKING``, where every resolve of the parameter raises ``TypeError`` rather than passing the raw kwarg.
The full route counts the kwargs the includes around the tree capture, so a ``wallet`` that ``path("wallets/<int:wallet>/", pages("wallets"))`` captures satisfies a ``DObject`` named ``wallet``.
``next.W126`` stays a warning because the walk of the URLconf cannot read an include hidden behind a lazy pattern sequence.
See :doc:`/content/ref/system-checks` for the full conditions.

Writing a custom provider
-------------------------

For data sources the built-in providers do not cover, register a parameter provider.
A model row the URL addresses is already covered by ``DObject``, so a custom provider serves other sources.
The base classes are ``RegisteredParameterProvider`` and ``DDependencyBase``.

.. code-block:: python
   :caption: notes/deps.py, the header marker

   from typing import get_args, get_origin

   from next.deps import DDependencyBase, RegisteredParameterProvider

   class DHeader[T](DDependencyBase[T]):
       __slots__ = ()

   class HeaderProvider(RegisteredParameterProvider):
       def can_handle(self, param, _context) -> bool:
           return get_origin(param.annotation) is DHeader

       def static_can_handle(self, param) -> bool | None:
           return get_origin(param.annotation) is DHeader

       def resolve(self, param, context):
           (target,) = get_args(param.annotation)
           request = context.request
           raw = None if request is None else request.headers.get(param.name.replace("_", "-"))
           if raw is None:
               return None
           try:
               return target(raw)
           except ValueError:
               return None

The parameter name names the header, so ``x_request_id: DHeader[str]`` reads ``X-Request-Id`` and ``DHeader[int]`` coerces the value, answering ``None`` for an absent or malformed header.
A header reaches a page render and a form action handler alike, so the same parameter works at either call site.
A header is client input, so the callable treats the value as untrusted, see :doc:`/content/security/di-and-untrusted-input`.

Use the new marker.

.. code-block:: python
   :caption: notes/pages/notes/page.py

   from notes.deps import DHeader

   from next import context

   @context("request_id")
   def request_id(x_request_id: DHeader[str]) -> str | None:
       return x_request_id

Two rules apply to the marker class.

Python 3.12 generic syntax.
   ``class DHeader[T](DDependencyBase[T])`` makes ``DHeader[str]`` a parameterised generic whose ``get_origin`` is ``DHeader``.
   A non generic ``class DHeader(DDependencyBase[str])`` does not behave like a parameter marker.

Declare the provider in ``deps.py``.
   A provider class registers itself when its module is imported, and the ``deps.py`` of every installed application is imported at startup.
   A provider declared in another module needs an import from ``deps.py`` or from ``AppConfig.ready``.
   A class that registers later still joins the provider list by priority, and the plans compiled without it are recompiled.

A custom provider that does not declare ``priority`` inherits the ``RegisteredParameterProvider`` default of ``100``.
The eleven built-in providers that fill parameters occupy the range ``0`` (object) through ``80`` (query string), so the default keeps a custom provider after each of them, and only the refusal of an unloaded ``DObject`` runs later, at ``1000``.
``FormProvider`` and ``CleanedDataProvider`` share priority ``40``.
Set ``priority`` on the subclass when the new provider has to claim a parameter the built-ins would otherwise match, for example a value below ``60`` for an annotation that should outrank ``DUrl``.

Static verdicts
~~~~~~~~~~~~~~~

The resolver asks each provider ``static_can_handle(param)`` once per callable and compiles the answers into that callable's injection plan.
``True`` claims the parameter in every context and ends the walk, ``False`` rules the provider out for that parameter for good, and ``None`` keeps ``can_handle`` running on every resolve.
``HeaderProvider`` above answers from the annotation alone, so its marker parameters are settled at compile time and no ``can_handle`` runs per request.

The parameter reaches the hook with its type hint already resolved through ``typing.get_type_hints``, extras included, so a hook compares real types even where the annotation was written as a string and can match on the metadata of an ``Annotated[...]`` hint.
A hint the framework cannot evaluate falls back to the raw annotation as written, and the plan built from it is not cached, so an annotation that only a later import can resolve takes effect on the next resolve.
A provider that implements the ``ParameterProvider`` protocol directly declares the method itself, while a ``RegisteredParameterProvider`` subclass inherits the ``None`` default.
The method is not optional.
A provider handed to ``add_provider``, ``prepend_provider``, ``register``, or the resolver constructor without a callable ``static_can_handle`` is refused with a ``TypeError`` naming the class, rather than failing later from inside the plan compiler.

A provider that answers ``True`` may go one step further and implement ``compile_resolve(param)``, which returns the call the plan makes with the resolution context alone.
That hook is spelled by ``CompilingParameterProvider``, a second protocol extending ``ParameterProvider``, so a provider that stops at the mandatory three methods still satisfies the first one.
``HeaderProvider`` above would read ``get_args(param.annotation)`` and the header name there, once per plan, and return a closure that is left with the header read.
Returning ``None`` from the hook keeps the parameter on the plain ``resolve`` path, and so does leaving the hook undefined.

Resolution cache
----------------

Each resolution pass wraps a per-render dependency cache in a fresh ``DependencyCache``.
The wrapper is new per call, but one backing store spans the whole page render, the callable ``cache`` of the ``page.py``, its ``render()`` function, every ``@context`` callable, and every ``@page.metadata`` callable.
A ``Depends("name")`` provider therefore runs once per render, for the first of these callables that asks for it, and every later one reads the value it returned.
The cache memoises ``Depends("name")`` callables, keyed by the registered name, and the rows ``DObject`` parameters load.

A second context function in the same page render that asks for the same ``Depends("name")`` dependency receives the memoised value, not a fresh call.
To share one value across several context functions in the same render, register it with ``resolver.dependency("active_tenant")`` and ask for it through ``Depends("active_tenant")`` in each callable that needs it.
The first callable to ask pays the resolution, and every later callable in the same pass reads the value the cache already holds.

That store covers the callables of the ``page.py``, and a component render is a pass of its own.
On an ordinary GET every ``@component.context`` callable of one component resolves against a cache built for that component, so a ``Depends("name")`` two components both ask for is resolved once per component rather than once per page.
A ``render`` function in a ``component.py`` is further apart still and always gets a fresh cache, on a GET and inside a form dispatch alike, so a value it shares with the page around it is computed again for the call.
Keep a dependency cheap when several components ask for it, or have it read a store of its own scoped to the request.

The cache lives for one form dispatch.
Every stage of that POST, from ``get_initial`` through the validation-failure re-render, shares it.
Each component instance of the re-rendered page reads a copy of it, so a value resolved earlier in the dispatch is reused while a value one instance resolves from its own props stays with that instance.
``FormActionDispatch`` attaches its dependency cache to the request, and ``render_dep_cache`` reads it back.
Outside a form dispatch the function answers a fresh empty dict the request never carries, so a caller never handles a missing cache.

.. code-block:: python
   :caption: reading the cache

   from django.http import HttpRequest

   from next.deps import render_dep_cache

   def render(request: HttpRequest) -> str:
       cache = render_dep_cache(request)
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
The key selects between the two shipped resolvers, ``next.deps.DependencyResolver`` and ``next.deps.linear.LinearDependencyResolver``, and it equally accepts a ``DependencyResolver`` subclass of your own.
The framework reads it at startup and on every settings reload, never per request.
See :doc:`/content/internals/di-resolver` for how a plan is compiled, cached, and invalidated, :doc:`/content/ref/settings` for the setting, and :doc:`extending` for where it sits among the extension mechanisms.

Future annotations and DI
-------------------------

A module the resolver inspects never carries ``from __future__ import annotations``.
The resolver resolves the annotations of each callable once through :func:`typing.get_type_hints`, so a string annotation carries only as far as its names are importable at runtime.
A hint that fails to evaluate leaves the raw string in the injection plan, and a marker such as ``DUrl[int]`` stops matching, because ``typing.get_origin`` returns ``None`` for a string.
Real annotations remove that failure mode outright, which is why a ``page.py`` or a ``component.py`` never carries the future import.

Two rules.

Do not use future annotations in modules with DI parameters.
   ``page.py``, ``component.py``, action handlers, and any ``get_initial`` or ``get_form_kwargs`` need real annotations.
   Plain Python files that only import the framework can use future annotations freely.
   A provider class is never introspected by :func:`typing.get_type_hints`, but a callable registered through ``resolver.dependency`` is, so a ``deps.py`` that holds named dependencies keeps real annotations as well.

Keep DI types runtime importable.
   A hint the resolver cannot evaluate never becomes the type a provider matches on.
   Types hidden behind ``if TYPE_CHECKING``, and types defined inside a function body, are invisible to that evaluation.
   Keep DI-touching annotations on classes that import at module top level.

See also
--------

.. seealso::

   :doc:`context` for the ``@context`` decorator and inheritance flow.
   :doc:`file-router` for ``DUrl`` and captured URL parameters.
   :doc:`testing` for the ``override_dependency`` and ``override_provider`` test helpers.
   :doc:`/content/faq/troubleshooting` for concrete resolver and dispatch errors.
   :doc:`/content/howto/share-context-across-pages` for the inherited context pattern.
   :doc:`/content/security/di-and-untrusted-input` for treating an injected URL or query value as untrusted.
   :doc:`/content/internals/di-resolver` for the resolver internals.
   :doc:`/content/ref/deps` for the public API and cache contract.
