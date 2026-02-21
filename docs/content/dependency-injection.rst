Dependency Injection
====================

Dependency injection in next.dj works at the **project level**: the same resolver
is used for context functions, custom render, and form actions across the app.
You declare the parameters you need; the framework injects values from the request
context — no ``*args`` or ``**kwargs`` required.

Where it is used
----------------

- **Context functions** — ``@context`` and ``@context("key")`` callables receive
  only the parameters they declare (e.g. ``request: HttpRequest``, ``id: int``).
- **Custom render** — A page's ``render`` function (when there is no template)
  is called with resolved dependencies: ``def render(request: HttpRequest, post_id: int)``.
- **Form actions** — ``get_initial`` and action handlers receive only declared
  parameters (e.g. ``id: int`` from the URL, ``form: MyForm``).

How it works
------------

The global **resolver** (``next.deps.resolver``) is a ``Deps`` instance built with
built-in **providers**. For each parameter of your function (excluding ``self``/``cls``),
the resolver asks the providers in order; the first one that can supply the value
does so. Parameters that no provider handles get ``None`` (or keep their default).

Built-in providers
------------------

- **HttpRequest** — Parameter annotated with ``HttpRequest`` (or subclass) receives
  the current request.
- **URL path parameters** — Parameter name matching the path segment (e.g. ``id``
  for ``[int:id]``, ``post_id`` for ``[int:post-id]``) receives the value from the
  URL; type coercion (e.g. to ``int``) is applied when the annotation is ``int``.
  For catch-all path segments (``[[args]]``), use annotation ``list[str]`` to get
  a list of path segments.
- **Form** — Parameter named ``form`` or annotated with the form class receives the
  form instance (in form actions).

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
``request.user``).

Implementing a provider
~~~~~~~~~~~~~~~~~~~~~~~

Implement the ``ParameterProvider`` protocol from ``next.deps``:

- ``can_handle(param, context) -> bool`` — Return ``True`` if this provider can
  supply a value for the parameter (e.g. by name or annotation).
- ``resolve(param, context) -> object`` — Return the value for the parameter.

Example: inject ``request.user`` for a parameter named ``user``:

.. code-block:: python

   from next.deps import RequestContext, resolver

   @resolver.register
   class UserProvider:
       def can_handle(self, param, context: RequestContext) -> bool:
           return param.name == "user" and context.request is not None

       def resolve(self, param, context: RequestContext) -> object:
           return context.request.user

Registering a provider
~~~~~~~~~~~~~~~~~~~~~~

**Option A — global resolver:** Use the ``@resolver.register`` decorator on your
provider class (as in the example above). The provider then runs together with
the built-in ones. Registered providers run after built-in providers.

**Option B — custom resolver:** Build your own resolver and pass it where the
context manager is created:

.. code-block:: python

   from next.deps import Deps, DEFAULT_PROVIDERS

   my_resolver = Deps(*DEFAULT_PROVIDERS, UserProvider())
   # Pass my_resolver to ContextManager(resolver=my_resolver)

You can also register on a custom resolver: ``my_resolver.register(UserProvider)``
or ``@my_resolver.register``. This keeps the global ``resolver`` unchanged and
applies your provider only where that resolver is used.

Parameters that no provider handles receive ``None`` (or keep their default if
they have one).

Replacing the resolver
----------------------

By default, context and form resolution use the global ``resolver`` from
``next.deps``. To use a custom resolver (e.g. with different or additional
providers), pass it when creating the context manager:

.. code-block:: python

   from next.deps import Deps, DEFAULT_PROVIDERS, DependencyResolver

   my_resolver: DependencyResolver = Deps(*DEFAULT_PROVIDERS, MyProvider())
   context_manager = ContextManager(resolver=my_resolver)

The ``Page`` class uses the global resolver by default; to plug in a custom one
you would need to construct the page backend with a context manager that was
given your resolver (implementation may vary by how you wire the backend).

API reference
-------------

For the full API of the ``next.deps`` module (resolver, Deps, providers, protocol),
see :doc:`/content/api/api/deps`.
