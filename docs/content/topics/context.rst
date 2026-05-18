.. _topics-context:

Context
=======

Context is the data that pages and components publish into their template scope.
This page covers the two shapes of the ``@context`` decorator and the ways to vary them, how inheritance flows down the route tree, how component context differs from page context, how to expose values to the JavaScript bundle, and how to swap out the serializer that ships values to the browser.

This page is the concept reference for context.
Once you understand the decorator and the ``serialize`` flag here, :doc:`/content/topics/static-assets/js-context` covers the full ``window.Next.context`` mechanics and :doc:`/content/howto/override-the-js-context-serializer` walks through replacing the serializer.

.. contents::
   :local:
   :depth: 2

Overview
--------

A context function is a Python callable that returns a value.
The framework calls it at request time, resolves its parameters through the :doc:`dependency injector <dependency-injection>`, and publishes the result under a key that the template can render.

Two call sites share the same decorator surface.

Page context.
   ``@context("key")`` in a ``page.py``.
   Resolves once for that page request.
   Add ``inherit_context=True`` to publish the value to every descendant route.

Component context.
   ``@component.context("key")`` in a ``component.py``.
   Resolves once per component instance during render.

The Decorator
-------------

The ``next.pages.context`` decorator has two shapes.
One is a keyed single value, the other is an unkeyed dict.
The ``inherit_context`` flag and direct registration, covered after the two shapes, vary how a function is registered.

Keyed Single Value
~~~~~~~~~~~~~~~~~~

The most common shape.
The decorator takes a single key and the function returns the value.

.. code-block:: python
   :caption: notes/pages/page.py

   from notes.models import Note
   from next.pages import context

   @context("notes")
   def recent_notes() -> list[Note]:
       return list(Note.objects.all())

Templates reference the value as ``{{ notes }}``.

Unkeyed Dict
~~~~~~~~~~~~

Decorating a function with bare ``@context`` and returning a dict merges every key into the template scope.

.. code-block:: python
   :caption: shared dependency

   from next.pages import context

   @context
   def post_context(post: Post) -> dict[str, object]:
       return {
           "post": post,
           "comments": list(post.comment_set.all()),
       }

This shape runs the dependency once.
Two separate ``@context("post")`` and ``@context("comments")`` would each hit the resolver and possibly the database twice.

The inherit_context Flag
~~~~~~~~~~~~~~~~~~~~~~~~

``inherit_context=True`` makes a keyed value visible to every descendant route, not only to the page that declares it.

.. code-block:: python
   :caption: notes/pages/page.py

   from next.pages import context

   @context("site_name", inherit_context=True)
   def site_name() -> str:
       return "Notes"

Use this for header copy, brand colors, feature flags, and other shared values.
Without the flag the value is only available when that exact ``page.py`` handles the request, and descendant routes cannot read it.

Direct Registration
~~~~~~~~~~~~~~~~~~~

Treat ``context("key")`` as a callable that registers an existing function.

.. code-block:: python
   :caption: registering an external function

   from notes.cache import pending_clicks
   from next.pages import context

   context("pending_clicks")(pending_clicks)

This is useful when the function lives in a shared module and you want to register it without a decorator on the original source.

Reading Values Into a Context Function
--------------------------------------

A context function receives its parameters through the :doc:`dependency injector <dependency-injection>`.
Three forms pull a value out of the surrounding context, and they differ only in how explicit the source is.

Plain parameter name.
   Declare a parameter whose name matches a context key and the value is injected with no marker.
   ``def greeting(user_name): ...`` receives the ``user_name`` context value.
   This is the terse form. The source is implied by the name.

``Context(...)`` default.
   ``Context()`` reads the parameter name from the context, exactly like the plain form.
   ``Context("user_name")`` reads a named key when the parameter name differs from the key.
   ``Context("user_name", default=...)`` supplies a fallback when the key is absent.
   ``Context(callable)`` calls a factory with its own DI-resolved arguments, and ``Context(value)`` injects a constant.
   Use ``Context`` when the source differs from the parameter name, when you need a default, or when you want the source visible at the call site.

The ``Context(callable)`` form is useful when a parameter needs a value computed from a factory rather than a context key.
The factory takes its own dependency-injected arguments, so it can ask for the request, captured URL parameters, or any registered provider.

.. code-block:: python
   :caption: notes/pages/notes/[int:note_id]/page.py

   from notes.models import Note
   from next.pages import Context, context
   from next.urls import DUrl

   def load_note(note_id: DUrl[int]) -> Note:
       return Note.objects.get(pk=note_id)

   @context("word_count")
   def word_count(note: Note = Context(load_note)) -> int:
       return len(note.body.split())

The framework resolves ``load_note`` with its own ``note_id`` argument from the URL, then passes the resulting ``Note`` into ``word_count`` as the ``note`` parameter.

``Depends(...)`` default.
   Reads a callable registered through ``next.deps.resolver.dependency`` rather than the request context.
   Use it for values produced by shared dependency callables.
   See :doc:`dependency-injection`.

``Context("user_name")`` and a plain parameter named ``user_name`` resolve to the same value.
``Context`` is the explicit form, the plain name is the implicit one. ``Depends`` reaches a different registry.

Resolution Order
----------------

The framework computes the template scope in this order.

1. URL kwargs from the matched route are seeded into the context dict.
2. Inherited context functions from every ancestor ``page.py``, walked from the route root inward.
3. Page level context functions declared in the current ``page.py``.
4. Context processors come from ``OPTIONS.context_processors`` on each page backend entry plus the ``context_processors`` list of the first ``TEMPLATES`` entry in Django settings.
   The two lists merge in that order with duplicate dotted paths dropped, so a processor listed twice runs once.
   Each processor return dict is applied with ``update`` after every ``@context`` callable, so a processor key overwrites a page or inherited value.

   .. code-block:: python
      :caption: config/settings.py

      NEXT_FRAMEWORK = {
          "DEFAULT_PAGE_BACKENDS": [
              {
                  "BACKEND": "next.urls.FileRouterBackend",
                  "DIRS": [],
                  "APP_DIRS": True,
                  "PAGES_DIR": "routes",
                  "OPTIONS": {
                      "context_processors": [
                          "django.template.context_processors.request",
                          "django.contrib.auth.context_processors.auth",
                      ],
                  },
              }
          ],
      }

5. Component context functions when a ``{% component %}`` tag is encountered during render.

A later step that uses the same key overrides earlier values.
The full merged dict is shared across the entire ``layout.djx`` chain for that request, so all layout wrappers see the same final scope.
The :doc:`layouts` page restates this from the layout side under *Context Processors*.

Inheritance Rules
-----------------

Inherited context follows the filesystem route tree.
The framework walks up from the current ``page.py`` directory and runs every ``@context`` callable marked ``inherit_context=True`` that it finds in ancestor ``page.py`` files.

- A ``page.py`` at ``notes/pages/`` publishes inherited values for every page under that root.
- A ``page.py`` at ``notes/pages/admin/`` publishes inherited values only for pages under ``/admin/``.
- A page at ``/admin/links/`` sees both layers because it sits below both directories.

The current page can shadow an inherited value by declaring a context function with the same key.
The page level value takes precedence, and every layout wrapper in the chain sees that value.

Inherited Function That Names a URL Parameter
---------------------------------------------

When an inherited context function is keyed under the same name as a captured URL segment, the parameter it asks for changes type across runs.
On the first run it holds the raw URL string. On a descendant re-run it holds the resolved object the function already produced.
Leave the parameter untyped and return early if it is already an instance of the model.

.. code-block:: python
   :caption: notes/pages/notes/[category]/page.py

   from notes.models import Category
   from next.pages import context

   @context("category", inherit_context=True)
   def category(category: object) -> Category:
       if isinstance(category, Category):
           return category
       return Category.objects.get(slug=category)

Declaring the parameter as ``str`` would break the descendant re-run.

Serialization for the Browser
-----------------------------

next.dj ships a ``window.Next`` object to the browser through the :doc:`static pipeline <static-assets/index>`.
Pass ``serialize=True`` on ``@context`` or ``@component.context`` to publish the return value under ``window.Next.context``.
Pass ``serializer=`` on that decorator for a per-key encoder, or set ``NEXT_FRAMEWORK["JS_CONTEXT_SERIALIZER"]`` for a project-wide default.

See :doc:`static-assets/js-context` for serializers, duplicate-key policies, ``NEXT_JS_OPTIONS``, and reading values from co-located JS.
See :doc:`/content/howto/override-the-js-context-serializer` for a guided recipe when the default JSON encoder is not enough.

Component Context vs Page Context
---------------------------------

Component context and page context share the same decorator pattern but differ in scope.

Page context.
   Resolves once per request for the ``page.py`` module that defines it.
   Use ``inherit_context=True`` to make it available to every descendant route in the filesystem tree.

Component context.
   Resolves once per component render.
   The framework forwards the surrounding template scope into the component automatically.
   The ``@component.context`` decorator accepts only ``serialize`` and ``serializer``. There is no ``inherit_context`` flag, and component context never flows beyond the component that declares it.

A component context function can ask for any value that the template forwards, plus any value that the dependency injector knows how to produce.
This includes the request, captured URL parameters, query strings, and custom providers.

Signal When Context Registers
-----------------------------

The framework fires ``context_registered`` after a ``@context`` callable in a ``page.py`` joins the registry.
Subscribe to it when an external system needs to track page context functions across reloads.

``@component.context`` does not emit its own signal.
A component folder fires ``component_registered`` when the folder is discovered and added to the component registry, with a ``ComponentInfo`` payload that describes the whole component.

Common Patterns
---------------

Per Page Title
~~~~~~~~~~~~~~

Publish the page title from each page.

.. code-block:: python
   :caption: notes/pages/notes/[id]/page.py

   from notes.models import Note
   from next.pages import context
   from next.urls import DUrl

   @context("page_title")
   def page_title(note_id: DUrl[int]) -> str:
       return Note.objects.get(pk=note_id).title

Render it in the layout.

.. code-block:: jinja
   :caption: layout

   <title>{{ page_title|default:"Notes" }}</title>

Site Wide Configuration
~~~~~~~~~~~~~~~~~~~~~~~

Publish branding and navigation from the root ``page.py`` with ``inherit_context=True``.
Every page under that directory reads the values without redeclaring them.

Filter Values From Query String
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Combine a context function with the ``DQuery[T]`` marker to read filters from the URL.

.. code-block:: python
   :caption: notes/pages/page.py

   from next.pages import context
   from next.urls import DQuery

   @context("active_tag")
   def active_tag(tag: DQuery[str] = "") -> str:
       return tag

Shared Dependency
~~~~~~~~~~~~~~~~~

When two context functions need the same expensive value, factor the dependency into a custom DI provider or use the unkeyed dict shape.

System Checks
-------------

The framework validates context functions through ``check_context_functions``.
Functions decorated with bare ``@context`` must return a dict.
Functions decorated with a key may return any value.
A wrong return shape surfaces during ``uv run python manage.py check``.

See Also
--------

.. seealso::

   :doc:`pages` for page level context.
   :doc:`layouts` for layout composition rules.
   :doc:`dependency-injection` for the resolver and providers.
   :doc:`static-assets/js-context` for the browser side ``Next`` object.
   :doc:`/content/ref/decorators` for the page-side ``@context`` API.
   :doc:`/content/ref/components` for the ``@component.context`` API.
