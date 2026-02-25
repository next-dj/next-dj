Dependency Injection
====================

Dependency injection in next.dj works at the **project level**: the same resolver
is used for context functions, custom render, and form actions across the app.
You declare the parameters you need; the framework injects values from the request
context — no ``*args`` or ``**kwargs`` required.

Where it is used
----------------

- **Context functions** — ``@context`` and ``@context("key")`` callables receive
  only the parameters they declare (e.g. ``request: HttpRequest``, ``id: int``,
  ``layout_theme: DGlobalContext["layout_theme"]``, ``custom_variable: DContext["custom_variable"]``).
  See :doc:`/content/core-features/context-system` and the **DContext vs DGlobalContext**
  section below.
- **Custom render** — A page's ``render`` function (when there is no template)
  is called with resolved dependencies: ``def render(request: HttpRequest, post_id: int)``.
- **Form actions** — ``get_initial`` and action handlers receive only declared
  parameters (e.g. ``id: int`` from the URL, ``form: MyForm``). Handlers can also
  use ``DGlobalContext["name"]`` or ``DContext["key"]``. See :doc:`/content/core-features/forms`.
- **Pages and layouts** — In child pages under a layout, context functions can
  inject layout-level global dependencies (``DGlobalContext["name"]``) and
  parent context variables (``DContext["key"]``). See :doc:`/content/core-features/templates-layouts`
  and the ``examples/layouts/`` example in the source tree.

How it works
------------

The global **resolver** (``next.deps.resolver``) is a ``DependencyResolver`` instance
that uses **providers** to supply values. For each parameter of your function
(excluding ``self``/``cls``), the resolver asks the providers in order; the first
one that can supply the value does so. Parameters that no provider handles get
``None`` (or keep their default).

DContext vs DGlobalContext
--------------------------

Two markers let you **explicitly** request injected values by name:

- **``DContext["key"]``** — Injects the value of a **context variable** from the
  current request: inherited layout context (e.g. ``@context("key", inherit_context=True)``)
  or context already produced by earlier context functions on the same page. Use this
  when a parent layout or a previous ``@context("key")`` has set that key.

- **``DGlobalContext["name"]``** — Injects the result of a **registered dependency**
  (a callable registered with ``@resolver.dependency("name")`` or
  ``resolver.register_dependency("name", callable)``). Use this for app-wide
  dependencies (e.g. theme config, current user service) that are not tied to
  the page/layout hierarchy.

**Summary:**

+------------------+--------------------------------+------------------------------------------+
| Marker           | Source                         | Use when                                 |
+==================+================================+==========================================+
| ``DContext["x"]``| ``context_data["x"]``          | Value from parent layout or same-page    |
|                  | (inherited + current page)     | context (key name)                       |
+------------------+--------------------------------+------------------------------------------+
| ``DGlobalContext | Registered callable by name   | App-level dependency (theme, user, etc.) |
| ["name"]``       |                                |                                          |
+------------------+--------------------------------+------------------------------------------+

**Example: layout-level global + parent context (see ``examples/layouts/``):**

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
   from next.pages import DContext, DGlobalContext, context

   @context("layout_theme_data")
   def guides_theme(layout_theme: DGlobalContext["layout_theme"]):
       return layout_theme

   @context("parent_context_data")
   def guides_parent(custom_variable: DContext["custom_variable"]):
       return custom_variable

You can also rely on **parameter name**: if a parameter has the same name as a
context key (and no annotation like ``DContext["key"]``), the built-in
``ContextByNameProvider`` injects that key's value. Using ``DContext["key"]``
makes the intent explicit and works when the key is not the same as the param name.

Built-in providers
------------------

- **HttpRequest** — Parameter annotated with ``HttpRequest`` (or subclass) receives
  the current request.
- **URL path parameters** — Parameter name matching the path segment (e.g. ``id``
  for ``[int:id]``), or annotation ``DUrl[int]`` / ``DUrl["param"]``; type coercion
  is applied. For catch-all (``[[args]]``), use ``list[str]``.
- **Form** — Parameter named ``form`` or annotated with ``DForm[FormClass]``
  receives the form instance (in form actions).
- **Context by key** — ``DContext["key"]``: value from current context (inherited
  + same page). Parameter name matching a context key also receives that value
  (``ContextByNameProvider``).
- **Global dependency** — ``DGlobalContext["name"]``: result of the callable
  registered under that name.

Example: context function with URL parameter

.. code-block:: python

   # pages/catalog/[int:id]/page.py
   @context("product")
   def get_product(request: HttpRequest, id: int) -> Product:  # noqa: A002
       return get_object_or_404(Product, pk=id)

No need for ``**kwargs`` or ``request.resolver_match.kwargs`` — declare ``id: int``
and it is injected.

Custom dependency providers
---------------------------

You can add providers that supply extra parameters (e.g. ``user`` from
``request.user``) by subclassing ``RegisteredParameterProvider`` from
``next.deps`` and implementing ``can_handle(param, context)`` and
``resolve(param, context)``. Register with ``@resolver.register`` or
``resolver.add_provider(instance)``. The ``context`` object is a dynamic
namespace (e.g. ``request``, ``form``, ``url_kwargs``, ``context_data``).
See :doc:`/content/api/api/deps` for the full API.

Parameters that no provider handles receive ``None`` (or keep their default).

Registered DI functions (DGlobalContext)
----------------------------------------

Register **dependency callables** by name with ``@resolver.dependency("name")``
and inject them anywhere using **``DGlobalContext["name"]``** (or a parameter
with the same name as the dependency).

**Register a callable:**

.. code-block:: python

   from next.deps import resolver

   @resolver.dependency("current_user")
   def get_current_user(request: HttpRequest):
       return request.user

   # or: resolver.register_dependency("current_user", get_current_user)

**Use it in context functions, form handlers, or custom render:** Use
``DGlobalContext["current_user"]`` for explicit injection, or a parameter named
``current_user``:

.. code-block:: python

   from next.pages import DGlobalContext, context

   @context("profile")
   def profile(current_user: DGlobalContext["current_user"]):
       return {"user": current_user}

- **Caching:** Within a single request, the result of each dependency callable
  is cached by name.
- **Cycles:** Circular dependencies raise :exc:`next.deps.DependencyCycleError`.
- **Order:** Built-in providers run first; avoid dependency names that clash
  with URL parameter names.

Replacing the resolver
----------------------

By default, context and form resolution use the global ``resolver`` from
``next.deps``. To use a custom resolver, create a ``DependencyResolver``
instance (with optional providers) and pass it to ``ContextManager(resolver=...)``.
The ``Page`` instance uses the global resolver unless wired with a context
manager that was given a custom resolver.

API reference
-------------

For the full API of the ``next.deps`` module (resolver, Deps, providers, protocol),
see :doc:`/content/api/api/deps`.
