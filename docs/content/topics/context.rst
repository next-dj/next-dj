.. _topics-context:

Context
=======

Context is the data that pages, layouts, and components publish into their template scope.
This page covers the four shapes of the ``@context`` decorator, how inheritance flows down the page tree, how component context differs from page context, how to expose values to the JavaScript bundle, and how to swap out the serializer that ships values to the browser.

.. contents::
   :local:
   :depth: 2

Overview
--------

A context function is a Python callable that returns a value.
The framework calls it at request time, resolves its parameters through the dependency injector, and publishes the result under a key that the template can render.

Three call sites share the same decorator surface.

Page context.
   ``@context("key")`` in a ``page.py``.
   Resolves once for that page request.

Layout context.
   ``@context("key")`` in a ``layout.py``.
   Resolves once per request for every descendant page when ``inherit_context=True``.

Component context.
   ``@component.context("key")`` in a ``component.py``.
   Resolves once per component instance during render.

The Decorator Shapes
--------------------

The ``next.pages.context`` decorator accepts four shapes.

Keyed Single Value
~~~~~~~~~~~~~~~~~~

The most common shape.
The decorator takes a single name and the function returns the value.

.. code-block:: python
   :caption: notes/routes/page.py

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

Inherited Single Value
~~~~~~~~~~~~~~~~~~~~~~

``inherit_context=True`` makes the value visible to every descendant page, not only to the layout that declares it.

.. code-block:: python
   :caption: notes/routes/layout.py

   from next.pages import context


   @context("site_name", inherit_context=True)
   def site_name() -> str:
       return "Notes"

Use this for header copy, brand colors, feature flags, and other layout-wide values.
Without the flag the layout still renders the value in its own template but pages cannot read it.

Direct Registration
~~~~~~~~~~~~~~~~~~~

Treat ``context("key")`` as a callable that registers an existing function.

.. code-block:: python
   :caption: registering an external function

   from notes.cache import pending_clicks

   from next.pages import context


   context("pending_clicks")(pending_clicks)

This is useful when the function lives in a shared module and you want to register it without a decorator on the original source.

Resolution Order
----------------

The framework computes the template scope in this order.

1. Context processors configured on the page backend.
2. Inherited context functions from every ancestor layout, evaluated from the page root downward.
3. Page level context functions declared in ``page.py``.
4. Component context functions when a ``{% component %}`` tag is encountered.

A later step that uses the same key overrides earlier values for that scope only.
The layout that publishes the key still sees its own value, the page that overrides it sees the new value, and so on.

Inheritance Rules
-----------------

Inherited context follows the layout tree, not the URL tree.

- A ``layout.py`` at ``notes/routes/`` publishes values for every page under that root.
- A ``layout.py`` at ``notes/routes/admin/`` publishes values only for pages under ``/admin/``.
- A page at ``/admin/links/`` sees both layers because it sits below both layouts.

The page itself can shadow an inherited value by declaring a context function with the same key.
The page level value takes precedence for that one request.

Inherited Function That Names a URL Parameter
---------------------------------------------

A subtle case appears when an inherited context function has the same name as a captured URL parameter, and its own parameter also carries that name.
The framework runs the function twice.
The first run receives the raw URL value, a string.
The second run receives the value the first run produced, already resolved.

Leave the parameter untyped and short circuit on the resolved type.

.. code-block:: python
   :caption: notes/routes/notes/[category]/page.py

   from notes.models import Category

   from next.pages import context


   @context("category", inherit_context=True)
   def category(category: object) -> Category:
       if isinstance(category, Category):
           return category
       return Category.objects.get(slug=category)

The ``isinstance`` guard makes the second run a no op.
Declaring the parameter as ``str`` would break the second run.

Serialization for the Browser
-----------------------------

next.dj ships a JavaScript object named ``Next`` to the browser through the static pipeline.
Any context value can opt into serialisation through the ``serialize`` argument.

.. code-block:: python
   :caption: shipping context to the browser

   from notes.models import Note

   from next.pages import context


   @context("note_count", serialize=True)
   def note_count() -> int:
       return Note.objects.count()

The default serializer is JSON.
The value lands in the browser at ``Next.context.note_count``.
The static pipeline emits one script tag per page that exposes the serialised values.

Per Key Serializer
~~~~~~~~~~~~~~~~~~

Pass ``serializer=`` to override the default for a single key.

.. code-block:: python
   :caption: custom serializer per key

   from pydantic import BaseModel

   from next.pages import context


   class NoteOut(BaseModel):
       id: int
       title: str


   def pydantic_serializer(value: NoteOut) -> dict:
       return value.model_dump()


   @context("featured", serialize=True, serializer=pydantic_serializer)
   def featured() -> NoteOut:
       return NoteOut(id=1, title="Hello")

The framework calls the function before JSON encoding so any Python value can reach the browser.

Project Wide Serializer
~~~~~~~~~~~~~~~~~~~~~~~

Configure a default serializer in ``NEXT_FRAMEWORK["JS_CONTEXT_SERIALIZER"]`` to apply it to every serialised value without per call overrides.

.. code-block:: python
   :caption: config/settings.py

   NEXT_FRAMEWORK = {
       "JS_CONTEXT_SERIALIZER": "notes.serializers.JsContextSerializer",
   }

See :doc:`static-assets/js-context` for the full serialization pipeline.

Component Context vs Page Context
---------------------------------

Component context and page context share the same decorator pattern but differ in scope.

Page context.
   Resolves once per request for the page module that defines it.
   Inherits down the layout chain when ``inherit_context=True`` is set.

Component context.
   Resolves once per component render.
   The framework forwards the surrounding template scope into the component.
   ``inherit_context=True`` makes the value reachable from nested component calls and from slot content.

A component context function can ask for any value that the template forwards, plus any value that the dependency injector knows how to produce.
This includes the request, captured URL parameters, query strings, and custom providers.

Signal When Context Registers
-----------------------------

The framework fires a signal when a context function joins the registry.

- ``context_registered`` for ``@context`` in a ``page.py`` or ``layout.py``.
- ``component_registered`` for ``@component.context`` in a ``component.py``.

Subscribe to either signal when an external system needs to track context functions across reloads.

Common Patterns
---------------

Per Page Title
~~~~~~~~~~~~~~

Publish the page title from each page.

.. code-block:: python
   :caption: notes/routes/notes/[id]/page.py

   from notes.models import Note

   from next.pages import context
   from next.urls.markers import DUrl


   @context("page_title")
   def page_title(note: Note) -> str:
       return note.title

Render it in the layout.

.. code-block:: jinja
   :caption: layout

   <title>{{ page_title|default:"Notes" }}</title>

Site Wide Configuration
~~~~~~~~~~~~~~~~~~~~~~~

Publish branding and navigation from a single layout with ``inherit_context=True``.
Pages do not redeclare those values.

Filter Values From Query String
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Combine a context function with the ``DQuery[T]`` marker to read filters from the URL.

.. code-block:: python
   :caption: notes/routes/page.py

   from next.pages import context
   from next.urls.markers import DQuery


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
Functions with a keyed name may return any value.
A wrong return shape surfaces during ``uv run python manage.py check``.

See Also
--------

.. seealso::

   :doc:`pages` for page level context.
   :doc:`layouts` for layout level inheritance.
   :doc:`dependency-injection` for the resolver and providers.
   :doc:`static-assets/js-context` for the browser side ``Next`` object.
   :doc:`/content/ref/decorators` for the ``@context`` and ``@component.context`` APIs.
