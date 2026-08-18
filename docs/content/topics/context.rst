.. _topics-context:

Context
=======

Context is the data that pages and components publish into their template scope.
This page covers the two shapes of the ``@context`` decorator and the ways to vary them.
It also walks inheritance down the route tree, how component context differs from page context, and how to expose values to the JavaScript bundle.
A final section covers how to swap out the serializer that ships values to the browser.

This page is the concept reference for context.
Once you understand the decorator and the ``serialize`` flag here, :doc:`/content/topics/static-assets/js-context` covers the full ``window.Next.context`` mechanics.
The :doc:`/content/howto/override-the-js-context-serializer` how-to walks through replacing the serializer.

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

Framework-provided keys
~~~~~~~~~~~~~~~~~~~~~~~

A page render starts with three keys populated.
A component body rendered by ``{% component %}`` carries a fourth.
The two path keys are seeded before any user-defined ``@context`` callable runs, while ``request`` joins the scope after context collection finishes.

``request``.
   The active :class:`~django.http.HttpRequest`, when the route was reached through the HTTP stack.

``current_template_path``.
   The absolute path of the body source on disk.
   It points at the sibling ``template.djx`` when one exists, or at the ``page.py`` itself when the body comes from ``render`` or ``template``.

``current_page_module_path``.
   The absolute path of the ``page.py`` module being rendered.

``current_component_module_path``.
   The absolute path of the ``component.py`` beside the component template being rendered, or ``None`` for a component without one.
   The ``{% component %}`` tag writes it for the component's own template body, so it never leaks into slot bodies rendered by the page.

A user ``@context`` callable reads ``request`` through an ``HttpRequest`` annotation rather than by parameter name.
These path keys live in the template scope for the ``{% form %}`` and ``{% component %}`` tags to consume.
They are not injected into a context callable by parameter name.

The decorator
-------------

The page-side ``@context`` decorator has two shapes.
One is a keyed single value, the other is an unkeyed dict.
The ``inherit_context`` flag and direct registration, covered after the two shapes, vary how a function is registered.

Keyed single value
~~~~~~~~~~~~~~~~~~

The most common shape.
The decorator takes a single key and the function returns the value.

.. code-block:: python
   :caption: notes/pages/page.py

   from next import context
   from notes.models import Note

   @context("notes")
   def recent_notes() -> list[Note]:
       return list(Note.objects.all())

Templates reference the value as ``{{ notes }}``.

Unkeyed dict
~~~~~~~~~~~~

Decorating a function with bare ``@context`` and returning a dict merges every key into the template scope.

.. code-block:: python
   :caption: shared dependency

   from next import context

   @context
   def post_context(post: Post) -> dict[str, object]:
       return {
           "post": post,
           "comments": list(post.comment_set.all()),
       }

This shape runs the dependency once.
Two separate ``@context("post")`` and ``@context("comments")`` would each hit the resolver and possibly the database twice.

The inherit_context flag
~~~~~~~~~~~~~~~~~~~~~~~~

``inherit_context=True`` makes a keyed value visible to every descendant route, not only to the page that declares it.

.. code-block:: python
   :caption: notes/pages/page.py

   from next import context

   @context("site_name", inherit_context=True)
   def site_name() -> str:
       return "Notes"

Use this for header copy, brand colors, feature flags, and other shared values.
Without the flag the value is only available when that exact ``page.py`` handles the request, and descendant routes cannot read it.

Reusing a shared helper
~~~~~~~~~~~~~~~~~~~~~~~

``@context`` keys the registration on the file where the decorated function is declared, not on the file that runs the decorator.
A helper that lives in a shared module therefore needs a thin wrapper in the page module that uses it.

.. code-block:: python
   :caption: notes/pages/dashboard/page.py

   from next import context
   from notes.cache import pending_clicks

   @context("pending_clicks")
   def dashboard_pending_clicks() -> dict[str, int]:
       return pending_clicks()

The same rule holds for ``@component.context``.
Decorating the imported helper directly registers it under the module that declares it, where no render reaches it.
A function imported from a sibling ``page.py`` lands the same way, on that other page rather than on the one running the decorator.
``manage.py check`` reports both as ``next.E074`` for a page context and ``next.E075`` for a component one.
:doc:`/content/topics/forms/actions` covers ``@action`` and what a decorator stacked under it has to preserve.

The decorated object has to carry a declaring file of its own, which a function, a class, or a ``functools.partial`` over either does.
A built-in such as ``datetime.now`` carries none, and decorating one raises ``TypeError`` at import time naming the object.
Wrap it in a function declared in the page module instead.

Reading values into a context function
--------------------------------------

A context function receives its parameters through the :doc:`dependency injector <dependency-injection>`.
Three forms pull a value out of the surrounding context, and they differ only in how explicit the source is.

Plain parameter name.
   Declare a parameter whose name matches a context key and the value is injected with no marker.
   ``def greeting(user_name): ...`` receives the ``user_name`` context value.
   This terse form implies the source from the parameter name.

``Context(...)`` default.
   ``Context()`` reads the parameter name from the context, exactly like the plain form.
   ``Context("user_name")`` reads a named key when the parameter name differs from the key.
   ``Context("user_name", default=...)`` supplies a fallback when the key is absent.
   ``Context(callable)`` calls a factory with its own DI-resolved arguments, and ``Context(value)`` injects a constant.
   Use ``Context`` when the source differs from the parameter name, when you need a default, or when you want the source visible at the call site.

The ``Context(callable)`` form is useful when a parameter needs a value computed from a factory rather than a context key.
The factory takes its own dependency-injected arguments, so it can ask for the request, captured URL parameters, any registered provider, and the active form when one is bound.

.. code-block:: python
   :caption: notes/pages/notes/[int:note_id]/page.py

   from next import context
   from next.pages import Context
   from next.urls import DUrl
   from notes.models import Note

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

Resolution order
----------------

The framework computes the template scope in this order.

1. URL kwargs from the matched route are seeded into the context dict.
2. Inherited context functions from every ancestor ``page.py``, walked from the current page upward through every ancestor directory, bounded at 64 levels.
3. Page level context functions declared in the current ``page.py``.
4. Context processors run after every ``@context`` callable.
   A processor is called with the request, and the callable must declare a parameter named ``request``.
   The first source is ``OPTIONS.context_processors`` on each page backend entry inside ``PAGE_BACKENDS``.
   The second source is the ``context_processors`` list of the first ``TEMPLATES`` entry in Django settings.
   See :ref:`ref-settings` and :doc:`project-layout` for the backend layout.
   The two lists merge in that order with duplicate dotted paths dropped, so a processor listed twice runs once.
   Each processor return dict is applied with ``update``, so a processor key overwrites a page or inherited value.
5. Component context functions when a ``{% component %}`` tag is encountered during render.

A later step that uses the same key overrides earlier values.
The full merged dict is shared across the entire ``layout.djx`` chain for that request, so all layout wrappers see the same final scope.
The :doc:`layouts` page restates this from the layout side under *Context processors*.

Inheritance rules
-----------------

Inherited context follows the filesystem route tree.
The framework walks up from the current ``page.py`` directory and runs every ``@context`` callable marked ``inherit_context=True`` that it finds in ancestor ``page.py`` files.

- A ``page.py`` at ``notes/pages/`` publishes inherited values for every page under that root.
- A ``page.py`` at ``notes/pages/admin/`` publishes inherited values only for pages under ``/admin/``.
- A page at ``/admin/links/`` sees both layers because it sits below both directories.

When two ancestor directories publish the same inherited key, the value from the outermost ancestor wins.

The current page can shadow an inherited value by declaring a context function with the same key.
The page level value takes precedence, and every layout wrapper in the chain sees that value.

Inherited function that names a URL parameter
---------------------------------------------

When an inherited context function is keyed under the same name as a captured URL segment, the parameter it asks for changes type across runs.
On a descendant request the callable runs once and receives the raw URL string.
On the declaring page's own request it runs twice, first in the inherited pass with the raw string, then in the page pass with the object the first run produced.
Leave the parameter untyped and return early when it is already a model instance.

.. code-block:: python
   :caption: notes/pages/notes/[category]/page.py

   from next import context
   from notes.models import Category

   @context("category", inherit_context=True)
   def category(category: object) -> Category:
       if isinstance(category, Category):
           return category
       return Category.objects.get(slug=category)

An annotation cannot be honest for both runs, because the second run on the declaring page receives the already resolved object, so leave the parameter untyped.

.. _topics-context-serialization:

Serialization for the browser
-----------------------------

next.dj ships a ``window.Next`` object to the browser through the :doc:`static pipeline <static-assets/index>`.
Pass ``serialize=True`` on ``@context`` or ``@component.context`` to publish the return value under ``window.Next.context``.
Pass ``serializer=`` on that decorator for a per-key encoder, or set ``NEXT_FRAMEWORK["JS_CONTEXT_SERIALIZER"]`` for a project-wide default.

A value marked ``serialize=True`` must be encodable by the active serializer.
The default ``JsonJsContextSerializer`` runs values through Django ``DjangoJSONEncoder``.
That encoder handles primitives, ``list``, ``dict``, ``datetime``, ``date``, ``time``, ``timedelta``, ``Decimal``, ``UUID``, and Django ``Promise`` instances such as lazy translation strings.
Switching ``JS_CONTEXT_SERIALIZER`` to ``PydanticJsContextSerializer`` also unwraps :class:`pydantic.BaseModel` subclasses via ``model_dump``.
A ``QuerySet``, a ``Manager``, a bare model instance, a Django ``Form``, and any other unsupported type raises ``TypeError`` at render time with the offending key in the message.
Materialise such values before returning.
Use ``list(queryset)`` for collections and a plain ``dict`` projection for model instances.

Values not marked ``serialize=True`` stay server-side only.
Template rendering iterates a queryset or resolves a lazy string directly.
The materialisation rule applies only to keys that travel to the client.

See :doc:`static-assets/js-context` for serializers, duplicate-key policies, ``NEXT_JS_OPTIONS``, and reading values from co-located JS.
See :doc:`/content/howto/override-the-js-context-serializer` for a guided recipe when the default JSON encoder is not enough.

Component context vs page context
---------------------------------

Component context and page context share the same decorator pattern but differ in scope.

Page context.
   Resolves once per request for the ``page.py`` module that defines it.
   Use ``inherit_context=True`` to make it available to every descendant route in the filesystem tree.

Component context.
   Resolves once per component render.
   The framework forwards the surrounding template scope into the component automatically.
   The ``@component.context`` decorator accepts only ``serialize`` and ``serializer``.
   There is no ``inherit_context`` flag, and component context never flows beyond the component that declares it.

A component context function can ask for any value that the template forwards, plus any value that the dependency injector knows how to produce.
This includes the request, captured URL parameters, query strings, and custom providers.

Signal when context registers
-----------------------------

The framework fires ``context_registered`` after a ``@context`` callable in a ``page.py`` joins the registry.
Subscribe to it when an external system needs to track page context functions across reloads.

``@component.context`` does not emit its own signal.
Folder discovery registers components through ``register_many``, which fires ``components_registered`` once per discovery batch with an ``infos`` tuple of ``ComponentInfo``.
The singular ``component_registered`` fires only on a one-at-a-time ``register`` call.

Common patterns
---------------

Per page title
~~~~~~~~~~~~~~

Publish the page title from each page.

.. code-block:: python
   :caption: notes/pages/notes/[id]/page.py

   from next import context
   from next.urls import DUrl
   from notes.models import Note

   @context("page_title")
   def page_title(note_id: DUrl[int]) -> str:
       return Note.objects.get(pk=note_id).title

Render it in the layout.

.. code-block:: jinja
   :caption: layout

   <title>{{ page_title|default:"Notes" }}</title>

Site wide configuration
~~~~~~~~~~~~~~~~~~~~~~~

Publish branding and navigation from the root ``page.py`` with ``inherit_context=True``.
Every page under that directory reads the values without redeclaring them.

Filter values from query string
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Combine a context function with the ``DQuery[T]`` marker to read filters from the URL.

.. code-block:: python
   :caption: notes/pages/page.py

   from next import context
   from next.urls import DQuery

   @context("active_tag")
   def active_tag(tag: DQuery[str] = "") -> str:
       return tag

Shared dependency
~~~~~~~~~~~~~~~~~

When two context functions need the same expensive value, factor the dependency into a custom DI provider or use the unkeyed dict shape.

System checks
-------------

The framework validates context functions through ``check_context_functions``.
A keyless ``@context`` callable with a non-dict return annotation reports ``next.E029`` during ``uv run python manage.py check``.
A keyless callable with no return annotation is accepted by the check and raises ``TypeError`` at render time if the value is not a mapping.
Functions decorated with a key may return any value.

A ``page.py`` holds one keyless slot.
Registering a second bare ``@context`` replaces the first, and ``next.E018`` reports the shadowed callable.
Give each function a key or merge them.

``check_context_processor_signature`` reports ``next.E040`` when a processor listed under ``OPTIONS.context_processors`` does not accept a ``request`` parameter.
The check covers ``PAGE_BACKENDS`` entries only, not the Django ``TEMPLATES`` list.

See also
--------

.. seealso::

   :doc:`pages` for page level context.
   :doc:`layouts` for layout composition rules.
   :doc:`dependency-injection` for the resolver and providers.
   :doc:`static-assets/js-context` for the browser side ``Next`` object.
   :doc:`/content/ref/decorators` for the page-side ``@context`` API.
   :doc:`/content/ref/components` for the ``@component.context`` API.
