.. _topics-url-reversing:

URL Reversing
=============

next.dj generates URL names for every file-routed page.
This page covers the two reverse helpers ``page_reverse`` and ``with_query`` exported from ``next.urls``.
``page_reverse`` builds a URL from a directory-shaped template.
``with_query`` adjusts the query string of an existing URL.
These helpers cover every location where Python code needs a URL path, including action redirects, signal payloads, and tests.

.. contents::
   :local:
   :depth: 2

Overview
--------

File-routed pages register Django URL names of the form ``next:page_<segments>``.
Those names work with the standard ``django.urls.reverse`` function.
``page_reverse`` accepts the directory template instead of the computed name, and reads ``URL_NAME_TEMPLATE`` itself so it keeps working when the prefix changes.
See :doc:`file-router` for the segment-naming rules and the ``URL_NAME_TEMPLATE`` setting.

``with_query`` composes the query string of a URL that already exists.
Pass keyword arguments to add or replace query parameters, pass ``None`` to remove a key, pass a list or tuple to repeat a key.

page_reverse
------------

The helper takes a path template that mirrors the directory tree.
Bracket parameters drop their type prefix.
Captured values are passed as keyword arguments.

.. code-block:: python
   :caption: examples

   from next.urls import page_reverse

   # routes/page.py
   page_reverse()                              # "/"

   # routes/blog/page.py
   page_reverse("blog")                        # "/blog/"

   # routes/posts/[slug]/page.py
   page_reverse("posts/[slug]", slug="hello")  # "/posts/hello/"

   # routes/posts/[int:post_id]/page.py
   page_reverse("posts/[int:post_id]", post_id=7)  # "/posts/7/"

The template is normalised through the same parser the router uses for URL name generation.
The template must repeat the same bracket text the directory uses, including any converter prefix, because the URL name is computed from the raw segment text.

The resulting segment string is fed into ``URL_NAME_TEMPLATE`` before the lookup.
The default ``page_{name}`` yields ``next:page_posts_slug``.
Changing ``URL_NAME_TEMPLATE`` changes the name ``page_reverse`` resolves against without any call-site edits.
See :doc:`file-router` for the setting and the segment-naming rules.

Namespace Override
~~~~~~~~~~~~~~~~~~

The default namespace is ``next``, configured through ``next.urls.manager.app_name``.
``page_reverse`` forwards ``namespace=`` straight to ``django.urls.reverse``, so the value must name a Django URL namespace that already exists.
The ``next`` namespace exists because ``next.urls`` sets ``app_name = "next"``.
A second namespace exists only when ``next.urls`` is mounted again under an explicit ``namespace`` argument.

.. code-block:: python
   :caption: config/urls.py

   from django.urls import include, path

   urlpatterns = [
       path("", include("next.urls")),
       path("admin/", include("next.urls", namespace="admin")),
   ]

With that second mount in place, ``page_reverse`` can target it.

.. code-block:: python
   :caption: secondary namespace

   page_reverse("dashboard", namespace="admin")  # "/admin/dashboard/"

The ``/admin/`` prefix comes from the ``path("admin/", ...)`` mount, not from the ``namespace`` argument.
``page_reverse`` only selects which namespace ``reverse`` resolves against.

Passing a ``namespace`` that no Django mount registers raises ``NoReverseMatch``.

When to Use page_reverse Instead of reverse
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``django.urls.reverse("next:page_posts_slug", kwargs={"slug": "hello"})`` and ``page_reverse("posts/[slug]", slug="hello")`` are equivalent.
Use ``page_reverse`` when the call site references the directory tree, ``reverse`` when the call site already has the URL name in a variable.

with_query
----------

The helper takes a URL string and adds, replaces, or removes query parameters.

.. code-block:: python
   :caption: query manipulation

   from next.urls import with_query

   with_query("/search/", query="next.dj")
   # "/search/?query=next.dj"

   with_query("/search/?query=django", query="next.dj")
   # "/search/?query=next.dj"

   with_query("/search/?query=django&page=2", page=None)
   # "/search/?query=django"

   with_query("/filter/", tag=["python", "django"])
   # "/filter/?tag=python&tag=django"

   # An empty-valued parameter is preserved unless you pass that key.
   with_query("/search/?flag=", query="next.dj")
   # "/search/?flag=&query=next.dj"

Multi Value Keys
~~~~~~~~~~~~~~~~

Passing a list or tuple repeats the key in the output.
A scalar value replaces every existing entry for that key.

.. code-block:: python
   :caption: replace vs repeat

   with_query("/filter/?tag=python&tag=django", tag="rust")
   # "/filter/?tag=rust"

   with_query("/filter/?tag=python", tag=["python", "django"])
   # "/filter/?tag=python&tag=django"

Combining page_reverse and with_query
-------------------------------------

Compose both helpers when the base URL needs both reversal and query parameters.

.. code-block:: python
   :caption: filtered listing

   from next.urls import page_reverse, with_query

   def filtered_notes_url(*, tag: str | None = None, page: int = 1) -> str:
       base = page_reverse("notes")
       return with_query(base, tag=tag, page=page)

   filtered_notes_url(tag="python", page=2)
   # "/notes/?tag=python&page=2"

   filtered_notes_url(tag=None, page=1)
   # "/notes/?page=1"

The ``None`` value drops the key entirely.
This is convenient for building pagination links where the current filter may or may not be set.

Common Patterns
---------------

Redirect After Action
~~~~~~~~~~~~~~~~~~~~~

An action handler can return an ``HttpResponseRedirect`` to a reversed page URL.

.. code-block:: python
   :caption: notes/pages/page.py

   from django.http import HttpResponseRedirect
   from notes.forms import NoteForm
   from next.forms import action
   from next.urls import page_reverse

   @action("create_note", form_class=NoteForm)
   def create_note(form: NoteForm) -> HttpResponseRedirect:
       form.save()
       return HttpResponseRedirect(page_reverse())

Building Links in Components
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A component can compute a URL through ``@component.context``.

.. code-block:: python
   :caption: _components/note_link/component.py

   from notes.models import Note
   from next.components import component
   from next.urls import page_reverse

   @component.context("href")
   def href(note: Note) -> str:
       return page_reverse("notes/[id]", id=note.id)

Pagination
~~~~~~~~~~

``with_query`` is the natural home for pagination links because each page changes one parameter.

.. code-block:: python
   :caption: pagination helper

   from next.urls import with_query

   def page_link(request, page: int) -> str:
       return with_query(request.get_full_path(), page=page)

Testing
-------

``with_query`` is a pure stdlib helper.
It accepts a URL string and keyword arguments and returns a string, so a unit test can call it without configuring Django.

.. code-block:: python
   :caption: with_query test

   from next.urls import with_query

   def test_with_query_drops_none() -> None:
       assert with_query("/?a=1", a=None) == "/"

``page_reverse`` calls into the Django URL resolver, so the test process needs configured Django settings and a loaded URL configuration before the helper resolves anything.
Use ``pytest-django`` or call ``django.setup()`` once, then assert against the produced path.

.. code-block:: python
   :caption: page_reverse test

   from next.urls import page_reverse

   def test_page_reverse_empty() -> None:
       assert page_reverse() == "/"

Reading the Query String Back
-----------------------------

The helpers on this page write query strings.
To read them in a page or component, annotate a parameter with the ``DQuery[T]`` marker.
``with_query`` and ``DQuery`` are two ends of the same wire.

``DQuery[list[T]]`` accepts several wire formats for a repeated parameter, and ``with_query`` emits the repeated-key form when you pass a list.
To read a repeated parameter outside the resolver, call ``get_multi_values(request, name)`` from ``next.urls``, which returns every value for that key as a list.
Captured path segments are separate.
They flow through ``DUrl`` or plain URL kwargs as described in :doc:`dependency-injection`.

See :doc:`/content/howto/read-query-parameters` for the full typed-query walkthrough.

See Also
--------

.. seealso::

   :doc:`file-router` for the URL name format and route shape.
   :doc:`/content/howto/reverse-urls` for a recipe-shaped walkthrough.
   :doc:`/content/ref/urls` for the full API.
