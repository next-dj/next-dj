Dependency Injection
====================

Dependency injection in next.dj works at the **project level**: the same resolver
is used for page context functions, page ``render``, **component** ``component.py``
helpers (``@context`` and composite ``render``), and form actions across the app.
You declare the parameters you need. The framework injects values from the request
context — no ``*args`` or ``**kwargs`` required.

Where it is used
----------------

- **Context functions** — ``@context`` and ``@context("key")`` callables receive
  only the parameters they declare (e.g. ``request: HttpRequest``, ``id: int``,
  ``layout_theme: dict = Depends("layout_theme")``, ``custom_variable: str = Context("custom_variable")``).
  See :doc:`context` and the **Context vs Depends**
  section below.
- **Component context and composite** ``render`` — In ``component.py`` use
  ``from next.components import context`` (not ``next.pages``). Functions registered
  with ``@context`` / ``@context("key")`` and the optional ``render`` callable use
  the same providers and markers (``Depends``, ``Context``, URL kwargs, ``request``,
  etc.). See :doc:`components`.
- **Custom render** — A page's ``render`` function (when there is no template)
  is called with resolved dependencies: ``def render(request: HttpRequest, post_id: int)``.
- **Form actions** — ``get_initial`` and action handlers receive only declared
  parameters (e.g. ``id: int`` from the URL, ``form: MyForm``). Handlers can also
  use ``Depends("name")`` or ``Context("key")``. See :doc:`forms`.
- **Pages and layouts** — In child pages under a layout, context functions can
  inject layout-level global dependencies (``Depends("name")``) and
  parent context variables (``Context("key")``). See :doc:`pages-and-templates`
  for a working example (``examples/feature-flags/flags/panels/admin/`` in
  the source tree uses a nested layout with inherited context).

How it works
------------

The global **resolver** (``next.deps.resolver``) is a ``DependencyResolver`` instance
that uses **providers** to supply values. For each parameter of your function
(excluding ``self``/``cls``), the resolver asks the providers in order. The first
one that can supply the value does so. Parameters that no provider handles get
``None`` (or keep their default).

Context vs Depends
------------------

Two markers let you **explicitly** request injected values by name, using
**default parameter values** (FastAPI-style):

- **``Context("key")``** — Injects the value of a **context variable** from the
  current request: inherited layout context (e.g. ``@context("key", inherit_context=True)``)
  or context already produced by earlier context functions on the same page. Use this
  when a parent layout or a previous ``@context("key")`` has set that key.

- **``Depends("name")``** — Injects the result of a **registered dependency**
  (a callable registered with ``@resolver.dependency("name")`` or
  ``resolver.register_dependency("name", callable)``). Use this for app-wide
  dependencies (e.g. theme config, current user service) that are not tied to
  the page/layout hierarchy.

**Summary:**

+------------------+--------------------------------+------------------------------------------+
| Marker           | Source                         | Use when                                 |
+==================+================================+==========================================+
| ``Context("x")`` | ``context_data["x"]``          | Value from parent layout or same-page    |
|                  | (inherited + current page)     | context (key name)                       |
+------------------+--------------------------------+------------------------------------------+
| ``Depends("name")`` | Registered callable by name | App-level dependency (theme, user, etc.) |
+------------------+--------------------------------+------------------------------------------+

Migration from DContext/DGlobalContext
-----------------------------------------

If you used the old annotation-based markers, migrate as follows:

- ``foo: DGlobalContext["name"]`` -> ``foo = Depends("name")``
- ``bar: DContext["key"]`` -> ``bar = Context("key")`` (or ``Context()`` when the key equals the parameter name)

**Example: layout-level global + parent context:**

.. code-block:: python

   # Layout page: register a global dependency
   from next.deps import resolver
   from next.pages import context

   @resolver.dependency("layout_theme")
   def get_layout_theme():
       return {"name": "Bootstrap", "version": "5.0"}

   @context("custom_variable", inherit_context=True)
   def custom_variable_context_with_inherit(request):
       return "Hello from layout!"

   # Child page (e.g. guides/page.py): inject both by name
   from next.deps import Depends
   from next.pages import Context, context

   @context("layout_theme_data")
   def guides_theme(layout_theme: dict[str, str] | None = Depends("layout_theme")):
       return layout_theme

   @context("parent_context_data")
   def guides_parent(custom_variable: str | None = Context("custom_variable")):
       return custom_variable

You can also rely on **parameter name**: if a parameter has the same name as a
context key (and no explicit ``Context(...)`` marker), the built-in
``ContextByNameProvider`` injects that key's value. Using ``Context("key")`` or
``Context()`` makes the intent explicit and works when the key is not the same
as the param name.

Built-in providers
------------------

- **HttpRequest** — Parameter annotated with ``HttpRequest`` (or subclass) receives
  the current request.
- **URL path parameters** — Parameter name matching the path segment (e.g. ``id``
  for ``[int:id]``), or annotation ``DUrl[int]`` / ``DUrl["param"]``. Type coercion
  is applied. For catch-all (``[[args]]``), use ``list[str]``.
- **Query string parameters**. A parameter annotated with ``DQuery[T]``
  reads ``request.GET`` by parameter name. Use ``DQuery[str]``,
  ``DQuery[int]``, ``DQuery[bool]``, or ``DQuery[float]`` for scalar
  values. Use ``DQuery[list[T]]`` for repeated keys such as
  ``?brand=a&brand=b``. The list form also accepts the qs-style bracket
  suffix ``?brand[]=a&brand[]=b`` emitted by axios and other front-end
  clients. The third accepted format is the comma-delimited form
  ``?brand=a,b`` produced by ``qs.stringify`` with the comma array
  format. Empty segments around commas are dropped. The provider
  returns the parameter default when the key is absent. When no
  default is given the provider returns ``None``. Use ``DUrl`` for URL
  path segments and ``DQuery`` for query string parameters.
- **Form** — Parameter named ``form`` or annotated with ``DForm[FormClass]``
  receives the form instance (in form actions).
- **Context by key** — ``Context("key")`` or ``Context()``: value from current
  context (inherited + same page). Parameter name matching a context key also
  receives that value (``ContextByNameProvider``).
- **Global dependency** — ``Depends("name")``: result of the callable registered
  under that name.

Example: context function with URL parameter

.. code-block:: python

   # pages/catalog/[int:id]/page.py
   @context("product")
   def get_product(request: HttpRequest, id: int) -> Product:  # noqa: A002
       return get_object_or_404(Product, pk=id)

No need for ``**kwargs`` or ``request.resolver_match.kwargs`` — declare ``id: int``
and it is injected.

Example: page reading the query string
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: python

   # storefront/catalog/page.py
   from next.pages import context
   from next.urls import DQuery

   @context("results")
   def search(
       q: DQuery[str] = "",
       brand: DQuery[list[str]] = (),
       page: DQuery[int] = 1,
   ):
       return run_search(q=q, brands=brand, page=page)

Custom dependency providers
---------------------------

You can add providers that supply extra parameters (e.g. ``user`` from
``request.user``) by implementing the ``ParameterProvider`` protocol from
``next.deps`` with ``can_handle(param, context)`` and
``resolve(param, context)`` methods. Register with ``@resolver.register`` or
``resolver.add_provider(instance)``. The ``context`` parameter is a
``ResolutionContext`` dataclass containing ``request``, ``form``, ``url_kwargs``,
``context_data``, ``cache``, and ``stack`` for cycle detection.
See :ref:`dependency-injection-api` for the full API.

Parameters that no provider handles receive ``None`` (or keep their default).

Registered DI functions (Depends)
---------------------------------

Register **dependency callables** by name with ``@resolver.dependency("name")``
and inject them anywhere using **``Depends("name")``** (or ``Depends()`` as a
shorthand for the parameter name).

**Register a callable:**

.. code-block:: python

   from next.deps import resolver

   @resolver.dependency("current_user")
   def get_current_user(request: HttpRequest):
       return request.user

   # or: resolver.register_dependency("current_user", get_current_user)

**Use it in context functions, form handlers, or custom render:** Use
``Depends("current_user")`` for explicit injection, or ``Depends()`` when the
parameter name is ``current_user``:

.. code-block:: python

   from next.deps import Depends
   from next.pages import context

   @context("profile")
   def profile(current_user = Depends("current_user")):
       return {"user": current_user}

- **Caching:** Within a single request, the result of each dependency callable
  is cached by name.
- **Cycles:** Circular dependencies raise :exc:`next.deps.DependencyCycleError`.
- **Order:** Built-in providers run first. Avoid dependency names that clash
  with URL parameter names.

Replacing the resolver
----------------------

By default, context and form resolution use the global ``resolver`` from
``next.deps``. To use a custom resolver, create a ``DependencyResolver``
instance (with optional providers) and pass it to ``PageContextRegistry(resolver=...)``.
The ``Page`` instance uses the global resolver unless wired with a context
manager that was given a custom resolver.

API reference
-------------

For the full API of the ``next.deps`` module (resolver, Deps, providers, protocol),
see :ref:`dependency-injection-api`.

Extension points
----------------

The dependency-injection layer exposes three pluggable surfaces.

* ``next.deps.providers.ParameterProvider`` is the minimal protocol consumed by ``DependencyResolver``. Implement it for ad-hoc providers passed directly to a custom resolver.
* ``next.deps.providers.RegisteredParameterProvider`` is the ABC used by built-in providers. Every concrete subclass auto-registers through ``__init_subclass__``, so importing the module that defines the subclass is enough to wire it into the resolver. ``next.urls.markers.QueryParamProvider`` is shipped through this mechanism. Its module loads with ``next.urls`` so ``DQuery[T]`` resolution is wired without explicit registration.
* ``DependencyResolver.register_dependency`` binds a callable to a name so ``Depends("name")`` resolves it.

Register a custom provider by importing the module that defines it.

.. code-block:: python

   from myapp.custom_provider import LayoutStampProvider  # noqa: F401 - import side-effect

The signal emitted by :mod:`next.deps.signals` lets external code observe wiring events.

* ``provider_registered`` fires when a ``RegisteredParameterProvider`` subclass joins the auto-registry.

Working providers live in ``examples/feature-flags/flags/providers.py``
(``FlagProvider`` + ``DFlag[T]``) and
``examples/shortener/shortener/providers.py`` (``LinkProvider`` +
``DLink[T]``). Both use the ``DDependencyBase`` marker and register on
``AppConfig.ready``. See :doc:`extending` for the overall extension model.

Next
----

:doc:`autoreload` — How ``runserver`` reloads (Python entrypoints, route set, and why ``.djx`` is not glob-watched).
