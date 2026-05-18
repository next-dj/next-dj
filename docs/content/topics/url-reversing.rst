.. _topics-url-reversing:

URL Reversing
=============

next.dj generates URL names for every file-routed page.
This page covers the two reverse helpers in ``next.urls.reverse``, ``page_reverse`` for building a URL from a directory-shaped template and ``with_query`` for tweaking the query string of an existing URL.
You will use these helpers anywhere Python code needs an absolute or relative URL, including action redirects, signal payloads, and tests.

.. contents::
   :local:
   :depth: 2

Overview
--------

File-routed pages register Django URL names of the form ``next:page_<segments>``.
The ``page_`` prefix is the ``URL_NAME_TEMPLATE`` setting, whose default is ``page_{name}``.
You can pass those names to the standard ``django.urls.reverse`` function.
``page_reverse`` does the same thing but accepts the directory template as input, which keeps callers close to the file tree they already understand.
``page_reverse`` reads ``URL_NAME_TEMPLATE`` itself, so it keeps working when you change the prefix.

``with_query`` composes the query string of a URL that already exists.
Pass keyword arguments to add or replace query parameters, pass ``None`` to remove a key, pass a list or tuple to repeat a key.

page_reverse
------------

The helper takes a path template that mirrors the directory tree.
Bracket parameters drop their type prefix.
Captured values are passed as keyword arguments.

.. code-block:: python
   :caption: examples

   from next.urls.reverse import page_reverse

   # routes/page.py
   page_reverse()                              # "/"

   # routes/blog/page.py
   page_reverse("blog")                        # "/blog/"

   # routes/posts/[slug]/page.py
   page_reverse("posts/[slug]", slug="hello")  # "/posts/hello/"

   # routes/posts/[int:post_id]/page.py
   page_reverse("posts/[post_id]", post_id=7)  # "/posts/7/"

The template is normalised through the same parser the router uses for URL name generation.
You can pass either ``[slug]`` or ``[str:slug]`` in the template, the parser only looks at the parameter name.

The resulting segment string is fed into ``URL_NAME_TEMPLATE`` before the lookup.
The default ``page_{name}`` yields ``next:page_posts_slug``.
Changing ``URL_NAME_TEMPLATE`` changes the name ``page_reverse`` resolves against without any call-site edits.
See :doc:`file-router` for the setting and the segment-naming rules.

Namespace Override
~~~~~~~~~~~~~~~~~~

The default namespace is ``next``, configured through ``next.urls.manager.app_name``.
Pass ``namespace=`` to point at a different installation when several routers are mounted under different namespaces.

.. code-block:: python
   :caption: secondary namespace

   page_reverse("dashboard", namespace="admin")  # "/admin/dashboard/"

When to Use page_reverse Instead of reverse
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Both helpers produce the same URL.
``django.urls.reverse("next:page_posts_slug", kwargs={"slug": "hello"})`` and ``page_reverse("posts/[slug]", slug="hello")`` are equivalent.
Use ``page_reverse`` when the call site references the directory tree, ``reverse`` when the call site already has the URL name in a variable.

with_query
----------

The helper takes a URL string and adds, replaces, or removes query parameters.

.. code-block:: python
   :caption: query manipulation

   from next.urls.reverse import with_query

   with_query("/search/", query="next.dj")
   # "/search/?query=next.dj"

   with_query("/search/?query=django", query="next.dj")
   # "/search/?query=next.dj"

   with_query("/search/?query=django&page=2", page=None)
   # "/search/?query=django"

   with_query("/filter/", tag=["python", "django"])
   # "/filter/?tag=python&tag=django"

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

   from next.urls.reverse import page_reverse, with_query


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
   :caption: notes/routes/page.py

   from django.http import HttpResponseRedirect
   from notes.forms import NoteForm

   from next.forms import action
   from next.urls.reverse import page_reverse


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
   from next.urls.reverse import page_reverse


   @component.context("href")
   def href(note: Note) -> str:
       return page_reverse("notes/[id]", id=note.id)

Pagination
~~~~~~~~~~

``with_query`` is the natural home for pagination links because each page changes one parameter.

.. code-block:: python
   :caption: pagination helper

   from next.urls.reverse import with_query


   def page_link(request, page: int) -> str:
       return with_query(request.get_full_path(), page=page)

Testing
-------

Both helpers are pure functions and are safe to call from tests without any Django request context.

.. code-block:: python
   :caption: tests

   from next.urls.reverse import page_reverse, with_query


   def test_page_reverse_empty() -> None:
       assert page_reverse() == "/"


   def test_with_query_drops_none() -> None:
       assert with_query("/?a=1", a=None) == "/"

Reading the Query String Back
-----------------------------

The helpers on this page write query strings.
To read them in a page or component, annotate a parameter with the ``DQuery[T]`` marker.
``with_query`` and ``DQuery`` are two ends of the same wire.

``DQuery[list[T]]`` accepts three wire formats for a repeated parameter.

- Repeated keys, ``?tag=a&tag=b``.
- Bracket suffix, ``?tag[]=a&tag[]=b``.
- Comma-delimited, ``?tag=a,b``.

``with_query`` emits the repeated-key form when you pass a list.
Captured **path** segments are separate. They flow through ``DUrl`` or plain URL kwargs as described in :doc:`dependency-injection`.

See :doc:`/content/howto/read-query-parameters` for the full typed-query walkthrough.

See Also
--------

.. seealso::

   :doc:`file-router` for the URL name format and route shape.
   :doc:`/content/howto/reverse-urls` for a recipe-shaped walkthrough.
   :doc:`/content/ref/urls` for the full API.
