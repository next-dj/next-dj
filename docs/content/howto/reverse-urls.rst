.. _howto-reverse-urls:

Reverse URLs
============

Problem
-------

You want to build a URL from Python code instead of hard coding a path.

Solution
--------

Use ``page_reverse`` to reverse a file-routed page URL.
Use ``with_query`` to add, replace, or remove query string parameters.

Walkthrough
-----------

Reverse a Static Page
~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python
   :caption: notes/pages/page.py

   from next.urls import page_reverse

   url = page_reverse()             # "/"
   url = page_reverse("blog")       # "/blog/"
   url = page_reverse("about/team") # "/about/team/"

Reverse a Captured Page
~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python
   :caption: captured params

   from next.urls import page_reverse

   url = page_reverse("notes/[id]", id=7)
   # "/notes/7/"

   url = page_reverse("posts/[slug]/comments", slug="hello")
   # "/posts/hello/comments/"

The path template uses the same bracket syntax that the file router uses.
The parameter name in the template matches the keyword argument.

Add Query Parameters
~~~~~~~~~~~~~~~~~~~~

.. code-block:: python
   :caption: search url

   from next.urls import page_reverse, with_query

   url = with_query(page_reverse("search"), query="next.dj", page=2)
   # "/search/?query=next.dj&page=2"

Pass ``None`` to remove a key.
Pass a list or tuple to repeat a key in the output.

Use Inside an Action Handler
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python
   :caption: notes/pages/page.py

   from django.http import HttpResponseRedirect
   from next.forms import action
   from next.urls import page_reverse
   from notes.forms import NoteForm

   @action("create_note", form_class=NoteForm)
   def create_note(form: NoteForm) -> HttpResponseRedirect:
       note = form.save()
       return HttpResponseRedirect(page_reverse("notes/[id]", id=note.id))

Use Inside a Component
~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: python
   :caption: _components/note_link/component.py

   from next.components import component
   from next.urls import page_reverse
   from notes.models import Note

   @component.context("href")
   def href(note: Note) -> str:
       return page_reverse("notes/[id]", id=note.id)

Verification
------------

Print the URL from a Django shell.

.. code-block:: bash
   :caption: shell

   uv run python manage.py shell -c "
   from next.urls import page_reverse
   print(page_reverse('notes/[id]', id=1))
   "

The shell prints ``/notes/1/``.

See Also
--------

.. seealso::

   :doc:`/content/topics/url-reversing` for the topic guide.
   :doc:`/content/topics/file-router` for the URL name format.
